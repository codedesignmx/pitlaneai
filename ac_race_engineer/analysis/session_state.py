from __future__ import annotations

from dataclasses import dataclass, field
import re

from ac_race_engineer.analysis.fuel import average_fuel_per_lap, estimate_laps_left
from ac_race_engineer.analysis.pace import average_last_n, best_lap, last_lap
from ac_race_engineer.analysis.time_format import (
    speak_delta_spanish,
    speak_lap_time_spanish,
    speak_laps_spanish,
)
from ac_race_engineer.storage.performance_history import load_historical_pace_summary
from ac_race_engineer.storage.results_summary import StandingEntry
from ac_race_engineer.storage.track_sections import label_for_position, load_sections
from ac_race_engineer.telemetry.models import LapRecord, SessionStats, TelemetrySnapshot


@dataclass(slots=True)
class SessionState:
    laps: list[LapRecord] = field(default_factory=list)
    last_snapshot: TelemetrySnapshot | None = None
    current_lap_start_fuel: float | None = None
    last_completed_lap_counter: int = -1  # -1 = todavía no inicializado
    last_collision_note: str | None = None
    live_standings: list[StandingEntry] = field(default_factory=list)
    live_gap_ahead_seconds: float | None = None
    live_gap_behind_seconds: float | None = None
    active_setup_id: str | None = None
    active_setup_label: str | None = None
    _track_sections: list = field(default_factory=list)
    microsector_count: int = 20
    current_lap_trace: list[tuple[float, float, float, float]] = field(default_factory=list)
    last_lap_micro_profile: dict[int, dict[str, float]] = field(default_factory=dict)
    best_lap_micro_profile: dict[int, dict[str, float]] = field(default_factory=dict)

    def update(self, snapshot: TelemetrySnapshot) -> LapRecord | None:
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
            self._track_sections = load_sections(track_name, track_layout or "")

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
        where_label = "en pits" if is_in_pit else "en pista"
        track_label = snap.track_name if snap.track_name and snap.track_name != "unknown" else "pista desconocida"
        
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
        
        info_line = f"Estamos en {track_label}. Sesión {session_label}, estado {where_label}."
        
        # Parte 2: Ritmo (si lo hay)
        stats = self.get_stats()
        if not is_in_pit and stats.best_lap_seconds is not None:
            pace_line = f"Mejor vuelta {speak_lap_time_spanish(stats.best_lap_seconds)}."
        else:
            pace_line = ""

        # Parte 3: Objetivo según sesión
        objective_line = ""
        configured_target_seconds = None
        if session_objectives is not None:
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
            if target_source == "rival" and history.rival_best_name:
                target_line += f" Referencia de {history.rival_best_name}."

        delta_line = ""
        if own_reference_seconds is not None and target_seconds is not None:
            delta_seconds = target_seconds - own_reference_seconds
            if delta_seconds < -0.02:
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
            rival_line = (
                f"Referencia rival histórica: {history.rival_best_name} con "
                f"{speak_lap_time_spanish(history.rival_best_seconds)}."
            )

        setup_line = ""
        if history.own_best_setup_id:
            best_setup_label = history.own_best_setup_label or history.own_best_setup_id
            active_label = self.active_setup_label or self.active_setup_id
            if self.active_setup_id and self.active_setup_id == history.own_best_setup_id:
                setup_line = f"Setup actual coincide con el mejor histórico: {best_setup_label}."
            else:
                setup_line = f"Setup con mejor resultado histórico: {best_setup_label}."
                if active_label:
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

    def build_box_box_report(self, setup_feedback: str = "") -> str:
        snap = self.last_snapshot
        if snap is None:
            return "Sin telemetría todavía para confirmar entrada a boxes."

        if snap.session_type == "race":
            strategy = self._build_race_box_strategy()
            return f"Box box, recibido. {strategy}" if strategy else "Box box, recibido."

        good_summary, bad_summary = self._build_performance_review()
        parts: list[str] = ["Box box, recibido."]
        if good_summary:
            parts.append(f"Lo bueno: {good_summary}.")
        if bad_summary:
            parts.append(f"Lo malo: {bad_summary}.")
        if setup_feedback:
            parts.append(setup_feedback)
        return " ".join(parts)

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
        if snap.session_time_left_seconds > 0 and avg_lap is not None and avg_lap > 0.0:
            return max(snap.session_time_left_seconds / avg_lap, 0.0)

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

        leader = self._get_standing_by_position(1)
        if leader is None or leader.best_lap_seconds is None:
            return ""

        if snap.player_position == 1:
            second = self._get_standing_by_position(2)
            if second is None or second.best_lap_seconds is None:
                return ""
            advantage = second.best_lap_seconds - stats.best_lap_seconds
            if advantage <= 0:
                return ""
            prefix = "Paso por meta. " if include_position else ""
            return (
                f"{prefix}Lideramos. Segundo {second.name} a "
                f"{speak_delta_spanish(advantage)}."
            )

        gap_to_leader = stats.best_lap_seconds - leader.best_lap_seconds
        parts = [
            f"Líder {leader.name} con {speak_lap_time_spanish(leader.best_lap_seconds)}.",
            f"Estamos a {speak_delta_spanish(gap_to_leader)} del mejor tiempo.",
        ]

        ahead = self._get_standing_by_position(snap.player_position - 1)
        if ahead is not None and ahead.best_lap_seconds is not None:
            gap_to_ahead = stats.best_lap_seconds - ahead.best_lap_seconds
            if gap_to_ahead > 0:
                parts.append(
                    f"Delante, {ahead.name} a {speak_delta_spanish(gap_to_ahead)}."
                )

        if include_position:
            return "Paso por meta. " + " ".join(parts)
        return " ".join(parts)

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
                return row
        return None

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
