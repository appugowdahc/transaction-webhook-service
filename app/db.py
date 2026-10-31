import asyncpg

from app.config import Settings

# One table is both the record store and the work queue. status = PROCESSING
# means "not done yet"; the partial index keeps the worker's scan cheap once
# most rows are PROCESSED.
SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id      TEXT PRIMARY KEY,
    source_account      TEXT NOT NULL,
    destination_account TEXT NOT NULL,
    amount              NUMERIC(18, 2) NOT NULL,
    currency            CHAR(3) NOT NULL,
    status              TEXT NOT NULL DEFAULT 'PROCESSING'
                        CHECK (status IN ('PROCESSING', 'PROCESSED', 'FAILED')),
    attempts            INTEGER NOT NULL DEFAULT 0,
    locked_until        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_transactions_pending
    ON transactions (created_at) WHERE status = 'PROCESSING';
"""

# Arbitrary constant; serialises schema setup when several instances boot together.
_SCHEMA_LOCK_ID = 726_001


async def create_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        # Fail fast instead of hanging a webhook past its latency budget.
        timeout=5,
        command_timeout=5,
        # Managed Postgres poolers (pgbouncer in transaction mode) don't support
        # server-side prepared statements.
        statement_cache_size=0,
    )


async def init_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _SCHEMA_LOCK_ID)
            await conn.execute(SCHEMA)
