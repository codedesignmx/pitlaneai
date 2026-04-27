from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING

from ac_race_engineer.analysis.fuel import average_fuel_per_lap, estimate_laps_left
from ac_race_engineer.analysis.pace import average_last_n, best_lap, last_lap
from ac_race_engineer.analysis.time_format import (
    speak_delta_spanish,
    speak_lap_time_spanish,
    speak_laps_spanish,
)
from ac_race_engineer.analysis.session_objectives import SessionObjectiveSet
from ac_race_engineer.storage.performance_history import load_historical_pace_summary
from ac_race_engineer.storage.results_summary import (
    StandingEntry,
    build_session_end_summary,
    load_ac_log_weather_info,
)
from ac_race_engineer.storage.track_sections import label_for_position, load_sections
from ac_race_engineer.telemetry.models import LapRecord, SessionStats, TelemetrySnapshot

if TYPE_CHECKING:
    from ac_race_engineer.analysis.setup_coach import SetupCoach


@dataclass(slots=True)
class SessionState:
    laps: list[LapRecord] = field(default_factory=list)
    last_snapshot: TelemetrySnapshot | None = None
    current_lap_start_fuel: float | None = None
    last_completed_lap_counter: int = -1  # -1 = todavía no inicializado
    last_collision_note: str | None = None
    live_standings: list[StandingEntry] = field(default_factory=list)
    session_best_standings: dict[str, StandingEntry] = field(default_factory=dict)
    live_gap_ahead_seconds: float | None = None
    live_gap_behind_seconds: float | None = None
    live_track_grip_percent: float | None = None
    live_air_temp_c: float | None = None
    live_asphalt_temp_c: float | None = None
    live_wind_speed_kmh: float | None = None
    local_driver_name: str | None = None
    last_known_track_name: str | None = None
    active_setup_id: str | None = None
    active_setup_label: str | None = None
    _track_sections: list = field(default_factory=list)
    active_objectives: SessionObjectiveSet | None = None
    objectives_intro_announced: bool = False
    objective_milestones_announced: set[str] = field(default_factory=set)
    session_total_seconds: float = 0.0
    session_lap_start_index: int = 0
    microsector_count: int = 20
    current_lap_trace: list[tuple[float, float, float, float]] = field(default_factory=list)
    last_lap_micro_profile: dict[int, dict[str, float]] = field(default_factory=dict)
    best_lap_micro_profile: dict[int, dict[str, float]] = field(default_factory=dict)

    def update(self, snapshot: TelemetrySnapshot) -> LapRecord | None:
        if snapshot.track_name and snapshot.track_name != "unknown":
            self.last_known_track_name = snapshot.track_name

        # Primera lectura: calibrar el contador base con el estado actual de AC
        # para no detectar como vuelta nueva lo que ya completó antes de arrancar.
        if self.last_completed_lap_counter == -1:
            self.last_completed_lap_counter = snapshot.lap_number
            self.current_lap_start_fuel = snapshot.fuel
            self._record_trace_sample(snapshot)
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
                micro_profile = self._build_microsector_profile(self.current_lap_trace)
                fuel_end = snapshot.fuel
                fuel_start = self.current_lap_start_fuel
                fuel_used = None
                if fuel_start is not None:
                    raw = fuel_start - fuel_end
                    fuel_used = raw if raw >= 0 else None

                prev_best = min((l.lap_time_seconds for l in self.laps), default=None)

                created_lap = LapRecord(
                    lap_number=new_lap_number,
                    lap_time_seconds=lap_time,
                    fuel_at_lap_start=fuel_start,
                    fuel_at_lap_end=fuel_end,
                    fuel_used=fuel_used,
                )
                self.laps.append(created_lap)
                self.last_lap_micro_profile = micro_profile
                if prev_best is None or lap_time < (prev_best - 0.001):
                    self.best_lap_micro_profile = dict(micro_profile)
                self.last_completed_lap_counter = new_lap_number
                self.current_lap_start_fuel = snapshot.fuel

        if lap_done:
            self.current_lap_trace = []
        self._record_trace_sample(snapshot)

        self.last_snapshot = snapshot
        return created_lap

    def restore_laps(self, laps_data: list[dict]) -> None:
        """Inyecta vueltas guardadas en el checkpoint al estado actual."""
        for item in laps_data:
            try:
                lap = LapRecord(
                    lap_number=int(item["lap_number"]),
                    lap_time_seconds=float(item["lap_time_seconds"]),
                    fuel_at_lap_start=item.get("fuel_at_lap_start"),
                    fuel_at_lap_end=item.get("fuel_at_lap_end"),
                    fuel_used=item.get("fuel_used"),
                )
                if 30.0 <= lap.lap_time_seconds <= 600.0:
                    self.laps.append(lap)
            except Exception:
                continue

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

    def update_live_timing(
        self,
        standings: list[StandingEntry],
        gap_ahead_seconds: float | None = None,
        gap_behind_seconds: float | None = None,
    ) -> None:
        self.live_standings = list(sorted(standings, key=lambda row: row.position))
        for row in standings:
            if not row.name:
                continue
            if row.is_player and not self.local_driver_name:
                self.local_driver_name = row.name.strip()
            key = row.name.strip().lower()
            prev = self.session_best_standings.get(key)
            if prev is None:
                self.session_best_standings[key] = row
                continue

            if prev.best_lap_seconds is None:
                best_lap = row.best_lap_seconds
            elif row.best_lap_seconds is None:
                best_lap = prev.best_lap_seconds
            else:
                best_lap = min(prev.best_lap_seconds, row.best_lap_seconds)

            best_pos = min(prev.position, row.position)
            merged = StandingEntry(
                position=best_pos,
                name=prev.name if len(prev.name) >= len(row.name) else row.name,
                best_lap_seconds=best_lap,
                is_player=bool(prev.is_player or row.is_player),
            )
            self.session_best_standings[key] = merged

        self.live_gap_ahead_seconds = gap_ahead_seconds
        self.live_gap_behind_seconds = gap_behind_seconds

    def update_setup_context(
        self,
        setup_id: str | None,
        setup_label: str | None = None,
        track_name: str | None = None,
        track_layout: str | None = None,
    ) -> None:
        clean = (setup_id or "").strip()
        self.active_setup_id = clean if clean else None
        label = (setup_label or "").strip()
        self.active_setup_label = label if label else None
        if track_name:
            if track_name != "unknown":
                self.last_known_track_name = track_name
            self._track_sections = load_sections(track_name, track_layout or "")

    def _resolve_track_label(self, snap: TelemetrySnapshot) -> str:
        if snap.track_name and snap.track_name != "unknown":
            return snap.track_name
        if self.last_known_track_name and self.last_known_track_name != "unknown":
            return self.last_known_track_name
        return "pista desconocida"

    def update_live_weather(
        self,
        track_grip_percent: float | None = None,
        air_temp_c: float | None = None,
        asphalt_temp_c: float | None = None,
        wind_speed_kmh: float | None = None,
    ) -> None:
        self.live_track_grip_percent = track_grip_percent
        self.live_air_temp_c = air_temp_c
        self.live_asphalt_temp_c = asphalt_temp_c
        self.live_wind_speed_kmh = wind_speed_kmh

    def record_tick(self, snapshot: TelemetrySnapshot) -> None:
        """Registra muestra en vivo para análisis por microsectores."""
        self._record_trace_sample(snapshot)

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
        # Algunos feeds tardan en marcar is_in_pit al arrancar; inferimos estado de boxes
        # si el coche está completamente detenido y aún sin vuelta válida.
        inferred_in_pit = bool(
            snap.is_in_pit
            or (
                snap.speed_kmh <= 1.0
                and snap.player_position <= 0
                and stats.best_lap_seconds is None
            )
        )
        where_label = "en pits" if inferred_in_pit else "en pista"
        track_label = self._resolve_track_label(snap)

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

        grip_value = (
            snap.track_grip_percent
            if snap.track_grip_percent is not None
            else self.live_track_grip_percent
        )
        # Preferir JSON en vivo sobre shared memory: SM no actualiza aire/asfalto mid-sesión.
        air_value = self.live_air_temp_c if self.live_air_temp_c is not None else snap.air_temp_c
        asphalt_value = (
            self.live_asphalt_temp_c if self.live_asphalt_temp_c is not None else snap.asphalt_temp_c
        )
        raw_wind_snapshot = snap.wind_speed_kmh
        raw_wind_live = self.live_wind_speed_kmh
        wind_value = None
        if raw_wind_snapshot is not None and raw_wind_snapshot > 0.1:
            wind_value = raw_wind_snapshot
        elif raw_wind_live is not None and raw_wind_live > 0.1:
            wind_value = raw_wind_live

        ac_log_weather = load_ac_log_weather_info()
        if air_value is None:
            air_value = ac_log_weather.air_temp_c
        if asphalt_value is None:
            asphalt_value = ac_log_weather.asphalt_temp_c
        if wind_value is None:
            wind_value = ac_log_weather.wind_speed_kmh

        weather_parts: list[str] = []
        if grip_value is not None:
            weather_parts.append(f"Grip de pista {grip_value:.0f} por ciento.")
        if air_value is not None:
            weather_parts.append(f"Aire {air_value:.0f} grados.")
        if asphalt_value is not None:
            weather_parts.append(f"Asfalto {asphalt_value:.0f} grados.")
        if wind_value is not None:
            weather_parts.append(f"Viento {wind_value:.0f} kilómetros por hora.")

        weather_line = (
            " ".join(weather_parts)
            if weather_parts
            else "Meteo detallada no disponible en este feed de telemetría."
        )

        if inferred_in_pit:
            return (
                f"Estamos en {track_label}. Sesión {session_label}, estado {where_label}. "
                f"{weather_line}"
            )

        return (
            f"Estamos en {track_label}. Sesión {session_label}, estado {where_label}. "
            f"{pace_line} {self._build_competitive_snapshot()} {fuel_line} {weather_line}"
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
                summary = self._build_hotlap_reference_summary(include_position=False)
                if summary:
                    return f"Clasificación activa. Tienes mejor vuelta marcada. {summary}"
                return "Clasificación activa. Tienes mejor vuelta marcada, prepara otra vuelta push."
            delta = stats.last_lap_seconds - stats.best_lap_seconds
            if delta > 0.4:
                base = (
                    f"Clasificación. Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}, "
                    f"a {speak_delta_spanish(delta)} de tu mejor. "
                    "Calienta neumáticos y maximiza salida de curva."
                )
                summary = self._build_hotlap_reference_summary(include_position=False)
                micro_tip = self._build_microsector_tip()
                text = f"{base} {summary}" if summary else base
                return f"{text} {micro_tip}" if micro_tip else text
            summary = self._build_hotlap_reference_summary(include_position=False)
            micro_tip = self._build_microsector_tip()
            if summary:
                text = f"Clasificación. Buena vuelta, mantente en ventana de push controlado. {summary}"
                return f"{text} {micro_tip}" if micro_tip else text
            text = "Clasificación. Buena vuelta, mantente en ventana de push controlado."
            return f"{text} {micro_tip}" if micro_tip else text

        if snap.session_type == "race":
            fuel_note = (
                f"Fuel para {speak_laps_spanish(stats.estimated_laps_left)}."
                if stats.estimated_laps_left is not None
                else f"Fuel actual {snap.fuel:.1f} litros."
            )

            parts: list[str] = []
            race_summary = self._build_race_reference_summary(
                gap_ahead_seconds=gap_ahead_seconds,
                gap_behind_seconds=gap_behind_seconds,
                include_position=False,
            )
            if race_summary:
                parts.append(race_summary)

            if not parts:
                parts.append("Sin datos de diferencia con coches cercanos.")
            parts.append(fuel_note)
            return " ".join(parts)

        if stats.last_lap_seconds is None:
            return "Sin vuelta válida todavía. Completa esta vuelta y te doy análisis de ritmo."

        parts: list[str] = []

        if stats.best_lap_seconds is not None and stats.last_lap_seconds is not None:
            delta = stats.last_lap_seconds - stats.best_lap_seconds
            if delta >= 0.6:
                parts.append("Perdiste ritmo, enfoca salida de curva y tracción.")

        if stats.estimated_laps_left is not None:
            parts.append(f"Combustible para {speak_laps_spanish(stats.estimated_laps_left)}.")
        else:
            parts.append(f"Combustible actual {snap.fuel:.1f} litros.")

        reference_summary = self._build_hotlap_reference_summary(include_position=False)
        if reference_summary:
            parts.append(reference_summary)

        micro_tip = self._build_microsector_tip()
        if micro_tip:
            parts.append(micro_tip)

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
            race_summary = self._build_race_reference_summary(include_position=False)
            if stats.last_lap_seconds is not None:
                base = (
                    f"{pos_line} Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}. "
                    f"Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."
                    if stats.best_lap_seconds is not None
                    else f"{pos_line} Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}."
                )
                return f"{base} {race_summary}" if race_summary else base
            if stats.best_lap_seconds is not None:
                base = f"{pos_line} Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."
                return f"{base} {race_summary}" if race_summary else base
            if race_summary:
                return f"{pos_line} {race_summary}"
            return pos_line + " Aún no hay vuelta válida."

        hotlap_summary = self._build_hotlap_reference_summary(include_position=False)
        if stats.best_lap_seconds is not None:
            base = f"{pos_line} Mejor tiempo {speak_lap_time_spanish(stats.best_lap_seconds)}."
            return f"{base} {hotlap_summary}" if hotlap_summary else base

        if stats.last_lap_seconds is not None:
            base = f"{pos_line} Última vuelta {speak_lap_time_spanish(stats.last_lap_seconds)}."
            return f"{base} {hotlap_summary}" if hotlap_summary else base

        if hotlap_summary:
            return f"{pos_line} {hotlap_summary}"

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

    def build_objective_report(self) -> str:
        """Reporte rápido de métricas objetivo en vivo."""
        snap = self.last_snapshot
        stats = self.get_stats()

        if snap is None or not self.laps:
            return "Sin datos de sesión todavía para análisis objetivo."

        parts: list[str] = []

        # Ritmo
        if stats.best_lap_seconds:
            parts.append(f"Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}.")
        if stats.avg_last_3_seconds:
            parts.append(f"Promedio últimas 3 {speak_lap_time_spanish(stats.avg_last_3_seconds)}.")

        # Consistencia
        if len(self.laps) >= 3:
            recent_times = [lap.lap_time_seconds for lap in self.laps[-3:]]
            if all(30.0 <= t <= 600.0 for t in recent_times):
                degradation = max(recent_times) - min(recent_times)
                if degradation < 0.2:
                    parts.append("Consistencia excelente.")
                elif degradation < 0.5:
                    parts.append("Consistencia buena.")

        # Fuel
        if stats.estimated_laps_left is not None:
            parts.append(f"Combustible para {speak_laps_spanish(stats.estimated_laps_left)}.")

        hotlap_summary = self._build_hotlap_reference_summary(include_position=False)
        if hotlap_summary and snap.session_type in {"practice", "qualifying"}:
            parts.append(hotlap_summary)

        race_summary = self._build_race_reference_summary(include_position=False)
        if race_summary and snap.session_type == "race":
            parts.append(race_summary)

        return " ".join(parts) if parts else "Datos insuficientes para análisis objetivo."

    def build_objective_briefing(self, session_objectives: dict[str, dict[str, str]] | None = None) -> str:
        """Briefing completo en pits: estado + objetivo + recomendación de setup.

        Ideal al decir 'Radio Check' al inicio de sesión desde pits.
        """
        snap = self.last_snapshot
        if snap is None:
            return "Sin telemetría todavía."

        session_type = snap.session_type
        is_in_pit = snap.is_in_pit

        # Parte 1: Info básica
        track_label = self._resolve_track_label(snap)
        
        session_name_map = {
            "practice": "práctica",
            "qualifying": "clasificación",
            "race": "carrera",
            "hotlap": "hotlap",
            "time_attack": "time attack",
            "drift": "drift",
            "drag": "drag",
            "unknown": "sesión desconocida",
        }
        session_label = session_name_map.get(session_type, "sesión desconocida")
        
        total_seconds = self._normalize_session_seconds(self.session_total_seconds)
        remaining_seconds = self._normalize_session_seconds(snap.session_time_left_seconds)

        # El feed de AC expone de forma confiable el tiempo restante; el "total" puede
        # degradarse si nos conectamos tarde. Solo lo anunciamos cuando aporta contexto real.
        total_is_reliable = bool(
            total_seconds is not None
            and total_seconds >= 600.0
            and (remaining_seconds is None or (total_seconds - remaining_seconds) >= 60.0)
        )

        time_bits: list[str] = []
        if total_is_reliable and total_seconds is not None:
            total_minutes = int(round(total_seconds / 60.0))
            total_word = "minuto" if total_minutes == 1 else "minutos"
            time_bits.append(f"tiempo total {total_minutes} {total_word}")
        if remaining_seconds is not None:
            remaining_minutes = int(round(remaining_seconds / 60.0))
            remaining_word = "minuto" if remaining_minutes == 1 else "minutos"
            time_bits.append(f"restan {remaining_minutes} {remaining_word}")

        if time_bits:
            info_line = f"Estamos en {track_label}. Sesión {session_label}, {', '.join(time_bits)}."
        else:
            info_line = f"Estamos en {track_label}. Sesión {session_label}."
        
        # Parte 2: Ritmo (si lo hay)
        stats = self.get_stats()
        if not is_in_pit and stats.best_lap_seconds is not None:
            pace_line = f"Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."
        else:
            pace_line = ""

        # Parte 3: Objetivo según sesión
        objective_line = ""
        configured_target_seconds = None
        if self.active_objectives is not None:
            objective_line = self.active_objectives.voice_intro()

        if not objective_line and session_objectives is not None:
            obj_data = session_objectives.get(session_type, session_objectives.get("unknown", {}))
            if obj_data:
                goal = obj_data.get("goal", "")
                target = obj_data.get("target_pace", "")
                configured_target_seconds = self._parse_target_pace_seconds(target)
                objective_line = f"Objetivo {session_label}: {goal}."

        history = load_historical_pace_summary(track_name=track_label, session_type=session_type)

        own_reference_seconds = stats.best_lap_seconds or history.own_best_seconds
        target_seconds = own_reference_seconds
        target_source = "propio"

        if history.rival_best_seconds is not None:
            if target_seconds is None or history.rival_best_seconds < target_seconds:
                target_seconds = history.rival_best_seconds
                target_source = "rival"

        if target_seconds is None and configured_target_seconds is not None:
            target_seconds = configured_target_seconds
            target_source = "configurado"

        target_line = ""
        if target_seconds is not None:
            target_line = f"Ritmo objetivo {speak_lap_time_spanish(target_seconds)}."

        delta_line = ""
        if own_reference_seconds is not None and target_seconds is not None:
            delta_seconds = target_seconds - own_reference_seconds
            if target_source == "rival" and history.rival_best_name and delta_seconds < -0.02:
                target_line = (
                    f"Ritmo objetivo {speak_lap_time_spanish(target_seconds)} con referencia de "
                    f"{history.rival_best_name}; delta a mejorar "
                    f"{speak_delta_spanish(abs(delta_seconds))} contra tu referencia."
                )
                delta_line = ""
            elif delta_seconds < -0.02:
                delta_line = (
                    f"Delta contra tu referencia: necesitas mejorar "
                    f"{speak_delta_spanish(abs(delta_seconds))}."
                )
            elif delta_seconds > 0.02:
                delta_line = (
                    f"Delta contra tu referencia: objetivo {speak_delta_spanish(delta_seconds)} "
                    f"más conservador."
                )
            else:
                delta_line = "Delta contra tu referencia: objetivo alineado."

        own_pace_line = ""
        if stats.best_lap_seconds is not None:
            own_pace_line = f"Mejor ritmo actual {speak_lap_time_spanish(stats.best_lap_seconds)}."
        elif history.own_best_seconds is not None:
            own_pace_line = f"Mejor ritmo histórico propio {speak_lap_time_spanish(history.own_best_seconds)}."

        rival_line = ""
        if history.rival_best_name and history.rival_best_seconds is not None:
            repeats_target_reference = (
                target_source == "rival"
                and target_seconds is not None
                and abs(history.rival_best_seconds - target_seconds) <= 0.001
            )
            if not repeats_target_reference:
                rival_line = (
                    f"Referencia rival histórica: {history.rival_best_name} con "
                    f"{speak_lap_time_spanish(history.rival_best_seconds)}."
                )

        setup_line = ""
        if history.own_best_setup_id:
            best_setup_label = history.own_best_setup_label or history.own_best_setup_id
            active_label = self.active_setup_label or self.active_setup_id
            same_setup_id = self.active_setup_id and self.active_setup_id == history.own_best_setup_id
            same_setup_label = (
                bool(active_label)
                and str(active_label).strip().lower() == str(best_setup_label).strip().lower()
            )
            setup_line = f"Setup con mejor resultado histórico: {best_setup_label}."
            if active_label and not same_setup_id and not same_setup_label:
                setup_line += f" Setup actual: {active_label}."
        elif self.active_setup_label:
            setup_line = f"Setup actual detectado: {self.active_setup_label}."
        elif self.active_setup_id:
            setup_line = f"Setup actual detectado: {self.active_setup_id}."
        
        # Parte 4: Fuel solo si aporta decisión estratégica en carrera.
        if session_type == "race" and snap.fuel > 0:
            fuel_line = f"Combustible actual {snap.fuel:.1f} litros."
        else:
            fuel_line = ""

        reference_line = self._build_competitive_snapshot()

        grip_value = (
            snap.track_grip_percent
            if snap.track_grip_percent is not None
            else self.live_track_grip_percent
        )
        # Preferir JSON en vivo sobre shared memory: SM no actualiza aire/asfalto mid-sesión.
        air_value = self.live_air_temp_c if self.live_air_temp_c is not None else snap.air_temp_c
        asphalt_value = (
            self.live_asphalt_temp_c if self.live_asphalt_temp_c is not None else snap.asphalt_temp_c
        )
        raw_wind_snapshot = snap.wind_speed_kmh
        raw_wind_live = self.live_wind_speed_kmh
        wind_value = None
        if raw_wind_snapshot is not None and raw_wind_snapshot > 0.1:
            wind_value = raw_wind_snapshot
        elif raw_wind_live is not None and raw_wind_live > 0.1:
            wind_value = raw_wind_live

        ac_log_weather = load_ac_log_weather_info()
        if air_value is None:
            air_value = ac_log_weather.air_temp_c
        if asphalt_value is None:
            asphalt_value = ac_log_weather.asphalt_temp_c
        if wind_value is None:
            wind_value = ac_log_weather.wind_speed_kmh

        weather_parts: list[str] = []
        if grip_value is not None:
            weather_parts.append(f"Grip de pista {grip_value:.0f} por ciento.")
        if air_value is not None:
            weather_parts.append(f"Aire {air_value:.0f} grados.")
        if asphalt_value is not None:
            weather_parts.append(f"Asfalto {asphalt_value:.0f} grados.")
        if wind_value is not None:
            weather_parts.append(f"Viento {wind_value:.0f} kilómetros por hora.")

        weather_line = (
            " ".join(weather_parts)
            if weather_parts
            else "Meteo detallada no disponible en este feed de telemetría."
        )

        parts = [
            p
            for p in [
                info_line,
                pace_line,
                objective_line,
                target_line,
                delta_line,
                own_pace_line,
                rival_line,
                setup_line,
                reference_line,
                fuel_line,
                weather_line,
            ]
            if p
        ]
        return " ".join(parts)

    def build_lap_competitor_summary(self) -> str:
        snap = self.last_snapshot
        if snap is None or snap.player_position <= 0:
            return ""

        if snap.session_type == "race":
            return self._build_race_reference_summary(include_position=True)

        if snap.session_type in {"practice", "qualifying"}:
            return self._build_hotlap_reference_summary(include_position=True)

        return ""

    def build_pit_exit_report(self) -> str:
        snap = self.last_snapshot
        if snap is None:
            return ""

        stats = self.get_stats()
        parts: list[str] = [f"Salida de pits. Combustible actual {snap.fuel:.1f} litros."]

        if stats.estimated_laps_left is not None:
            parts.append(f"Estimado para {speak_laps_spanish(stats.estimated_laps_left)}.")

        if snap.session_type in {"practice", "qualifying"}:
            if stats.best_lap_seconds is not None:
                parts.append(f"Mejor ritmo actual {speak_lap_time_spanish(stats.best_lap_seconds)}.")
            hotlap_summary = self._build_hotlap_reference_summary(include_position=False)
            if hotlap_summary:
                parts.append(hotlap_summary)

        if snap.session_type == "race":
            race_summary = self._build_race_reference_summary(include_position=False)
            if race_summary:
                parts.append(race_summary)

        return " ".join(parts)

    def capture_session_time(self, seconds: float) -> bool:
        """Registra una estimación del tiempo total de sesión.

        Retorna True cuando mejora la mejor estimación previa.
        """
        normalized_seconds = self._normalize_session_seconds(seconds)
        if normalized_seconds is None or normalized_seconds <= 5.0:
            return False

        # Conservamos el mayor valor observado de "time left" como mejor proxy
        # de la duración total cuando no existe el campo explícito en el feed.
        if normalized_seconds > (self.session_total_seconds + 5.0):
            self.session_total_seconds = normalized_seconds
            if self.active_objectives is not None:
                self.active_objectives.update_session_time(normalized_seconds / 60.0)
            return True
        return False

    @staticmethod
    def _normalize_session_seconds(seconds: float | None) -> float | None:
        if seconds is None or seconds <= 0.0:
            return None

        value = float(seconds)
        # Algunos feeds llegan en milisegundos; convertir para evitar tiempos absurdos.
        if value > 21600.0:  # 6 horas
            value = value / 1000.0

        # Si sigue fuera de rango razonable, mejor no anunciarlo.
        if value <= 0.0 or value > 86400.0:
            return None

        return value

    def build_box_box_report(
        self,
        setup_feedback: str = "",
        setup_coach: "SetupCoach | None" = None,
    ) -> str:
        snap = self.last_snapshot
        if snap is None:
            return "Sin telemetría todavía para confirmar entrada a boxes."

        parts: list[str] = ["Box box, recibido."]

        # Evaluación de objetivos si existen
        obj_set = self.active_objectives
        if obj_set is not None:
            obj_set.evaluate(self, setup_coach=setup_coach)
            obj_summary = obj_set.voice_summary()
            if obj_summary:
                parts.append(obj_summary)
        else:
            # Fallback: resumen bueno/malo sin objetivos formales
            good_summary, bad_summary = self._build_performance_review()
            if good_summary:
                parts.append(f"Lo bueno: {good_summary}.")
            if bad_summary:
                parts.append(f"Lo malo: {bad_summary}.")

        if snap.session_type == "race":
            strategy = self._build_race_box_strategy()
            if strategy:
                parts.append(strategy)
        elif setup_feedback:
            parts.append(setup_feedback)

        return " ".join(parts)

    def build_session_summary(self) -> str:
        snap = self.last_snapshot
        if snap is None:
            return "Sin telemetría todavía para resumen de sesión."

        if snap.session_type not in {"practice", "qualifying", "race"}:
            return f"No tengo resumen configurado para la sesión {snap.session_type}."

        standings: list[StandingEntry] = []
        if snap.session_type in {"practice", "qualifying"}:
            standings = self._get_hotlap_ranked_standings()
        elif self.live_standings:
            standings = list(self.live_standings)
        elif self.session_best_standings:
            standings = list(self.session_best_standings.values())

        stats = self.get_stats()
        return build_session_end_summary(
            session_label=snap.session_type,
            own_position=snap.player_position,
            own_best_lap=stats.best_lap_seconds,
            standings=standings,
        )

    def build_rivals_report(self) -> str:
        snap = self.last_snapshot
        if snap is None:
            return "Sin telemetría todavía para reporte de rivales."
        if not self.live_standings:
            return "Sin timing de rivales en vivo todavía."

        if snap.session_type == "race":
            race_summary = self._build_race_reference_summary(include_position=False)
            if race_summary:
                return race_summary
            return "Sin gaps en vivo para rivales cercanos en carrera."

        leader = self._get_standing_by_position(1)
        if leader is None:
            return "Sin líder confirmado en timing en vivo."

        parts: list[str] = []
        if leader.best_lap_seconds is not None:
            parts.append(
                f"Líder {leader.name} con {speak_lap_time_spanish(leader.best_lap_seconds)}."
            )
        else:
            parts.append(f"Líder actual {leader.name}.")

        if snap.player_position > 0:
            ahead = self._get_standing_by_position(snap.player_position - 1)
            if ahead is not None and ahead.best_lap_seconds is not None:
                stats = self.get_stats()
                if stats.best_lap_seconds is not None:
                    gap_to_ahead = stats.best_lap_seconds - ahead.best_lap_seconds
                    if gap_to_ahead > 0:
                        parts.append(f"Delante, {ahead.name} a {speak_delta_spanish(gap_to_ahead)}.")

        return " ".join(parts)

    def _build_performance_review(self) -> tuple[str, str]:
        snap = self.last_snapshot
        stats = self.get_stats()
        if snap is None:
            return "", ""

        good: list[str] = []
        bad: list[str] = []

        if self.laps:
            last_time = self.laps[-1].lap_time_seconds
            best_time = min(lap.lap_time_seconds for lap in self.laps)
            delta = last_time - best_time
            if delta <= 0.12:
                good.append("última vuelta muy cerca de la referencia")
            elif delta >= 0.6:
                bad.append("última vuelta lejos de tu mejor ritmo")

        if len(self.laps) >= 3:
            recent_times = [lap.lap_time_seconds for lap in self.laps[-3:]]
            spread = max(recent_times) - min(recent_times)
            if spread <= 0.35:
                good.append("consistencia sólida en las últimas vueltas")
            elif spread >= 0.9:
                bad.append("mucho ruido entre vueltas, falta una tanda limpia")

        if snap.session_type == "qualifying" and stats.best_lap_seconds is not None:
            hotlap_summary = self._build_hotlap_reference_summary(include_position=False)
            if hotlap_summary:
                good.append("ya tienes una referencia útil de clasificación")

        insights = self._compute_microsector_insights(limit=1)
        if insights:
            label = self._microsector_label(int(insights[0]["index"]))
            bad.append(f"sigues perdiendo tiempo en {label}")

        return ", ".join(good[:2]), ", ".join(bad[:2])

    def _build_race_box_strategy(self) -> str:
        snap = self.last_snapshot
        stats = self.get_stats()
        if snap is None:
            return ""

        parts: list[str] = []
        remaining_laps = self._estimate_remaining_race_laps()
        if remaining_laps is not None and stats.avg_fuel_per_lap is not None:
            fuel_required = remaining_laps * stats.avg_fuel_per_lap + 0.8
            add_fuel = fuel_required - snap.fuel
            if add_fuel > 0.4:
                parts.append(f"Carga {add_fuel:.1f} litros para llegar al final con margen.")
            else:
                parts.append("Con el combustible actual llegamos al final.")
        elif stats.estimated_laps_left is not None:
            parts.append(f"Fuel actual para {speak_laps_spanish(stats.estimated_laps_left)}.")
        else:
            parts.append(f"Fuel actual {snap.fuel:.1f} litros, aún sin cálculo fiable de stint.")

        if self.live_gap_ahead_seconds is not None and self.live_gap_behind_seconds is not None:
            if self.live_gap_ahead_seconds > 8.0 and self.live_gap_behind_seconds > 8.0:
                parts.append("Ventana relativamente limpia para parar.")
            elif self.live_gap_behind_seconds < 2.5:
                parts.append("Tráfico cercano detrás, cuida reincorporación.")

        if len(self.laps) >= 4:
            recent_times = [lap.lap_time_seconds for lap in self.laps[-4:]]
            stint_drop = recent_times[-1] - min(recent_times)
            if stint_drop >= 0.8:
                parts.append("Ritmo cayó en el stint; si el reglamento lo permite, neumático nuevo tiene sentido.")
            elif stint_drop >= 0.4:
                parts.append("Hay caída ligera de ritmo; valora cambio de llantas si buscas undercut.")
        else:
            parts.append("No tengo desgaste directo de llantas en este feed; la llamada de neumáticos sigue siendo manual.")

        return " ".join(parts)

    def _estimate_remaining_race_laps(self) -> float | None:
        snap = self.last_snapshot
        stats = self.get_stats()
        if snap is None:
            return None

        if snap.session_laps_total > 0:
            remaining = max(float(snap.session_laps_total - snap.lap_number), 0.0)
            return remaining

        avg_lap = stats.avg_last_5_seconds or stats.avg_last_3_seconds or stats.best_lap_seconds
        remaining_seconds = self._normalize_session_seconds(snap.session_time_left_seconds)
        if remaining_seconds is not None and avg_lap is not None and avg_lap > 0.0:
            return max(remaining_seconds / avg_lap, 0.0)

        return None

    def build_microsector_report(self) -> str:
        if not self.last_lap_micro_profile or not self.best_lap_micro_profile:
            return "Necesito al menos una vuelta de referencia para análisis por microsectores."

        insights = self._compute_microsector_insights(limit=3)
        if not insights:
            return "No detecté pérdidas claras por microsector en la última vuelta."

        parts: list[str] = ["Top zonas de mejora por microsector:"]
        for item in insights:
            idx = int(item["index"])
            label = self._microsector_label(idx)
            loss = speak_delta_spanish(float(item["loss_seconds"]))
            speed_delta = float(item["speed_delta_kmh"])
            if speed_delta < 0:
                detail = f"{label}, pérdida estimada {loss}. Llegas {abs(speed_delta):.0f} kilómetros por hora más lento."
            else:
                detail = f"{label}, pérdida estimada {loss}."
            hint = self._build_technique_hint(item)
            if hint:
                detail += f" {hint}"
            parts.append(detail)

        return " ".join(parts)

    def _build_competitive_snapshot(self) -> str:
        snap = self.last_snapshot
        if snap is None:
            return ""
        if snap.session_type == "race":
            return self._build_race_reference_summary(include_position=False)
        if snap.session_type in {"practice", "qualifying"}:
            return self._build_hotlap_reference_summary(include_position=False)
        return ""

    def _build_hotlap_reference_summary(self, include_position: bool) -> str:
        snap = self.last_snapshot
        stats = self.get_stats()
        if snap is None or snap.player_position <= 0 or stats.best_lap_seconds is None:
            return ""

        ranked = self._get_hotlap_ranked_standings()
        if not ranked:
            return ""

        leader = ranked[0]
        leader_best = float(leader.best_lap_seconds or 0.0)
        if leader_best <= 0.0:
            return ""

        own_best = stats.best_lap_seconds

        # Liderato por tabla de mejor vuelta de sesión (no por posición cruda del feed).
        if own_best <= leader_best + 0.001:
            second = ranked[1] if len(ranked) > 1 else None
            if second is None or second.best_lap_seconds is None:
                return ""
            advantage = float(second.best_lap_seconds) - own_best
            if advantage <= 0:
                return ""
            prefix = "Paso por meta. " if include_position else ""
            return (
                f"{prefix}Lideramos. Segundo {second.name} a "
                f"{speak_delta_spanish(advantage)}."
            )

        gap_to_leader = own_best - leader_best
        parts = [
            f"Líder {leader.name} con {speak_lap_time_spanish(leader_best)}.",
            f"Estamos a {speak_delta_spanish(gap_to_leader)} del mejor tiempo.",
        ]

        ahead = self._get_hotlap_ahead_by_time(own_best)
        if ahead is not None and ahead.best_lap_seconds is not None:
            gap_to_ahead = own_best - float(ahead.best_lap_seconds)
            if gap_to_ahead > 0:
                parts.append(
                    f"Delante, {ahead.name} a {speak_delta_spanish(gap_to_ahead)}."
                )

        if include_position:
            return "Paso por meta. " + " ".join(parts)
        return " ".join(parts)

    def _get_hotlap_ranked_standings(self) -> list[StandingEntry]:
        local_name_keys = self._get_local_driver_name_keys()
        combined: dict[str, StandingEntry] = {}
        for row in self.session_best_standings.values():
            if row.best_lap_seconds is None or row.best_lap_seconds <= 0.0:
                continue
            key = row.name.strip().lower()
            if row.is_player or key in local_name_keys:
                continue
            combined[key] = row

        for row in self.live_standings:
            if row.best_lap_seconds is None or row.best_lap_seconds <= 0.0:
                continue
            key = row.name.strip().lower()
            if row.is_player or key in local_name_keys:
                continue
            prev = combined.get(key)
            if prev is None or float(row.best_lap_seconds) < float(prev.best_lap_seconds):
                combined[key] = row

        rows = list(combined.values())
        rows.sort(key=lambda r: (float(r.best_lap_seconds), r.name.lower()))
        return rows

    def _get_hotlap_ahead_by_time(self, own_best: float) -> StandingEntry | None:
        ranked = self._get_hotlap_ranked_standings()
        faster = [row for row in ranked if float(row.best_lap_seconds or 0.0) + 0.001 < own_best]
        if not faster:
            return None
        return faster[-1]

    def _build_race_reference_summary(
        self,
        gap_ahead_seconds: float | None = None,
        gap_behind_seconds: float | None = None,
        include_position: bool = False,
    ) -> str:
        snap = self.last_snapshot
        if snap is None or snap.player_position <= 0:
            return ""

        ahead_gap = gap_ahead_seconds if gap_ahead_seconds is not None else self.live_gap_ahead_seconds
        behind_gap = gap_behind_seconds if gap_behind_seconds is not None else self.live_gap_behind_seconds
        ahead = self._get_standing_by_position(snap.player_position - 1)
        behind = self._get_standing_by_position(snap.player_position + 1)

        parts: list[str] = []
        if include_position:
            parts.append(f"Paso por meta. Posición {snap.player_position}.")

        if ahead is not None and ahead_gap is not None:
            parts.append(f"Adelante, {ahead.name} a {speak_delta_spanish(ahead_gap)}.")
        elif ahead_gap is not None:
            parts.append(f"Adelante a {speak_delta_spanish(ahead_gap)}.")

        if behind is not None and behind_gap is not None:
            parts.append(f"Detrás, {behind.name} a {speak_delta_spanish(behind_gap)}.")
        elif behind_gap is not None:
            parts.append(f"Detrás a {speak_delta_spanish(behind_gap)}.")

        return " ".join(parts)

    def _record_trace_sample(self, snapshot: TelemetrySnapshot) -> None:
        if snapshot.status != "live":
            return
        pos = min(max(snapshot.normalized_car_position, 0.0), 1.0)
        speed = max(0.0, snapshot.speed_kmh)
        throttle = float(snapshot.throttle or 0.0)
        brake = float(snapshot.brake or 0.0)
        if self.current_lap_trace:
            last_pos = self.current_lap_trace[-1][0]
            if abs(last_pos - pos) < 0.002:
                return
        self.current_lap_trace.append((pos, speed, throttle, brake))

    def _build_microsector_profile(self, trace: list[tuple[float, float, float, float]]) -> dict[int, dict[str, float]]:
        if not trace:
            return {}

        buckets: dict[int, list[tuple[float, float, float]]] = {}
        for pos, speed, throttle, brake in trace:
            idx = int(pos * self.microsector_count)
            if idx >= self.microsector_count:
                idx = self.microsector_count - 1
            if idx < 0:
                idx = 0
            buckets.setdefault(idx, []).append((speed, throttle, brake))

        profile: dict[int, dict[str, float]] = {}
        for idx, samples in buckets.items():
            n = float(len(samples))
            profile[idx] = {
                "speed_kmh": sum(s[0] for s in samples) / n,
                "throttle": sum(s[1] for s in samples) / n,
                "brake": sum(s[2] for s in samples) / n,
                "samples": n,
            }
        return profile

    def _compute_microsector_insights(self, limit: int = 3) -> list[dict[str, float]]:
        stats = self.get_stats()
        if stats.best_lap_seconds is None:
            return []

        segment_time = stats.best_lap_seconds / float(self.microsector_count)
        insights: list[dict[str, float]] = []
        for idx in range(self.microsector_count):
            last = self.last_lap_micro_profile.get(idx)
            best = self.best_lap_micro_profile.get(idx)
            if last is None or best is None:
                continue
            v_last = float(last.get("speed_kmh", 0.0))
            v_best = float(best.get("speed_kmh", 0.0))
            if v_best < 20.0:
                continue
            speed_ratio_loss = max(0.0, (v_best - v_last) / max(v_best, 1.0))
            est_loss = segment_time * speed_ratio_loss
            if est_loss < 0.03:
                continue
            insights.append(
                {
                    "index": float(idx),
                    "loss_seconds": est_loss,
                    "speed_delta_kmh": v_last - v_best,
                    "throttle_delta": float(last.get("throttle", 0.0)) - float(best.get("throttle", 0.0)),
                    "brake_delta": float(last.get("brake", 0.0)) - float(best.get("brake", 0.0)),
                }
            )

        insights.sort(key=lambda x: x["loss_seconds"], reverse=True)
        return insights[:limit]

    def _build_microsector_tip(self) -> str:
        insights = self._compute_microsector_insights(limit=1)
        if not insights:
            return ""
        item = insights[0]
        label = self._microsector_label(int(item["index"]))
        loss = speak_delta_spanish(float(item["loss_seconds"]))
        hint = self._build_technique_hint(item)
        base = f"Zona crítica {label}, pérdida estimada {loss}."
        return f"{base} {hint}" if hint else base

    def _build_technique_hint(self, insight: dict[str, float]) -> str:
        throttle_delta = float(insight.get("throttle_delta", 0.0))
        brake_delta = float(insight.get("brake_delta", 0.0))

        if brake_delta > 0.08 and throttle_delta < -0.05:
            return "Frenas largo y aceleras tarde; prueba soltar freno antes y abrir gas progresivo."
        if brake_delta > 0.08:
            return "Frenada larga en esa zona; prueba soltar freno antes del vértice."
        if throttle_delta < -0.08:
            return "Salida tímida de gas; abre acelerador antes y más progresivo."
        if throttle_delta > 0.12:
            return "Posible sobreaceleración; cuida tracción para no perder salida."
        return "Repite con foco en línea limpia y velocidad mínima más alta."

    def _microsector_label(self, idx: int) -> str:
        idx = max(0, min(self.microsector_count - 1, idx))
        if self._track_sections:
            # El centro del microsector en posición normalizada
            center = (idx + 0.5) / self.microsector_count
            name = label_for_position(center, self._track_sections)
            if name:
                return name
        sector = int((idx * 3) / self.microsector_count) + 1
        return f"sector {sector} micro {idx + 1}"

    def _get_standing_by_position(self, position: int) -> StandingEntry | None:
        if position <= 0:
            return None
        for row in self.live_standings:
            if row.position == position:
                if self._is_local_driver_row(row):
                    return None
                return row
        return None

    def _get_local_driver_name_keys(self) -> set[str]:
        keys: set[str] = set()
        if self.local_driver_name:
            keys.add(self.local_driver_name.strip().lower())
        for row in self.live_standings:
            if row.is_player and row.name:
                keys.add(row.name.strip().lower())
        for row in self.session_best_standings.values():
            if row.is_player and row.name:
                keys.add(row.name.strip().lower())
        return keys

    def _is_local_driver_row(self, row: StandingEntry | None) -> bool:
        if row is None:
            return False
        if row.is_player:
            return True
        name_key = (row.name or "").strip().lower()
        if not name_key:
            return False
        return name_key in self._get_local_driver_name_keys()

    @staticmethod
    def _format_target_pace_for_voice(target_pace: str) -> str:
        """Convierte 1:45.000 en frase amigable para TTS en espanol."""
        raw = (target_pace or "").strip()
        if not raw:
            return "no definido"

        match = re.match(r"^(\d+):(\d{2})(?:\.(\d{1,3}))?$", raw)
        if not match:
            return raw

        minutes = int(match.group(1))
        seconds = int(match.group(2))
        millis_raw = match.group(3) or "000"
        millis = int(millis_raw.ljust(3, "0"))

        minute_word = "minuto" if minutes == 1 else "minutos"
        return f"{minutes} {minute_word} {seconds} segundos {millis:03d} milésimas"

    @staticmethod
    def _parse_target_pace_seconds(target_pace: str) -> float | None:
        raw = (target_pace or "").strip()
        if not raw:
            return None

        match = re.match(r"^(\d+):(\d{2})(?:\.(\d{1,3}))?$", raw)
        if not match:
            return None

        minutes = int(match.group(1))
        seconds = int(match.group(2))
        millis_raw = match.group(3) or "000"
        millis = int(millis_raw.ljust(3, "0"))
        return minutes * 60.0 + seconds + millis / 1000.0
