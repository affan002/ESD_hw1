"""FastAPI application factory and routes for the sensor ingestion service."""

import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db, metrics
from app.anomaly import compute_status, evaluate
from app.config import Settings, load_settings
from app.faults import FaultConfig, FaultState
from app.middleware import RequestContextMiddleware
from app.models import (
    DEVICE_ID_PATTERN,
    AnomalyList,
    DeviceList,
    DeviceReadings,
    DeviceStatus,
    DeviceStatusValue,
    ReadingIn,
    ReadingOut,
)
from app.timeutil import parse_iso, utc_iso, utc_now

log = logging.getLogger("app")

_DEVICE_ID_RE = re.compile(DEVICE_ID_PATTERN)
STATIC_DIR = Path(__file__).parent / "static"


def get_conn(request: Request):
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Limit = Annotated[int, Query(ge=1, le=500)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_schema(settings.db_path)
        log.info(
            "sensor-api starting",
            extra={"event": "startup", "config": {"db_path": settings.db_path, "stale_after_s": settings.stale_after_s}},
        )
        yield

    app = FastAPI(title="Sensor Ingestion API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.faults = faults = FaultState()
    metrics.DEVICE_STATUS.configure(settings)
    app.add_middleware(RequestContextMiddleware)

    # Operator dashboard: static files that call the JSON API below.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "index.html")

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # Log only the field and error type, never the submitted values.
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body") or "body", "type": e["type"]}
            for e in exc.errors()
        ]
        extra = {"errors": errors}
        if request.method == "POST" and request.url.path == "/readings":
            metrics.READINGS.labels("rejected").inc()
            for e in exc.errors():
                metrics.VALIDATION_ERRORS.labels(metrics.validation_field(e["loc"]), e["type"]).inc()
            extra["event"] = "reading_rejected"
            body = exc.body
            if isinstance(body, dict) and isinstance(body.get("device_id"), str) and _DEVICE_ID_RE.match(body["device_id"]):
                extra["device_id"] = body["device_id"]
            log.warning("reading rejected", extra=extra)
        else:
            extra["event"] = "request_invalid"
            log.warning("request rejected", extra=extra)
        return await request_validation_exception_handler(request, exc)

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint():
        body, content_type = metrics.render()
        return Response(content=body, media_type=content_type)

    @app.get("/health")
    def health():
        try:
            conn = db.connect(settings.db_path)
            try:
                db.ping(conn)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.error("database health check failed", extra={"event": "health_check_failed", "error": str(exc)})
            return JSONResponse(status_code=503, content={"status": "degraded", "db": "error"})
        return {"status": "ok", "db": "ok"}

    @app.post("/readings", status_code=201, response_model=ReadingOut)
    def create_reading(reading: ReadingIn, conn: Conn):
        try:
            faults.apply()
        except HTTPException:
            metrics.READINGS.labels("failed").inc()
            raise
        received_at = utc_now()
        if reading.timestamp - received_at > timedelta(seconds=settings.future_skew_s):
            raise RequestValidationError(
                [
                    {
                        "type": "timestamp_in_future",
                        "loc": ("body", "timestamp"),
                        "msg": f"timestamp is more than {settings.future_skew_s:g}s in the future",
                        "input": reading.timestamp.isoformat(),
                    }
                ],
                body=reading.model_dump(mode="json"),
            )

        reasons = evaluate(reading.temperature_c, reading.humidity_pct, reading.battery_pct)
        recorded_iso, received_iso = utc_iso(reading.timestamp), utc_iso(received_at)
        try:
            with metrics.DB_WRITE.time():
                reading_id, is_new_device = db.insert_reading(
                    conn,
                    device_id=reading.device_id,
                    recorded_at=recorded_iso,
                    received_at=received_iso,
                    temperature_c=reading.temperature_c,
                    humidity_pct=reading.humidity_pct,
                    battery_pct=reading.battery_pct,
                    anomaly_reasons=reasons,
                )
        except Exception:
            metrics.READINGS.labels("failed").inc()
            raise
        metrics.READINGS.labels("anomaly" if reasons else "normal").inc()
        for reason in reasons:
            metrics.ANOMALIES.labels(reason).inc()

        if is_new_device:
            log.info("device registered", extra={"event": "device_registered", "device_id": reading.device_id})
        log.info(
            "reading stored",
            extra={
                "event": "reading_stored",
                "device_id": reading.device_id,
                "reading_id": reading_id,
                "is_anomaly": bool(reasons),
            },
        )
        if reasons:
            log.warning(
                "anomaly detected: %s",
                ",".join(reasons),
                extra={
                    "event": "anomaly_detected",
                    "device_id": reading.device_id,
                    "reading_id": reading_id,
                    "anomaly_reasons": reasons,
                    "temperature_c": reading.temperature_c,
                    "humidity_pct": reading.humidity_pct,
                    "battery_pct": reading.battery_pct,
                },
            )

        return ReadingOut(
            id=reading_id,
            device_id=reading.device_id,
            timestamp=recorded_iso,
            received_at=received_iso,
            temperature_c=reading.temperature_c,
            humidity_pct=reading.humidity_pct,
            battery_pct=reading.battery_pct,
            is_anomaly=bool(reasons),
            anomaly_reasons=reasons,
        )

    def with_status(device: dict) -> DeviceStatus:
        status = compute_status(
            parse_iso(device["last_seen"]),
            utc_now(),
            device["latest_reading"]["is_anomaly"],
            settings.stale_after_s,
        )
        return DeviceStatus(status=status, **device)

    @app.get("/devices", response_model=DeviceList)
    def list_devices(conn: Conn, status: DeviceStatusValue | None = None):
        devices = [with_status(d) for d in db.list_devices(conn)]
        if status is not None:
            devices = [d for d in devices if d.status == status]
        return DeviceList(count=len(devices), devices=devices)

    @app.get("/devices/{device_id}", response_model=DeviceStatus)
    def get_device(device_id: str, conn: Conn):
        device = db.get_device(conn, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        return with_status(device)

    @app.get("/devices/{device_id}/readings", response_model=DeviceReadings)
    def list_device_readings(device_id: str, conn: Conn, limit: Limit = 50, anomalies_only: bool = False):
        if db.get_device(conn, device_id) is None:
            raise HTTPException(status_code=404, detail="device not found")
        return DeviceReadings(device_id=device_id, readings=db.list_readings(conn, device_id, limit, anomalies_only))

    @app.get("/anomalies", response_model=AnomalyList)
    def list_anomalies(conn: Conn, limit: Limit = 50):
        return AnomalyList(readings=db.list_anomalies(conn, limit))

    # Operator controls for fault injection. No auth: local experiments only.
    @app.get("/admin/faults", response_model=FaultConfig)
    def get_faults():
        return faults.config

    @app.put("/admin/faults", response_model=FaultConfig)
    def put_faults(config: FaultConfig):
        log.warning("fault config changed", extra={"event": "fault_config_changed", "config": config.model_dump()})
        return faults.set(config)

    @app.delete("/admin/faults", response_model=FaultConfig)
    def delete_faults():
        config = FaultConfig()
        log.warning("fault config reset", extra={"event": "fault_config_changed", "config": config.model_dump()})
        return faults.set(config)

    return app
