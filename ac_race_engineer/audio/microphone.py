from __future__ import annotations

import re
import threading
import time

import speech_recognition as sr

from config import SETTINGS
from ac_race_engineer.ai.client import OpenAIAssistantClient
from ac_race_engineer.ai.prompt_builder import build_practice_prompt
from ac_race_engineer.analysis.setup_coach import SetupCoach
from ac_race_engineer.analysis.session_state import SessionState
from ac_race_engineer.audio.controller import ControllerMonitor
from ac_race_engineer.audio.speaker import Speaker

KEYWORD = "radio check"


class MicrophoneListener:
    """Escucha por micrófono con dos modos de activación:

    Modo PTT (botón de control):
    - Pulsa el botón configurado → el asistente escucha un comando y responde.
    - Requiere ControllerMonitor inicializado con el control detectado.

        Modo palabra clave:
    - Di 'Radio Check' para activar la escucha libre.
    - Di 'Cancelar Radio' para desactivarla.
    - Di 'Resetear hilo' para borrar el hilo conversacional.
    - Cualquier frase se manda al asistente de IA con contexto de sesión.

        Nota:
        - Si se pasa controller, el listener usa modo PTT estricto y NO entra
            en modo palabra clave aunque el control tarde en inicializar.
    """

    def __init__(
        self,
        speaker: Speaker,
        ai_client: OpenAIAssistantClient,
        session_state: SessionState | None = None,
        setup_coach: SetupCoach | None = None,
        controller: ControllerMonitor | None = None,
    ) -> None:
        self._speaker = speaker
        self._ai_client = ai_client
        self._session_state = session_state
        self._setup_coach = setup_coach
        self._controller = controller
        self._activated = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._assistant_lock = threading.Lock()
        self._assistant_request_counter = 0
        self._assistant_ignore_before = 0
        self._r = sr.Recognizer()
        self._r.energy_threshold = 3000

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

    # ------------------------------------------------------------------
    # Bucle principal (igual a escucha_microfono de iris.py)
    # ------------------------------------------------------------------

    def _run(self) -> None:
        if self._controller is not None:
            # Evita una carrera al arrancar: el monitor del control inicia en otro
            # hilo y puede tardar un instante en marcarse como disponible.
            init_deadline = time.time() + 2.0
            while (
                not self._controller.available
                and not self._stop_event.is_set()
                and time.time() < init_deadline
            ):
                time.sleep(0.05)

            if self._controller.available:
                print("[MIC] Modo PTT activo. Mantén pulsado el botón para transmitir.")
                self._run_ptt()
                return

            print(
                "[MIC] PTT habilitado pero no hay control disponible. "
                "No se activará escucha por voz libre."
            )
            while not self._stop_event.is_set():
                time.sleep(0.2)
            return

        with sr.Microphone() as source:
            self._r.adjust_for_ambient_noise(source, duration=1)
            print("[MIC] Modo palabra clave. Di 'Radio Check' para activar el asistente.")
            self._run_keyword(source)

    def _run_ptt(self) -> None:
        """Graba mientras el botón está sostenido; procesa al soltar."""
        import pyaudio  # ya instalado como dependencia de SpeechRecognition

        assert self._controller is not None

        RATE = 16000
        CHUNK = 512
        p = pyaudio.PyAudio()

        try:
            while not self._stop_event.is_set():
                # Esperar flanco de subida (timeout corto para revisar stop_event)
                if not self._controller.wait_for_press(timeout=0.4):
                    continue
                if self._stop_event.is_set():
                    break

                # Prioridad PTT: si está hablando, corta de inmediato para capturar comando.
                self._speaker.interrupt_current_speech(clear_queue=True)

                # Grabar mientras el botón esté sostenido
                frames: list[bytes] = []
                try:
                    stream = p.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK,
                    )
                    print("[MIC] PTT — transmitiendo...")
                    while self._controller.is_button_held and not self._stop_event.is_set():
                        data = stream.read(CHUNK, exception_on_overflow=False)
                        frames.append(data)
                    stream.stop_stream()
                    stream.close()
                    print("[MIC] PTT — fin de transmisión.")
                except Exception as exc:
                    print(f"[MIC] Error de captura: {exc}")
                    continue

                # Ignorar si el audio es demasiado corto (ruido de rebote)
                if len(frames) < 4:
                    continue

                # Reconocer y procesar
                try:
                    audio_data = sr.AudioData(b"".join(frames), RATE, 2)
                    text = self._r.recognize_google(audio_data, language="es-ES")
                    print(f"[MIC] PTT escuchó: '{text}'")
                    self._dispatch_command_async(text)
                except sr.UnknownValueError:
                    pass  # silencio o interferencia
                except sr.RequestError as exc:
                    print(f"[MIC] Error de reconocimiento: {exc}")
                    self._speaker.speak("Hubo un problema con el reconocimiento de voz.")
                except Exception as exc:
                    print(f"[MIC] Error inesperado procesando comando PTT: {exc}")
        finally:
            p.terminate()

    def _dispatch_command_async(self, text: str) -> None:
        """Procesa comandos sin bloquear el loop de PTT."""
        clean_text = (text or "").strip()
        if not clean_text:
            return

        def _worker() -> None:
            try:
                self._handle_command(clean_text)
            except Exception as exc:
                print(f"[MIC] Error en comando asíncrono: {exc}")

        threading.Thread(
            target=_worker,
            daemon=True,
            name="PTTCommandWorker",
        ).start()

    def _run_keyword(self, source: sr.Microphone) -> None:
        """Bucle principal en modo palabra clave (Radio Check)."""
        while not self._stop_event.is_set():
            # ---- Esperando palabra clave ----
            if not self._activated:
                try:
                    audio = self._r.listen(source, timeout=5, phrase_time_limit=4)
                    text = self._r.recognize_google(audio, language="es-ES")
                    print(f"[MIC] keyword escuché: '{text}'")
                    if self._is_radio_check_command(text):
                        self._activated = True
                        self._r.energy_threshold = max(self._r.energy_threshold, 4500)
                        print("[MIC] Asistente activado...")
                        self._speaker.speak("Loud and Clear")
                        briefing = self._build_radio_check_briefing()
                        if briefing:
                            print(f"[SPEAK] {briefing}")
                            self._speaker.speak(briefing)
                except sr.WaitTimeoutError:
                    pass
                except sr.UnknownValueError:
                    pass
                except sr.RequestError as exc:
                    print(f"[MIC] Error de reconocimiento: {exc}")
                continue

            # ---- Escucha libre ----
            if self._speaker.is_speaking:
                time.sleep(0.1)
                continue

            try:
                audio = self._r.listen(source, timeout=10, phrase_time_limit=7)
                text = self._r.recognize_google(audio, language="es-ES")
                print(f"[MIC] Escuchado: {text}")
                self._handle_command(text)
            except sr.WaitTimeoutError:
                pass
            except sr.UnknownValueError:
                pass  # ruido ambiente del juego
            except sr.RequestError as exc:
                print(f"[MIC] Error de reconocimiento: {exc}")
                self._speaker.speak("Hubo un problema con el reconocimiento de voz.")
            except Exception as exc:
                print(f"[MIC] Error inesperado procesando comando: {exc}")

    # ------------------------------------------------------------------
    # Manejo de comandos (igual a manejar_comando de iris.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_radio_check_command(text: str) -> bool:
        """Detecta radio check incluyendo variantes frecuentes de ASR."""
        normalized = (text or "").lower().strip()
        normalized = normalized.translate(str.maketrans("áéíóúü", "aeiouu"))
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()

        if not normalized:
            return False

        # En PTT suele recortarse a "radio"; lo aceptamos como activación.
        if normalized == "radio":
            return True

        if "radio check" in normalized:
            return True

        if not normalized.startswith("radio"):
            return False

        tail = normalized[len("radio") :].strip()
        if not tail:
            return True

        if tail in {
            "check",
            "chek",
            "chec",
            "che",
            "cheque",
            "sheck",
            "shrek",
            "tek",
            "tec",
            "tech",
        }:
            return True

        # Cubre recortes/fonética de ASR: "ch", "chee", "tec..."
        if len(tail) <= 5 and (
            tail.startswith("ch")
            or tail.startswith("sh")
            or tail.startswith("te")
        ):
            return True

        return False

    def _build_radio_check_briefing(self) -> str:
        if self._session_state is None:
            return ""

        state = self._session_state
        in_pit = bool(state.last_snapshot and state.last_snapshot.is_in_pit)
        if in_pit:
            briefing = state.build_objective_briefing(
                session_objectives=SETTINGS.session_objectives
            )
            if self._setup_coach is not None:
                setup_goal = self._setup_coach.build_objective_guidance(state)
                if setup_goal:
                    briefing = f"{briefing} {setup_goal}"

            # En pits, radio check entrega primero el briefing completo sin prefijos
            # extra para evitar duplicar objetivos/referencias históricas.
            if not state.objectives_intro_announced:
                state.objectives_intro_announced = True

            return briefing

        return state.build_radio_briefing()

    def _handle_command(self, text: str) -> None:
        t = text.lower().strip()

        # Prioridad radio: interrumpe TTS en curso y limpia backlog de telemetría.
        if t:
            self._speaker.interrupt_current_speech(clear_queue=True)

        # Detección de radio check - respuesta automática sin asistente
        if self._is_radio_check_command(t):
            self._ignore_pending_assistant_responses()
            print("[MIC] Radio check detectado.")
            print("[SPEAK] Loud and Clear")
            self._speaker.speak("Loud and Clear")
            briefing = self._build_radio_check_briefing()
            if briefing:
                print(f"[SPEAK] {briefing}")
                self._speaker.speak(briefing)
            return

        if "cancelar radio" in t:
            self._ignore_pending_assistant_responses()
            if self._controller is not None and self._controller.available:
                # En modo PTT no hay estado activo que cancelar
                return
            self._activated = False
            print("[MIC] Desactivando asistente.")
            print("[SPEAK] Cancelando")
            self._speaker.speak("Cancelando")
            return

        if self._is_briefing_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is not None:
                briefing_msg = self._session_state.build_radio_briefing()
                print(f"[SPEAK] {briefing_msg}")
                self._speaker.speak(briefing_msg)
            return

        if "resetear hilo" in t or "reiniciar hilo" in t or "borrar memoria" in t:
            self._ignore_pending_assistant_responses()
            self._ai_client.reset_thread()
            reset_msg = "He creado una nueva conversacion. La memoria previa fue reiniciada."
            print(f"[SPEAK] {reset_msg}")
            self._speaker.speak(reset_msg)
            return

        if "volumen" in t:
            self._ignore_pending_assistant_responses()
            if "bajo" in t:
                if self._speaker.set_volume_preset("bajo"):
                    print("[SPEAK] Volumen bajo activado")
                    self._speaker.speak("Volumen bajo activado")
                return
            if "medio" in t:
                if self._speaker.set_volume_preset("medio"):
                    print("[SPEAK] Volumen medio activado")
                    self._speaker.speak("Volumen medio activado")
                return
            if "alto" in t:
                if self._speaker.set_volume_preset("alto"):
                    print("[SPEAK] Volumen alto activado")
                    self._speaker.speak("Volumen alto activado")
                return
            print("[SPEAK] Usa volumen bajo, medio o alto")
            self._speaker.speak("Usa volumen bajo, medio o alto")
            return

        if self._is_position_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                pos_msg = "Sin telemetría todavía, no puedo confirmar posición."
                print(f"[SPEAK] {pos_msg}")
                self._speaker.speak(pos_msg)
                return
            pos_report = self._session_state.build_position_report()
            print(f"[SPEAK] {pos_report}")
            self._speaker.speak(pos_report)
            return

        if self._is_car_status_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                status_msg = "Sin telemetría todavía para revisar estado del auto."
                print(f"[SPEAK] {status_msg}")
                self._speaker.speak(status_msg)
                return
            status_report = self._session_state.build_car_status_report()
            print(f"[SPEAK] {status_report}")
            self._speaker.speak(status_report)
            return

        if self._is_objective_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                obj_msg = "Sin datos de sesión todavía para análisis objetivo."
                print(f"[SPEAK] {obj_msg}")
                self._speaker.speak(obj_msg)
                return
            obj_report = self._session_state.build_objective_report()
            if self._setup_coach is not None:
                setup_goal = self._setup_coach.build_objective_guidance(self._session_state)
                if setup_goal:
                    obj_report = f"{obj_report} {setup_goal}"
            print(f"[SPEAK] {obj_report}")
            self._speaker.speak(obj_report)
            return

        if self._is_session_objectives_query(t):
            self._ignore_pending_assistant_responses()
            state = self._session_state
            obj_set = state.active_objectives if state is not None else None
            if obj_set is not None:
                obj_set.evaluate(state, setup_coach=self._setup_coach)
                obj_msg = f"Objetivos de sesión: {obj_set.voice_summary()}"
            else:
                obj_msg = "Sin objetivos definidos para esta sesión todavía."
            print(f"[SPEAK] {obj_msg}")
            self._speaker.speak(obj_msg)
            return

        if self._is_session_summary_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                summary_msg = "Sin telemetría todavía para resumen de sesión."
                print(f"[SPEAK] {summary_msg}")
                self._speaker.speak(summary_msg)
                return
            summary_msg = self._session_state.build_session_summary()
            print(f"[SPEAK] {summary_msg}")
            self._speaker.speak(summary_msg)
            return

        if self._is_box_box_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                pit_msg = "Sin telemetría todavía para confirmar entrada a boxes."
                print(f"[SPEAK] {pit_msg}")
                self._speaker.speak(pit_msg)
                return
            setup_feedback = ""
            if self._setup_coach is not None:
                auto_setup = self._setup_coach.build_automatic_recommendation(self._session_state)
                if auto_setup:
                    setup_feedback = auto_setup
                else:
                    setup_feedback = self._setup_coach.build_setup_feedback(self._session_state)
            pit_msg = self._session_state.build_box_box_report(
                setup_feedback=setup_feedback,
                setup_coach=self._setup_coach,
            )
            print(f"[SPEAK] {pit_msg}")
            self._speaker.speak(pit_msg)
            return

        if self._is_setup_coach_start_query(t):
            self._ignore_pending_assistant_responses()
            if self._setup_coach is None:
                coach_msg = "Setup coach no disponible en esta versión."
                print(f"[SPEAK] {coach_msg}")
                self._speaker.speak(coach_msg)
                return
            session_type = (
                self._session_state.last_snapshot.session_type
                if self._session_state is not None and self._session_state.last_snapshot is not None
                else "unknown"
            )
            coach_msg = self._setup_coach.start(session_type=session_type)
            print(f"[SPEAK] {coach_msg}")
            self._speaker.speak(coach_msg)
            return

        if self._is_setup_coach_stop_query(t):
            self._ignore_pending_assistant_responses()
            if self._setup_coach is None:
                coach_msg = "Setup coach no disponible en esta versión."
                print(f"[SPEAK] {coach_msg}")
                self._speaker.speak(coach_msg)
                return
            coach_msg = self._setup_coach.stop()
            print(f"[SPEAK] {coach_msg}")
            self._speaker.speak(coach_msg)
            return

        if self._is_setup_feedback_query(t):
            self._ignore_pending_assistant_responses()
            if self._setup_coach is None:
                coach_msg = "Setup coach no disponible en esta versión."
                print(f"[SPEAK] {coach_msg}")
                self._speaker.speak(coach_msg)
                return
            coach_msg = self._setup_coach.process_feedback(t, session_state=self._session_state)
            print(f"[SPEAK] {coach_msg}")
            self._speaker.speak(coach_msg)
            return

        if self._is_rivals_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                rivals_msg = "Sin datos de rivales todavía."
                print(f"[SPEAK] {rivals_msg}")
                self._speaker.speak(rivals_msg)
                return
            rivals_report = self._session_state.build_rivals_report()
            print(f"[SPEAK] {rivals_report}")
            self._speaker.speak(rivals_report)
            return

        if self._is_microsector_query(t):
            self._ignore_pending_assistant_responses()
            if self._session_state is None:
                ms_msg = "Sin datos todavía para análisis por microsectores."
                print(f"[SPEAK] {ms_msg}")
                self._speaker.speak(ms_msg)
                return
            ms_report = self._session_state.build_microsector_report()
            print(f"[SPEAK] {ms_report}")
            self._speaker.speak(ms_report)
            return

        if t.startswith("radio") and len(t.split()) <= 2:
            self._ignore_pending_assistant_responses()
            hint_msg = "Comando de radio no reconocido. Di radio check o solo radio."
            print(f"[SPEAK] {hint_msg}")
            self._speaker.speak(hint_msg)
            return

        self._send_to_assistant(text)

    def _ignore_pending_assistant_responses(self) -> None:
        """Marca como descartables respuestas de IA enviadas antes del comando actual."""
        with self._assistant_lock:
            self._assistant_ignore_before = self._assistant_request_counter

    @staticmethod
    def _is_briefing_query(text: str) -> bool:
        return any(
            token in text
            for token in ("informe", "briefing", "situación general", "situacion general", "estado general")
        )

    @staticmethod
    def _is_objective_query(text: str) -> bool:
        if MicrophoneListener._is_session_objectives_query(text):
            return False
        return any(
            token in text
            for token in ("objetivo", "métricas", "metricas", "ritmo", "pace", "consumo", "fuel", "readiness", "listos")
        )

    @staticmethod
    def _is_session_objectives_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "mis objetivos",
                "objetivos de sesión",
                "cuáles son los objetivos",
                "cuales son los objetivos",
                "qué objetivos tengo",
                "que objetivos tengo",
                "resumen de objetivos",
            )
        )

    @staticmethod
    def _is_session_summary_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "resumen",
                "resumen de carrera",
                "resumen de sesión",
                "resumen de sesion",
                "resumen de práctica",
                "resumen de practica",
                "resumen de qualy",
                "resumen final",
                "repaso general",
            )
        )

    @staticmethod
    def _is_box_box_query(text: str) -> bool:
        normalized = (text or "").lower().strip()
        if normalized in {"box", "boxx", "vox", "bos", "box box", "boxbox", "box bo", "boxbo"}:
            return True
        return any(
            token in normalized
            for token in (
                "box box",
                "box bo",
                "voy a pits",
                "voy a boxes",
                "entro a pits",
                "entrando a pits",
                "entraré a pits",
                "voy por boxes",
            )
        )

    @staticmethod
    def _is_rivals_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "rivales",
                "reporte de rivales",
                "estado de rivales",
                "quien va adelante",
                "quién va adelante",
                "quien va detras",
                "quién va detrás",
            )
        )

    @staticmethod
    def _is_microsector_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "microsector",
                "microsectores",
                "sectores",
                "en que fallo",
                "en qué fallo",
                "donde pierdo",
                "dónde pierdo",
                "curvas",
                "trazada",
            )
        )

    @staticmethod
    def _is_setup_coach_start_query(text: str) -> bool:
        if any(
            token in text
            for token in (
                "detener setup coach",
                "terminar setup coach",
                "salir setup coach",
                "desactivar setup coach",
            )
        ):
            return False
        return any(
            token in text
            for token in (
                "setup coach",
                "iniciar setup",
                "inicia setup",
                "coach de setup",
                "ayuda setup",
            )
        )

    @staticmethod
    def _is_setup_coach_stop_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "detener setup coach",
                "terminar setup coach",
                "salir setup coach",
                "desactivar setup coach",
            )
        )

    @staticmethod
    def _is_setup_feedback_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "subvir",
                "sobrevir",
                "traccion",
                "tracción",
                "frenada",
                "punta",
                "rebota",
                "piano",
                "mejor",
                "mejoro",
                "mejoró",
                "va mejor",
                "mucho mejor",
                "empeoro",
                "empeoró",
                "peor",
                "igual",
                "funciono",
                "funcionó",
                "no sirvio",
                "no sirvió",
                "gano tiempo",
                "ganó tiempo",
                "mas estable",
                "más estable",
                "mas inestable",
                "más inestable",
                "perdio agarre",
                "perdió agarre",
                "no funciono",
                "no funcionó",
                "salio peor",
                "salió peor",
                "sin cambio",
                "sin cambios",
                "sin diferencia",
                "se siente mejor",
                "se siente igual",
                "mas o menos igual",
                "más o menos igual",
                "parecido",
                "feedback setup",
            )
        )

    @staticmethod
    def _is_position_query(text: str) -> bool:
        return any(token in text for token in ("lugar", "posicion", "posición", "puesto"))

    @staticmethod
    def _is_car_status_query(text: str) -> bool:
        return any(
            token in text
            for token in (
                "estado del auto",
                "estado del coche",
                "danos",
                "daños",
                "golpe",
                "colision",
                "colisión",
            )
        )

    def _send_to_assistant(self, text: str) -> None:
        """Envía texto al asistente en un hilo separado (no-bloqueante)."""
        with self._assistant_lock:
            self._assistant_request_counter += 1
            request_id = self._assistant_request_counter

        # Lanzar en hilo daemon para no bloquear captura de micrófono
        thread = threading.Thread(
            target=self._ask_assistant_blocking,
            args=(text, request_id),
            daemon=True,
        )
        thread.start()

    def _ask_assistant_blocking(self, text: str, request_id: int) -> None:
        """Ejecuta la llamada a OpenAI (bloqueante) en hilo separado."""
        # Anteponer contexto de sesion al mensaje del piloto
        if self._session_state is not None:
            context = build_practice_prompt(self._session_state)
            full_text = context + text
        else:
            full_text = text

        print(f"[AI] Enviando: {text}")
        try:
            response = self._ai_client.ask(full_text)
            if not response:
                print("[AI] ⚠️ Asistente no disponible. Configura OPENAI_API_KEY.")
                unavailable_msg = "Asistente no disponible. Configura tu API key de OpenAI."
                print(f"[SPEAK] {unavailable_msg}")
                self._speaker.speak(unavailable_msg)
                return
            # Eliminar referencias internas como iris.py hace con filtrar_referencias
            clean = re.sub(r"【.*?】", "", response or "").strip()
            with self._assistant_lock:
                should_discard = request_id <= self._assistant_ignore_before
            if should_discard:
                print("[AI] Respuesta descartada por comando local más reciente.")
                return
            print(f"[SPEAK] {clean}")
            self._speaker.speak(clean)
        except Exception as exc:
            print(f"[AI] Error: {exc}")
            error_msg = "Hubo un error al comunicarse con el asistente."
            print(f"[SPEAK] {error_msg}")
            self._speaker.speak(error_msg)
