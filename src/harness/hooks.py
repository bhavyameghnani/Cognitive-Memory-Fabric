"""Strands lifecycle hooks used by the harness (public Strands API: strands.plugins.Plugin + @hook, strands.hooks events).

NOTE: no `from __future__ import annotations` here.  Strands' @hook infers the event type from the parameter annotation at decoration time.

What is enforced through Strands (verified against strands-agents 1.57.1):
  BeforeModelCallEvent.cancel   -> the request budget refuses the model call BEFORE the provider is contacted.  Strands answers a cancelled call with a
                                   normal-looking assistant message, so the denial is also stored on CallState and re-raised by the executor; a denied
                                   call can therefore never be mistaken for model output.
  AfterModelCallEvent           -> records the stop reason and that the lifecycle completed (telemetry).
Strands' own invocation `limits` (turns / output_tokens / total_tokens) are applied by StrandsExecutor in strands_adapter.py.
"""
from strands.hooks import AfterModelCallEvent, BeforeModelCallEvent
from strands.plugins import Plugin, hook

from harness.budget import BudgetExceeded


class CmfBudgetPlugin(Plugin):
    name = "cmf-harness-budget"

    def __init__(self, guard, state) -> None:
        super().__init__()
        self.guard, self.state = guard, state

    # Both hooks are MANDATORY.  "Registered" is not "executed": each one leaves proof on CallState (before_ok / strands_after_called) that StrandsExecutor and the model
    # adapter verify, and a hook that raises FAILS CLOSED (the model call is cancelled and the failure is recorded as hook_error) instead of letting the call run uncharged.
    @hook
    def enforce_budget(self, event: BeforeModelCallEvent):
        self.state.before_called = True
        try:
            self.guard.before(self.state)
            self.state.before_ok = True
        except BudgetExceeded as e:
            self.state.denied = e
            event.cancel = f"harness denied the model call: {e}"
        except Exception as e:                                # noqa: BLE001 - a bug in a mandatory control must never let the call through
            self.state.hook_error = e
            event.cancel = "harness budget hook failed; the model call was not made"

    @hook
    def record_completion(self, event: AfterModelCallEvent):
        try:
            self.state.strands_after_called = True
            if event.stop_response is not None:
                self.state.stop_reason = event.stop_response.stop_reason
        except Exception as e:                                # noqa: BLE001
            self.state.hook_error = e
