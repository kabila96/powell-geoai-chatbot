"""Thin wrapper around the OpenAI SDK so the provider can be swapped in one place."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from openai import OpenAI

from app.config import settings


class MissingAPIKeyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-your"):
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=2,
    )


def embed_texts(texts: list[str], batch_size: int = 64) -> np.ndarray:
    """Return L2-normalised embeddings (float32, shape [n, dim])."""
    client = get_client()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = [t.replace("\n", " ")[:8000] for t in texts[start : start + batch_size]]
        response = client.embeddings.create(model=settings.embedding_model, input=batch)
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def chat_completion(messages: list[dict]) -> str:
    client = get_client()
    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        temperature=settings.temperature,
        max_tokens=settings.max_output_tokens,
    )
    return (response.choices[0].message.content or "").strip()
