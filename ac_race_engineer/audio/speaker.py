from __future__ import annotations

import os
import time
import threading

import pygame
from gtts import gTTS

from ac_race_engineer.audio.queue import SpeechMessageQueue

AUDIO_DIR = "audio_tmp"
os.makedirs(AUDIO_DIR, exist_ok=True)

VOLUME_PRESETS: dict[str, float] = {
    "bajo": 1.00,
    "medio": 1.50,
    "alto": 2.00,
}


class Speaker:
    """Reproduce mensajes de voz usando gTTS + pygame, igual que iris.py.

    speak() puede ser llamado directamente (desde el micrófono) o indirectamente
    a través de la cola interna de eventos de telemetría. Ambas rutas comparten
    el mismo lock para no superponerse.
    """

    def __init__(
        self,
        queue: SpeechMessageQueue,
        lang: str = "es",
        volume_multiplier: float = 1.0,
    ) -> None:
        self._queue = queue
        self._lang = lang
        self._volume_multiplier = max(0.0, float(volume_multiplier))
        self._volume_preset = self._infer_preset(self._volume_multiplier)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._talk_lock = threading.Lock()
        self.is_speaking = False
        pygame.mixer.init()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Public speak — idéntico al hablar() de iris.py
    # ------------------------------------------------------------------

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        with self._talk_lock:
            self.is_speaking = True
            try:
                mp3_path = os.path.join(AUDIO_DIR, f"msg_{int(time.time() * 1000)}.mp3")
                tts = gTTS(text=text, lang=self._lang, slow=False)
                tts.save(mp3_path)

                played = False
                if self._volume_multiplier > 1.0:
                    played = self._play_with_gain_boost(mp3_path, self._volume_multiplier)

                if not played:
                    pygame.mixer.music.load(mp3_path)
                    pygame.mixer.music.set_volume(min(self._volume_multiplier, 1.0))
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        pygame.time.Clock().tick(20)

                try:
                    pygame.mixer.music.stop()
                    if hasattr(pygame.mixer.music, "unload"):
                        pygame.mixer.music.unload()
                except Exception:
                    pass

                time.sleep(0.05)
                try:
                    os.remove(mp3_path)
                except PermissionError:
                    time.sleep(0.2)
                    try:
                        os.remove(mp3_path)
                    except Exception:
                        print(f"[AUDIO] No se pudo eliminar '{os.path.basename(mp3_path)}'")
            except Exception as exc:
                print(f"[AUDIO] Error al reproducir: {exc}")
            finally:
                self.is_speaking = False

    def _play_with_gain_boost(self, file_path: str, gain: float) -> bool:
        """Try software amplification for gain > 1.0 using numpy + sndarray.

        Returns True if playback happened via amplified buffer, otherwise False.
        """
        try:
            import numpy as np

            sound = pygame.mixer.Sound(file_path)
            arr = pygame.sndarray.array(sound)
            dtype = arr.dtype
            info = np.iinfo(dtype)
            boosted = np.clip(arr.astype(np.float32) * gain, info.min, info.max).astype(dtype)
            boosted_sound = pygame.sndarray.make_sound(boosted)
            channel = boosted_sound.play()
            while channel is not None and channel.get_busy():
                pygame.time.Clock().tick(20)
            return True
        except Exception:
            return False

    def set_volume_preset(self, preset: str) -> bool:
        key = (preset or "").strip().lower()
        if key not in VOLUME_PRESETS:
            return False
        self._volume_preset = key
        self._volume_multiplier = VOLUME_PRESETS[key]
        return True

    def get_volume_preset(self) -> str:
        return self._volume_preset

    @staticmethod
    def _infer_preset(value: float) -> str:
        nearest = min(VOLUME_PRESETS.items(), key=lambda item: abs(item[1] - value))
        return nearest[0]

    # ------------------------------------------------------------------
    # Background thread — consume la cola de eventos de telemetría
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            message = self._queue.pop(timeout=0.2)
            if message is not None:
                self.speak(message)
