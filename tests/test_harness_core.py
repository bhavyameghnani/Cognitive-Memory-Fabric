"""Harness core: configuration safety, the shared request budget (direct AND Strands transports), usage capture, compression, caching."""
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import AuthContext, BudgetExceeded, ConfigError, ContextOverflow, Harness, HarnessConfig  # noqa: E402
from harness.compression import classify, compress  # noqa: E402
from harness.token_counter import TokenCounter  # noqa: E402
from llm.client import LLMUsage, MockLLM, StructuredOutputError, generate_structured  # noqa: E402
from orchestration.tracing import Tracer  # noqa: E402


class Out(BaseModel):
    ok: bool


def runtime(cfg=None, actor="advisor-1", clients=("CLIENT-001",), harness=None, auth=None):
    h = harness or Harness(cfg or HarnessConfig.for_test())
    rt = h.start("REQ-T", Tracer("REQ-T", persist=False), auth or AuthContext.for_test(actor, "admin"), None)
    rt.client_ids = tuple(clients)
    return h, rt


class UsageClient(MockLLM):
    """MockLLM that reports provider usage, like OpenAILLM/OllamaLLM do."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self._u = None

    def complete(self, agent, system, user, schema=None):
        out = super().complete(agent, system, user, schema)
        self._u = LLMUsage(input_tokens=321, output_tokens=7, cached_tokens=256, exact=True)
        return out

    def pop_usage(self):
        u, self._u = self._u, None
        return u


# ------------------------------------------------------------------------------------------------------------------ configuration
def test_production_cannot_disable_safety_controls():
    cfg = HarnessConfig.from_env({"HARNESS_PERMISSION_GATE": "false", "HARNESS_EVIDENCE_VERIFICATION": "false", "HARNESS_MEMORY_WRITE_VALIDATION": "no",
                                  "HARNESS_APPROVAL_REQUIRED_FOR_HIGH_RISK": "0", "HARNESS_ENABLED": "false", "HARNESS_ALLOW_ANONYMOUS_DEV": "true"})
    assert cfg.profile == "production" and cfg.permission_gate and cfg.evidence_verification and cfg.memory_write_validation
    assert cfg.approval_required_for_high_risk and cfg.enabled and not cfg.allow_anonymous_dev
    assert len(cfg.warnings) >= 5


def test_missing_environment_is_safe_and_development_may_relax():
    cfg = HarnessConfig.from_env({})
    assert cfg.profile == "production" and cfg.permission_gate and cfg.cache_enabled and not cfg.allow_asserted_identity
    dev = HarnessConfig.from_env({"HARNESS_PROFILE": "development", "HARNESS_PERMISSION_GATE": "false"})
    assert dev.permission_gate is False and dev.allow_anonymous_dev


def test_invalid_configuration_is_rejected():
    for env in ({"HARNESS_MAX_LLM_CALLS": "0"}, {"HARNESS_MAX_LLM_CALLS": "many"}, {"HARNESS_PROFILE": "staging"}, {"HARNESS_CACHE_ENABLED": "maybe"},
                {"HARNESS_MAX_TOTAL_TOKENS": "10"}, {"HARNESS_RESERVED_OUTPUT_TOKENS": "999999"}):
        with pytest.raises(ConfigError):
            HarnessConfig.from_env(env)


# ------------------------------------------------------------------------------------------------------------------ token counting
def test_token_counter_flags_estimates_and_is_conservative():
    c = TokenCounter("qwen2.5:3b").count("x" * 300)
    assert c.exact is False and c.method.startswith("estimate") and c.tokens == 100
    assert TokenCounter("gpt-4o-mini").count("hello").exact == TokenCounter("gpt-4o-mini").exact   # exact only if tiktoken could load its vocabulary
    assert TokenCounter("qwen").count("").tokens == 0


# ------------------------------------------------------------------------------------------------------------------ shared budget
@pytest.mark.parametrize("strands", [True, False])
def test_global_llm_call_cap_spans_all_four_agents(strands):
    inner = MockLLM(handlers={a: (lambda s, u, sc: {"ok": True}) for a in ("planner", "memory", "tool_selector", "report")})
    h, rt = runtime(HarnessConfig.for_test(max_llm_calls=3, strands_execution=strands, cache_enabled=False))
    llm = rt.wrap_llm(inner)
    for a in ("planner", "memory", "tool_selector"):
        llm.complete(a, "sys", "user " + a)
    before = len(inner.calls)
    with pytest.raises(BudgetExceeded) as e:
        llm.complete("report", "sys", "user report")
    assert e.value.reason == "max_llm_calls" and len(inner.calls) == before        # the provider was never contacted
    assert rt.budget.llm_calls == 3 and rt.budget.exhausted[0]["reason"] == "max_llm_calls"
    assert rt.telemetry.summary()["denied"] == 1
    assert {r.transport for r in rt.telemetry.records if r.outcome == "ok"} == {"strands" if strands else "direct"}


@pytest.mark.parametrize("strands", [True, False])
def test_structured_output_retries_count_against_the_shared_budget(strands):
    inner = MockLLM(handlers={"report": ["not json", "still not json", "nope", {"ok": True}]})
    h, rt = runtime(HarnessConfig.for_test(max_retries=1, max_retries_per_operation=2, strands_execution=strands, cache_enabled=False))
    llm = rt.wrap_llm(inner)
    with pytest.raises(BudgetExceeded) as e:                                       # first call + 1 retry allowed, the 2nd retry is refused
        generate_structured(llm, "report", "sys", "usr", Out, retries=2)
    assert e.value.reason == "max_retries"
    assert rt.budget.llm_calls == 2 and rt.budget.retries == 1


def test_per_operation_retry_cap():
    inner = MockLLM(handlers={"report": ["x", "y", "z", {"ok": True}]})
    h, rt = runtime(HarnessConfig.for_test(max_retries=10, max_retries_per_operation=1, cache_enabled=False))
    with pytest.raises(BudgetExceeded) as e:
        generate_structured(rt.wrap_llm(inner), "report", "sys", "usr", Out, retries=2)
    assert e.value.reason == "max_retries_per_operation"


def test_runtime_deadline_uses_monotonic_time_and_blocks_calls():
    inner = MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}})
    h, rt = runtime(HarnessConfig.for_test(max_runtime_seconds=5.0, cache_enabled=False))
    llm = rt.wrap_llm(inner)
    llm.complete("planner", "s", "u1")
    rt.budget.started -= 10.0                       # ten seconds pass
    with pytest.raises(BudgetExceeded) as e:
        llm.complete("planner", "s", "u2")
    assert e.value.reason == "runtime_deadline" and len(inner.calls) == 1


def test_token_budget_is_enforced_before_the_call():
    inner = MockLLM(handlers={"report": lambda s, u, sc: {"ok": True}})
    h, rt = runtime(HarnessConfig.for_test(max_input_tokens=400, max_total_tokens=1000, cache_enabled=False))
    llm = rt.wrap_llm(inner)
    llm.complete("report", "s", "a" * 300)           # ~100 tokens
    llm.complete("report", "s", "b" * 300)
    llm.complete("report", "s", "c" * 300)
    with pytest.raises(BudgetExceeded) as e:
        llm.complete("report", "s", "d" * 900)
    assert e.value.reason == "max_input_tokens"


def test_provider_usage_is_captured_and_marked_exact():
    inner = UsageClient(handlers={"report": lambda s, u, sc: {"ok": True}})
    for strands in (True, False):
        h, rt = runtime(HarnessConfig.for_test(strands_execution=strands, cache_enabled=False))
        rt.wrap_llm(inner).complete("report", "s", "u")
        rec = rt.telemetry.records[-1]
        assert (rec.input_tokens, rec.output_tokens, rec.cached_tokens, rec.exact) == (321, 7, 256, True)
        assert rt.telemetry.summary()["provider_prompt_cache_reported"] is True
        assert rt.budget.input_tokens == 321


def test_estimated_usage_is_flagged_when_provider_reports_nothing():
    h, rt = runtime(HarnessConfig.for_test(cache_enabled=False))
    rt.wrap_llm(MockLLM(handlers={"report": lambda s, u, sc: {"ok": True}})).complete("report", "s", "u")
    s = rt.telemetry.summary()
    assert s["estimated_usage_calls"] == 1 and s["exact_usage_calls"] == 0 and s["provider_prompt_cache_reported"] is False


def test_strands_hook_cancels_the_model_call_before_the_provider():
    """Direct test of the Strands integration: BeforeModelCallEvent.cancel stops the model; the denial is not mistaken for an answer."""
    from harness.budget import RequestBudget
    from harness.strands_adapter import CallState, ModelCallGuard, StrandsExecutor
    inner = MockLLM(handlers={"report": lambda s, u, sc: {"ok": True}})
    budget = RequestBudget(HarnessConfig.for_test(max_llm_calls=1))
    budget.begin_llm_call("report", 0, 10)                                    # budget already spent
    ex = StrandsExecutor(inner, ModelCallGuard(budget), budget, TokenCounter("mock"))
    st = CallState("report", "sys", "usr", None, 0, 10)
    with pytest.raises(BudgetExceeded):
        ex.run(st)
    assert st.denied is not None and st.text is None and inner.calls == []


def test_strands_executor_returns_model_text_and_lifecycle_runs():
    from harness.budget import RequestBudget
    from harness.strands_adapter import CallState, ModelCallGuard, StrandsExecutor
    inner = MockLLM(handlers={"report": lambda s, u, sc: '{"ok": true}'})
    budget = RequestBudget(HarnessConfig.for_test())
    st = CallState("report", "sys", "usr", None, 0, 10)
    assert StrandsExecutor(inner, ModelCallGuard(budget), budget, TokenCounter("mock")).run(st) == '{"ok": true}'
    assert st.strands_after_called and st.stop_reason == "end_turn" and budget.llm_calls == 1


# ------------------------------------------------------------------------------------------------------------------ compression
def _prompt(n_ep=30):
    head = ["REQUEST: Why did Kenji's retirement age change, and was it never confirmed before 2026-05-01?",
            "CLIENTS: CLIENT-001", "TASK TYPE: history", "EVIDENCE:",
            "[E1 memory] Current knowledge; retirement_age; value 63; active/confirmed; valid 2026-05-01 to open: retirement_age = 63 (valid from 2026-05-01)",
            "[E2 memory] Unresolved item; investments; unconfirmed/unconfirmed: The client may prefer low-risk investments but has not confirmed this."]
    eps = [f"[E{i} conversation_evidence] Episode; retirement_age; exploratory/hypothetical on 2026-0{1 + i % 5}-1{i % 9}: discussion note number {i} " + "filler " * 12 for i in range(3, 3 + n_ep)]
    tail = ["KNOWN GAPS: none", "Return the JSON now."]
    return "\n".join(head + eps + tail), head, tail


def test_compression_deletes_only_low_priority_lines_and_never_rewrites():
    text, head, tail = _prompt()
    counter = TokenCounter("qwen")
    budget = counter.n("\n".join(head + tail)) + 300
    res = compress(text, budget, counter, "test")
    assert res.fits and res.compressed_tokens < res.original_tokens and res.ratio < 1
    assert res.removed.get("episode", 0) > 0 and res.removed_refs
    out = res.text.split("\n")
    original = set(text.split("\n"))
    assert all(ln in original or ln.startswith("[context compressed:") for ln in out)       # nothing was rewritten
    for ln in head + tail:
        assert ln in out                                                                    # every protected line survives byte-for-byte
    assert "has not confirmed this" in res.text and "was it never confirmed before 2026-05-01" in res.text   # negation, date preserved


def test_compression_does_not_turn_a_maybe_into_a_verified_requirement():
    text, _, _ = _prompt()
    res = compress(text, 400, TokenCounter("qwen"), "test")
    kept = [ln for ln in res.text.split("\n") if "low-risk" in ln]
    assert kept == ["[E2 memory] Unresolved item; investments; unconfirmed/unconfirmed: The client may prefer low-risk investments but has not confirmed this."]
    assert "verified" not in res.text.lower() and "suitability requirement" not in res.text.lower()


def test_compression_fails_safely_when_protected_content_does_not_fit():
    text, _, _ = _prompt(5)
    res = compress(text, 20, TokenCounter("qwen"), "tiny")
    assert res.fits is False
    cfg = HarnessConfig.for_test(agent_context_tokens={"report": 120}, max_context_tokens=2000, reserved_output_tokens=500, cache_enabled=False)
    h, rt = runtime(cfg)
    llm = rt.wrap_llm(MockLLM(handlers={"report": lambda s, u, sc: {"ok": True}}))
    with pytest.raises(ContextOverflow):
        llm.complete("report", "sys", text)
    assert rt.telemetry.summary()["denied"] == 1


def test_duplicates_and_failed_tool_attempts_are_dropped_first():
    lines = ["REQUEST: x", "[EX-1-01] it1 get_client_goals({}) -> success: " + "g" * 200, "[EX-1-02] it2 get_client_goals({}) -> rejected: duplicate call", "[EX-1-01] it1 get_client_goals({}) -> success: " + "g" * 200,
             "RESULTS: end"]
    assert [classify(ln) for ln in lines] == ["protected", "old_tool_result", "failed_attempt", "old_tool_result", "protected"]
    res = compress("\n".join(lines), 10_000, TokenCounter("qwen"))
    assert res.removed == {"duplicate": 1}


def test_compression_activates_only_near_the_limit_and_is_traced():
    cfg = HarnessConfig.for_test(cache_enabled=False)
    h, rt = runtime(cfg)
    llm = rt.wrap_llm(MockLLM(handlers={"report": lambda s, u, sc: {"ok": True}}))
    llm.complete("report", "sys", "short prompt")
    assert not rt.telemetry.records[-1].compressed
    text, _, _ = _prompt(40)
    cfg2 = HarnessConfig.for_test(cache_enabled=False, agent_context_tokens={"report": 900}, max_context_tokens=3000)
    h2, rt2 = runtime(cfg2)
    rt2.wrap_llm(MockLLM(handlers={"report": lambda s, u, sc: {"ok": True}})).complete("report", "sys", text)
    assert rt2.telemetry.records[-1].compressed
    ev = [e for e in rt2.tracer.events if e["event"] == "compression"][0]["payload"]
    assert ev["compressed_tokens"] < ev["original_tokens"] and ev["removed"] and ev["fits"]


# ------------------------------------------------------------------------------------------------------------------ cache
def _validated(llm, agent="planner", user="same question", system="sys"):
    return generate_structured(llm, agent, system, user, Out, retries=0)


def test_identical_safe_requests_hit_the_cache_without_a_model_call_or_budget():
    h = Harness(HarnessConfig.for_test())
    inner = MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}})
    _, rt1 = runtime(harness=h)
    _validated(rt1.wrap_llm(inner))
    _, rt2 = runtime(harness=h)
    _validated(rt2.wrap_llm(inner))
    assert len(inner.calls) == 1 and rt2.budget.llm_calls == 0
    assert rt2.telemetry.records[-1].cache == "hit" and h.cache.stats.hits == 1


def test_only_validated_output_is_cached():
    h = Harness(HarnessConfig.for_test())
    inner = MockLLM(handlers={"planner": ["garbage", {"ok": True}, {"ok": True}]})
    _, rt = runtime(harness=h)
    llm = rt.wrap_llm(inner)
    with pytest.raises(StructuredOutputError):
        generate_structured(llm, "planner", "sys", "u", Out, retries=0)           # invalid output must not be stored
    assert len(h.cache) == 0
    llm.complete("planner", "sys", "plain complete without validation")             # complete() alone never stores either
    assert len(h.cache) == 0


def test_cache_is_isolated_per_client_actor_and_data_version():
    h = Harness(HarnessConfig.for_test())
    inner = MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}})
    _, a = runtime(harness=h, clients=("CLIENT-001",))
    _validated(a.wrap_llm(inner))
    for kw in (dict(clients=("CLIENT-002",)), dict(actor="advisor-2", clients=("CLIENT-001",))):
        _, other = runtime(harness=h, **kw)
        _validated(other.wrap_llm(inner))
        assert other.telemetry.records[-1].cache == "miss", kw
    _, changed = runtime(harness=h, clients=("CLIENT-001",))
    changed.data_version = "mem:CHANGED"
    _validated(changed.wrap_llm(inner))
    assert changed.telemetry.records[-1].cache == "miss"
    assert len(inner.calls) == 4


def test_cache_key_covers_prompt_system_schema_and_model():
    h = Harness(HarnessConfig.for_test())
    inner = MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}})
    _, rt = runtime(harness=h)
    llm = rt.wrap_llm(inner)
    _validated(llm)
    _validated(llm, user="different question")
    _validated(llm, system="sys v2")
    inner.model = "other-model"
    _validated(llm)
    assert len(inner.calls) == 4
    inner.model = "mock-qwen"
    _validated(llm)                                                                 # original key again -> hit
    assert len(inner.calls) == 4


def test_cache_can_be_disabled_and_never_covers_tool_selector_or_sampling():
    inner = MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}, "tool_selector": lambda s, u, sc: {"ok": True}})
    h = Harness(HarnessConfig.for_test(cache_enabled=False))
    _, rt = runtime(harness=h)
    _validated(rt.wrap_llm(inner))
    _validated(rt.wrap_llm(inner))
    assert len(inner.calls) == 2
    h2 = Harness(HarnessConfig.for_test())
    _, rt2 = runtime(harness=h2)
    for _ in range(2):
        _validated(rt2.wrap_llm(inner), agent="tool_selector")
    assert len(inner.calls) == 4                                                    # tool_selector is stateful: never cached
    hot = MockLLM(handlers={"planner": lambda s, u, sc: {"ok": True}})
    hot.temperature = 0.7
    for _ in range(2):
        _validated(rt2.wrap_llm(hot))
    assert len(hot.calls) == 2                                                      # sampled output is never cached


def test_cache_ttl_and_size_bound():
    from harness.cache import ResponseCache
    t = [0.0]
    c = ResponseCache(max_entries=2, ttl_seconds=10, clock=lambda: t[0])
    c.put("a", "1"); c.put("b", "2"); c.put("c", "3")
    assert c.get("a") is None and c.get("b") == "2" and c.stats.evictions == 1
    t[0] = 11
    assert c.get("b") is None and c.stats.expired == 1
