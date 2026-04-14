from __future__ import annotations

import math
from dataclasses import dataclass

from ac_race_engineer.analysis.time_format import speak_lap_time_spanish


@dataclass(slots=True)
class ObjectiveMetrics:
    """Métricas objetivo para decisiones objetivas."""

    # Setup
    setup_score: float | None = None  # 0-100, basado en consistencia
    setup_confidence: str | None = None  # "baja", "media", "alta"

    # Qualy
    qualy_best_lap: float | None = None
    qualy_theoretical_max: float | None = None  # suma de mejores sectores
    qualy_gap_to_max: float | None = None
    qualy_readiness: str | None = None  # "not_ready", "medium", "ready"

    # Race
    race_pace_avg: float | None = None
    race_degradation_per_lap: float | None = None  # segundos/vuelta
    race_estimated_stint_laps: int = 0
    race_recommended_pace_target: float | None = None

    # Fuel
    fuel_consumption_per_lap: float | None = None
    fuel_for_remaining_time: float | None = None
    fuel_margin_minutes: float | None = None  # +/- minutos extra


def compute_setup_score(
    laps_by_setup: dict[str, list[float]],
) -> dict[str, dict[str, float | str]]:
    """Calcula score de cada setup testeado (A/B/C).

    Entrada: dict con clave "setup_X" → lista de tiempos de vuelta válidos
    Salida: dict con setup → {"score", "avg_time", "degradation", "consistency"}
    """
    result: dict[str, dict[str, float | str]] = {}

    for setup_name, times in laps_by_setup.items():
        if not times or len(times) < 2:
            continue

        times_sorted = sorted(times)
        best_2 = times_sorted[:2]
        avg_best_2 = sum(best_2) / len(best_2)

        # Degradación: diferencia entre mejor y peor
        degradation = times_sorted[-1] - times_sorted[0]

        # Consistencia: desviación estándar
        mean = sum(times) / len(times)
        variance = sum((t - mean) ** 2 for t in times) / len(times)
        std_dev = math.sqrt(variance)

        # Score: 100 = muy consistente y rápido; baja por degradación y varianza
        consistency_penalty = min(std_dev * 100, 30)
        degradation_penalty = min(degradation * 50, 20)
        score = max(0, 100 - consistency_penalty - degradation_penalty)

        result[setup_name] = {
            "score": round(score, 1),
            "avg_best_2": round(avg_best_2, 3),
            "degradation": round(degradation, 3),
            "consistency_std": round(std_dev, 3),
            "samples": len(times),
        }

    return result


def compute_qualy_readiness(
    best_lap: float | None,
    sector_times: list[float] | None = None,
) -> ObjectiveMetrics:
    """Evalúa readiness para qualy: cuánto potencial queda sin explotar.

    Si tienes best lap + sector times (mejores por sector de toda la mañana),
    suma de sectores = máximo teórico.
    """
    metrics = ObjectiveMetrics(qualy_best_lap=best_lap)

    if best_lap is None:
        metrics.qualy_readiness = "not_ready"
        return metrics

    if sector_times and len(sector_times) >= 3:
        theoretical_max = sum(sector_times)
        gap = theoretical_max - best_lap
        metrics.qualy_theoretical_max = round(theoretical_max, 3)
        metrics.qualy_gap_to_max = round(gap, 3)

        # Readiness: si gap < 0.3s → ready; < 0.6s → medium; else → not_ready
        if gap < 0.3:
            metrics.qualy_readiness = "ready"
        elif gap < 0.6:
            metrics.qualy_readiness = "medium"
        else:
            metrics.qualy_readiness = "not_ready"
    else:
        metrics.qualy_readiness = "medium"  # sin sectores, asumir neutral

    return metrics


def compute_race_pace(
    laps: list[float],
    window_size: int = 5,
) -> ObjectiveMetrics:
    """Calcula pace sostenible y degradación en carrera.

    Filtra outliers y calcula promedio de ventana para tendencia.
    """
    metrics = ObjectiveMetrics()

    if not laps or len(laps) < 3:
        return metrics

    # Filtrar outliers: vueltas fuera de rango 2σ
    mean = sum(laps) / len(laps)
    std = math.sqrt(sum((t - mean) ** 2 for t in laps) / len(laps))
    clean = [t for t in laps if abs(t - mean) <= 2.5 * std]

    if len(clean) < 3:
        clean = laps

    # Pace promedio
    pace_avg = sum(clean) / len(clean)
    metrics.race_pace_avg = round(pace_avg, 3)

    # Degradación: pendiente lineal (least squares)
    if len(clean) >= 4:
        n = len(clean)
        x = list(range(n))
        mean_x = sum(x) / n
        mean_y = sum(clean) / n
        slope = sum((x[i] - mean_x) * (clean[i] - mean_y) for i in range(n)) / sum(
            (x[i] - mean_x) ** 2 for i in range(n)
        )
        metrics.race_degradation_per_lap = round(slope, 4)
    else:
        metrics.race_degradation_per_lap = 0.0

    return metrics


def compute_fuel_predictor(
    avg_fuel_per_lap: float | None,
    time_left_minutes: float,
    avg_lap_time_seconds: float | None,
) -> ObjectiveMetrics:
    """Calcula fuel necesario para X tiempo restante.

    Para carrera por tiempo: estima vueltas por min restante, calcula fuel.
    """
    metrics = ObjectiveMetrics()

    if avg_fuel_per_lap is None or avg_lap_time_seconds is None:
        return metrics

    # Vueltas estimadas en tiempo restante
    laps_in_remaining_time = (time_left_minutes * 60.0) / avg_lap_time_seconds
    metrics.race_estimated_stint_laps = int(laps_in_remaining_time)

    # Fuel necesario + margen de seguridad
    fuel_base = laps_in_remaining_time * avg_fuel_per_lap
    fuel_margin = 0.8  # margen 5% (consumo extra) + 0.5 vuelta buffer
    fuel_total = fuel_base * (1.0 + 0.05) + 0.8

    metrics.fuel_for_remaining_time = round(fuel_total, 2)
    metrics.fuel_consumption_per_lap = round(avg_fuel_per_lap, 3)

    # Margen en minutos teóricos
    margin_laps = fuel_margin / avg_fuel_per_lap
    margin_minutes = (margin_laps * avg_lap_time_seconds) / 60.0
    metrics.fuel_margin_minutes = round(margin_minutes, 1)

    return metrics


def build_objective_summary(metrics: ObjectiveMetrics) -> str:
    """Resumen de voz para reportar métricas objetivas."""
    parts: list[str] = []

    if metrics.setup_score is not None:
        parts.append(f"Setup score {metrics.setup_score:.0f} cien.")

    if metrics.qualy_readiness == "ready":
        parts.append("Estás listo para clasificación.")
    elif metrics.qualy_readiness == "medium":
        parts.append("Clasificación medianamente lista.")
    elif metrics.qualy_readiness == "not_ready" and metrics.qualy_gap_to_max:
        parts.append(
            f"Aún hay {metrics.qualy_gap_to_max:.2f} segundos de potencial sin explorar."
        )

    if metrics.race_pace_avg is not None:
        parts.append(f"Ritmo de carrera {speak_lap_time_spanish(metrics.race_pace_avg)}.")

    if metrics.fuel_for_remaining_time is not None:
        parts.append(
            f"Combustible necesario {metrics.fuel_for_remaining_time:.1f} litros. "
            f"Margen {metrics.fuel_margin_minutes:.1f} minutos."
        )

    return " ".join(parts) if parts else "Sin métricas objetivo aún."
