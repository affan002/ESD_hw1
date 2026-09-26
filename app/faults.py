"""Switchable fault injection for POST /readings, used by the experiments.

State is in memory only: off by default, and reset by DELETE /admin/faults or a restart.
Faults are deterministic (every Nth request) so experiments are repeatable.
"""

import logging
import threading
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app import metrics

log = logging.getLogger("app.faults")


class FaultConfig(BaseModel):
    delay_ms: int = Field(default=0, ge=0, le=5000, description="Extra latency added to affected requests")
    delay_every_n: int = Field(default=1, ge=1, description="Delay every Nth POST /readings (1 = every request)")
    error_every_n: int = Field(default=0, ge=0, description="Fail every Nth POST /readings with 500 (0 = off)")


class FaultState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._config = FaultConfig()
        self._count = 0
        self._publish(self._config)

    @staticmethod
    def _publish(config: "FaultConfig") -> None:
        metrics.FAULT_ACTIVE.labels("delay").set(1 if config.delay_ms > 0 else 0)
        metrics.FAULT_ACTIVE.labels("error").set(1 if config.error_every_n > 0 else 0)

    @property
    def config(self) -> FaultConfig:
        with self._lock:
            return self._config

    def set(self, config: FaultConfig) -> FaultConfig:
        with self._lock:
            self._config = config
            self._count = 0
        self._publish(config)
        return config

    def apply(self) -> None:
        """Called at the start of POST /readings. May sleep, or raise a 500 before anything is stored."""
        with self._lock:
            self._count += 1
            n, cfg = self._count, self._config
        if cfg.delay_ms > 0 and n % cfg.delay_every_n == 0:
            log.warning(
                "injected delay of %d ms",
                cfg.delay_ms,
                extra={"event": "fault_injected", "fault": "delay", "delay_ms": cfg.delay_ms},
            )
            metrics.FAULTS_INJECTED.labels("delay").inc()
            time.sleep(cfg.delay_ms / 1000)
        if cfg.error_every_n > 0 and n % cfg.error_every_n == 0:
            log.error("injected error", extra={"event": "fault_injected", "fault": "error"})
            metrics.FAULTS_INJECTED.labels("error").inc()
            raise HTTPException(status_code=500, detail="injected fault")
