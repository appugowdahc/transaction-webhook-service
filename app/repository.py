from dataclasses import dataclass

import asyncpg

from app.models import TransactionWebhook


@dataclass(frozen=True)
class Claim:
    transaction_id: str
    # Seconds left until created_at + processing delay. 0 if already past.
    remaining_delay: float


async def insert_if_absent(pool: asyncpg.Pool, txn: TransactionWebhook) -> bool:
    """Store a webhook. Returns False if transaction_id was already known.

    The primary key does the dedup, so concurrent duplicates can't both insert.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO transactions
            (transaction_id, source_account, destination_account, amount, currency)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (transaction_id) DO NOTHING
        RETURNING transaction_id
        """,
        txn.transaction_id,
        txn.source_account,
        txn.destination_account,
        txn.amount,
        txn.currency,
    )
    return row is not None


async def get(pool: asyncpg.Pool, transaction_id: str) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        SELECT transaction_id, source_account, destination_account, amount,
               currency, status, created_at, processed_at
        FROM transactions
        WHERE transaction_id = $1
        """,
        transaction_id,
    )


async def fail_exhausted(pool: asyncpg.Pool, max_attempts: int) -> int:
    """Give up on rows that keep getting claimed without finishing."""
    result = await pool.execute(
        """
        UPDATE transactions
        SET status = 'FAILED', locked_until = NULL
        WHERE status = 'PROCESSING'
          AND attempts >= $1
          AND (locked_until IS NULL OR locked_until < now())
        """,
        max_attempts,
    )
    return int(result.split()[-1])


async def claim_batch(
    pool: asyncpg.Pool, limit: int, lease_seconds: float, delay_seconds: float
) -> list[Claim]:
    """Lease up to `limit` unfinished rows for this worker.

    SKIP LOCKED lets several instances poll at once without blocking each
    other. A row whose lease has expired (worker crashed or was killed) is
    picked up again.
    """
    rows = await pool.fetch(
        """
        UPDATE transactions t
        SET locked_until = now() + make_interval(secs => $2),
            attempts = attempts + 1
        WHERE t.transaction_id IN (
            SELECT transaction_id FROM transactions
            WHERE status = 'PROCESSING'
              AND (locked_until IS NULL OR locked_until < now())
            ORDER BY created_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING t.transaction_id,
                  GREATEST(0, EXTRACT(EPOCH FROM (t.created_at - now())) + $3)::float8
                      AS remaining_delay
        """,
        limit,
        lease_seconds,
        delay_seconds,
    )
    return [Claim(r["transaction_id"], r["remaining_delay"]) for r in rows]


async def mark_processed(pool: asyncpg.Pool, transaction_id: str) -> bool:
    row = await pool.fetchrow(
        """
        UPDATE transactions
        SET status = 'PROCESSED', processed_at = now(), locked_until = NULL
        WHERE transaction_id = $1 AND status = 'PROCESSING'
        RETURNING transaction_id
        """,
        transaction_id,
    )
    return row is not None


async def release(pool: asyncpg.Pool, transaction_ids: list[str]) -> None:
    """Drop leases so another instance can take the rows immediately."""
    if not transaction_ids:
        return
    await pool.execute(
        """
        UPDATE transactions SET locked_until = NULL
        WHERE transaction_id = ANY($1::text[]) AND status = 'PROCESSING'
        """,
        transaction_ids,
    )
