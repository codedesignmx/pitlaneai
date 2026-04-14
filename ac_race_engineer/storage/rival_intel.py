from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ac_race_engineer.storage.results_summary import StandingEntry


@dataclass(slots=True)
class RivalSessionStats:
    name: str
    best_lap_seconds: float | None = None
    seen_samples: int = 0
    position_sum: int = 0
    last_position: int = 0
    improvements: int = 0
    ahead_samples: int = 0
    behind_samples: int = 0

    def to_dict(self) -> dict[str, object]:
        avg_position = (self.position_sum / self.seen_samples) if self.seen_samples > 0 else None
        return {
            "name": self.name,
            "best_lap_seconds": self.best_lap_seconds,
            "seen_samples": self.seen_samples,
            "avg_position": avg_position,
            "last_position": self.last_position,
            "improvements": self.improvements,
            "ahead_samples": self.ahead_samples,
            "behind_samples": self.behind_samples,
        }


class RivalIntelStore:
    def __init__(self, session_output_dir: str = "session_logs/rivals", db_path: str = "database/rival_history.json") -> None:
        self.session_output_dir = Path(session_output_dir)
        self.session_output_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.active_track = "unknown"
        self.active_session_type = "unknown"
        self.active_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.active: dict[str, RivalSessionStats] = {}

    def begin_session(self, track_name: str, session_type: str, stamp: str | None = None) -> None:
        self.active_track = track_name or "unknown"
        self.active_session_type = session_type or "unknown"
        self.active_stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.active = {}

    def observe(self, standings: list[StandingEntry], player_position: int) -> None:
        if not standings:
            return

        for row in standings:
            if row.position <= 0 or not row.name:
                continue

            key = row.name.strip().lower()
            if not key:
                continue

            stats = self.active.get(key)
            if stats is None:
                stats = RivalSessionStats(name=row.name)
                self.active[key] = stats

            stats.seen_samples += 1
            stats.position_sum += row.position
            stats.last_position = row.position

            if row.best_lap_seconds is not None and row.best_lap_seconds > 0:
                if stats.best_lap_seconds is None:
                    stats.best_lap_seconds = row.best_lap_seconds
                elif row.best_lap_seconds < (stats.best_lap_seconds - 0.01):
                    stats.improvements += 1
                    stats.best_lap_seconds = row.best_lap_seconds

            if player_position > 0:
                if row.position < player_position:
                    stats.ahead_samples += 1
                elif row.position > player_position:
                    stats.behind_samples += 1

    def finalize_active_session(self) -> str | None:
        if not self.active:
            return None

        payload = {
            "timestamp": datetime.now().isoformat(),
            "session_type": self.active_session_type,
            "track": self.active_track,
            "session_stamp": self.active_stamp,
            "rivals": [
                stats.to_dict()
                for stats in sorted(
                    self.active.values(),
                    key=lambda s: (s.last_position if s.last_position > 0 else 9999, s.name.lower()),
                )
            ],
        }

        filename = f"rivals_{self.active_track}_{self.active_session_type}_{self.active_stamp}.json"
        out_path = self.session_output_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        self._merge_into_history(payload)
        return str(out_path)

    def _merge_into_history(self, payload: dict[str, object]) -> None:
        history = self._load_history()
        rivals_map = history.setdefault("rivals", {})

        track = str(payload.get("track") or "unknown")
        session_type = str(payload.get("session_type") or "unknown")
        session_rivals = payload.get("rivals", [])

        if not isinstance(session_rivals, list):
            return

        for item in session_rivals:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()

            entry = rivals_map.get(key)
            if not isinstance(entry, dict):
                entry = {
                    "name": name,
                    "sessions": 0,
                    "best_lap_seconds": None,
                    "tracks": {},
                    "session_types": {},
                }
                rivals_map[key] = entry

            entry["name"] = name
            entry["sessions"] = int(entry.get("sessions", 0)) + 1

            best_lap = item.get("best_lap_seconds")
            if isinstance(best_lap, (int, float)) and best_lap > 0:
                current_best = entry.get("best_lap_seconds")
                if not isinstance(current_best, (int, float)) or best_lap < float(current_best):
                    entry["best_lap_seconds"] = float(best_lap)

            tracks = entry.setdefault("tracks", {})
            if not isinstance(tracks, dict):
                tracks = {}
                entry["tracks"] = tracks
            track_entry = tracks.get(track)
            if not isinstance(track_entry, dict):
                track_entry = {"sessions": 0, "best_lap_seconds": None}
                tracks[track] = track_entry
            track_entry["sessions"] = int(track_entry.get("sessions", 0)) + 1
            if isinstance(best_lap, (int, float)) and best_lap > 0:
                tb = track_entry.get("best_lap_seconds")
                if not isinstance(tb, (int, float)) or best_lap < float(tb):
                    track_entry["best_lap_seconds"] = float(best_lap)

            sessions = entry.setdefault("session_types", {})
            if not isinstance(sessions, dict):
                sessions = {}
                entry["session_types"] = sessions
            st_entry = sessions.get(session_type)
            if not isinstance(st_entry, dict):
                st_entry = {"sessions": 0, "best_lap_seconds": None}
                sessions[session_type] = st_entry
            st_entry["sessions"] = int(st_entry.get("sessions", 0)) + 1
            if isinstance(best_lap, (int, float)) and best_lap > 0:
                sb = st_entry.get("best_lap_seconds")
                if not isinstance(sb, (int, float)) or best_lap < float(sb):
                    st_entry["best_lap_seconds"] = float(best_lap)

        history["updated_at"] = datetime.now().isoformat()
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

    def _load_history(self) -> dict[str, object]:
        if not self.db_path.exists():
            return {"updated_at": datetime.now().isoformat(), "rivals": {}}
        try:
            with open(self.db_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {"updated_at": datetime.now().isoformat(), "rivals": {}}
