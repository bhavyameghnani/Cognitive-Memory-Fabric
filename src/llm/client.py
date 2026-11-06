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

import contextvars
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMTimeoutError(LLMError):
    """The provider did not answer within the time it was given (a native socket timeout, derived from the request budget when the harness is in front)."""


class ContextLengthError(LLMError):
    """The provider refused the prompt because it exceeds the model's context window.  Distinct from an ordinary provider failure: a smaller prompt can succeed."""


class PartialResponseError(LLMError):
    """The provider stopped on its output limit (finish reason 'length'/'max_tokens'): the text is INCOMPLETE and must never be used as an answer."""


class StructuredOutputError(LLMError):
    pass


# ---- harness side channel (backward compatible: complete() still returns a plain string) ---------------------------------
ATTEMPT: contextvars.ContextVar[int] = contextvars.ContextVar("cmf_llm_attempt", default=0)
"""0 for the first model call of a structured operation, 1.. for its validation retries (set by generate_structured)."""
CALL_TIMEOUT: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar("cmf_llm_call_timeout", default=None)
"""Seconds this ONE provider call may take, derived by the harness from the remaining request budget.  Providers that can enforce a timeout natively use
min(their own timeout, this).  None (no harness) leaves the provider's own timeout untouched."""
CALL_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("cmf_llm_call_id", default=None)
"""Correlation id of the provider call in flight; a provider that supports cancellation (`cancel_call(call_id) -> bool`) can map it to its connection."""
TRUNCATED_FINISH = ("length", "max_tokens")
_CONTEXT_PAT = re.compile(r"context[_ ]length|maximum context|context window|too many tokens|prompt is too long|input length exceeds|exceeds the (?:model|context)", re.I)


def call_timeout(own: float) -> float:
    """The timeout a provider should apply to this call: its own, never longer than the harness-derived per-call budget."""
    limit = CALL_TIMEOUT.get()
    return own if limit is None else max(0.001, min(own, limit))


def _is_timeout(e: BaseException) -> bool:
    if isinstance(e, TimeoutError):
        return True
    reason = getattr(e, "reason", None)
    return isinstance(reason, TimeoutError) or "timed out" in str(e).lower()


@dataclass
class LLMUsage:
    """Token usage reported by the provider for ONE complete() call.  exact=True means the provider reported it."""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    exact: bool = True
    finish_reason: Optional[str] = None     # the provider's stop reason ('stop', 'length', ...); 'length'/'max_tokens' means the text is truncated


class _UsageMixin:
    """Per-thread record of the last call's usage; read it with pop_usage() from the SAME thread that called complete()."""

    def _set_usage(self, usage: Optional[LLMUsage]) -> None:
        tl = self.__dict__.setdefault("_usage_tl", threading.local())
        tl.usage = usage

    def pop_usage(self) -> Optional[LLMUsage]:
        tl = self.__dict__.get("_usage_tl")
        usage = getattr(tl, "usage", None) if tl is not None else None
        if tl is not None:
            tl.usage = None
        return usage


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
class OllamaLLM(_UsageMixin):
    model: str = field(default_factory=lambda: os.environ.get("CMF_LLM_MODEL", "qwen2.5:3b"))
    base_url: str = field(default_factory=lambda: os.environ.get("CMF_LLM_BASE_URL", "http://localhost:11434"))
    temperature: float = field(default_factory=lambda: float(os.environ.get("CMF_LLM_TEMPERATURE", "0")))
    num_ctx: int = field(default_factory=lambda: int(os.environ.get("CMF_LLM_NUM_CTX", "6144")))
    max_tokens: int = field(default_factory=lambda: int(os.environ.get("CMF_LLM_MAX_TOKENS", "700")))
    timeout: float = field(default_factory=lambda: float(os.environ.get("CMF_LLM_TIMEOUT", "900")))
    calls: int = 0
    native_timeout = True       # urllib applies a socket timeout (min of this and the harness-derived CALL_TIMEOUT); there is no cooperative cancel_call

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
            with urllib.request.urlopen(req, timeout=call_timeout(self.timeout)) as r:
                out = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (400, 413) and _CONTEXT_PAT.search(detail):
                raise ContextLengthError(f"LLM endpoint rejected the prompt as too long (HTTP {e.code})") from e
            raise LLMError(f"LLM endpoint HTTP {e.code}: {detail[:200]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if _is_timeout(e):
                raise LLMTimeoutError(f"LLM endpoint {self.base_url} did not answer in time") from e
            raise LLMError(f"LLM endpoint {self.base_url} unavailable: {e}") from e
        self.calls += 1
        finish = out.get("done_reason")
        if "prompt_eval_count" in out or "eval_count" in out or finish:  # Ollama reports exact token counts
            exact = "prompt_eval_count" in out or "eval_count" in out
            self._set_usage(LLMUsage(int(out.get("prompt_eval_count") or 0), int(out.get("eval_count") or 0), 0, exact, finish))
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


def _ssl_context():
    """python.org builds on macOS ship without CA certificates; use certifi's bundle when available."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


@dataclass
class OpenAILLM(_UsageMixin):
    """OpenAI Chat Completions (JSON mode). The key is read ONLY from the environment (OPENAI_API_KEY or CMF_LLM_API_KEY); never stored or logged.
    The pydantic schema is given in the prompt (strict json_schema mode would reject optional fields); generate_structured validates and retries.
    CMF_OPENAI_MODEL (default gpt-4o-mini), CMF_OPENAI_BASE_URL (default https://api.openai.com/v1)."""
    model: str = field(default_factory=lambda: os.environ.get("CMF_OPENAI_MODEL", "gpt-4o-mini"))
    base_url: str = field(default_factory=lambda: os.environ.get("CMF_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    temperature: float = field(default_factory=lambda: float(os.environ.get("CMF_LLM_TEMPERATURE", "0")))
    max_tokens: int = field(default_factory=lambda: int(os.environ.get("CMF_LLM_MAX_TOKENS", "1500")))
    timeout: float = field(default_factory=lambda: float(os.environ.get("CMF_LLM_TIMEOUT", "120")))
    calls: int = 0
    native_timeout = True       # see OllamaLLM

    def complete(self, agent: str, system: str, user: str, schema: Optional[dict] = None) -> str:
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CMF_LLM_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY is not set")
        sys_msg = system + (("\n\nReturn ONLY a JSON object that conforms to this JSON schema:\n" + json.dumps(schema)) if schema else "")
        body: dict[str, Any] = {"model": self.model, "temperature": self.temperature, "max_tokens": self.max_tokens,
                                "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]}
        if schema is not None:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=call_timeout(self.timeout), context=_ssl_context()) as r:
                out = json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code in (400, 413) and _CONTEXT_PAT.search(detail):
                raise ContextLengthError(f"OpenAI rejected the prompt as too long (HTTP {e.code})") from e
            raise LLMError(f"OpenAI HTTP {e.code}: {detail[:200]}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if _is_timeout(e):
                raise LLMTimeoutError("OpenAI endpoint did not answer in time") from e
            raise LLMError(f"OpenAI endpoint unavailable: {e}") from e
        self.calls += 1
        u = out.get("usage") or {}
        finish = (out["choices"][0].get("finish_reason") if out.get("choices") else None)
        if u or finish:  # prompt_tokens_details.cached_tokens is only present when the provider actually served a cached prefix
            cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            self._set_usage(LLMUsage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), cached, bool(u), finish))
        return out["choices"][0]["message"]["content"]


def make_llm() -> LLMClient:
    backend = os.environ.get("CMF_LLM_BACKEND", "ollama")
    if backend == "mock":
        return MockLLM()
    if backend == "openai":
        return OpenAILLM()
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
        ATTEMPT.set(attempt)
        try:
            raw = llm.complete(agent, system, prompt, schema)
        finally:
            ATTEMPT.set(0)
        if tracer:
            tracer.event(agent, "llm_call", attempt=attempt, model=llm.model, ms=int((time.perf_counter() - t0) * 1000), chars=len(raw))
        try:
            obj = model_cls.model_validate(parse_json(raw))
            if validator:
                validator(obj)
            getattr(llm, "accept_last", lambda: None)()   # optional hook: lets a caching wrapper store only VALIDATED output
            return obj
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            getattr(llm, "reject_last", lambda: None)()
            last = str(e)[:600]
            if tracer:
                tracer.event(agent, "llm_output_invalid", attempt=attempt, error=last[:300])
            prompt = f"{user}\n\nYour previous answer was rejected: {last}\nReturn only corrected JSON that satisfies the schema."
    raise StructuredOutputError(f"{agent}: no valid structured output after {retries + 1} attempts: {last}")
