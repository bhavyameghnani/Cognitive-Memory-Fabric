"""Token counting with an explicit exact / estimated distinction.

* OpenAI-family model names: tiktoken, when its vocabulary can be loaded (it downloads on first use; offline or behind a broken TLS store it cannot).
* Everything else (Ollama/Qwen, mock, tiktoken unavailable): a CONSERVATIVE estimate = ceil(utf-8 bytes / 3).  English averages about 4 bytes per
  token, so this deliberately over-counts by roughly a third; over-counting makes budgets trip early rather than late.
Every result says whether it is exact, so telemetry never presents an estimate as a measurement.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

BYTES_PER_TOKEN_ESTIMATE = 3.0
_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt", "text-embedding")
_encoders: dict = {}


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    exact: bool
    method: str   # "tiktoken:<encoding>" | "estimate:utf8_bytes/3"


def _tiktoken_encoder(model: str):
    if os.environ.get("HARNESS_TOKENIZER", "").lower() == "estimate" or not model.lower().startswith(_OPENAI_PREFIXES):
        return None
    if model in _encoders:
        return _encoders[model]
    enc = None
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
    except Exception:   # unknown model name, missing vocabulary file, no network: fall back to the estimate
        enc = None
    _encoders[model] = enc
    return enc


class TokenCounter:
    def __init__(self, model: str = "") -> None:
        self.model = model or ""
        self._enc = _tiktoken_encoder(self.model)

    @property
    def exact(self) -> bool:
        return self._enc is not None

    @property
    def method(self) -> str:
        return f"tiktoken:{self._enc.name}" if self._enc is not None else "estimate:utf8_bytes/3"

    def count(self, text: str) -> TokenCount:
        if not text:
            return TokenCount(0, self.exact, self.method)
        if self._enc is not None:
            return TokenCount(len(self._enc.encode(text, disallowed_special=())), True, self.method)
        return TokenCount(math.ceil(len(text.encode("utf-8")) / BYTES_PER_TOKEN_ESTIMATE), False, self.method)

    def n(self, text: str) -> int:
        return self.count(text).tokens


def for_model(model: Optional[str]) -> TokenCounter:
    return TokenCounter(model or "")
