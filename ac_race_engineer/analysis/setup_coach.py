from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ac_race_engineer.storage.setup_registry import SetupInfo
from ac_race_engineer.telemetry.models import LapRecord

if TYPE_CHECKING:
    from ac_race_engineer.analysis.session_state import SessionState


@dataclass(slots=True)
class SetupRecommendation:
    parameter: str
    direction: int  # +1 subir, -1 bajar
    step: float
    reason: str
    current_value: float
    target_value: float
    min_value: float
    max_value: float


@dataclass(slots=True)
class SetupIteration:
    timestamp_iso: str
    setup_id: str | None
    setup_label: str | None
    issue: str
    parameter: str
    direction: int
    step: float
    current_value: float
    target_value: float
    min_value: float
    max_value: float
    reason: str
    outcome: str = "pending"


class SetupCoach:
    """Coach iterativo de setup basado en sensaciones del piloto.

    - Solo orientado a práctica.
    - Recomienda cambios pequeños y controlados.
    - Usa rangos observados en setups del coche para evitar sugerencias absurdas.
    """

    def __init__(self) -> None:
        self.active = False
        self.current_setup_id: str | None = None
        self.current_setup_label: str | None = None
        self.current_car_model: str | None = None
        self.current_track_name: str | None = None
        self.current_values: dict[str, float] = {}
        self._bounds_cache: dict[str, dict[str, tuple[float, float]]] = {}
        self.last_recommendation: SetupRecommendation | None = None
        self.last_issue: str | None = None
        self.iterations: list[SetupIteration] = []
        self._setup_lap_history: dict[str, dict[str, object]] = {}
        self._parameter_outcomes: dict[str, dict[str, int]] = {}
        self._parameter_limits: dict[str, dict[str, float]] = {}
        self._learning_store_path = Path("database/setup_learning.json")
        self._global_learning: dict[str, object] = self._load_learning_store()

    def reset_session_notes(self) -> None:
        self.last_recommendation = None
        self.last_issue = None
        self.iterations = []
        self._setup_lap_history = {}
        self._parameter_outcomes = {}
        self._parameter_limits = {}

    def export_iterations(self) -> list[dict[str, object]]:
        return [
            {
                "timestamp": item.timestamp_iso,
                "setup_id": item.setup_id,
                "setup_label": item.setup_label,
                "issue": item.issue,
                "parameter": item.parameter,
                "direction": "up" if item.direction > 0 else "down",
                "step": item.step,
                "current_value": item.current_value,
                "target_value": item.target_value,
                "range": [item.min_value, item.max_value],
                "reason": item.reason,
                "outcome": item.outcome,
            }
            for item in self.iterations
        ]

    def restore_state(self, data: dict) -> None:
        """Restaura el estado del coach desde un checkpoint de sesión."""
        self.active = bool(data.get("active", False))

        self.iterations = []
        for item in data.get("iterations", []):
            try:
                rng = item.get("range", [0, 10])
                self.iterations.append(
                    SetupIteration(
                        timestamp_iso=str(item.get("timestamp", "")),
                        setup_id=item.get("setup_id"),
                        setup_label=item.get("setup_label"),
                        issue=str(item.get("issue", "")),
                        parameter=str(item.get("parameter", "")),
                        direction=1 if item.get("direction") == "up" else -1,
                        step=float(item.get("step", 1.0)),
                        current_value=float(item.get("current_value", 0.0)),
                        target_value=float(item.get("target_value", 0.0)),
                        min_value=float(rng[0]),
                        max_value=float(rng[1]),
                        reason=str(item.get("reason", "")),
                        outcome=str(item.get("outcome", "pending")),
                    )
                )
            except Exception:
                continue

        raw_outcomes = data.get("parameter_outcomes", {})
        if isinstance(raw_outcomes, dict):
            self._parameter_outcomes = {
                k: dict(v) for k, v in raw_outcomes.items() if isinstance(v, dict)
            }

        raw_limits = data.get("parameter_limits", {})
        if isinstance(raw_limits, dict):
            self._parameter_limits = {
                k: {str(inner_k): float(inner_v) for inner_k, inner_v in v.items() if isinstance(inner_v, (int, float))}
                for k, v in raw_limits.items()
                if isinstance(v, dict)
            }

        raw_history = data.get("setup_lap_history", {})
        if isinstance(raw_history, dict):
            self._setup_lap_history = raw_history

        rec_data = data.get("last_recommendation")
        if rec_data is not None and isinstance(rec_data, dict):
            try:
                self.last_recommendation = SetupRecommendation(
                    parameter=str(rec_data["parameter"]),
                    direction=int(rec_data["direction"]),
                    step=float(rec_data["step"]),
                    reason=str(rec_data["reason"]),
                    current_value=float(rec_data["current_value"]),
                    target_value=float(rec_data["target_value"]),
                    min_value=float(rec_data["min_value"]),
                    max_value=float(rec_data["max_value"]),
                )
            except Exception:
                self.last_recommendation = None
        else:
            self.last_recommendation = None

    def update_from_setup(self, setup_info: SetupInfo) -> None:
        self.current_setup_id = setup_info.setup_id
        self.current_setup_label = setup_info.setup_label
        self.current_car_model = setup_info.car_model
        self.current_track_name = setup_info.track_name
        self.current_values = self._parse_setup_values(setup_info.setup_text)

    def register_lap_result(self, setup_info: SetupInfo, lap: LapRecord) -> None:
        if lap.lap_time_seconds < 30.0 or lap.lap_time_seconds > 600.0:
            return

        setup_id = setup_info.setup_id or "unknown"
        entry = self._setup_lap_history.setdefault(
            setup_id,
            {
                "setup_id": setup_id,
                "label": setup_info.setup_label,
                "role": self._classify_setup_role(setup_info.setup_label),
                "laps": [],
            },
        )
        entry["label"] = setup_info.setup_label
        entry["role"] = self._classify_setup_role(setup_info.setup_label)
        entry_laps = entry.setdefault("laps", [])
        if isinstance(entry_laps, list):
            entry_laps.append(float(lap.lap_time_seconds))

    def start(self, session_type: str) -> str:
        if session_type not in {"practice", "qualifying"}:
            self.active = False
            return "El setup coach detallado está optimizado para práctica y qualy, no para carrera."

        self.active = True
        self.last_recommendation = None

        intro = "Setup coach activado."
        if self.current_setup_label:
            intro += f" Setup base detectado: {self.current_setup_label}."

        if session_type == "qualifying":
            intro += " En qualy solo haremos microajustes, no cambios grandes."

        questions = (
            "Dime sensaciones concretas: "
            "subviraje entrada, subviraje salida, sobreviraje entrada, sobreviraje salida, "
            "frenada inestable, poca tracción, poca punta, o rebota en pianos."
        )
        return f"{intro} {questions}"

    def stop(self) -> str:
        self.active = False
        self.last_recommendation = None
        return "Setup coach desactivado."

    def process_feedback(self, text: str, session_state: "SessionState | None" = None) -> str:
        t = (text or "").lower()

        # Permitir validar outcomes de un ajuste pendiente aunque el coach
        # no se haya iniciado manualmente en esta sesión.
        has_pending_recommendation = self.last_recommendation is not None
        if not self.active and not has_pending_recommendation:
            return "Setup coach inactivo. Di iniciar setup coach."

        if self.last_recommendation is not None:
            outcome = self._detect_outcome(t)
            if outcome is not None:
                return self._handle_outcome(outcome, session_state=session_state)

        issue = self._detect_issue(t)
        if issue is None:
            return (
                "No detecté el síntoma. Prueba con: subviraje entrada, sobreviraje salida, "
                "frenada inestable, poca tracción, poca punta, rebota en pianos."
            )

        rec = self._recommend_for_issue(issue)
        if rec is None:
            return (
                "No pude generar ajuste específico con el setup actual. "
                "Confirma que el setup esté guardado y vuelve a intentarlo."
            )

        self.last_recommendation = rec
        self.last_issue = issue
        self.iterations.append(
            SetupIteration(
                timestamp_iso=datetime.now().isoformat(),
                setup_id=self.current_setup_id,
                setup_label=self.current_setup_label,
                issue=issue,
                parameter=rec.parameter,
                direction=rec.direction,
                step=rec.step,
                current_value=rec.current_value,
                target_value=rec.target_value,
                min_value=rec.min_value,
                max_value=rec.max_value,
                reason=rec.reason,
            )
        )
        action = "sube" if rec.direction > 0 else "baja"
        return (
            f"Recomendación específica: {action} {rec.parameter} en {rec.step:.0f} clic. "
            f"Actual {rec.current_value:.0f}, objetivo {rec.target_value:.0f}, "
            f"rango {rec.min_value:.0f} a {rec.max_value:.0f}. "
            f"Motivo: {rec.reason}. Da 2 vueltas y dime mejoró, igual o peor."
        )

    def build_setup_feedback(self, session_state: "SessionState | None" = None) -> str:
        session_type = (
            session_state.last_snapshot.session_type
            if session_state is not None and session_state.last_snapshot is not None
            else "unknown"
        )
        role = self._classify_setup_role(self.current_setup_label)
        confirmed = sum(1 for item in self.iterations if item.outcome == "better")
        rejected = sum(1 for item in self.iterations if item.outcome == "worse")
        pending = sum(1 for item in self.iterations if item.outcome == "pending")

        parts: list[str] = []
        if confirmed > rejected:
            parts.append("Setup en buena dirección según tus sensaciones")
        elif rejected > confirmed:
            parts.append("la línea actual de setup no está convergiendo")
        elif pending > 0:
            parts.append("aún hay cambios pendientes por validar")
        else:
            parts.append("todavía faltan referencias para validar el setup")

        if session_state is not None and len(session_state.laps) >= 3:
            recent_times = [lap.lap_time_seconds for lap in session_state.laps[-3:]]
            spread = max(recent_times) - min(recent_times)
            best_recent = min(recent_times)
            best_session = min(lap.lap_time_seconds for lap in session_state.laps)
            if spread <= 0.35 and best_recent <= best_session + 0.2:
                parts.append("las últimas vueltas son lo bastante limpias para medir cambios")
            elif spread >= 0.9:
                parts.append("necesitas dos o tres vueltas limpias antes de decidir")

        role_note = self._build_role_note(role=role, session_type=session_type)
        if role_note:
            parts.append(role_note)

        return "Setup: " + ". ".join(parts) + "."

    def build_objective_guidance(self, session_state: "SessionState | None" = None) -> str:
        session_type = (
            session_state.last_snapshot.session_type
            if session_state is not None and session_state.last_snapshot is not None
            else "unknown"
        )
        role = self._classify_setup_role(self.current_setup_label)

        current_eval = self._evaluate_current_setup()
        best_eval = self._pick_best_setup()
        limit_note = self._build_limit_note()

        if session_type == "practice":
            parts: list[str] = []
            if current_eval is None:
                lap_count = len(session_state.laps) if session_state is not None else 0
                if lap_count == 0:
                    parts.append(
                        "Objetivo: sal con esta base y completa dos vueltas limpias para validar el pico de qualy."
                    )
                elif lap_count < 2:
                    parts.append(
                        "Objetivo: completa dos vueltas limpias para validar el pico de qualy."
                    )
                else:
                    parts.append(
                        "Objetivo: cierra la validación de qualy con una vuelta limpia y sin errores."
                    )
            else:
                parts.append(self._build_practice_guidance(current_eval, role))
                auto_plan = self.build_automatic_recommendation(session_state)
                if auto_plan:
                    parts.append(auto_plan)
            if best_eval is not None:
                parts.append(self._build_best_setup_note(best_eval))
            if limit_note:
                parts.append(limit_note)
            return " ".join(parts)

        if session_type == "qualifying":
            parts = []
            if role == "race":
                parts.append(
                    "Objetivo setup: la base actual parece de carrera; para qualy evita cambios grandes y usa una base general o qualy si buscas más pico."
                )
            elif current_eval is not None and current_eval["qualy_validated"]:
                parts.append(
                    "Objetivo setup: fase qualy validada; en esta sesión solo toca microajuste y ejecutar vuelta limpia."
                )
            else:
                parts.append(
                    "Objetivo setup: en qualy solo valen microajustes; prioriza confianza en frenada y tracción antes que perseguir un cambio grande por una sola vuelta."
                )
            if limit_note:
                parts.append(limit_note)
            return " ".join(parts)

        return ""

    def _evaluate_current_setup(self) -> dict[str, object] | None:
        if not self.current_setup_id:
            return None
        entry = self._setup_lap_history.get(self.current_setup_id)
        if not entry:
            return None
        return self._evaluate_setup_entry(entry)

    def _evaluate_setup_entry(self, entry: dict[str, object]) -> dict[str, object] | None:
        laps_raw = entry.get("laps")
        if not isinstance(laps_raw, list):
            return None
        laps = [float(item) for item in laps_raw if isinstance(item, (int, float))]
        if len(laps) < 2:
            return None

        best_two = sorted(laps)[:2]
        qualy_avg = sum(best_two) / len(best_two)
        qualy_spread = max(best_two) - min(best_two)
        qualy_validated = len(best_two) >= 2 and qualy_spread <= 0.35

        race_window = laps[-min(len(laps), 4):]
        race_avg = sum(race_window) / len(race_window)
        race_spread = max(race_window) - min(race_window)
        race_drop = race_window[-1] - min(race_window)
        race_validated = len(race_window) >= 4 and race_spread <= 0.8 and race_drop <= 0.6

        related_iterations = [
            item
            for item in self.iterations
            if item.setup_id == entry.get("setup_id")
        ]
        better_count = sum(1 for item in related_iterations if item.outcome == "better")
        worse_count = sum(1 for item in related_iterations if item.outcome == "worse")

        combined_score = qualy_avg + max(0.0, race_spread * 0.35) + max(0.0, race_drop * 0.25)
        combined_score += max(0, worse_count - better_count) * 0.15

        return {
            "setup_id": entry.get("setup_id"),
            "label": entry.get("label") or entry.get("setup_id") or "unknown",
            "role": entry.get("role") or "unknown",
            "lap_count": len(laps),
            "qualy_avg": qualy_avg,
            "qualy_spread": qualy_spread,
            "qualy_validated": qualy_validated,
            "race_avg": race_avg,
            "race_spread": race_spread,
            "race_drop": race_drop,
            "race_validated": race_validated,
            "better_count": better_count,
            "worse_count": worse_count,
            "combined_score": combined_score,
        }

    def _pick_best_setup(self) -> dict[str, object] | None:
        evaluations = []
        for entry in self._setup_lap_history.values():
            if isinstance(entry, dict):
                evaluated = self._evaluate_setup_entry(entry)
                if evaluated is not None:
                    evaluations.append(evaluated)

        if not evaluations:
            return None

        confirmed = [item for item in evaluations if item["qualy_validated"] and item["race_validated"]]
        pool = confirmed if confirmed else [item for item in evaluations if item["qualy_validated"]]
        if not pool:
            pool = evaluations
        return min(pool, key=lambda item: float(item["combined_score"]))

    def _build_practice_guidance(self, current_eval: dict[str, object], role: str) -> str:
        label = str(current_eval["label"])
        qualy_avg = float(current_eval["qualy_avg"])
        qualy_spread = float(current_eval["qualy_spread"])
        race_avg = float(current_eval["race_avg"])
        race_spread = float(current_eval["race_spread"])
        race_drop = float(current_eval["race_drop"])

        parts: list[str] = [f"Objetivo setup para {label}."]
        if current_eval["qualy_validated"]:
            parts.append(
                f"Fase qualy validada con promedio de mejores dos vueltas en {qualy_avg:.3f} y dispersión de {qualy_spread:.3f}."
            )
        else:
            parts.append(
                f"Fase qualy aún no validada; necesitas dos vueltas limpias con dispersión menor a 0.35. Ahora estás en {qualy_spread:.3f}."
            )

        if current_eval["race_validated"]:
            parts.append(
                f"Ritmo de carrera validado con promedio reciente de {race_avg:.3f} y caída de stint de {race_drop:.3f}."
            )
        else:
            parts.append(
                f"Ritmo de carrera pendiente; busca al menos cuatro vueltas consistentes. Ventana actual {race_spread:.3f}, caída {race_drop:.3f}."
            )

        if role == "race":
            parts.append("La base actual parece de carrera; buena para la segunda fase, pero no para declarar pico definitivo de qualy.")
        elif role == "qualy":
            parts.append("La base actual parece de qualy; si pasa fase qualy, el siguiente filtro debe ser estabilidad en stint.")
        else:
            parts.append("Usa esta base como referencia neutral y solo declara ganador cuando pase pico y stint.")

        return " ".join(parts)

    def _build_best_setup_note(self, best_eval: dict[str, object]) -> str:
        label = str(best_eval["label"])
        if best_eval["qualy_validated"] and best_eval["race_validated"]:
            return f"Mejor setup confirmado hasta ahora: {label}, ya pasó fase qualy y ritmo de carrera."
        if best_eval["qualy_validated"]:
            return f"Mejor setup provisional: {label}, ya pasó fase qualy pero todavía falta validarlo en stint de carrera."
        return f"Referencia actual de setup: {label}, pero todavía no hay validación suficiente para declararlo ganador."

    def _build_limit_note(self) -> str:
        current_iterations = [
            item for item in self.iterations if item.setup_id == self.current_setup_id
        ]
        if not current_iterations:
            return ""

        capped: list[str] = []
        for item in current_iterations[-4:]:
            if abs(item.target_value - item.max_value) <= 0.01:
                capped.append(f"{item.parameter} en máximo")
            elif abs(item.target_value - item.min_value) <= 0.01:
                capped.append(f"{item.parameter} en mínimo")

        if not capped:
            return ""

        unique = []
        for label in capped:
            if label not in unique:
                unique.append(label)
        joined = ", ".join(unique[:2])
        return f"Atención a umbrales: ya rozas {joined}; no conviene seguir en esa dirección sin cambiar de parámetro o de base."

    def _handle_outcome(self, outcome: str, session_state: "SessionState | None" = None) -> str:
        rec = self.last_recommendation
        if rec is None:
            return "No tengo ajuste pendiente para evaluar."

        self._register_parameter_outcome(rec, outcome, session_state=session_state)

        if self.iterations and self.iterations[-1].outcome == "pending":
            self.iterations[-1].outcome = outcome

        if outcome == "better":
            confirmed_parameter = rec.parameter
            confirmed_value = rec.target_value
            self.last_recommendation = None
            next_step = ""
            if session_state is not None:
                next_step = self.build_automatic_recommendation(
                    session_state,
                    excluded_parameters={confirmed_parameter},
                )
            if next_step:
                return (
                    f"Perfecto, se confirma mejora. Mantén {confirmed_parameter} en {confirmed_value:.0f}. "
                    f"{next_step}"
                )
            return (
                f"Perfecto, se confirma mejora. Mantén {confirmed_parameter} en {confirmed_value:.0f}. "
                "De momento no conviene forzar otra línea sobre ese mismo parámetro."
            )

        if outcome == "same":
            action = "sube" if rec.direction > 0 else "baja"
            return (
                f"Sin cambio claro. Segunda iteración: {action} {rec.parameter} 1 clic adicional, "
                f"sin salir de rango {rec.min_value:.0f} a {rec.max_value:.0f}."
            )

        # worse
        reverse_target = rec.current_value - rec.direction * rec.step
        reverse_target = min(max(reverse_target, rec.min_value), rec.max_value)
        self.last_recommendation = None
        return (
            f"Empeoró. Revierte hacia {reverse_target:.0f} en {rec.parameter} y descartamos esa línea. "
            "Pásame otra sensación para proponer alternativa."
        )

    def build_automatic_recommendation(
        self,
        session_state: "SessionState | None" = None,
        excluded_parameters: set[str] | None = None,
    ) -> str:
        """Propone automáticamente el siguiente ajuste en práctica.

        Usa la fase de validación actual (qualy/race), respeta límites de parámetro
        y evita insistir en parámetros con historial negativo en la sesión.
        """
        if session_state is None or session_state.last_snapshot is None:
            return ""
        if session_state.last_snapshot.session_type != "practice":
            return ""

        # No proponer una nueva línea si hay un cambio pendiente de evaluación.
        if self.iterations and self.iterations[-1].outcome == "pending":
            last = self.iterations[-1]
            return (
                f"Ajuste pendiente: valida primero {last.parameter} en pista y confirma si mejoró, igual o peor."
            )

        current_eval = self._evaluate_current_setup()
        if current_eval is None:
            return ""

        role = str(current_eval.get("role") or "unknown")
        issue_candidates = self._build_auto_issue_candidates(current_eval, role)

        chosen: SetupRecommendation | None = None
        chosen_issue = ""
        excluded = {item.strip().upper() for item in (excluded_parameters or set()) if item}
        for issue in issue_candidates:
            rec = self._recommend_for_issue(issue)
            if rec is None:
                continue

            if rec.parameter.strip().upper() in excluded:
                continue

            score = self._parameter_score(rec.parameter, session_state=session_state)
            # Si una línea fue claramente mala en esta sesión, no insistimos.
            if score <= -2:
                continue

            if self._crosses_learned_limit(rec, session_state=session_state):
                continue

            chosen = rec
            chosen_issue = issue
            break

        if chosen is None:
            return ""

        self.last_recommendation = chosen
        self.last_issue = chosen_issue
        # Modo auto: habilita aceptación de "mejoró/igual/empeoró" sin pedir start manual.
        self.active = True
        self.iterations.append(
            SetupIteration(
                timestamp_iso=datetime.now().isoformat(),
                setup_id=self.current_setup_id,
                setup_label=self.current_setup_label,
                issue=f"auto_{chosen_issue}",
                parameter=chosen.parameter,
                direction=chosen.direction,
                step=chosen.step,
                current_value=chosen.current_value,
                target_value=chosen.target_value,
                min_value=chosen.min_value,
                max_value=chosen.max_value,
                reason=chosen.reason,
            )
        )

        action = "sube" if chosen.direction > 0 else "baja"
        return (
            f"Recomendación automática: {action} {chosen.parameter} en {chosen.step:.0f} clic. "
            f"Actual {chosen.current_value:.0f}, objetivo {chosen.target_value:.0f}, "
            f"rango {chosen.min_value:.0f} a {chosen.max_value:.0f}. "
            f"Motivo: {chosen.reason}. Haz dos vueltas y confirma mejoró, igual o peor."
        )

    @staticmethod
    def _build_auto_issue_candidates(current_eval: dict[str, object], role: str) -> list[str]:
        qualy_ok = bool(current_eval.get("qualy_validated"))
        race_ok = bool(current_eval.get("race_validated"))
        race_spread = float(current_eval.get("race_spread") or 0.0)
        race_drop = float(current_eval.get("race_drop") or 0.0)
        qualy_spread = float(current_eval.get("qualy_spread") or 0.0)

        if not qualy_ok:
            if qualy_spread > 0.6:
                return ["braking_instability", "oversteer_entry", "understeer_entry"]
            return ["understeer_entry", "oversteer_entry", "braking_instability"]

        if not race_ok:
            if race_drop > 0.8:
                return ["poor_traction", "oversteer_exit", "understeer_exit"]
            if race_spread > 1.0:
                return ["oversteer_mid", "understeer_mid", "braking_instability"]
            return ["understeer_mid", "oversteer_mid", "poor_traction"]

        if role == "qualy":
            return ["low_top_speed", "understeer_mid", "oversteer_mid"]
        return ["understeer_mid", "low_top_speed", "poor_traction"]

    def _register_parameter_outcome(
        self,
        recommendation: SetupRecommendation,
        outcome: str,
        session_state: "SessionState | None" = None,
    ) -> None:
        key = (recommendation.parameter or "").strip().upper()
        if not key:
            return
        bucket = self._parameter_outcomes.setdefault(
            key,
            {"better": 0, "same": 0, "worse": 0},
        )
        if outcome in bucket:
            bucket[outcome] += 1

        limits = self._parameter_limits.setdefault(key, {})
        target = float(recommendation.target_value)
        if outcome == "better":
            if recommendation.direction > 0:
                limits["best_high"] = max(float(limits.get("best_high", target)), target)
            else:
                limits["best_low"] = min(float(limits.get("best_low", target)), target)
        elif outcome == "worse":
            if recommendation.direction > 0:
                limits["worse_high"] = min(float(limits.get("worse_high", target)), target)
            else:
                limits["worse_low"] = max(float(limits.get("worse_low", target)), target)

        self._register_global_outcome(recommendation, outcome, session_state=session_state)

    def _parameter_score(self, parameter: str, session_state: "SessionState | None" = None) -> int:
        key = (parameter or "").strip().upper()
        bucket = self._parameter_outcomes.get(key)
        session_score = 0
        if bucket:
            session_score = int(bucket.get("better", 0)) - int(bucket.get("worse", 0))
        global_score = self._global_parameter_score(key, session_state=session_state)
        return session_score + global_score

    def _crosses_learned_limit(
        self,
        recommendation: SetupRecommendation,
        session_state: "SessionState | None" = None,
    ) -> bool:
        key = (recommendation.parameter or "").strip().upper()
        limits = self._parameter_limits.get(key)
        target = float(recommendation.target_value)

        if limits:
            if recommendation.direction > 0:
                worse_high = limits.get("worse_high")
                if isinstance(worse_high, (int, float)) and target >= float(worse_high) - 0.01:
                    return True
            else:
                worse_low = limits.get("worse_low")
                if isinstance(worse_low, (int, float)) and target <= float(worse_low) + 0.01:
                    return True

        if self._crosses_global_learned_limit(recommendation, session_state=session_state):
            return True

        return False

    def _load_learning_store(self) -> dict[str, object]:
        default_payload: dict[str, object] = {"version": 1, "contexts": {}}
        try:
            if not self._learning_store_path.exists():
                return default_payload
            with self._learning_store_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return default_payload
            if not isinstance(data.get("contexts"), dict):
                data["contexts"] = {}
            if "version" not in data:
                data["version"] = 1
            return data
        except Exception:
            return default_payload

    def _save_learning_store(self) -> None:
        try:
            self._learning_store_path.parent.mkdir(parents=True, exist_ok=True)
            with self._learning_store_path.open("w", encoding="utf-8") as f:
                json.dump(self._global_learning, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _build_learning_context_key(self, session_state: "SessionState | None" = None) -> str:
        car = (self.current_car_model or "unknown").strip().lower()
        track = (self.current_track_name or "unknown").strip().lower()

        phase = "unknown"
        fuel_bucket = "unknown"
        if session_state is not None and session_state.last_snapshot is not None:
            snap = session_state.last_snapshot
            phase = (snap.session_type or "unknown").strip().lower()
            fuel = float(snap.fuel or 0.0)
            if fuel < 10.0:
                fuel_bucket = "low"
            elif fuel < 25.0:
                fuel_bucket = "mid"
            else:
                fuel_bucket = "high"

        return f"{car}|{track}|{phase}|{fuel_bucket}"

    def _build_learning_prefix(self) -> str:
        car = (self.current_car_model or "unknown").strip().lower()
        track = (self.current_track_name or "unknown").strip().lower()
        return f"{car}|{track}|"

    def _register_global_outcome(
        self,
        recommendation: SetupRecommendation,
        outcome: str,
        session_state: "SessionState | None" = None,
    ) -> None:
        key = (recommendation.parameter or "").strip().upper()
        if not key:
            return

        contexts = self._global_learning.setdefault("contexts", {})
        if not isinstance(contexts, dict):
            return

        ctx_key = self._build_learning_context_key(session_state=session_state)
        ctx_payload = contexts.setdefault(ctx_key, {})
        if not isinstance(ctx_payload, dict):
            return

        bucket = ctx_payload.setdefault(
            key,
            {
                "better": 0,
                "same": 0,
                "worse": 0,
                "best_high": None,
                "best_low": None,
                "worse_high": None,
                "worse_low": None,
            },
        )
        if not isinstance(bucket, dict):
            return

        if outcome in {"better", "same", "worse"}:
            bucket[outcome] = int(bucket.get(outcome, 0)) + 1

        target = float(recommendation.target_value)
        if outcome == "better":
            if recommendation.direction > 0:
                current = bucket.get("best_high")
                bucket["best_high"] = target if current is None else max(float(current), target)
            else:
                current = bucket.get("best_low")
                bucket["best_low"] = target if current is None else min(float(current), target)
        elif outcome == "worse":
            if recommendation.direction > 0:
                current = bucket.get("worse_high")
                bucket["worse_high"] = target if current is None else min(float(current), target)
            else:
                current = bucket.get("worse_low")
                bucket["worse_low"] = target if current is None else max(float(current), target)

        self._save_learning_store()

    def _global_parameter_score(
        self,
        parameter_key: str,
        session_state: "SessionState | None" = None,
    ) -> int:
        contexts = self._global_learning.get("contexts")
        if not isinstance(contexts, dict):
            return 0

        exact_key = self._build_learning_context_key(session_state=session_state)
        prefix = self._build_learning_prefix()

        score = 0
        for ctx_key, ctx_payload in contexts.items():
            if not isinstance(ctx_payload, dict):
                continue
            if not str(ctx_key).startswith(prefix):
                continue
            bucket = ctx_payload.get(parameter_key)
            if not isinstance(bucket, dict):
                continue

            local_score = int(bucket.get("better", 0)) - int(bucket.get("worse", 0))
            weight = 3 if str(ctx_key) == exact_key else 1
            score += weight * local_score

        return score

    def _crosses_global_learned_limit(
        self,
        recommendation: SetupRecommendation,
        session_state: "SessionState | None" = None,
    ) -> bool:
        contexts = self._global_learning.get("contexts")
        if not isinstance(contexts, dict):
            return False

        key = (recommendation.parameter or "").strip().upper()
        if not key:
            return False

        prefix = self._build_learning_prefix()
        target = float(recommendation.target_value)

        worse_high_values: list[float] = []
        worse_low_values: list[float] = []
        for ctx_key, ctx_payload in contexts.items():
            if not isinstance(ctx_payload, dict):
                continue
            if not str(ctx_key).startswith(prefix):
                continue
            bucket = ctx_payload.get(key)
            if not isinstance(bucket, dict):
                continue

            wh = bucket.get("worse_high")
            wl = bucket.get("worse_low")
            if isinstance(wh, (int, float)):
                worse_high_values.append(float(wh))
            if isinstance(wl, (int, float)):
                worse_low_values.append(float(wl))

        if recommendation.direction > 0 and worse_high_values:
            if target >= (min(worse_high_values) - 0.01):
                return True

        if recommendation.direction < 0 and worse_low_values:
            if target <= (max(worse_low_values) + 0.01):
                return True

        return False

    def _detect_issue(self, t: str) -> str | None:
        if any(k in t for k in ("subvir", "understeer")):
            if any(k in t for k in ("entrada", "fren", "turn-in", "turn in")):
                return "understeer_entry"
            if any(k in t for k in ("salida", "aceler", "exit")):
                return "understeer_exit"
            return "understeer_mid"

        if any(k in t for k in ("sobrevir", "oversteer")):
            if any(k in t for k in ("entrada", "fren", "turn-in", "turn in")):
                return "oversteer_entry"
            if any(k in t for k in ("salida", "aceler", "exit")):
                return "oversteer_exit"
            return "oversteer_mid"

        if any(k in t for k in ("frenada", "inestable", "bloquea", "bloqueo")):
            return "braking_instability"

        if any(k in t for k in ("traccion", "tracción")):
            return "poor_traction"

        if any(k in t for k in ("punta", "velocidad punta", "top speed", "recta")):
            return "low_top_speed"

        if any(k in t for k in ("piano", "rebota", "bache")):
            return "kerb_bounce"

        return None

    @staticmethod
    def _detect_outcome(text: str) -> str | None:
        t = (text or "").lower()

        worse_patterns = (
            "empeor",
            "peor",
            "salio peor",
            "salió peor",
            "mas inestable",
            "más inestable",
            "perdio agarre",
            "perdió agarre",
            "no funciono",
            "no funcionó",
            "no sirvio",
            "no sirvió",
        )
        if any(p in t for p in worse_patterns):
            return "worse"

        better_patterns = (
            "mejor",
            "mejoro",
            "mejoró",
            "mucho mejor",
            "va mejor",
            "se siente mejor",
            "gano tiempo",
            "ganó tiempo",
            "mas estable",
            "más estable",
            "funciono",
            "funcionó",
        )
        if any(p in t for p in better_patterns):
            return "better"

        same_patterns = (
            "igual",
            "sin cambio",
            "sin cambios",
            "sin diferencia",
            "se siente igual",
            "mas o menos igual",
            "más o menos igual",
            "parecido",
        )
        if any(p in t for p in same_patterns):
            return "same"

        return None

    def _recommend_for_issue(self, issue: str) -> SetupRecommendation | None:
        templates: dict[str, list[tuple[str, int, float, str]]] = {
            "understeer_entry": [
                ("WING_1", 1, 1.0, "más apoyo delantero en entrada"),
                ("ARB_FRONT", -1, 1.0, "eje delantero más libre en apoyo inicial"),
            ],
            "understeer_mid": [
                ("ARB_FRONT", -1, 1.0, "más rotación en mitad de curva"),
                ("CAMBER_LF", -1, 1.0, "más agarre lateral delantero"),
            ],
            "understeer_exit": [
                ("DIFF_POWER", -1, 2.0, "mejor rotación al abrir gas"),
                ("WING_2", -1, 1.0, "menos carga trasera para ayudar rotación"),
            ],
            "oversteer_entry": [
                ("FRONT_BIAS", 1, 1.0, "más estabilidad en frenada"),
                ("WING_1", -1, 1.0, "menos agresivo delante al girar"),
            ],
            "oversteer_mid": [
                ("ARB_REAR", -1, 1.0, "trasera más dócil en apoyo"),
                ("WING_2", 1, 1.0, "más estabilidad trasera en curva"),
            ],
            "oversteer_exit": [
                ("DIFF_POWER", 1, 2.0, "entrega de par más estable en salida"),
                ("WING_2", 1, 1.0, "más apoyo trasero al acelerar"),
            ],
            "braking_instability": [
                ("FRONT_BIAS", 1, 1.0, "evitar inestabilidad en fase de frenada"),
            ],
            "poor_traction": [
                ("DIFF_POWER", 1, 2.0, "mejor tracción en salida"),
            ],
            "low_top_speed": [
                ("WING_2", -1, 1.0, "menos drag para mejorar punta"),
                ("WING_1", -1, 1.0, "reducir drag delantero"),
            ],
            "kerb_bounce": [
                ("DAMP_FAST_BUMP_HF", -1, 2.0, "absorber mejor pianos"),
                ("PACKER_RANGE_LF", 1, 2.0, "más margen de suspensión en compresión"),
            ],
        }

        candidates = templates.get(issue, [])
        for parameter, direction, step, reason in candidates:
            current = self.current_values.get(parameter)
            if current is None:
                continue
            min_v, max_v = self._get_bounds(parameter, current)
            target = current + direction * step
            target = min(max(target, min_v), max_v)
            if abs(target - current) < 0.01:
                continue
            return SetupRecommendation(
                parameter=parameter,
                direction=direction,
                step=step,
                reason=reason,
                current_value=current,
                target_value=target,
                min_value=min_v,
                max_value=max_v,
            )

        return None

    def _get_bounds(self, parameter: str, fallback_current: float) -> tuple[float, float]:
        if not self.current_car_model:
            return self._fallback_bounds(parameter, fallback_current)

        bounds_for_car = self._bounds_cache.get(self.current_car_model)
        if bounds_for_car is None:
            bounds_for_car = self._scan_bounds_for_car(self.current_car_model)
            self._bounds_cache[self.current_car_model] = bounds_for_car

        detected = bounds_for_car.get(parameter)
        if detected is not None and detected[0] < detected[1]:
            return detected

        return self._fallback_bounds(parameter, fallback_current)

    def _scan_bounds_for_car(self, car_model: str) -> dict[str, tuple[float, float]]:
        setups_root = Path(os.path.expanduser("~/Documents/Assetto Corsa/setups")) / car_model
        if not setups_root.exists() or not setups_root.is_dir():
            return {}

        mins: dict[str, float] = {}
        maxs: dict[str, float] = {}

        for path in setups_root.rglob("*.ini"):
            values = self._parse_setup_values(path.read_text(encoding="utf-8", errors="replace"))
            for key, value in values.items():
                mins[key] = value if key not in mins else min(mins[key], value)
                maxs[key] = value if key not in maxs else max(maxs[key], value)

        return {key: (mins[key], maxs[key]) for key in mins.keys()}

    @staticmethod
    def _parse_setup_values(setup_text: str | None) -> dict[str, float]:
        if not setup_text:
            return {}

        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        try:
            parser.read_string(setup_text)
        except Exception:
            return {}

        values: dict[str, float] = {}
        for section in parser.sections():
            if not parser.has_option(section, "VALUE"):
                continue
            raw = parser.get(section, "VALUE").strip()
            try:
                values[section] = float(raw)
            except ValueError:
                continue
        return values

    @staticmethod
    def _fallback_bounds(parameter: str, current: float) -> tuple[float, float]:
        hardcoded: dict[str, tuple[float, float]] = {
            "WING_1": (0.0, 20.0),
            "WING_2": (0.0, 20.0),
            "DIFF_POWER": (0.0, 100.0),
            "DIFF_COAST": (0.0, 100.0),
            "FRONT_BIAS": (45.0, 65.0),
            "ARB_FRONT": (0.0, 50.0),
            "ARB_REAR": (0.0, 50.0),
            "DAMP_FAST_BUMP_HF": (0.0, 60.0),
            "PACKER_RANGE_LF": (0.0, 80.0),
            "CAMBER_LF": (-60.0, 0.0),
        }
        if parameter in hardcoded:
            return hardcoded[parameter]

        # fallback genérico si no conocemos el parámetro
        span = max(5.0, abs(current) * 0.4)
        return current - span, current + span

    @staticmethod
    def _classify_setup_role(setup_label: str | None) -> str:
        label = (setup_label or "").lower()
        if not label:
            return "unknown"
        if any(token in label for token in ("race", "carrera", "stint", "enduro", "long")):
            return "race"
        if any(token in label for token in ("qualy", "quali", "push", "hotlap")):
            return "qualy"
        if any(token in label for token in ("base", "general", "baseline", "default")):
            return "general"
        return "unknown"

    @staticmethod
    def _build_role_note(role: str, session_type: str) -> str:
        if session_type == "practice" and role == "race":
            return "la base actual parece de carrera; mejor afina sobre general o qualy y luego traslada cambios útiles"
        if session_type == "practice" and role == "qualy":
            return "la base actual parece de qualy; buena para buscar pico, no tanto para validar stint largo"
        if session_type == "qualifying" and role == "race":
            return "la base actual parece de carrera y puede limitar pico en qualy"
        if role == "general":
            return "la base actual parece general, buena para iterar sin sesgar demasiado el coche"
        return ""
