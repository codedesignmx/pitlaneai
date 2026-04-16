from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class HistoricalPaceSummary:
    own_best_seconds: float | None = None
    own_best_setup_id: str | None = None
    own_best_setup_label: str | None = None
    own_avg_fuel_per_lap: float | None = None
    own_fuel_sample_count: int = 0
    rival_best_name: str | None = None
    rival_best_seconds: float | None = None


def load_historical_pace_summary(
    track_name: str,
    session_type: str,
    session_logs_dir: str = "session_logs",
    rival_history_path: str = "database/rival_history.json",
) -> HistoricalPaceSummary:
    summary = HistoricalPaceSummary()

    _load_own_history(summary, track_name=track_name, session_type=session_type, session_logs_dir=session_logs_dir)
    _load_rival_history(summary, track_name=track_name, rival_history_path=rival_history_path)

    return summary


def _load_own_history(
    summary: HistoricalPaceSummary,
    track_name: str,
    session_type: str,
    session_logs_dir: str,
) -> None:
    root = Path(session_logs_dir)
    if not root.exists() or not root.is_dir():
        return

    best_by_setup: dict[str, float] = {}
    fuel_samples: list[float] = []

    for path in root.glob("session_*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue

        payload_track = str(payload.get("track") or "unknown")
        payload_session = str(payload.get("session_type") or "unknown")

        if payload_track != track_name:
            continue

        snapshots = payload.get("snapshots")
        if not isinstance(snapshots, list):
            continue

        for snap in snapshots:
            if not isinstance(snap, dict):
                continue

            fuel_used = snap.get("fuel_used")
            if isinstance(fuel_used, (int, float)) and float(fuel_used) > 0.0:
                fuel_samples.append(float(fuel_used))

            if payload_session != session_type:
                continue

            lap_time = snap.get("lap_time")
            if not isinstance(lap_time, (int, float)):
                continue
            lap_time = float(lap_time)
            if lap_time < 30.0 or lap_time > 600.0:
                continue

            if summary.own_best_seconds is None or lap_time < summary.own_best_seconds:
                summary.own_best_seconds = lap_time

            setup_id = str(snap.get("setup_hash") or "").strip()
            if setup_id:
                prev = best_by_setup.get(setup_id)
                if prev is None or lap_time < prev:
                    best_by_setup[setup_id] = lap_time

    if best_by_setup:
        non_default = [sid for sid in best_by_setup.keys() if sid and sid != "default"]
        candidate_ids = non_default
        if not candidate_ids:
            return
        best_setup_id = min(candidate_ids, key=lambda sid: best_by_setup[sid])
        summary.own_best_setup_id = best_setup_id
        summary.own_best_setup_label = _load_setup_label(best_setup_id, session_logs_dir=session_logs_dir)

    if fuel_samples:
        summary.own_avg_fuel_per_lap = sum(fuel_samples) / len(fuel_samples)
        summary.own_fuel_sample_count = len(fuel_samples)


def _load_rival_history(
    summary: HistoricalPaceSummary,
    track_name: str,
    rival_history_path: str,
) -> None:
    path = Path(rival_history_path)
    if not path.exists() or not path.is_file():
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return

    if not isinstance(payload, dict):
        return

    rivals = payload.get("rivals")
    if not isinstance(rivals, dict):
        return

    best_name: str | None = None
    best_seconds: float | None = None

    for entry in rivals.values():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue

        candidate: float | None = None

        tracks = entry.get("tracks")
        if isinstance(tracks, dict):
            track_info = tracks.get(track_name)
            if isinstance(track_info, dict):
                best_track = track_info.get("best_lap_seconds")
                if isinstance(best_track, (int, float)) and float(best_track) > 0:
                    candidate = float(best_track)

        if candidate is None:
            global_best = entry.get("best_lap_seconds")
            if isinstance(global_best, (int, float)) and float(global_best) > 0:
                candidate = float(global_best)

        if candidate is None:
            continue

        if best_seconds is None or candidate < best_seconds:
            best_seconds = candidate
            best_name = name

    summary.rival_best_name = best_name
    summary.rival_best_seconds = best_seconds


def _load_setup_label(setup_id: str, session_logs_dir: str) -> str | None:
    setups_dir = Path(session_logs_dir) / "setups"
    if not setups_dir.exists() or not setup_id:
        return None

    setup_doc = setups_dir / f"{setup_id}setup.txt"
    if not setup_doc.exists() or not setup_doc.is_file():
        return None

    try:
        with open(setup_doc, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if line.lower().startswith("label:"):
                    label = line.split(":", 1)[1].strip()
                    return label if label else None
    except Exception:
        return None
    return None
