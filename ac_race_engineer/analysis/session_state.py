from __future__ import annotations

from dataclasses import dataclass, field

from ac_race_engineer.analysis.fuel import average_fuel_per_lap, estimate_laps_left
from ac_race_engineer.analysis.pace import average_last_n, best_lap, last_lap
from ac_race_engineer.analysis.time_format import (
    speak_delta_spanish,
    speak_lap_time_spanish,
    speak_laps_spanish,
)
from ac_race_engineer.telemetry.models import LapRecord, SessionStats, TelemetrySnapshot


@dataclass(slots=True)
class SessionState:
    laps: list[LapRecord] = field(default_factory=list)
    last_snapshot: TelemetrySnapshot | None = None
    current_lap_start_fuel: float | None = None
    last_completed_lap_counter: int = -1  # -1 = todavía no inicializado
    last_collision_note: str | None = None

    def update(self, snapshot: TelemetrySnapshot) -> LapRecord | None:
        # Primera lectura: calibrar el contador base con el estado actual de AC
        # para no detectar como vuelta nueva lo que ya completó antes de arrancar.
        if self.last_completed_lap_counter == -1:
            self.last_completed_lap_counter = snapshot.lap_number
            self.current_lap_start_fuel = snapshot.fuel
            self.last_snapshot = snapshot
            return None

        if self.current_lap_start_fuel is None:
            self.current_lap_start_fuel = snapshot.fuel

        lap_done = False
        new_lap_number = snapshot.lap_number

        if new_lap_number > self.last_completed_lap_counter:
            lap_done = True
        elif self.last_snapshot is not None:
            wrapped = (
                self.last_snapshot.normalized_car_position > 0.95
                and snapshot.normalized_car_position < 0.10
            )
            if wrapped and snapshot.last_lap_time_seconds is not None:
                lap_done = True
                new_lap_number = self.last_completed_lap_counter + 1

        created_lap: LapRecord | None = None
        if lap_done and snapshot.last_lap_time_seconds is not None:
            lap_time = snapshot.last_lap_time_seconds
            if 30.0 <= lap_time <= 600.0:
                fuel_end = snapshot.fuel
                fuel_start = self.current_lap_start_fuel
                fuel_used = None
                if fuel_start is not None:
                    raw = fuel_start - fuel_end
                    fuel_used = raw if raw >= 0 else None

                created_lap = LapRecord(
                    lap_number=new_lap_number,
                    lap_time_seconds=lap_time,
                    fuel_at_lap_start=fuel_start,
                    fuel_at_lap_end=fuel_end,
                    fuel_used=fuel_used,
                )
                self.laps.append(created_lap)
                self.last_completed_lap_counter = new_lap_number
                self.current_lap_start_fuel = snapshot.fuel

        self.last_snapshot = snapshot
        return created_lap

    def get_stats(self) -> SessionStats:
        best = best_lap(self.laps)
        last = last_lap(self.laps)
        avg3 = average_last_n(self.laps, 3)
        avg5 = average_last_n(self.laps, 5)
        avg_fuel = average_fuel_per_lap(self.laps, min_samples=3)
        fuel_now = self.last_snapshot.fuel if self.last_snapshot is not None else 0.0
        est_laps = estimate_laps_left(fuel_now, avg_fuel)
        return SessionStats(
            best_lap_seconds=best,
            last_lap_seconds=last,
            avg_last_3_seconds=avg3,
            avg_last_5_seconds=avg5,
            avg_fuel_per_lap=avg_fuel,
            estimated_laps_left=est_laps,
        )

    def build_radio_briefing(self) -> str:
        """Short briefing spoken right after Radio Check activation."""
        snap = self.last_snapshot
        stats = self.get_stats()

        if snap is None:
            return "Sin telemetría todavía."

        session_name_map = {
            "practice": "practica",
            "qualifying": "clasificación",
            "race": "carrera",
            "hotlap": "hotlap",
            "time_attack": "time attack",
            "drift": "drift",
            "drag": "drag",
            "unknown": "sesión desconocida",
        }
        session_label = session_name_map.get(snap.session_type, "sesión desconocida")
        where_label = "en pits" if snap.is_in_pit else "en pista"
        track_label = snap.track_name if snap.track_name and snap.track_name != "unknown" else "pista desconocida"

        if stats.best_lap_seconds is None:
            pace_line = "Aún no hay vueltas válidas para analizar ritmo."
        else:
            pace_line = f"Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."

        if stats.estimated_laps_left is None:
            fuel_line = f"Combustible actual {snap.fuel:.1f} litros."
        else:
            fuel_line = (
                f"Combustible {snap.fuel:.1f} litros, estimado para "
                f"{speak_laps_spanish(stats.estimated_laps_left)}."
            )

        weather_parts: list[str] = []
        if snap.track_grip_percent is not None:
            weather_parts.append(f"Grip de pista {snap.track_grip_percent:.0f} por ciento.")
        if snap.air_temp_c is not None:
            weather_parts.append(f"Aire {snap.air_temp_c:.0f} grados.")
        if snap.asphalt_temp_c is not None:
            weather_parts.append(f"Asfalto {snap.asphalt_temp_c:.0f} grados.")
        if snap.wind_speed_kmh is not None:
            weather_parts.append(f"Viento {snap.wind_speed_kmh:.0f} kilómetros por hora.")

        weather_line = (
            " ".join(weather_parts)
            if weather_parts
            else "Meteo detallada no disponible en este feed de telemetría."
        )
        return (
            f"Estamos en {track_label}. Sesión {session_label}, estado {where_label}. "
            f"{pace_line} {fuel_line} {weather_line}"
        )

    def build_auto_feedback(
        self,
        gap_ahead_seconds: float | None = None,
        gap_behind_seconds: float | None = None,
    ) -> str:
        """Periodic autonomous feedback while driving."""
        snap = self.last_snapshot
        stats = self.get_stats()

        if snap is None or snap.status != "live":
            return ""

        if snap.is_in_pit:
            if stats.avg_fuel_per_lap is None:
                return "Estado en pits."
            return (
                f"Estado en pits. Consumo medio {stats.avg_fuel_per_lap:.2f} por vuelta, "
                f"combustible actual {snap.fuel:.1f} litros."
            )

        # Session-aware coaching so LFMS and offline races feel different.
        if snap.session_type == "qualifying":
            if stats.best_lap_seconds is None:
                return "Clasificación activa. Push limpio, busca una vuelta de referencia ahora."
            if stats.last_lap_seconds is None:
                return "Clasificación activa. Tienes mejor vuelta marcada, prepara otra vuelta push."
            delta = stats.last_lap_seconds - stats.best_lap_seconds
            if delta > 0.4:
                return (
                    f"Clasificación. Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}, "
                    f"a {speak_delta_spanish(delta)} de tu mejor. "
                    "Calienta neumáticos y maximiza salida de curva."
                )
            return "Clasificación. Buena vuelta, mantente en ventana de push controlado."

        if snap.session_type == "race":
            fuel_note = (
                f"Fuel para {speak_laps_spanish(stats.estimated_laps_left)}."
                if stats.estimated_laps_left is not None
                else f"Fuel actual {snap.fuel:.1f} litros."
            )

            parts: list[str] = []
            if gap_ahead_seconds is not None:
                parts.append(
                    f"Diferencia con el de adelante {speak_delta_spanish(gap_ahead_seconds)}."
                )
            if gap_behind_seconds is not None:
                parts.append(
                    f"Ventaja con el de atrás {speak_delta_spanish(gap_behind_seconds)}."
                )

            if not parts:
                parts.append("Sin datos de diferencia con coches cercanos.")
            parts.append(fuel_note)
            return " ".join(parts)

        if stats.last_lap_seconds is None:
            return "Sin vuelta válida todavía. Completa esta vuelta y te doy análisis de ritmo."

        parts: list[str] = []
        parts.append(f"Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}.")

        if stats.best_lap_seconds is not None:
            delta = stats.last_lap_seconds - stats.best_lap_seconds
            if delta <= 0.15:
                parts.append("Ritmo fuerte, estás muy cerca de tu mejor vuelta.")
            elif delta >= 0.6:
                parts.append("Perdiste ritmo, enfoca salida de curva y tracción.")
            else:
                parts.append("Ritmo estable, hay margen pequeño para mejorar.")

        if stats.estimated_laps_left is not None:
            parts.append(f"Combustible para {speak_laps_spanish(stats.estimated_laps_left)}.")
        else:
            parts.append(f"Combustible actual {snap.fuel:.1f} litros.")

        return " ".join(parts)

    def build_position_report(self) -> str:
        """Voice reply for 'que lugar vamos' style queries."""
        snap = self.last_snapshot
        stats = self.get_stats()
        if snap is None:
            return "Sin telemetría todavía, no puedo confirmar posición."

        session_name_map = {
            "practice": "practica",
            "qualifying": "clasificación",
            "race": "carrera",
            "hotlap": "hotlap",
            "time_attack": "time attack",
            "drift": "drift",
            "drag": "drag",
            "unknown": "sesión desconocida",
        }
        session_label = session_name_map.get(snap.session_type, "sesión desconocida")
        pos_line = (
            f"Estamos en posición {snap.player_position} en {session_label}."
            if snap.player_position > 0
            else f"No tengo posición confirmada para {session_label}."
        )

        if snap.session_type == "race":
            if stats.last_lap_seconds is not None:
                return (
                    f"{pos_line} Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}. "
                    f"Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."
                    if stats.best_lap_seconds is not None
                    else f"{pos_line} Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}."
                )
            if stats.best_lap_seconds is not None:
                return f"{pos_line} Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."
            return pos_line + " Aún no hay vuelta válida."

        if stats.best_lap_seconds is not None:
            return f"{pos_line} Mejor tiempo {speak_lap_time_spanish(stats.best_lap_seconds)}."

        if stats.last_lap_seconds is not None:
            return f"{pos_line} Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}."

        return pos_line + " Aún no hay vuelta válida."

    def register_collision_note(self, note: str) -> None:
        clean = (note or "").strip()
        if clean:
            self.last_collision_note = clean

    def build_car_status_report(self) -> str:
        snap = self.last_snapshot
        if snap is None:
            return "Sin telemetría todavía para revisar estado del auto."

        base = (
            f"Estado actual. Velocidad {snap.speed_kmh:.0f} kilómetros por hora. "
            f"Combustible {snap.fuel:.1f} litros."
        )

        damage_note = (
            " En AC base no hay sensor directo confiable de daño estructural en este feed."
        )

        if self.last_collision_note:
            return base + f" Último contacto: {self.last_collision_note}." + damage_note
        return base + " Sin colisión reciente detectada." + damage_note
