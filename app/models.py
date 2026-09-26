"""Pydantic request/response models.

`ReadingIn` enforces the sensor's *physical* limits: anything outside them is
impossible data and is rejected with 422. The *expected* operating range
(anomalies) lives in app/anomaly.py.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

DEVICE_ID_PATTERN = r"^dev-\d{3}$"

DeviceStatusValue = Literal["ok", "anomaly", "stale"]


class ReadingIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    device_id: str = Field(pattern=DEVICE_ID_PATTERN, examples=["dev-001"])
    timestamp: AwareDatetime = Field(description="Device clock, with timezone, e.g. 2026-09-26T10:00:00Z")
    temperature_c: float = Field(ge=-40, le=85)
    humidity_pct: float = Field(ge=0, le=100)
    battery_pct: float = Field(ge=0, le=100)


class ReadingOut(BaseModel):
    id: int
    device_id: str
    timestamp: str
    received_at: str
    temperature_c: float
    humidity_pct: float
    battery_pct: float
    is_anomaly: bool
    anomaly_reasons: list[str]


class DeviceStatus(BaseModel):
    device_id: str
    status: DeviceStatusValue
    first_seen: str
    last_seen: str
    reading_count: int
    anomaly_count: int
    latest_reading: ReadingOut


class DeviceList(BaseModel):
    count: int
    devices: list[DeviceStatus]


class DeviceReadings(BaseModel):
    device_id: str
    readings: list[ReadingOut]


class AnomalyList(BaseModel):
    readings: list[ReadingOut]
