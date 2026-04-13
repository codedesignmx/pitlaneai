from __future__ import annotations

from ac_race_engineer.telemetry.models import LapRecord


def average_fuel_per_lap(laps: list[LapRecord], min_samples: int = 3) -> float | None:
    samples = [lap.fuel_used for lap in laps if lap.fuel_used is not None and lap.fuel_used > 0.0]
    if len(samples) < min_samples:
        return None
    return sum(samples) / len(samples)


def estimate_laps_left(current_fuel: float, avg_fuel_per_lap: float | None) -> float | None:
    if avg_fuel_per_lap is None or avg_fuel_per_lap <= 0:
        return None
    return max(0.0, current_fuel / avg_fuel_per_lap)
