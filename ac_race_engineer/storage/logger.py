from __future__ import annotations

import json
import os
from dataclasses import asdict

from ac_race_engineer.telemetry.models import Event, LapRecord


class SessionLogger:
    def __init__(self, log_dir: str = "logs") -> None:
        os.makedirs(log_dir, exist_ok=True)
        self._lap_file = open(os.path.join(log_dir, "laps.jsonl"), "a", encoding="utf-8")
        self._event_file = open(os.path.join(log_dir, "events.jsonl"), "a", encoding="utf-8")

    def log_lap(self, lap: LapRecord) -> None:
        self._lap_file.write(json.dumps(asdict(lap), ensure_ascii=False) + "\n")
        self._lap_file.flush()

    def log_event(self, event: Event) -> None:
        self._event_file.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        self._event_file.flush()

    def close(self) -> None:
        self._lap_file.close()
        self._event_file.close()
