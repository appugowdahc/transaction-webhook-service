import os

# Must be set before the app reads settings. Short delay keeps the suite quick.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/transactions"
)
os.environ["PROCESSING_DELAY_SECONDS"] = "1"
os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "0.2"
os.environ["MAX_ATTEMPTS"] = "3"

import asyncio  # noqa: E402

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        asyncio.run(_truncate())
        yield c


async def _truncate():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute("TRUNCATE transactions")
    finally:
        await conn.close()


@pytest.fixture
def sql():
    """Run a statement against the test DB from a sync test."""

    def run(query, *args, fetch=False):
        async def _go():
            conn = await asyncpg.connect(os.environ["DATABASE_URL"])
            try:
                if fetch:
                    return await conn.fetch(query, *args)
                return await conn.execute(query, *args)
            finally:
                await conn.close()

        return asyncio.run(_go())

    return run
