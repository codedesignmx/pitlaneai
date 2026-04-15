from __future__ import annotations


def format_lap_time(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total_millis = int(round(seconds * 1000.0))
    minutes, remainder = divmod(total_millis, 60000)
    secs, millis = divmod(remainder, 1000)
    return f"{minutes}:{secs:02d}.{millis:03d}"


def format_delta(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    sign = "+" if seconds > 0 else ""
    return f"{sign}{seconds:.3f}s"


def speak_lap_time_spanish(seconds: float | None) -> str:
    if seconds is None:
        return "sin tiempo"
    total_millis = int(round(seconds * 1000.0))
    minutes, remainder = divmod(total_millis, 60000)
    secs, millis = divmod(remainder, 1000)
    second_word = "segundo" if secs == 1 else "segundos"
    milli_word = "milésima" if millis == 1 else "milésimas"
    if minutes > 0:
        minute_word = "minuto" if minutes == 1 else "minutos"
        return f"{minutes} {minute_word} : {secs} {second_word} . {millis:03d} {milli_word}"
    return f"0 minutos : {secs} {second_word} . {millis:03d} {milli_word}"


def speak_delta_spanish(seconds: float | None) -> str:
    if seconds is None:
        return "sin delta"
    value = abs(seconds)
    total_millis = int(round(value * 1000.0))
    minutes, remainder = divmod(total_millis, 60000)
    secs, millis = divmod(remainder, 1000)
    second_word = "segundo" if secs == 1 else "segundos"
    milli_word = "milésima" if millis == 1 else "milésimas"
    if minutes > 0:
        minute_word = "minuto" if minutes == 1 else "minutos"
        return f"{minutes} {minute_word} {secs} {second_word} {millis:03d} {milli_word}"
    return f"{secs} {second_word} {millis:03d} {milli_word}"


def speak_laps_spanish(laps: float | None) -> str:
    if laps is None:
        return "sin estimacion de vueltas"
    rounded = max(0.0, round(float(laps), 1))
    whole = int(rounded)
    tenths = int(round((rounded - whole) * 10))
    if tenths == 10:
        whole += 1
        tenths = 0
    return f"{whole} coma {tenths} vueltas"
