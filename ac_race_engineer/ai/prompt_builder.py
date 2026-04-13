from __future__ import annotations

from ac_race_engineer.analysis.session_state import SessionState
from ac_race_engineer.analysis.time_format import format_lap_time


def _fmt(value: float | None, decimals: int = 3, suffix: str = "") -> str:
    if value is None:
        return "sin datos"
    return f"{value:.{decimals}f}{suffix}"


def _fmt_lap(value: float | None) -> str:
    return format_lap_time(value) if value is not None else "sin datos"


def build_practice_prompt(state: SessionState) -> str:
    """Construye el contexto de sesión que se antepone al mensaje del piloto.

    El asistente de OpenAI ya tiene su personalidad de ingeniero de carrera
    configurada. Este texto solo aporta los datos actuales de la sesión para
    que pueda responder con información real.
    """
    stats = state.get_stats()
    snap = state.last_snapshot
    laps_done = len(state.laps)

    fuel_line = (
        f"Combustible actual: {snap.fuel:.2f} L. "
        if snap is not None
        else ""
    )

    return (
        f"[CONTEXTO DE SESIÓN - {laps_done} vuelta(s) completada(s)] "
        f"Última vuelta: {_fmt_lap(stats.last_lap_seconds)}. "
        f"Mejor vuelta: {_fmt_lap(stats.best_lap_seconds)}. "
        f"Promedio últimas 3: {_fmt_lap(stats.avg_last_3_seconds)}. "
        f"Promedio últimas 5: {_fmt_lap(stats.avg_last_5_seconds)}. "
        f"Consumo medio por vuelta: {_fmt(stats.avg_fuel_per_lap, 3, ' L')}. "
        f"{fuel_line}"
        f"Vueltas estimadas restantes: {_fmt(stats.estimated_laps_left, 1)}. "
        f"[MENSAJE DEL PILOTO] "
    )
