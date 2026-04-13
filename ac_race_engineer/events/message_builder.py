from __future__ import annotations

import math

from ac_race_engineer.analysis.time_format import (
    speak_delta_spanish,
    speak_lap_time_spanish,
    speak_laps_spanish,
)
from ac_race_engineer.telemetry.models import Event


def build_event_message(event: Event) -> str:
    if event.name == "new_best_lap":
        lap_time = _as_float(event.payload.get("lap_time"))
        improvement = _as_float(event.payload.get("improvement"))
        position = _as_float(event.payload.get("position"))
        base = (
            "Nueva mejor vuelta. "
            f"Tiempo {speak_lap_time_spanish(lap_time)}. "
            f"Mejoramos {speak_delta_spanish(improvement)}."
        )
        if position is not None and position > 0:
            if int(position) == 1:
                return base + " Tenemos el mejor tiempo general. Estamos en posición 1."
            return base + f" Estamos en posición {int(position)}."
        return base

    if event.name == "pace_improving":
        delta = _as_float(event.payload.get("delta"))
        return f"Buen ritmo. Mejorando {speak_delta_spanish(delta)} frente al promedio reciente."

    if event.name == "pace_drop":
        delta = _as_float(event.payload.get("delta"))
        return f"Perdimos ritmo. La última vuelta fue {speak_delta_spanish(delta)} más lenta."

    if event.name == "stint_consistent":
        return "Stint consistente. Mantén esa ventana de ritmo."

    if event.name == "fuel_update":
        fuel_per_lap = _as_float(event.payload.get("avg_fuel_per_lap"))
        laps_left = _as_float(event.payload.get("estimated_laps_left"))
        if fuel_per_lap is None or laps_left is None:
            return "Consumo estimado actualizado."

        laps_completed = _as_float(event.payload.get("laps_completed"))
        session_laps_total = _as_float(event.payload.get("session_laps_total"))
        estimated_laps_to_finish = _as_float(event.payload.get("estimated_laps_to_finish"))

        # Compute remaining race laps and fuel suggestion (fixed laps or timed race)
        pit_suffix = ""
        if session_laps_total and session_laps_total > 0 and laps_completed is not None:
            remaining = max(0, int(session_laps_total) - int(laps_completed))
            if remaining > 0:
                suggested = math.ceil((remaining + 1) * fuel_per_lap * 10) / 10.0
                pit_suffix = f" Restan {remaining} vueltas. Carga al menos {suggested:.1f} litros."
        elif estimated_laps_to_finish is not None and estimated_laps_to_finish > 0:
            suggested = math.ceil((estimated_laps_to_finish + 0.5) * fuel_per_lap * 10) / 10.0
            pit_suffix = (
                f" Carrera por tiempo: quedan aprox {speak_laps_spanish(estimated_laps_to_finish)}."
                f" Carga al menos {suggested:.1f} litros."
            )

        if laps_left < 1.0:
            return (
                "Alerta de combustible. No terminas esta vuelta. Entra a pits ahora."
                + pit_suffix
            )

        if laps_left < 1.5:
            return (
                f"Combustible bajo, menos de dos vueltas. Planea entrar a pits pronto."
                + pit_suffix
            )

        return (
            f"Consumo {fuel_per_lap:.2f} litros por vuelta. "
            f"Combustible para {speak_laps_spanish(laps_left)}."
            + pit_suffix
        )

    if event.name == "traffic_close":
        count = _as_float(event.payload.get("count"))
        gap_seconds = _as_float(event.payload.get("closest_gap_seconds"))
        closest = _as_float(event.payload.get("closest_m"))
        if count is None:
            return "Atención, tráfico cercano."
        if gap_seconds is not None:
            return f"Atención, {int(count)} coche cerca. Está a {speak_delta_spanish(gap_seconds)}."
        if closest is None:
            return f"Atención, tienes {int(count)} coche cerca."
        return f"Atención, {int(count)} coche cerca. Aproximadamente a {closest:.1f} metros."

    if event.name == "incident_nearby":
        sector = _as_float(event.payload.get("sector"))
        count = _as_float(event.payload.get("count"))
        if sector is None:
            return "Bandera de precaución, posible incidente cercano."
        if count is None:
            return f"Precaución, posible incidente cercano en sector {int(sector) + 1}."
        return (
            f"Precaución, {int(count)} coche con posible incidente cerca en tu sector "
            f"{int(sector) + 1}."
        )

    if event.name == "collision_contact":
        role = str(event.payload.get("role") or "contact")
        opponent = str(event.payload.get("opponent_name") or "")
        speed_drop = _as_float(event.payload.get("speed_drop_kmh"))

        if role == "got_hit":
            if opponent:
                return f"Contacto detectado. Nos golpeó {opponent}."
            return "Contacto detectado. Nos golpearon."

        if role == "hit_other":
            if opponent:
                return f"Contacto detectado. Golpeamos a {opponent}."
            return "Contacto detectado. Golpeamos a otro coche."

        if opponent and speed_drop is not None:
            return f"Contacto con {opponent}. Caída de velocidad {speed_drop:.1f} kilómetros por hora."
        if opponent:
            return f"Contacto detectado con {opponent}."
        return "Contacto detectado."

    return event.name.replace("_", " ")


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
