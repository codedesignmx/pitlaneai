from __future__ import annotations

import time
from queue import Empty, PriorityQueue


class SpeechMessageQueue:
    def __init__(self) -> None:
        self._queue: PriorityQueue[tuple[int, int, str]] = PriorityQueue()
        self._seq = 0
        self._last_text = ""
        self._last_by_key: dict[str, float] = {}

    def push(self, text: str) -> None:
        msg = (text or "").strip()
        if not msg:
            return

        normalized = msg.lower()
        if normalized == self._last_text:
            return

        now = time.monotonic()
        key, cooldown_seconds = self._classify_cooldown_key(normalized)
        if key:
            prev = self._last_by_key.get(key)
            if prev is not None and (now - prev) < cooldown_seconds:
                return
            self._last_by_key[key] = now

        priority = self._classify_priority(normalized)
        qsize = self._queue.qsize()

        # Si hay backlog, descarta lo poco importante para no saturar.
        if qsize >= 12 and priority >= 2:
            return
        if qsize >= 8 and priority >= 3:
            return

        self._seq += 1
        self._last_text = normalized
        self._queue.put((priority, self._seq, msg))

    def pop(self, timeout: float = 0.2) -> str | None:
        try:
            _, _, text = self._queue.get(timeout=timeout)
            return text
        except Empty:
            return None

    def clear(self) -> int:
        removed = 0
        while True:
            try:
                self._queue.get_nowait()
                removed += 1
            except Empty:
                break
        return removed

    @staticmethod
    def _classify_priority(normalized: str) -> int:
        # 0 = crítico, 1 = alto, 2 = normal, 3 = bajo
        critical_tokens = (
            "colisión",
            "collision",
            "nueva mejor vuelta",
            "récord de sesión",
            "record de sesión",
            "final de",
            "finalizamos",
        )
        if any(token in normalized for token in critical_tokens):
            return 0

        high_tokens = (
            "paso por meta",
            "salida de pits",
            "box box",
            "consumo",
            "combustible necesario",
        )
        if any(token in normalized for token in high_tokens):
            return 1

        low_tokens = (
            "buen ritmo",
            "perdiste ritmo",
            "líder",
            "estamos a",
            "delante,",
            "combustible para",
        )
        if any(token in normalized for token in low_tokens):
            return 3

        return 2

    @staticmethod
    def _classify_cooldown_key(normalized: str) -> tuple[str, float]:
        if " mejora. posición " in normalized or " mejora. posicion " in normalized:
            return "standings_improve", 45.0
        if "consumo" in normalized or "combustible para" in normalized:
            return "fuel_status", 20.0
        if "líder" in normalized or "estamos a" in normalized or "delante," in normalized:
            return "timing_status", 15.0
        if "buen ritmo" in normalized or "perdiste ritmo" in normalized:
            return "pace_hint", 20.0
        if "paso por meta" in normalized:
            return "lap_call", 8.0
        return "", 0.0
