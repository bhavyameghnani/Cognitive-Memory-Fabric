"""Embedding backends for hybrid memory retrieval (no new dependencies; stdlib only).

Backends (env CMF_EMBED_BACKEND; unset = none, so retrieval is unchanged unless you opt in):
    hash    offline, deterministic hashed word + character-trigram vectors.  Matches morphological variants
            ("retire"/"retirement"/"retiring") but NOT true synonyms; it is a safe fallback, not a semantic model.
    ollama  real embeddings from a local Ollama server (CMF_EMBED_MODEL, default nomic-embed-text; CMF_LLM_BASE_URL)
    openai  real embeddings from OpenAI (CMF_EMBED_MODEL, default text-embedding-3-small; OPENAI_API_KEY from the environment only)
    none    (default) disable embeddings; retrieval ranks by certainty weight + keyword overlap exactly as before

Vectors are L2-normalised so cosine similarity is a dot product.  Results are cached in-process by (model, text hash).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from typing import Optional, Protocol, Sequence


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))        # vectors are already unit length


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Signed feature hashing of word unigrams (weight 1.0) and character trigrams (weight 0.5) into a fixed dimension."""

    def __init__(self, dim: int = 512) -> None:
        self.dim, self.name = dim, f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in _WORD.findall(text.lower()):
            self._add(v, "w:" + w, 1.0)
            p = f"^{w}$"
            for i in range(len(p) - 2):
                self._add(v, "c:" + p[i:i + 3], 0.5)
        return _norm(v)

    def _add(self, v: list[float], feat: str, weight: float) -> None:
        d = hashlib.blake2b(feat.encode(), digest_size=8).digest()
        idx = int.from_bytes(d[:4], "big") % self.dim
        v[idx] += weight if d[4] & 1 else -weight

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class OllamaEmbedder:
    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None, timeout: float = 60.0) -> None:
        self.model = model or os.environ.get("CMF_EMBED_MODEL", "nomic-embed-text")
        self.base_url = (base_url or os.environ.get("CMF_LLM_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.timeout, self.name = timeout, "ollama:" + self.model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        req = urllib.request.Request(self.base_url + "/api/embed", data=json.dumps({"model": self.model, "input": list(texts)}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            raise EmbeddingError(f"ollama embedding failed: {e}") from e
        vecs = out.get("embeddings")
        if not vecs or len(vecs) != len(texts):
            raise EmbeddingError("ollama returned an unexpected embedding payload")
        return [_norm(v) for v in vecs]


class OpenAIEmbedder:
    def __init__(self, model: Optional[str] = None, timeout: float = 60.0) -> None:
        self.model = model or os.environ.get("CMF_EMBED_MODEL", "text-embedding-3-small")
        self.timeout, self.name = timeout, "openai:" + self.model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise EmbeddingError("OPENAI_API_KEY is not set")
        req = urllib.request.Request("https://api.openai.com/v1/embeddings", data=json.dumps({"model": self.model, "input": list(texts)}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            raise EmbeddingError(f"openai embedding failed: {type(e).__name__}") from e     # never echo the request (it carries the key)
        data = sorted(out.get("data", []), key=lambda d: d.get("index", 0))
        if len(data) != len(texts):
            raise EmbeddingError("openai returned an unexpected embedding payload")
        return [_norm(d["embedding"]) for d in data]


class CachedEmbedder:
    """Wraps a backend with an in-process cache so each distinct text is embedded once."""

    def __init__(self, inner: Embedder, max_items: int = 20000) -> None:
        self.inner, self.name, self.max_items = inner, inner.name, max_items
        self._cache: dict[tuple[str, str], list[float]] = {}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        keys = [(self.name, hashlib.sha1(t.encode()).hexdigest()) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self._cache]
        if missing:
            for i, v in zip(missing, self.inner.embed([texts[i] for i in missing])):
                if len(self._cache) >= self.max_items:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[keys[i]] = v
        return [self._cache[k] for k in keys]


_DEFAULT: dict[str, CachedEmbedder] = {}


def default_embedder() -> Optional[Embedder]:
    """Embedder selected by CMF_EMBED_BACKEND (None when it is 'none').  One cached instance per backend setting."""
    backend = os.environ.get("CMF_EMBED_BACKEND", "none").lower()
    if backend == "none":
        return None
    if backend not in _DEFAULT:
        inner: Embedder
        if backend == "ollama":
            inner = OllamaEmbedder()
        elif backend == "openai":
            inner = OpenAIEmbedder()
        elif backend == "hash":
            inner = HashingEmbedder()
        else:
            raise EmbeddingError(f"unknown CMF_EMBED_BACKEND '{backend}' (use hash|ollama|openai|none)")
        _DEFAULT[backend] = CachedEmbedder(inner)
    return _DEFAULT[backend]
