from __future__ import annotations

from ac_race_engineer.analysis.consistency import is_consistent_stint
from ac_race_engineer.analysis.fuel import average_fuel_per_lap, estimate_laps_left
from ac_race_engineer.analysis.pace import average_last_n, average_previous_n
from ac_race_engineer.analysis.session_state import SessionState
from ac_race_engineer.events.cooldown import CooldownManager
from ac_race_engineer.telemetry.models import Event, TelemetrySnapshot


class EventDetector:
    def __init__(
        self,
        cooldown_seconds: float = 10.0,
        thresholds_by_session: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.cooldown = CooldownManager(default_seconds=cooldown_seconds)
        self.thresholds_by_session = thresholds_by_session or {}
        self._last_speed_kmh: float | None = None
        self._last_ts: float | None = None

    def on_new_lap(self, state: SessionState, session_type: str = "unknown") -> list[Event]:
        events: list[Event] = []
        if not state.laps:
            return events

        thresholds = self.thresholds_by_session.get(
            session_type, self.thresholds_by_session.get("unknown", {})
        )
        pace_drop_limit = float(thresholds.get("pace_drop", 0.5))
        pace_improving_limit = float(thresholds.get("pace_improving", -0.3))
        consistency_window = float(thresholds.get("consistency_window", 0.3))

        last = state.laps[-1]
        previous = state.laps[:-1]

        if previous:
            prev_best = min(l.lap_time_seconds for l in previous)
            if last.lap_time_seconds < prev_best and self.cooldown.can_emit("new_best_lap", 8.0):
                improvement = prev_best - last.lap_time_seconds
                events.append(
                    Event(
                        name="new_best_lap",
                        payload={
                            "lap_time": round(last.lap_time_seconds, 3),
                            "prev_best": round(prev_best, 3),
                            "improvement": round(improvement, 3),
                            "position": state.last_snapshot.player_position
                            if state.last_snapshot is not None
                            else 0,
                        },
                    )
                )

        recent_avg = average_previous_n(state.laps, n=3)
        if recent_avg is not None:
            delta = last.lap_time_seconds - recent_avg
            if delta >= pace_drop_limit and self.cooldown.can_emit("pace_drop", 12.0):
                events.append(Event(name="pace_drop", payload={"delta": round(delta, 3)}))
            elif delta <= pace_improving_limit and self.cooldown.can_emit("pace_improving", 12.0):
                events.append(Event(name="pace_improving", payload={"delta": round(delta, 3)}))

        if is_consistent_stint(state.laps, window_size=3, max_delta_seconds=consistency_window):
            if self.cooldown.can_emit("stint_consistent", 20.0):
                events.append(
                    Event(name="stint_consistent", payload={"window": 3, "delta": consistency_window})
                )

        avg_fuel = average_fuel_per_lap(state.laps, min_samples=3)
        if avg_fuel is not None and state.last_snapshot is not None:
            laps_left = estimate_laps_left(state.last_snapshot.fuel, avg_fuel)
            laps_to_finish = None
            normalized_time_left = self._normalize_session_seconds(
                state.last_snapshot.session_time_left_seconds
            )

            # Fixed-lap race
            if state.last_snapshot.session_laps_total > 0:
                laps_to_finish = max(
                    0.0,
                    float(state.last_snapshot.session_laps_total - state.last_snapshot.lap_number),
                )

            # Timed race fallback: derive laps to go from time left and current pace
            elif state.last_snapshot.session_type == "race":
                avg_lap_for_projection = average_last_n(state.laps, 3) or average_last_n(state.laps, 1)
                if (
                    avg_lap_for_projection is not None
                    and avg_lap_for_projection > 0.0
                    and normalized_time_left is not None
                    and normalized_time_left > 0.0
                ):
                    current_lap_remaining = max(
                        0.0,
                        avg_lap_for_projection - state.last_snapshot.current_lap_time_seconds,
                    )
                    total_time_to_cover = normalized_time_left + current_lap_remaining
                    laps_to_finish = total_time_to_cover / avg_lap_for_projection

                    # Protección contra lecturas corruptas: evita anunciar miles de vueltas.
                    if laps_to_finish > 150.0:
                        laps_to_finish = None

            if self.cooldown.can_emit("fuel_update", 25.0):
                events.append(
                    Event(
                        name="fuel_update",
                        payload={
                            "avg_fuel_per_lap": round(avg_fuel, 3),
                            "estimated_laps_left": round(laps_left, 2) if laps_left is not None else None,
                            "laps_completed": state.last_snapshot.lap_number,
                            "session_laps_total": state.last_snapshot.session_laps_total,
                            "estimated_laps_to_finish": round(laps_to_finish, 2)
                            if laps_to_finish is not None
                            else None,
                            "session_time_left_seconds": round(normalized_time_left, 1)
                            if normalized_time_left is not None
                            else None,
                        },
                    )
                )

        return events

    def on_tick(self, snapshot: TelemetrySnapshot) -> list[Event]:
        events: list[Event] = []

        if self._last_speed_kmh is not None and self._last_ts is not None:
            dt = max(0.05, snapshot.timestamp - self._last_ts)
            speed_drop_kmh = max(0.0, self._last_speed_kmh - snapshot.speed_kmh)
            decel_mps2 = (speed_drop_kmh / 3.6) / dt

            close_enough = (
                snapshot.closest_car_distance_m is not None
                and snapshot.closest_car_distance_m <= 7.0
            )
            if (
                close_enough
                and snapshot.speed_kmh > 12.0
                and speed_drop_kmh >= 18.0
                and decel_mps2 >= 9.0
                and self.cooldown.can_emit("collision_contact", 4.0)
            ):
                role = "contact"
                other_kmh = snapshot.closest_car_speed_kmh
                if other_kmh is not None:
                    if snapshot.speed_kmh - other_kmh >= 12.0:
                        role = "hit_other"
                    elif other_kmh - snapshot.speed_kmh >= 12.0:
                        role = "got_hit"

                events.append(
                    Event(
                        name="collision_contact",
                        payload={
                            "role": role,
                            "closest_car_index": snapshot.closest_car_index,
                            "closest_m": round(snapshot.closest_car_distance_m, 1)
                            if snapshot.closest_car_distance_m is not None
                            else None,
                            "speed_drop_kmh": round(speed_drop_kmh, 1),
                        },
                    )
                )

        self._last_speed_kmh = snapshot.speed_kmh
        self._last_ts = snapshot.timestamp

        if snapshot.nearby_car_count > 0 and self.cooldown.can_emit("traffic_close", 8.0):
            gap_seconds = None
            if snapshot.closest_car_distance_m is not None and snapshot.speed_kmh > 3.0:
                speed_mps = snapshot.speed_kmh / 3.6
                gap_seconds = snapshot.closest_car_distance_m / speed_mps
            events.append(
                Event(
                    name="traffic_close",
                    payload={
                        "count": snapshot.nearby_car_count,
                        "closest_m": round(snapshot.closest_car_distance_m, 1)
                        if snapshot.closest_car_distance_m is not None
                        else None,
                        "closest_gap_seconds": round(gap_seconds, 2) if gap_seconds is not None else None,
                    },
                )
            )

        if snapshot.nearby_incident_count > 0 and self.cooldown.can_emit("incident_nearby", 10.0):
            events.append(
                Event(
                    name="incident_nearby",
                    payload={
                        "count": snapshot.nearby_incident_count,
                        "sector": snapshot.current_sector_index,
                    },
                )
            )

        return events

    @staticmethod
    def _normalize_session_seconds(seconds: float | None) -> float | None:
        if seconds is None:
            return None
        value = float(seconds)
        if value > 21600.0:
            value = value / 1000.0
        if value < 0.0:
            value = 0.0
        if value > 86400.0:
            return None
        return value
