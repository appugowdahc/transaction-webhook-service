import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    processing_delay_seconds: float = 30.0
    worker_concurrency: int = 200
    worker_poll_interval_seconds: float = 1.0
    max_attempts: int = 5
    # Extra time on top of the processing delay before another worker may
    # reclaim a row. Must stay comfortably above DB/GC hiccups.
    lease_buffer_seconds: float = 30.0

    @property
    def lease_seconds(self) -> float:
        return self.processing_delay_seconds + self.lease_buffer_seconds

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        return cls(
            database_url=url,
            processing_delay_seconds=float(os.environ.get("PROCESSING_DELAY_SECONDS", 30)),
            worker_concurrency=int(os.environ.get("WORKER_CONCURRENCY", 200)),
            worker_poll_interval_seconds=float(os.environ.get("WORKER_POLL_INTERVAL_SECONDS", 1)),
            max_attempts=int(os.environ.get("MAX_ATTEMPTS", 5)),
        )
