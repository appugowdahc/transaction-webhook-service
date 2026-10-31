import asyncio
import logging

import asyncpg

from app import repository
from app.config import Settings

log = logging.getLogger(__name__)


class Worker:
    """Polls the transactions table and processes claimed rows.

    Processing is a sleep standing in for the external API call, so it is
    cheap to run hundreds concurrently on one event loop.
    """

    def __init__(self, pool: asyncpg.Pool, settings: Settings):
        self._pool = pool
        self._settings = settings
        self._in_flight: dict[str, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._run(), name="txn-worker")

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)

        pending = list(self._in_flight.items())
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(t for _, t in pending), return_exceptions=True)

        # Hand unfinished rows back so a restart doesn't wait for lease expiry.
        try:
            await repository.release(self._pool, [tid for tid, _ in pending])
        except Exception:
            log.exception("failed to release leases on shutdown")

    async def _run(self) -> None:
        s = self._settings
        while True:
            try:
                await repository.fail_exhausted(self._pool, s.max_attempts)
                free = s.worker_concurrency - len(self._in_flight)
                if free > 0:
                    claims = await repository.claim_batch(
                        self._pool, free, s.lease_seconds, s.processing_delay_seconds
                    )
                    for c in claims:
                        task = asyncio.create_task(self._process(c))
                        self._in_flight[c.transaction_id] = task
                        task.add_done_callback(
                            lambda _t, tid=c.transaction_id: self._in_flight.pop(tid, None)
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                # DB blip etc. Keep the loop alive; rows are still safe in the table.
                log.exception("worker poll failed")
            await asyncio.sleep(s.worker_poll_interval_seconds)

    async def _process(self, claim: repository.Claim) -> None:
        tid = claim.transaction_id
        try:
            await asyncio.sleep(claim.remaining_delay)
            if await repository.mark_processed(self._pool, tid):
                log.info("processed %s", tid)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Lease stays in place; the row is retried after it expires.
            log.exception("processing failed for %s", tid)
