from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(slots=True)
class TelemetrySnapshot:
    timestamp: float = field(default_factory=time)
    fuel: float = 0.0
    speed_kmh: float = 0.0
    gear: int = 0
    rpm: int = 0
    lap_number: int = 0
    current_lap_time_seconds: float = 0.0
    last_lap_time_seconds: float | None = None
    normalized_car_position: float = 0.0
    throttle: float | None = None
    brake: float | None = None
    session_type: str = "unknown"
    status: str = "unknown"
    player_position: int = 0
    is_in_pit: bool = False
    current_sector_index: int = 0
    nearby_car_count: int = 0
    closest_car_distance_m: float | None = None
    closest_car_index: int | None = None
    closest_car_speed_kmh: float | None = None
    nearby_incident_count: int = 0
    track_name: str = "unknown"
    vehicle_name: str = "unknown"
    session_laps_total: int = 0
    session_time_left_seconds: float = 0.0
    track_grip_percent: float | None = None
    air_temp_c: float | None = None
    asphalt_temp_c: float | None = None
    wind_speed_kmh: float | None = None


@dataclass(slots=True)
class LapRecord:
    lap_number: int
    lap_time_seconds: float
    fuel_at_lap_start: float | None
    fuel_at_lap_end: float | None
    fuel_used: float | None
    created_at: float = field(default_factory=time)


@dataclass(slots=True)
class SessionStats:
    best_lap_seconds: float | None
    last_lap_seconds: float | None
    avg_last_3_seconds: float | None
    avg_last_5_seconds: float | None
    avg_fuel_per_lap: float | None
    estimated_laps_left: float | None


@dataclass(slots=True)
class Event:
    name: str
    payload: dict[str, float | int | str | None]
    timestamp: float = field(default_factory=time)
