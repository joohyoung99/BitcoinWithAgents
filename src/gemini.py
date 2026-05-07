from __future__ import annotations

import inspect
import os
import time
from typing import Any

import dotenv
from google import genai

from src.db import log_llm

dotenv.load_dotenv()

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        project = os.getenv("VERTEX_PROJECT_ID", "")
        location = os.getenv("VERTEX_LOCATION", "us-central1")
        _client = genai.Client(vertexai=True, project=project, location=location)
    return _client


def _caller_module() -> str:
    for frame_info in inspect.stack():
        mod = frame_info[0].f_globals.get("__name__", "")
        if mod and mod != __name__ and not mod.startswith("_"):
            return mod.split(".")[-1]
    return "unknown"


class _Model:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def generate_content(self, prompt: str) -> Any:
        t0 = time.time()
        try:
            result = _get_client().models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
            duration_ms = int((time.time() - t0) * 1000)
            log_llm(
                module=_caller_module(),
                model=self._model_name,
                prompt=prompt,
                response_text=result.text or "",
                duration_ms=duration_ms,
                success=True,
            )
            return result
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            log_llm(
                module=_caller_module(),
                model=self._model_name,
                prompt=prompt,
                response_text=str(e),
                duration_ms=duration_ms,
                success=False,
            )
            raise


def get_model(model_name: str) -> _Model:
    return _Model(model_name)
