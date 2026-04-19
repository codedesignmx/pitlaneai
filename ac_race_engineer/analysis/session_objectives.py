"""
session_objectives.py
---------------------
Genera y evalúa objetivos reales de sesión basados en historial, setup y tiempo disponible.

Ciclo de vida:
  1. Inicio de sesión  → build_objectives()
  2. Primer tick válido → obj_set.update_session_time()  (ajusta stint si hay tiempo suficiente)
  3. "box box" / "qué objetivos tengo" → obj_set.evaluate(state, setup_coach) + voice_summary()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ac_race_engineer.analysis.time_format import speak_delta_spanish, speak_lap_time_spanish

if TYPE_CHECKING:
    from ac_race_engineer.analysis.session_state import SessionState
    from ac_race_engineer.analysis.setup_coach import SetupCoach
    from ac_race_engineer.storage.performance_history import HistoricalPaceSummary
    from ac_race_engineer.storage.setup_registry import SetupInfo


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SessionObjective:
    id: str
    category: str        # "fuel" | "pace" | "consistency" | "stint" | "setup"
    target_label: str    # frase corta para TTS
    target_value: float | None
    priority: int        # 1 = alta, 2 = media
    met: bool | None = None
    feedback: str = ""


@dataclass
class SessionObjectiveSet:
    session_type: str
    session_total_minutes: float = 0.0
    objectives: list[SessionObjective] = field(default_factory=list)
    context_note: str = ""

    # ------------------------------------------------------------------
    # Actualización de tiempo de sesión (se llama en primer tick válido)
    # ------------------------------------------------------------------

    def update_session_time(self, total_minutes: float) -> None:
        """Registra el tiempo total de sesión al primer tick válido.

        Si la sesión es suficientemente larga, agrega el objetivo de stint si aún no existe.
        """
        if self.session_total_minutes == 0.0 and total_minutes > 0.0:
            self.session_total_minutes = total_minutes
            if self.session_type == "practice" and total_minutes >= 30.0:
                if not any(o.id == "stint_validation" for o in self.objectives):
                    self.objectives.append(
                        SessionObjective(
                            id="stint_validation",
                            category="stint",
                            target_label="Cuatro vueltas consecutivas dentro de 0.8 segundos",
                            target_value=4.0,
                            priority=2,
                        )
                    )

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------

    def evaluate(
        self,
        state: "SessionState",
        setup_coach: "SetupCoach | None" = None,
    ) -> None:
        session_laps = [
            lap.lap_time_seconds
            for lap in state.laps[state.session_lap_start_index :]
            if 30.0 <= lap.lap_time_seconds <= 600.0
        ]
        for obj in self.objectives:
            _evaluate_objective(obj, session_laps, state, setup_coach)

    # ------------------------------------------------------------------
    # Voz
    # ------------------------------------------------------------------

    def voice_intro(self) -> str:
        """Resumen breve al inicio de sesión. Solo prioridad alta."""
        if not self.objectives:
            return ""
        high = [o for o in self.objectives if o.priority == 1]
        labels = [o.target_label for o in high[:2]]
        header = f"Objetivos de {self.session_type}:"
        body = "; ".join(labels) if labels else "sin objetivos definidos"
        ctx = f" {self.context_note}" if self.context_note else ""
        return f"{header} {body}.{ctx}"

    def voice_summary(self) -> str:
        """Evaluación completa — usada en 'box box' y comando 'mis objetivos'."""
        if not self.objectives:
            return "Sin objetivos definidos para esta sesión."

        met = [o for o in self.objectives if o.met is True]
        unmet = [o for o in self.objectives if o.met is False]
        unknown = [o for o in self.objectives if o.met is None]

        parts: list[str] = []

        if met:
            texts = [o.feedback for o in met if o.feedback][:2]
            parts.append(f"Logrado: {'; '.join(texts)}.")

        if unmet:
            sorted_unmet = sorted(unmet, key=lambda o: o.priority)
            texts = [o.feedback for o in sorted_unmet if o.feedback][:2]
            parts.append(f"Pendiente: {'; '.join(texts)}.")

        if not met and not unmet and unknown:
            parts.append("Datos insuficientes para evaluar objetivos todavía.")

        return " ".join(parts) if parts else "Sin datos suficientes."


# ---------------------------------------------------------------------------
# Evaluadores internos
# ---------------------------------------------------------------------------

def _evaluate_objective(
    obj: SessionObjective,
    session_laps: list[float],
    state: "SessionState",
    setup_coach: "SetupCoach | None",
) -> None:
    if obj.category == "fuel":
        _eval_fuel(obj, state)
    elif obj.category == "pace":
        _eval_pace(obj, session_laps)
    elif obj.category == "consistency":
        _eval_consistency(obj, session_laps)
    elif obj.category == "stint":
        _eval_stint(obj, session_laps)
    elif obj.category == "setup":
        _eval_setup(obj, setup_coach)


def _eval_fuel(obj: SessionObjective, state: "SessionState") -> None:
    session_laps = state.laps[state.session_lap_start_index :]
    valid_samples = [lap.fuel_used for lap in session_laps if lap.fuel_used is not None and lap.fuel_used > 0.0]
    required_samples = max(2, int(obj.target_value)) if obj.target_value is not None else 3

    if len(valid_samples) < required_samples:
        obj.met = False
        obj.feedback = (
            f"Necesitas {required_samples} vueltas con consumo válido; llevas {len(valid_samples)}"
        )
        return

    avg_fuel = sum(float(sample) for sample in valid_samples) / len(valid_samples)
    obj.met = True
    obj.feedback = (
        f"Consumo base fijado en {avg_fuel:.3f} litros por vuelta con {len(valid_samples)} muestras"
    )


def _eval_pace(obj: SessionObjective, laps: list[float]) -> None:
    if not laps:
        obj.met = False
        obj.feedback = "Sin vueltas válidas todavía"
        return

    best = min(laps)

    if obj.target_value is None:
        # Solo establecer referencia
        obj.met = len(laps) >= 2
        obj.feedback = (
            f"Referencia establecida en {speak_lap_time_spanish(best)}"
            if obj.met
            else "Completa al menos dos vueltas para establecer referencia"
        )
        return

    if best <= obj.target_value:
        gap = obj.target_value - best
        obj.met = True
        obj.feedback = (
            f"Mejor vuelta {speak_lap_time_spanish(best)}, "
            f"{speak_delta_spanish(gap)} bajo el objetivo"
        )
    else:
        gap = best - obj.target_value
        obj.met = False
        obj.feedback = (
            f"Mejor vuelta {speak_lap_time_spanish(best)}, "
            f"objetivo {speak_lap_time_spanish(obj.target_value)}, "
            f"faltan {speak_delta_spanish(gap)}"
        )


def _eval_consistency(obj: SessionObjective, laps: list[float]) -> None:
    required_spread = obj.target_value if obj.target_value is not None else 0.35

    if len(laps) < 2:
        obj.met = False
        obj.feedback = "Necesitas al menos dos vueltas válidas"
        return

    fastest = min(laps)
    # Evita validar pares con vueltas claramente no representativas
    # (salida de boxes, in/out laps, tráfico extremo, etc.).
    representative_limit = fastest + max(2.5, required_spread * 6.0)
    representative_pairs = [
        abs(laps[i] - laps[i + 1])
        for i in range(len(laps) - 1)
        if laps[i] <= representative_limit and laps[i + 1] <= representative_limit
    ]

    if not representative_pairs:
        obj.met = False
        obj.feedback = (
            "Aún no hay par consecutivo representativo; completa dos vueltas limpias "
            "en ventana de ritmo."
        )
        return

    best_spread = min(representative_pairs)

    if best_spread <= required_spread:
        obj.met = True
        obj.feedback = f"Par de vueltas consecutivas con diferencia de {best_spread:.3f}s"
    else:
        obj.met = False
        obj.feedback = (
            f"Mejor par consecutivo a {best_spread:.3f}s; objetivo {required_spread:.2f}s"
        )


def _eval_stint(obj: SessionObjective, laps: list[float]) -> None:
    required_window = max(2, int(obj.target_value)) if obj.target_value is not None else 4
    required_spread = 0.8 if required_window <= 4 else 1.0

    if len(laps) < required_window:
        obj.met = False
        obj.feedback = (
            f"Necesitas {required_window} vueltas limpias; llevas {len(laps)} válidas"
        )
        return

    best_spread = min(
        max(laps[i : i + required_window]) - min(laps[i : i + required_window])
        for i in range(len(laps) - required_window + 1)
    )

    if best_spread <= required_spread:
        obj.met = True
        obj.feedback = (
            f"Ventana de {required_window} vueltas con dispersión {best_spread:.3f}s"
        )
    else:
        obj.met = False
        obj.feedback = (
            f"Mejor ventana de {required_window} vueltas tiene dispersión {best_spread:.3f}s "
            f"(objetivo {required_spread:.2f}s)"
        )


def _eval_setup(obj: SessionObjective, setup_coach: "SetupCoach | None") -> None:
    if setup_coach is None:
        obj.met = None
        obj.feedback = "Coach no activo"
        return

    confirmed = sum(1 for item in setup_coach.iterations if item.outcome == "better")
    required = max(1, int(obj.target_value)) if obj.target_value is not None else 1

    if confirmed >= required:
        obj.met = True
        obj.feedback = f"{confirmed} ajuste(s) de setup confirmado(s) con mejora"
    elif setup_coach.iterations:
        total = len(setup_coach.iterations)
        obj.met = False
        obj.feedback = f"{confirmed} de {total} cambio(s) confirmado(s) con mejora"
    else:
        obj.met = False
        obj.feedback = "Aún no probaste cambios con el setup coach"


# ---------------------------------------------------------------------------
# Constructor principal
# ---------------------------------------------------------------------------

def build_objectives(
    session_type: str,
    setup_info: "SetupInfo | None" = None,
    history: "HistoricalPaceSummary | None" = None,
    session_total_minutes: float = 0.0,
    setup_coach: "SetupCoach | None" = None,
) -> SessionObjectiveSet:
    obj_set = SessionObjectiveSet(
        session_type=session_type,
        session_total_minutes=session_total_minutes,
    )

    if session_type == "practice":
        _build_practice(obj_set, history, session_total_minutes, setup_coach)
    elif session_type == "qualifying":
        _build_qualy(obj_set, history)
    elif session_type == "race":
        _build_race(obj_set, history)

    return obj_set


# ---------------------------------------------------------------------------
# Práctica
# ---------------------------------------------------------------------------

def _build_practice(
    obj_set: SessionObjectiveSet,
    history: "HistoricalPaceSummary | None",
    session_total_minutes: float,
    setup_coach: "SetupCoach | None",
) -> None:
    own_best = history.own_best_seconds if history else None
    historical_fuel = history.own_avg_fuel_per_lap if history else None
    historical_fuel_samples = history.own_fuel_sample_count if history else 0
    rival_best = history.rival_best_seconds if history else None
    rival_name = (history.rival_best_name or "rival") if history else "rival"
    has_fuel_history = historical_fuel is not None and historical_fuel_samples >= 3

    ctx_parts: list[str] = []

    if not has_fuel_history:
        obj_set.objectives.append(
            SessionObjective(
                id="fuel_calibration",
                category="fuel",
                target_label="Salir con tanque lleno y completar 3 vueltas limpias para medir consumo por vuelta",
                target_value=3.0,
                priority=1,
            )
        )
        ctx_parts.append("Sin histórico fiable de combustible; prioridad uno, calibrar consumo con tanque lleno")
    else:
        ctx_parts.append(
            f"Consumo histórico {historical_fuel:.3f} litros por vuelta con {historical_fuel_samples} muestras"
        )

    # --- 1. Objetivo de ritmo ---
    if own_best is not None:
        # Si tenemos tiempo suficiente, apuntamos a mejora real; si no, a igualar
        improvement = 0.3 if session_total_minutes == 0.0 or session_total_minutes >= 25.0 else 0.1
        pace_target = own_best - improvement
        obj_set.objectives.append(
            SessionObjective(
                id="pace_target",
                category="pace",
                target_label=f"Bajar de {speak_lap_time_spanish(pace_target)}",
                target_value=pace_target,
                priority=2 if not has_fuel_history else 1,
            )
        )
        ctx_parts.append(f"Referencia propia previa {speak_lap_time_spanish(own_best)}")
    elif rival_best is not None:
        pace_target = rival_best + 0.5
        obj_set.objectives.append(
            SessionObjective(
                id="pace_target",
                category="pace",
                target_label=f"Ritmo por debajo de {speak_lap_time_spanish(pace_target)}",
                target_value=pace_target,
                priority=2 if not has_fuel_history else 1,
            )
        )
        ctx_parts.append(f"Sin referencia propia; referencia rival {speak_lap_time_spanish(rival_best)}")
    else:
        obj_set.objectives.append(
            SessionObjective(
                id="pace_target",
                category="pace",
                target_label="Establecer primera referencia de ritmo con al menos 2 vueltas",
                target_value=None,
                priority=2 if not has_fuel_history else 1,
            )
        )
        ctx_parts.append("Primera sesión en este circuito; establece referencia base")

    # Rival como segundo objetivo si van por delante
    if rival_best is not None and own_best is not None and rival_best < own_best:
        obj_set.objectives.append(
            SessionObjective(
                id="rival_pace",
                category="pace",
                target_label=f"Igualar a {rival_name} en {speak_lap_time_spanish(rival_best)}",
                target_value=rival_best,
                priority=2,
            )
        )
        # El delta ya se comunica en el briefing principal para evitar duplicidad de mensaje.

    # --- 2. Validación qualy (consistencia) — siempre ---
    obj_set.objectives.append(
        SessionObjective(
            id="qualy_validation",
            category="consistency",
            target_label="Dos vueltas consecutivas dentro de 0.35 segundos",
            target_value=0.35,
            priority=2 if not has_fuel_history else 1,
        )
    )

    # --- 3. Stint — solo si hay tiempo suficiente o es desconocido ---
    if session_total_minutes == 0.0 or session_total_minutes >= 30.0:
        obj_set.objectives.append(
            SessionObjective(
                id="stint_validation",
                category="stint",
                target_label="Cuatro vueltas consecutivas dentro de 0.8 segundos",
                target_value=4.0,
                priority=2,
            )
        )

    # --- 4. Setup coach — si hay tiempo para probar cambios ---
    if has_fuel_history and (session_total_minutes == 0.0 or session_total_minutes >= 20.0):
        setup_role = "unknown"
        if setup_coach is not None and setup_coach.current_setup_label:
            setup_role = setup_coach._classify_setup_role(setup_coach.current_setup_label)

        # Con base de carrera no pedimos iteraciones de coach, pedimos stint directamente
        if setup_role != "race":
            required = 1 if (session_total_minutes == 0.0 or session_total_minutes < 30.0) else 2
            obj_set.objectives.append(
                SessionObjective(
                    id="setup_improvements",
                    category="setup",
                    target_label=f"Confirmar al menos {required} mejora(s) de setup con el coach",
                    target_value=float(required),
                    priority=2,
                )
            )

    obj_set.context_note = ". ".join(ctx_parts)


# ---------------------------------------------------------------------------
# Qualy
# ---------------------------------------------------------------------------

def _build_qualy(
    obj_set: SessionObjectiveSet,
    history: "HistoricalPaceSummary | None",
) -> None:
    own_best = history.own_best_seconds if history else None
    rival_best = history.rival_best_seconds if history else None
    rival_name = (history.rival_best_name or "rival") if history else "rival"

    if own_best is not None:
        obj_set.objectives.append(
            SessionObjective(
                id="qualy_beat_own",
                category="pace",
                target_label=f"Superar referencia propia {speak_lap_time_spanish(own_best)}",
                target_value=own_best,
                priority=1,
            )
        )
    else:
        obj_set.objectives.append(
            SessionObjective(
                id="qualy_establish",
                category="pace",
                target_label="Completar vuelta de clasificación válida",
                target_value=None,
                priority=1,
            )
        )

    if rival_best is not None:
        obj_set.objectives.append(
            SessionObjective(
                id="qualy_beat_rival",
                category="pace",
                target_label=f"Igualar a {rival_name}: {speak_lap_time_spanish(rival_best)}",
                target_value=rival_best,
                priority=2,
            )
        )

    # Par de vueltas como confirmación de preparación
    obj_set.objectives.append(
        SessionObjective(
            id="qualy_clean_pair",
            category="consistency",
            target_label="Par de vueltas dentro de 0.5 segundos entre sí",
            target_value=0.5,
            priority=2,
        )
    )


# ---------------------------------------------------------------------------
# Carrera
# ---------------------------------------------------------------------------

def _build_race(
    obj_set: SessionObjectiveSet,
    history: "HistoricalPaceSummary | None",
) -> None:
    own_best = history.own_best_seconds if history else None

    if own_best is not None:
        race_target = own_best + 0.8
        obj_set.objectives.append(
            SessionObjective(
                id="race_pace",
                category="pace",
                target_label=f"Promedio de vuelta por debajo de {speak_lap_time_spanish(race_target)}",
                target_value=race_target,
                priority=1,
            )
        )

    obj_set.objectives.append(
        SessionObjective(
            id="race_stint",
            category="stint",
            target_label="Cinco vueltas consecutivas dentro de 1.0 segundo de dispersión",
            target_value=5.0,
            priority=1,
        )
    )
