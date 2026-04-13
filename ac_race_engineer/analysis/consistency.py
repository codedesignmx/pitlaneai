from __future__ import annotations

from ac_race_engineer.telemetry.models import LapRecord


def is_consistent_stint(laps: list[LapRecord], window_size: int = 3, max_delta_seconds: float = 0.3) -> bool:
    if len(laps) < window_size:
        return False
    window = [lap.lap_time_seconds for lap in laps[-window_size:]]
    return (max(window) - min(window)) <= max_delta_seconds
