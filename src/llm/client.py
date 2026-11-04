"""Shared LLM abstraction for all four agents (Qwen2.5 through a local Ollama server by default).

Configuration (environment variables, nothing hard-coded):
    CMF_LLM_BACKEND   ollama (default) | mock
    CMF_LLM_BASE_URL  default http://localhost:11434
    CMF_LLM_MODEL     default qwen2.5:3b
    CMF_LLM_API_KEY   optional bearer token for a protected endpoint (never logged)
    CMF_LLM_TEMPERATURE / CMF_LLM_NUM_CTX / CMF_LLM_MAX_TOKENS / CMF_LLM_TIMEOUT
Agents depend only on LLMClient.complete(); they never import an HTTP library.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class StructuredOutputError(LLMError):
    pass


class LLMClient(Protocol):
    model: str

    def complete(self, agent: str, system: str, user: str, schema: Optional[dict] = None) -> str: ...


def inline_refs(schema: dict) -> dict:
    """Flatten $defs/$ref so grammar-constrained decoding (llama.cpp) can use the pydantic schema."""
    defs = schema.get("$defs", {})

    def walk(n: Any) -> Any:
        if isinstance(n, dict):
            if "$ref" in n:
                return walk(defs[n["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in n.items() if k not in ("$defs", "title")}
        if isinstance(n, list):
            return [walk(v) for v in n]
        return n

    return walk(schema)


@dataclass
class OllamaLLM:
    model: str = field(default_factory=lambda: os.environ.get("CMF_LLM_MODEL", "qwen2.5:3b"))
    base_url: str = field(default_factory=lambda: os.environ.get("CMF_LLM_BASE_URL", "http://localhost:11434"))
    temperature: float = field(default_factory=lambda: float(os.environ.get("CMF_LLM_TEMPERATURE", "0")))
    num_ctx: int = field(default_factory=lambda: int(os.environ.get("CMF_LLM_NUM_CTX", "6144")))
    max_tokens: int = field(default_factory=lambda: int(os.environ.get("CMF_LLM_MAX_TOKENS", "700")))
    timeout: float = field(default_factory=lambda: float(os.environ.get("CMF_LLM_TIMEOUT", "900")))
    calls: int = 0

    def complete(self, agent: str, system: str, user: str, schema: Optional[dict] = None) -> str:
        body: dict[str, Any] = {"model": self.model, "stream": False, "keep_alive": "30m",
                                "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "num_predict": self.max_tokens},
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if schema is not None:
            body["format"] = schema
        headers = {"Content-Type": "application/json"}
        if os.environ.get("CMF_LLM_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["CMF_LLM_API_KEY"]
        req = urllib.request.Request(self.base_url.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise LLMError(f"LLM endpoint {self.base_url} unavailable: {e}") from e
        self.calls += 1
        return out["message"]["content"]


@dataclass
class MockLLM:
    """Deterministic stand-in for tests. handlers[agent] is a callable (system, user, schema) -> str|dict, or a list
    consumed in order. Every call is recorded in .calls."""
    handlers: dict[str, Any] = field(default_factory=dict)
    model: str = "mock-qwen"
    calls: list[dict] = field(default_factory=list)

    def complete(self, agent: str, system: str, user: str, schema: Optional[dict] = None) -> str:
        h = self.handlers.get(agent)
        if h is None:
            raise LLMError(f"MockLLM has no handler for agent {agent!r}")
        out = h.pop(0) if isinstance(h, list) else h(system, user, schema)
        if isinstance(out, Exception):
            raise out
        self.calls.append(dict(agent=agent, system=system, user=user))
        return out if isinstance(out, str) else json.dumps(out)


def make_llm() -> LLMClient:
    backend = os.environ.get("CMF_LLM_BACKEND", "ollama")
    if backend == "mock":
        return MockLLM()
    return OllamaLLM()


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json(text: str) -> Any:
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def generate_structured(llm: LLMClient, agent: str, system: str, user: str, model_cls: Type[T], *, retries: int = 2,
                        tracer=None, validator: Optional[Callable[[T], None]] = None) -> T:
    """Ask for JSON matching model_cls, parse, validate (schema + optional semantic validator), retry with the error."""
    schema = inline_refs(model_cls.model_json_schema())
    prompt, last = user, ""
    for attempt in range(retries + 1):
        t0 = time.perf_counter()
        raw = llm.complete(agent, system, prompt, schema)
        if tracer:
            tracer.event(agent, "llm_call", attempt=attempt, model=llm.model, ms=int((time.perf_counter() - t0) * 1000), chars=len(raw))
        try:
            obj = model_cls.model_validate(parse_json(raw))
            if validator:
                validator(obj)
            return obj
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            last = str(e)[:600]
            if tracer:
                tracer.event(agent, "llm_output_invalid", attempt=attempt, error=last[:300])
            prompt = f"{user}\n\nYour previous answer was rejected: {last}\nReturn only corrected JSON that satisfies the schema."
    raise StructuredOutputError(f"{agent}: no valid structured output after {retries + 1} attempts: {last}")
