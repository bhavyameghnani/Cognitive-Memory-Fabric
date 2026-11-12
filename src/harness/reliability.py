"""Reliability vocabulary shared by the Strands boundary, HarnessLLM, telemetry and the runtime (harness P1.3).

An INCIDENT is a structured record of something that went wrong around a model call.  Some incidents make the request's answer DEGRADED (it may be incomplete or built
by a fallback); a degraded request is never reported as a normal success - the report carries `validation["harness_runtime"]` and a limitation, and the harness summary
carries `outcome: "degraded"`.  Incidents contain agent / attempt / call id / counts and fixed vocabulary only - never prompts, report text, tokens or provider bodies.
"""
from __future__ import annotations

import re

from llm.client import LLMError

# kind -> does it make the answer degraded?
INCIDENT_KINDS = {
    "provider_timeout": True,            # the model call exceeded its time (native provider timeout, or the harness deadline with the worker abandoned)
    "provider_cancel_started": False,    # best-effort cancel_call was DISPATCHED to a bounded cancel thread (nothing is known about the provider work yet)
    "provider_cancel_ok": False,         # cancel_call returned True: the provider ACKNOWLEDGED the request.  It does NOT prove the provider work stopped
    "provider_cancel_failed": False,     # the provider tried to cancel and could not
    "provider_cancel_unsupported": False,  # the provider has no cancellation: the worker thread was abandoned and may still be running
    "provider_cancel_timeout": False,    # cancel_call did not return within the cancel timeout (its thread keeps its bounded slot until it exits)
    "provider_cancel_rejected": False,   # the process-wide cancel pool was full: no cancellation was attempted
    "worker_capacity_exhausted": True,   # the process-wide cap on live/abandoned provider workers was reached: the call was refused before the provider was contacted
    "ghost_call_late_result": False,     # an abandoned call finished later; its result was DISCARDED (informational: the timeout incident already degraded the request)
    "context_overflow": False,           # the prompt was refused as too long.  NOT degrading by itself: if recovery fails the budget records `context_overflow` as exhausted (degrading)
    "context_retry": False,              # a bounded compress-and-retry was attempted after an overflow
    "partial_response": True,            # the provider stopped on its output limit: the text is incomplete and was discarded
    "hook_missing": True,                # a mandatory Strands hook did not run
    "hook_failed": True,                 # a mandatory Strands hook raised
    "budget_exhausted": True,
}


class InstrumentationError(LLMError):
    """A mandatory harness control around a model call was missing or failed.  The call's output (if any) is DISCARDED: a partially instrumented execution is
    never a normal success.  kind is 'hook_missing' or 'hook_failed'."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"instrumentation failure: {kind}" + (f" ({detail})" if detail else ""))
        self.kind, self.detail = kind, detail


_SECRETS = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._\-]+|sk-[A-Za-z0-9_\-]{8,}|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*|(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+)")


def safe_error(e: BaseException, limit: int = 160) -> str:
    """Exception class + a short, secret-redacted message.  Used for telemetry; provider bodies can be long and may echo request data, so this is deliberately short."""
    msg = _SECRETS.sub("***", str(e)).replace("\n", " ")
    return f"{type(e).__name__}: {msg[:limit]}"
