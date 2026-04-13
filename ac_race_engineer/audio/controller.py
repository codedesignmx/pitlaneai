from __future__ import annotations

import threading
import time

try:
    import pygame
    _PYGAME_AVAILABLE = True
except ImportError:
    _PYGAME_AVAILABLE = False


class ControllerMonitor:
    """Monitorea un botón de gamepad/joystick para activación push-to-talk.

    Corre un hilo de fondo que detecta el flanco de subida del botón
    configurado y notifica a MicrophoneListener para que inicie la escucha.

    Si el control no se detecta, `available` queda en False y el asistente
    cae automáticamente al modo de palabra clave 'Radio Check'.

    Uso rápido para detectar qué número tiene tu botón:
        Python: from ac_race_engineer.audio.controller import print_button_map; print_button_map()
    """

    def __init__(
        self,
        joystick_index: int = 0,
        button_index: int = 0,
        poll_hz: float = 60.0,
    ) -> None:
        self._joystick_index = joystick_index
        self._button_index = button_index
        self._poll_interval = 1.0 / max(poll_hz, 1.0)
        self._pressed_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._available = False
        self._current_pressed = False

    @property
    def available(self) -> bool:
        """True si el control fue detectado e inicializado correctamente."""
        return self._available

    @property
    def is_button_held(self) -> bool:
        """True mientras el botón PTT esté físicamente presionado."""
        return self._current_pressed

    def wait_for_press(self, timeout: float | None = None) -> bool:
        """Bloquea hasta detectar una pulsación. Devuelve True si fue pulsado."""
        self._pressed_event.clear()
        return self._pressed_event.wait(timeout=timeout)

    def start(self) -> None:
        if not _PYGAME_AVAILABLE:
            print("[CTL] pygame no está instalado; modo PTT no disponible.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ControllerMonitor"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Hilo de fondo
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            if not pygame.get_init():
                pygame.init()
            if not pygame.joystick.get_init():
                pygame.joystick.init()

            count = pygame.joystick.get_count()
            if count == 0 or self._joystick_index >= count:
                print(
                    f"[CTL] Control no encontrado en índice {self._joystick_index}. "
                    f"Controles detectados: {count}. "
                    "Modo PTT desactivado; usa 'Radio Check' por voz."
                )
                return

            joy = pygame.joystick.Joystick(self._joystick_index)
            joy.init()
            self._available = True
            print(
                f"[CTL] Control listo: '{joy.get_name()}' "
                f"({joy.get_numbuttons()} botones). "
                f"Botón PTT asignado: {self._button_index}. "
                "Pulsa para hablar."
            )

            prev_pressed = False
            while not self._stop_event.is_set():
                try:
                    pygame.event.pump()
                    pressed = bool(joy.get_button(self._button_index))
                    self._current_pressed = pressed
                    if pressed and not prev_pressed:  # flanco de subida
                        self._pressed_event.set()
                    prev_pressed = pressed
                except Exception:
                    pass
                time.sleep(self._poll_interval)

        except Exception as exc:
            print(f"[CTL] Error al inicializar el control: {exc}")


# ------------------------------------------------------------------
# Utilidad de diagnóstico: muestra qué botón se está pulsando
# ------------------------------------------------------------------

def print_button_map(joystick_index: int = 0, duration_seconds: float = 15.0) -> None:
    """Imprime en consola qué botón del control se pulsa.

    Útil para descubrir el número correcto antes de configurar `ptt_button_index`.
    Ejecuta durante `duration_seconds` segundos y luego termina.

    Ejemplo de uso desde terminal:
        python -c "from ac_race_engineer.audio.controller import print_button_map; print_button_map()"
    """
    if not _PYGAME_AVAILABLE:
        print("pygame no está instalado.")
        return

    pygame.init()
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    if count == 0 or joystick_index >= count:
        print(f"Sin controles detectados (índice {joystick_index}). Detectados: {count}")
        return

    joy = pygame.joystick.Joystick(joystick_index)
    joy.init()
    print(f"Control: '{joy.get_name()}' — {joy.get_numbuttons()} botones")
    print(f"Pulsa botones durante {duration_seconds}s para ver su número...\n")

    deadline = time.time() + duration_seconds
    prev = [False] * joy.get_numbuttons()
    while time.time() < deadline:
        pygame.event.pump()
        for i in range(joy.get_numbuttons()):
            state = bool(joy.get_button(i))
            if state and not prev[i]:
                print(f"  → Botón {i} pulsado")
            prev[i] = state
        time.sleep(0.016)

    print("\nDiagnóstico terminado. Usa ese número en config.py → ptt_button_index.")
    pygame.quit()
