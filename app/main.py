import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings
from app.db import create_pool, init_schema
from app.routes import router
from app.worker import Worker

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    pool = await create_pool(settings)
    await init_schema(pool)
    worker = Worker(pool, settings)
    worker.start()
    app.state.pool = pool
    try:
        yield
    finally:
        await worker.stop()
        await pool.close()


app = FastAPI(title="Transaction Webhook Service", lifespan=lifespan)
app.include_router(router)
