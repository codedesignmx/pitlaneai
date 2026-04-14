from __future__ import annotations

import json
import os
import threading
import time
import warnings

from openai import OpenAI

# Mirrors iris.py values exactly, as requested.
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, message="The Assistants API is deprecated.*"
)

API_KEY = os.getenv("OPENAI_API_KEY", "")
ASSISTANT_ID = os.getenv("OPENAI_ASSISTANT_ID", "asst_god3XpwwwXnf5P19pXp5ZnaL")
THREAD_FILE = "thread_id.json"


class OpenAIAssistantClient:
    """Prepared client for future API integration.

    The MVP does not call the API by default. Keep this class as extension point.

    Thread-safe: serializa llamadas a ask() con un lock interno para evitar
    conflictos cuando múltiples hilos envían comandos al mismo thread de OpenAI.
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.client = None
        self._ask_lock = threading.Lock()  # Serializa ask() para evitar race conditions
        if self.enabled:
            if not API_KEY:
                print("[AI] ⚠️ OPENAI_API_KEY no disponible. Desactivando asistente.")
                self.enabled = False
                return
            try:
                self.client = OpenAI(api_key=API_KEY)
                print("[AI] ✅ Cliente OpenAI inicializado correctamente.")
            except Exception as e:
                print(f"[AI] ⚠️ Error al inicializar OpenAI: {e}")
                self.enabled = False
                self.client = None

    def _require_client(self) -> OpenAI:
        if self.client is None:
            raise RuntimeError("Cliente OpenAI no inicializado")
        return self.client

    def load_thread_id(self) -> str | None:
        if os.path.exists(THREAD_FILE):
            with open(THREAD_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("thread_id")
        return None

    def save_thread_id(self, thread_id: str) -> None:
        with open(THREAD_FILE, "w", encoding="utf-8") as f:
            json.dump({"thread_id": thread_id}, f)

    def get_or_create_thread(self) -> str:
        client = self._require_client()
        tid = self.load_thread_id()
        if tid:
            try:
                client.beta.threads.retrieve(tid)
                return tid
            except Exception:
                pass

        thread = client.beta.threads.create()
        self.save_thread_id(thread.id)
        return thread.id

    def _wait_for_active_runs(self, thread_id: str, timeout_seconds: float = 120.0) -> None:
        """Espera a que todos los runs activos se completen o los cancela."""
        client = self._require_client()
        deadline = time.time() + timeout_seconds
        last_status = None
        
        while time.time() < deadline:
            try:
                runs = client.beta.threads.runs.list(thread_id=thread_id, limit=1)
                if not runs.data:
                    # Sin runs activos
                    time.sleep(0.5)
                    return
                
                active_run = runs.data[0]
                
                # Log state change
                if active_run.status != last_status:
                    print(f"[AI] Run state: {active_run.status}")
                    last_status = active_run.status
                
                if active_run.status in ("completed", "failed", "cancelled", "expired"):
                    # Run terminó
                    time.sleep(0.5)  # Extra delay para asegurar que OpenAI actualiza el estado
                    return
                
                # Run en progreso, esperar
                time.sleep(1.0)
            except Exception as e:
                print(f"[AI] Error esperando runs previos: {e}")
                return
        
        # Si expira el timeout, intentar cancelar
        print(f"[AI] ⚠️ Timeout esperando run. Intentando cancelar...")
        try:
            runs = client.beta.threads.runs.list(thread_id=thread_id, limit=1)
            if runs.data and runs.data[0].status not in ("completed", "failed", "cancelled", "expired"):
                client.beta.threads.runs.cancel(thread_id=thread_id, run_id=runs.data[0].id)
                print(f"[AI] Run cancelado: {runs.data[0].id}")
                time.sleep(2.0)  # Esperar a que se procese la cancelación
        except Exception as e:
            print(f"[AI] Error cancelando run: {e}")

    def _wait_for_run_completion(self, thread_id: str, run_id: str, timeout_seconds: float = 60.0) -> str:
        """Espera a que un run se complete y retorna su estado final."""
        client = self._require_client()
        deadline = time.time() + timeout_seconds
        poll_interval = 0.5
        
        while time.time() < deadline:
            try:
                run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
                if run.status in ("completed", "failed", "cancelled", "expired"):
                    return run.status
                time.sleep(poll_interval)
            except Exception as e:
                print(f"[AI] Error esperando run {run_id}: {e}")
                return "error"
        
        # Timeout: intentar cancelar
        try:
            client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_id)
            print(f"[AI] Run {run_id} cancelado por timeout")
        except Exception:
            pass
        return "timeout"

    def ask(self, user_text: str) -> str | None:
        if not self.enabled:
            # API desactivada: no devolver nada para que el mic no hable
            return None

        # Lock: serializa acceso al thread para evitar conflictos
        # "Can't add messages to thread while a run is active"
        with self._ask_lock:
            client = self._require_client()
            thread_id = self.get_or_create_thread()
            
            # Esperar a que cualquier run previo termine o sea cancelado
            self._wait_for_active_runs(thread_id)
            
            # Retry logic: si falla al crear mensaje, reintentar
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Crear mensaje
                    client.beta.threads.messages.create(
                        thread_id=thread_id, 
                        role="user", 
                        content=user_text
                    )
                    break  # Éxito
                except Exception as e:
                    if "while a run" in str(e) and attempt < max_retries - 1:
                        print(f"[AI] Reintentando crear mensaje (intento {attempt + 1}/{max_retries})...")
                        time.sleep(2.0)
                        self._wait_for_active_runs(thread_id, timeout_seconds=60.0)
                        continue
                    else:
                        raise
            
            try:
                # Crear run y esperar manualmente a que se complete
                run = client.beta.threads.runs.create(
                    thread_id=thread_id, 
                    assistant_id=ASSISTANT_ID
                )
                
                # Esperar a que el run termine
                final_status = self._wait_for_run_completion(thread_id, run.id)
                
                if final_status != "completed":
                    return f"Run no se completó. Estado: {final_status}"

                # Obtener la respuesta del asistente
                messages = client.beta.threads.messages.list(
                    thread_id=thread_id, 
                    order="desc", 
                    limit=10
                )
                for msg in messages.data:
                    if msg.role != "assistant":
                        continue
                    for block in msg.content:
                        if hasattr(block, "text") and hasattr(block.text, "value"):
                            return str(block.text.value)
                return "Sin respuesta válida"
                
            except Exception as e:
                print(f"[AI] Excepción en ask(): {e}")
                raise

    def reset_thread(self) -> None:
        """Borra el hilo conversacional guardado, igual que reset_thread_id() de iris.py."""
        if os.path.exists(THREAD_FILE):
            os.remove(THREAD_FILE)
