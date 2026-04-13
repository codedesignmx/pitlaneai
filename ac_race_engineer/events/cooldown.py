from __future__ import annotations

import time


class CooldownManager:
    def __init__(self, default_seconds: float = 10.0) -> None:
        self._default_seconds = default_seconds
        self._last_emit: dict[str, float] = {}

    def can_emit(self, key: str, seconds: float | None = None) -> bool:
        now = time.time()
        window = self._default_seconds if seconds is None else seconds
        last = self._last_emit.get(key)
        if last is None or (now - last) >= window:
            self._last_emit[key] = now
            return True
        return False
