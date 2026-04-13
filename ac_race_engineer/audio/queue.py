from __future__ import annotations

from queue import Empty, Queue


class SpeechMessageQueue:
    def __init__(self) -> None:
        self._queue: Queue[str] = Queue()

    def push(self, text: str) -> None:
        if text.strip():
            self._queue.put(text)

    def pop(self, timeout: float = 0.2) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            return None
