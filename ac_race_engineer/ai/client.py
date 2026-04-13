from __future__ import annotations

import json
import os
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
    """

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.client = None
        if self.enabled:
            if not API_KEY:
                raise RuntimeError(
                    "OPENAI_API_KEY no esta configurada. Define la variable de entorno antes de usar el asistente."
                )
            self.client = OpenAI(api_key=API_KEY)

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

    def ask(self, user_text: str) -> str | None:
        if not self.enabled:
            # API desactivada: no devolver nada para que el mic no hable
            return None

        client = self._require_client()
        thread_id = self.get_or_create_thread()
        client.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_text)
        run = client.beta.threads.runs.create_and_poll(
            thread_id=thread_id, assistant_id=ASSISTANT_ID
        )
        if run.status != "completed":
            return "No se pudo completar la respuesta del asistente"

        messages = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=10)
        for msg in messages.data:
            if msg.role != "assistant":
                continue
            for block in msg.content:
                if hasattr(block, "text") and hasattr(block.text, "value"):
                    return str(block.text.value)
        return "Sin respuesta válida"

    def reset_thread(self) -> None:
        """Borra el hilo conversacional guardado, igual que reset_thread_id() de iris.py."""
        if os.path.exists(THREAD_FILE):
            os.remove(THREAD_FILE)
