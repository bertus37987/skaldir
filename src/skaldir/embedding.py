"""Embedding providers for the memory layer.

``HashingEmbeddings`` is a dependency-free, deterministic fallback: it hashes
character trigrams into a fixed-dimension vector. It captures *lexical*
overlap only — no semantics. A real provider (API embeddings or a local
model) plugs in behind the same protocol; vector search degrades cleanly to
exact/full-text search when no provider is configured.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

DIM = 256


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbeddings:
    """Deterministic trigram-hashing embeddings (lexical overlap only)."""

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        normalized = " ".join(text.lower().split())
        if not normalized:
            return vector
        padded = f"  {normalized}  "
        for idx in range(len(padded) - 2):
            trigram = padded[idx : idx + 3]
            digest = hashlib.sha256(trigram.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
