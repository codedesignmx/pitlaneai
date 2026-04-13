from __future__ import annotations

from ac_race_engineer.telemetry.models import LapRecord


def best_lap(laps: list[LapRecord]) -> float | None:
    if not laps:
        return None
    return min(l.lap_time_seconds for l in laps)


def last_lap(laps: list[LapRecord]) -> float | None:
    if not laps:
        return None
    return laps[-1].lap_time_seconds


def average_last_n(laps: list[LapRecord], n: int) -> float | None:
    if len(laps) < n:
        return None
    last = laps[-n:]
    return sum(l.lap_time_seconds for l in last) / n


def average_previous_n(laps: list[LapRecord], n: int) -> float | None:
    if len(laps) <= 1:
        return None
    pool = laps[:-1]
    if len(pool) < n:
        return None
    chunk = pool[-n:]
    return sum(l.lap_time_seconds for l in chunk) / n
