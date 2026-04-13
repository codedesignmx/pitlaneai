from __future__ import annotations

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


@dataclass(slots=True)
class LiveGapInfo:
    gap_ahead_seconds: float | None = None
    gap_behind_seconds: float | None = None


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

    top = sorted(standings, key=lambda s: s.position)
    recap_parts: list[str] = []
    for row in top[:20]:
        if row.best_lap_seconds is not None:
            recap_parts.append(
                f"Posición {row.position}, {row.name}, mejor vuelta {speak_lap_time_spanish(row.best_lap_seconds)}"
            )
        else:
            recap_parts.append(f"Posición {row.position}, {row.name}")
    return own_line + " Repaso general. " + ". ".join(recap_parts) + "."


def load_latest_standings(
    result_dirs: list[str],
) -> list[StandingEntry]:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=30):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        rows: list[StandingEntry] = []
        _collect_rows(payload, rows)

        unique: dict[tuple[int, str], StandingEntry] = {}
        for row in rows:
            if row.position <= 0 or not row.name:
                continue
            key = (row.position, row.name.lower())
            if key not in unique:
                unique[key] = row

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


def load_live_gap_info(result_dirs: list[str]) -> LiveGapInfo:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=20):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, dict):
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


def load_live_car_index_map(result_dirs: list[str]) -> dict[int, str]:
    for candidate in _find_recent_json_candidates(result_dirs, max_files=20):
        try:
            with candidate.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, dict):
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
) -> list[str]:
    prev_map = {row.name.lower(): row for row in previous}
    updates: list[tuple[int, str]] = []

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
    sorted_rows = sorted(rows, key=lambda r: r.position)
    for row in sorted_rows:
        if row.best_lap_seconds is not None and row.best_lap_seconds > 0:
            return row
    return None


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

    return StandingEntry(position=pos, name=name, best_lap_seconds=lap_seconds)


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
