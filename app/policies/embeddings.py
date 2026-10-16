"""Embedding providers.

``local`` (default): deterministic feature-hashing embeddings over unigrams and
bigrams. No external service, no large ML dependencies, stable across runs.
``openai``: any OpenAI-compatible ``/embeddings`` endpoint (requires an API key).
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re

import httpx

from app.config import get_settings

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "by",
        "it",
        "this",
        "that",
        "with",
        "as",
        "at",
        "from",
        "was",
        "were",
        "not",
        "no",
    ]
)


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def _local_embed(text: str, dims: int) -> list[float]:
    vec = [0.0] * dims
    toks = tokens(text)
    features = toks + [f"{a}_{b}" for a, b in itertools.pairwise(toks)]
    for feat in features:
        h = hashlib.md5(feat.encode(), usedforsecurity=False).digest()
        idx = int.from_bytes(h[:4], "little") % dims
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign * (1.0 if "_" not in feat else 0.5)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if settings.embedding_provider == "openai" and settings.llm_api_key:
        base = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        r = httpx.post(
            f"{base}/embeddings",
            timeout=30,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={"model": settings.embedding_model, "input": texts, "dimensions": settings.embedding_dimensions},
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]
    return [_local_embed(t, settings.embedding_dimensions) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
