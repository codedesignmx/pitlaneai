from __future__ import annotations

import configparser
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ac_race_engineer.analysis.time_format import speak_lap_time_spanish


@dataclass(slots=True)
class StandingEntry:
    position: int
    name: str
    best_lap_seconds: float | None = None
    is_player: bool = False


@dataclass(slots=True)
class LiveGapInfo:
    gap_ahead_seconds: float | None = None
    gap_behind_seconds: float | None = None


@dataclass(slots=True)
class LiveWeatherInfo:
    track_grip_percent: float | None = None
    air_temp_c: float | None = None
    asphalt_temp_c: float | None = None
    wind_speed_kmh: float | None = None


def build_session_end_summary(
    session_label: str,
    own_position: int,
    own_best_lap: float | None,
    standings: list[StandingEntry],
) -> str:
    own_line = (
        f"Final de {session_label}. Terminamos en posición {own_position}."
        if own_position > 0
        else f"Final de {session_label}. No pude confirmar nuestra posición final."
    )
    if own_best_lap is not None:
        own_line += f" Mejor vuelta {speak_lap_time_spanish(own_best_lap)}."

    if not standings:
        return own_line + " No encontré clasificación completa para repasar a todos."

    if session_label in {"practice", "qualifying"}:
        # En práctica/qualy la clasificación correcta es por mejor vuelta de sesión.
        top = sorted(
            standings,
            key=lambda s: (
                s.best_lap_seconds is None,
                float(s.best_lap_seconds or 1e9),
                s.position,
            ),
        )
    else:
        top = sorted(standings, key=lambda s: s.position)

    recap_parts: list[str] = []
    for idx, row in enumerate(top[:20], start=1):
        spoken_pos = idx if session_label in {"practice", "qualifying"} else row.position
        if row.best_lap_seconds is not None:
            recap_parts.append(
                f"Posición {spoken_pos}, {row.name}, mejor vuelta {speak_lap_time_spanish(row.best_lap_seconds)}"
            )
        else:
            recap_parts.append(f"Posición {spoken_pos}, {row.name}")
    return own_line + " Repaso general. " + ". ".join(recap_parts) + "."


def load_latest_standings(
    result_dirs: list[str],
    expected_session_type: str | None = None,
) -> list[StandingEntry]:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=30):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not _matches_session_type(payload, expected_session_type):
            continue

        rows: list[StandingEntry] = []
        _collect_rows(payload, rows)

        unique: dict[str, StandingEntry] = {}
        for row in rows:
            if row.position <= 0 or not row.name:
                continue
            key = row.name.lower()
            prev = unique.get(key)
            if prev is None:
                unique[key] = row
                continue

            # Consolidar por nombre para evitar duplicados del mismo piloto
            # en distintos nodos/posiciones del JSON.
            best_position = min(prev.position, row.position)
            if prev.best_lap_seconds is None:
                best_lap = row.best_lap_seconds
            elif row.best_lap_seconds is None:
                best_lap = prev.best_lap_seconds
            else:
                best_lap = min(prev.best_lap_seconds, row.best_lap_seconds)

            chosen_name = prev.name if len(prev.name) >= len(row.name) else row.name
            unique[key] = StandingEntry(
                position=best_position,
                name=chosen_name,
                best_lap_seconds=best_lap,
                is_player=bool(prev.is_player or row.is_player),
            )

        standings = sorted(unique.values(), key=lambda r: r.position)
        if len(standings) >= 2:
            return standings

    return []


def describe_standings_source(result_dirs: list[str]) -> str:
    candidates = _find_recent_json_candidates(result_dirs, max_files=1)
    if not candidates:
        return "sin archivo json de standings"
    latest = candidates[0]
    age_s = max(0.0, time.time() - latest.stat().st_mtime)
    return f"archivo {latest.name}, actualizado hace {age_s:.1f}s"


def load_live_gap_info(
    result_dirs: list[str],
    expected_session_type: str | None = None,
) -> LiveGapInfo:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=20):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue
        if not _matches_session_type(payload, expected_session_type):
            continue

        gap_ahead = _pick_float(payload, ["gapAheadSeconds", "gap_ahead_seconds"])
        gap_behind = _pick_float(payload, ["gapBehindSeconds", "gap_behind_seconds"])

        if gap_ahead is not None or gap_behind is not None:
            return LiveGapInfo(gap_ahead_seconds=gap_ahead, gap_behind_seconds=gap_behind)

        standings_node = payload.get("standings")
        if isinstance(standings_node, list):
            for row in standings_node:
                if not isinstance(row, dict):
                    continue
                is_player = bool(row.get("isPlayer"))
                if not is_player:
                    continue

                gap_ahead = _pick_float(row, ["gapAheadSeconds", "gap_ahead_seconds"])
                gap_behind = _pick_float(row, ["gapBehindSeconds", "gap_behind_seconds"])
                if gap_ahead is not None or gap_behind is not None:
                    return LiveGapInfo(gap_ahead_seconds=gap_ahead, gap_behind_seconds=gap_behind)

    return LiveGapInfo()


def load_live_weather_info(
    result_dirs: list[str],
    expected_session_type: str | None = None,
) -> LiveWeatherInfo:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=20):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue
        if not _matches_session_type(payload, expected_session_type):
            continue

        raw_grip = _extract_first_float_by_keys(
            payload,
            keys={"trackgrip", "track_grip", "surfacegrip", "surface_grip", "grip"},
        )
        raw_air = _extract_first_float_by_keys(
            payload,
            keys={
                "airtemp",
                "air_temp",
                "ambienttemp",
                "ambient_temp",
                "airtemperature",
                "airtempc",
                "ambienttempc",
            },
        )
        raw_asphalt = _extract_first_float_by_keys(
            payload,
            keys={
                "roadtemp",
                "road_temp",
                "asphalttemp",
                "asphalt_temp",
                "tracktemp",
                "track_temp",
                "tarmactemp",
                "tarmac_temp",
                "roadtempc",
                "tracktempc",
            },
        )
        raw_wind = _extract_first_float_by_keys(
            payload,
            keys={
                "windspeed",
                "wind_speed",
                "windkmh",
                "wind_kmh",
                "windspeedkmh",
                "wind",
            },
        )

        grip_percent = None
        if raw_grip is not None:
            grip_percent = raw_grip * 100.0 if 0.0 < raw_grip <= 2.0 else raw_grip
            if not (0.0 <= grip_percent <= 200.0):
                grip_percent = None

        air_temp_c = raw_air if raw_air is not None and -40.0 <= raw_air <= 80.0 else None
        asphalt_temp_c = (
            raw_asphalt if raw_asphalt is not None and -40.0 <= raw_asphalt <= 120.0 else None
        )
        wind_speed_kmh = raw_wind if raw_wind is not None and 0.0 <= raw_wind <= 250.0 else None

        if (
            grip_percent is not None
            or air_temp_c is not None
            or asphalt_temp_c is not None
            or wind_speed_kmh is not None
        ):
            return LiveWeatherInfo(
                track_grip_percent=round(grip_percent, 1) if grip_percent is not None else None,
                air_temp_c=round(air_temp_c, 1) if air_temp_c is not None else None,
                asphalt_temp_c=round(asphalt_temp_c, 1) if asphalt_temp_c is not None else None,
                wind_speed_kmh=round(wind_speed_kmh, 1) if wind_speed_kmh is not None else None,
            )

    return LiveWeatherInfo()


def load_race_ini_weather_info(
    expected_track_name: str | None = None,
    expected_session_type: str | None = None,
    max_age_seconds: float = 7200.0,
) -> LiveWeatherInfo:
    """Lee meteo desde race.ini (pantalla inicial de AC/CM).

    Se usa solo si el archivo es reciente y coincide con pista/sesión esperadas.
    """
    race_ini = Path(os.path.expanduser("~/Documents/Assetto Corsa/cfg/race.ini"))
    if not race_ini.exists() or not race_ini.is_file():
        return LiveWeatherInfo()

    try:
        age_seconds = max(0.0, time.time() - race_ini.stat().st_mtime)
        if age_seconds > max_age_seconds:
            return LiveWeatherInfo()
    except Exception:
        return LiveWeatherInfo()

    parser = configparser.ConfigParser()
    parser.optionxform = str
    try:
        parser.read(race_ini, encoding="utf-8")
    except Exception:
        return LiveWeatherInfo()

    ini_track = parser.get("RACE", "TRACK", fallback="").strip()
    ini_mode = parser.get("METADATA", "GAMEMODE", fallback="").strip().lower()

    if expected_track_name and ini_track:
        if not _tracks_match(ini_track, expected_track_name):
            return LiveWeatherInfo()

    if expected_session_type and ini_mode:
        if not _session_modes_match(ini_mode, expected_session_type):
            return LiveWeatherInfo()

    ambient = _ini_get_float(parser, "TEMPERATURE", "AMBIENT")
    road = _ini_get_float(parser, "TEMPERATURE", "ROAD")
    wind_min = _ini_get_float(parser, "WIND", "SPEED_KMH_MIN")
    wind_max = _ini_get_float(parser, "WIND", "SPEED_KMH_MAX")

    wind = None
    if wind_min is not None and wind_max is not None:
        wind = (wind_min + wind_max) / 2.0
    elif wind_min is not None:
        wind = wind_min
    elif wind_max is not None:
        wind = wind_max

    if ambient is not None and not (-40.0 <= ambient <= 80.0):
        ambient = None
    if road is not None and not (-40.0 <= road <= 120.0):
        road = None
    if wind is not None and not (0.0 <= wind <= 250.0):
        wind = None

    if ambient is None and road is None and wind is None:
        return LiveWeatherInfo()

    return LiveWeatherInfo(
        air_temp_c=round(ambient, 1) if ambient is not None else None,
        asphalt_temp_c=round(road, 1) if road is not None else None,
        wind_speed_kmh=round(wind, 1) if wind is not None else None,
    )


def load_ac_log_weather_info(max_age_seconds: float = 7200.0) -> LiveWeatherInfo:
    """Lee meteo en vivo desde log.txt de AC/CSP.

    Busca las ultimas lineas con:
    - ACP_WEATHER_UPDATE: Ambient=.. Road=..
    - Setting wind .. kmh
    """
    ac_log_path = Path(os.path.expanduser("~/Documents/Assetto Corsa/logs/log.txt"))
    csp_log_path = Path(os.path.expanduser("~/Documents/Assetto Corsa/logs/custom_shaders_patch.log"))

    candidates = [p for p in (ac_log_path, csp_log_path) if p.exists() and p.is_file()]
    if not candidates:
        return LiveWeatherInfo()

    recent_texts: list[str] = []
    for log_path in candidates:
        try:
            age_seconds = max(0.0, time.time() - log_path.stat().st_mtime)
            if age_seconds > max_age_seconds:
                continue
        except Exception:
            continue

        # El valor de viento suele aparecer al inicio de carga de sesión.
        # Si el log es muy verboso, 600 KB puede dejar esa línea fuera.
        text = _read_text_tail(log_path, max_bytes=5_000_000)
        if text:
            recent_texts.append(text)

    text = "\n".join(recent_texts)
    if not text:
        return LiveWeatherInfo()

    weather_match = None
    for match in re.finditer(
        r"ACP_WEATHER_UPDATE:\s*Ambient=([-+]?\d+(?:\.\d+)?)\s*Road=([-+]?\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    ):
        weather_match = match

    wind_match = None
    wind_patterns = (
        r"Setting\s+wind\s+([-+]?\d+(?:\.\d+)?)\s*kmh",
        r"wind\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*(?:kmh|km/h)",
        r"wind\s+([-+]?\d+(?:\.\d+)?)\s*(?:kmh|km/h)",
    )
    for pattern in wind_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            wind_match = match

    air_temp = None
    asphalt_temp = None
    wind_speed = None

    if weather_match is not None:
        try:
            air_temp = float(weather_match.group(1))
            asphalt_temp = float(weather_match.group(2))
        except ValueError:
            air_temp = None
            asphalt_temp = None

    if wind_match is not None:
        try:
            wind_speed = float(wind_match.group(1))
        except ValueError:
            wind_speed = None

    if air_temp is not None and not (-40.0 <= air_temp <= 80.0):
        air_temp = None
    if asphalt_temp is not None and not (-40.0 <= asphalt_temp <= 120.0):
        asphalt_temp = None
    if wind_speed is not None and not (0.0 <= wind_speed <= 250.0):
        wind_speed = None

    if air_temp is None and asphalt_temp is None and wind_speed is None:
        return LiveWeatherInfo()

    return LiveWeatherInfo(
        air_temp_c=round(air_temp, 1) if air_temp is not None else None,
        asphalt_temp_c=round(asphalt_temp, 1) if asphalt_temp is not None else None,
        wind_speed_kmh=round(wind_speed, 1) if wind_speed is not None else None,
    )


def load_live_car_index_map(
    result_dirs: list[str],
    expected_session_type: str | None = None,
) -> dict[int, str]:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=20):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue
        if not _matches_session_type(payload, expected_session_type):
            continue

        standings = payload.get("standings")
        if not isinstance(standings, list):
            continue

        mapping: dict[int, str] = {}
        for row in standings:
            if not isinstance(row, dict):
                continue
            car_idx = _pick_int(row, ["carIndex", "car_index", "index"])
            name = _pick_str(
                row,
                ["name", "driverName", "driver_name", "fullName", "playerName", "steamName"],
            )
            if car_idx is None or name is None:
                continue
            mapping[car_idx] = name

        if mapping:
            return mapping

    return {}


def detect_standings_updates(
    previous: list[StandingEntry],
    current: list[StandingEntry],
    player_position: int | None = None,
    player_name: str | None = None,
) -> list[str]:
    prev_map = {row.name.lower(): row for row in previous}
    updates: list[tuple[int, str]] = []
    local_name_key = _normalize_name(player_name)

    prev_leader = _leader_with_time(previous)
    curr_leader = _leader_with_time(current)
    if (
        prev_leader is not None
        and curr_leader is not None
        and curr_leader.best_lap_seconds is not None
        and prev_leader.best_lap_seconds is not None
        and curr_leader.best_lap_seconds < (prev_leader.best_lap_seconds - 0.02)
    ):
        updates.append(
            (
                0,
                f"Récord de sesión para {curr_leader.name}. Posición 1, "
                f"tiempo {speak_lap_time_spanish(curr_leader.best_lap_seconds)}.",
            )
        )

    for row in current:
        if row.is_player:
            # El propio piloto nunca debe anunciarse como rival.
            continue
        if local_name_key and _normalize_name(row.name) == local_name_key:
            continue
        if player_position is not None and player_position > 0 and row.position == player_position:
            # No anunciar mejoras del propio piloto en el canal de rivales.
            continue

        key = row.name.lower()
        prev = prev_map.get(key)

        if row.best_lap_seconds is None:
            continue

        if prev is None:
            updates.append(
                (
                    row.position,
                    f"Nuevo tiempo de {row.name}. Posición {row.position}, "
                    f"{speak_lap_time_spanish(row.best_lap_seconds)}.",
                )
            )
            continue

        if prev.best_lap_seconds is None or row.best_lap_seconds < (prev.best_lap_seconds - 0.05):
            updates.append(
                (
                    row.position,
                    f"{row.name} mejora. Posición {row.position}, "
                    f"tiempo {speak_lap_time_spanish(row.best_lap_seconds)}.",
                )
            )

    updates.sort(key=lambda x: x[0])
    return [msg for _, msg in updates[:3]]


def _leader_with_time(rows: list[StandingEntry]) -> StandingEntry | None:
    if not rows:
        return None
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            r.best_lap_seconds is None,
            float(r.best_lap_seconds or 1e9),
            r.position,
        ),
    )
    for row in sorted_rows:
        if row.best_lap_seconds is not None and row.best_lap_seconds > 0:
            return row
    return None


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    cleaned = name.strip().lower()
    return " ".join(cleaned.split())


def _find_recent_json_candidates(result_dirs: list[str], max_files: int = 20) -> list[Path]:
    candidates: list[Path] = []
    for folder in result_dirs:
        expanded = os.path.expandvars(os.path.expanduser(folder))
        root = Path(expanded)
        if not root.exists() or not root.is_dir():
            continue
        candidates.extend(root.rglob("*.json"))

    if not candidates:
        return []

    keywords = ("result", "leader", "standing", "session", "qual", "race", "lfm")

    def score(path: Path) -> tuple[int, float]:
        name = path.name.lower()
        hit = 1 if any(k in name for k in keywords) else 0
        return hit, path.stat().st_mtime

    ranked = sorted(candidates, key=score, reverse=True)
    return ranked[:max_files]


def _collect_rows(node: object, rows: list[StandingEntry]) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_rows(item, rows)
        return

    if not isinstance(node, dict):
        return

    maybe = _row_from_dict(node)
    if maybe is not None:
        rows.append(maybe)

    for value in node.values():
        _collect_rows(value, rows)


def _row_from_dict(data: dict[str, object]) -> StandingEntry | None:
    pos = _pick_int(
        data,
        ["position", "pos", "place", "rank", "overallPosition", "carPosition", "positionIndex"],
    )
    name = _pick_str(
        data,
        [
            "name",
            "driverName",
            "driver_name",
            "fullname",
            "fullName",
            "playerName",
            "player_name",
            "carName",
            "car_name",
            "steamName",
            "steam_name",
            "nickname",
        ],
    )
    if name is None:
        driver_node = data.get("driver")
        if isinstance(driver_node, dict):
            name = _pick_str(
                driver_node,
                ["name", "fullName", "driverName", "nickname", "steamName", "playerName"],
            )

    if pos is None or name is None:
        return None

    lap_ms = _pick_float(
        data,
        [
            "bestLap",
            "best_lap",
            "bestLapMs",
            "best_lap_ms",
            "bestLapTime",
            "bestLapTimeMs",
            "best_lap_time",
            "best",
            "bestTime",
        ],
    )
    lap_seconds = None
    if lap_ms is not None:
        if lap_ms > 1000.0:
            lap_seconds = lap_ms / 1000.0
        elif lap_ms > 0.0:
            lap_seconds = lap_ms

    is_player = _pick_bool(data, ["isPlayer", "is_player", "player", "self"]) or False
    if not is_player:
        driver_node = data.get("driver")
        if isinstance(driver_node, dict):
            is_player = _pick_bool(driver_node, ["isPlayer", "is_player", "player", "self"]) or False

    return StandingEntry(
        position=pos,
        name=name,
        best_lap_seconds=lap_seconds,
        is_player=is_player,
    )


def _pick_int(data: dict[str, object], keys: list[str]) -> int | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _pick_float(data: dict[str, object], keys: list[str]) -> float | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip().replace(",", ".")
            by_format = _parse_lap_time_string(cleaned)
            if by_format is not None:
                return by_format
            try:
                return float(cleaned)
            except ValueError:
                pass
    return None


def _pick_bool(data: dict[str, object], keys: list[str]) -> bool | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "si", "sí"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
    return None


def _extract_first_float_by_keys(node: object, keys: set[str]) -> float | None:
    if isinstance(node, dict):
        for raw_key, value in node.items():
            normalized_key = str(raw_key).strip().lower().replace("-", "_").replace(" ", "")
            if normalized_key in keys:
                if isinstance(value, (int, float)):
                    return float(value)
                if isinstance(value, str):
                    cleaned = value.strip().replace(",", ".")
                    try:
                        return float(cleaned)
                    except ValueError:
                        pass

        for value in node.values():
            found = _extract_first_float_by_keys(value, keys)
            if found is not None:
                return found

    if isinstance(node, list):
        for item in node:
            found = _extract_first_float_by_keys(item, keys)
            if found is not None:
                return found

    return None


def _ini_get_float(parser: configparser.ConfigParser, section: str, key: str) -> float | None:
    if not parser.has_section(section) or not parser.has_option(section, key):
        return None
    value = parser.get(section, key, fallback="").strip().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _read_text_tail(path: Path, max_bytes: int = 200_000) -> str:
    try:
        file_size = path.stat().st_size
        read_size = min(max_bytes, file_size)
        with path.open("rb") as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").strip().lower())


def _tracks_match(ini_track: str, expected_track: str) -> bool:
    a = _normalize_token(ini_track)
    b = _normalize_token(expected_track)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _session_modes_match(ini_mode: str, expected_session: str) -> bool:
    mode = _normalize_token(ini_mode)
    expected = _normalize_token(expected_session)
    if not mode or not expected:
        return False

    aliases = {
        "practice": {"practice", "practica", "practica1", "practicesession"},
        "qualifying": {"qualifying", "qualification", "qualy", "clasificacion"},
        "race": {"race", "carrera"},
    }

    canonical_mode = None
    canonical_expected = None
    for key, values in aliases.items():
        if mode in values:
            canonical_mode = key
        if expected in values:
            canonical_expected = key

    if canonical_mode and canonical_expected:
        return canonical_mode == canonical_expected

    return mode == expected


def _parse_lap_time_string(value: str) -> float | None:
    text = value.strip().lower()
    if not text or text in {"-", "--", "none", "null", "dnf"}:
        return None

    # M:SS.mmm or MM:SS.mmm
    match = re.fullmatch(r"(\d+):(\d{1,2})(?:\.(\d{1,3}))?", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        millis_txt = match.group(3) or "0"
        millis = int(millis_txt.ljust(3, "0")[:3])
        return minutes * 60.0 + seconds + millis / 1000.0

    # SS.mmm
    match = re.fullmatch(r"(\d+)(?:\.(\d{1,3}))", text)
    if match:
        seconds = int(match.group(1))
        millis = int((match.group(2) or "0").ljust(3, "0")[:3])
        return seconds + millis / 1000.0

    return None


def _pick_str(data: dict[str, object], keys: list[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _matches_session_type(payload: object, expected_session_type: str | None) -> bool:
    if expected_session_type is None:
        return True
    if not isinstance(payload, dict):
        return True

    session_value = _pick_str(
        payload,
        ["sessionType", "session_type", "session", "sessionName"],
    )
    if session_value is None:
        return True

    normalized_payload = session_value.strip().lower()
    normalized_expected = expected_session_type.strip().lower()

    aliases = {
        "practice": {"practice", "practica", "práctica"},
        "qualifying": {"qualifying", "qualification", "qualy", "clasificacion", "clasificación"},
        "race": {"race", "carrera"},
    }

    for canonical, values in aliases.items():
        if normalized_expected in values:
            normalized_expected = canonical
            break

    for canonical, values in aliases.items():
        if normalized_payload in values:
            normalized_payload = canonical
            break

    return normalized_payload == normalized_expected
