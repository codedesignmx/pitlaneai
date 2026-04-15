"""Checkpoint de sesión activa.

Guarda el estado de la sesión en curso (vueltas + setup coach) después de cada
vuelta.  Al reiniciar el programa, si el checkpoint corresponde a la misma
pista y tipo de sesión, se restaura automáticamente para no perder el progreso.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ac_race_engineer.analysis.setup_coach import SetupCoach
    from ac_race_engineer.telemetry.models import LapRecord

CHECKPOINT_PATH = Path("session_logs/checkpoint_live.json")


# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------


def save_checkpoint(
    track: str,
    session_type: str,
    laps: "list[LapRecord]",
    setup_coach: "SetupCoach",
) -> None:
    """Escribe el checkpoint de la sesión activa a disco."""
    CHECKPOINT_PATH.parent.mkdir(exist_ok=True)

    laps_data = [
        {
            "lap_number": lap.lap_number,
            "lap_time_seconds": lap.lap_time_seconds,
            "fuel_at_lap_start": lap.fuel_at_lap_start,
            "fuel_at_lap_end": lap.fuel_at_lap_end,
            "fuel_used": lap.fuel_used,
        }
        for lap in laps
    ]

    rec = setup_coach.last_recommendation
    last_rec_data: dict | None = None
    if rec is not None:
        last_rec_data = {
            "parameter": rec.parameter,
            "direction": rec.direction,
            "step": rec.step,
            "reason": rec.reason,
            "current_value": rec.current_value,
            "target_value": rec.target_value,
            "min_value": rec.min_value,
            "max_value": rec.max_value,
        }

    payload = {
        "track": track,
        "session_type": session_type,
        "saved_at": datetime.now().isoformat(),
        "laps": laps_data,
        "setup_coach": {
            "active": setup_coach.active,
            "iterations": setup_coach.export_iterations(),
            "parameter_outcomes": setup_coach._parameter_outcomes,
            "parameter_limits": setup_coach._parameter_limits,
            "setup_lap_history": setup_coach._setup_lap_history,
            "last_recommendation": last_rec_data,
        },
    }

    try:
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[CHECKPOINT] Error guardando: {exc}")


# ---------------------------------------------------------------------------
# Cargar
# ---------------------------------------------------------------------------


def load_checkpoint(track: str, session_type: str) -> dict | None:
    """Devuelve el checkpoint si coincide con la sesión actual, o None."""
    if not CHECKPOINT_PATH.exists():
        return None

    try:
        with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    if data.get("track") != track or data.get("session_type") != session_type:
        return None

    laps = data.get("laps")
    if not isinstance(laps, list) or len(laps) == 0:
        return None

    return data


# ---------------------------------------------------------------------------
# Limpiar
# ---------------------------------------------------------------------------


def clear_checkpoint() -> None:
    """Elimina el checkpoint (sesión terminada o cambiada)."""
    try:
        CHECKPOINT_PATH.unlink(missing_ok=True)
    except Exception:
        pass
