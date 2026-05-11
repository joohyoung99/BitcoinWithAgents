from __future__ import annotations

import os
import time
from typing import Any

import dotenv
from google import genai

dotenv.load_dotenv()

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        project = os.getenv("VERTEX_PROJECT_ID", "")
        location = os.getenv("VERTEX_LOCATION", "us-central1")
        _client = genai.Client(vertexai=True, project=project, location=location)
    return _client


class _Model:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name

    def generate_content(self, prompt: str) -> Any:
        return _get_client().models.generate_content(
            model=self._model_name,
            contents=prompt,
        )


def get_model(model_name: str) -> _Model:
    return _Model(model_name)
