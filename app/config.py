"""Runtime settings, read from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str
    log_level: str
    api_host: str
    api_port: int
    stale_after_s: float
    future_skew_s: float


def load_settings() -> Settings:
    return Settings(
        db_path=os.getenv("DB_PATH", "./data/sensors.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("API_PORT", "8000")),
        stale_after_s=float(os.getenv("STALE_AFTER_S", "30")),
        future_skew_s=float(os.getenv("FUTURE_SKEW_S", "60")),
    )
