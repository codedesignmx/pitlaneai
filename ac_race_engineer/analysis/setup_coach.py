from __future__ import annotations

import configparser
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

    def reset_session_notes(self) -> None:
        self.last_recommendation = None
        self.last_issue = None
        self.iterations = []
        self._setup_lap_history = {}

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

    def process_feedback(self, text: str) -> str:
        if not self.active:
            return "Setup coach inactivo. Di iniciar setup coach."

        t = (text or "").lower()

        if self.last_recommendation is not None and any(k in t for k in ("mejor", "mejoró", "mejoro")):
            return self._handle_outcome("better")
        if self.last_recommendation is not None and "igual" in t:
            return self._handle_outcome("same")
        if self.last_recommendation is not None and any(k in t for k in ("peor", "empeor", "empeoró", "empeoro")):
            return self._handle_outcome("worse")

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
                parts.append(
                    "Objetivo setup: aún no hay suficientes vueltas para evaluar. Primero valida el pico de qualy con dos vueltas limpias."
                )
            else:
                parts.append(self._build_practice_guidance(current_eval, role))
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

    def _handle_outcome(self, outcome: str) -> str:
        rec = self.last_recommendation
        if rec is None:
            return "No tengo ajuste pendiente para evaluar."

        if self.iterations and self.iterations[-1].outcome == "pending":
            self.iterations[-1].outcome = outcome

        if outcome == "better":
            self.last_recommendation = None
            return (
                "Perfecto, se confirma mejora. Mantén ese ajuste y dime el siguiente síntoma "
                "si quieres afinar más."
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
