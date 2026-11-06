"""Centralised harness configuration with validated, safe-by-default values.

Profiles
    production   (default)  safety-critical controls are ALWAYS on; an env var that tries to switch one off is ignored and a warning is recorded.
    development / test      controls may be switched off (for ablation experiments and local work); anonymous dev identities are allowed.
A missing variable never weakens a control: every default below is the safe value.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional

SAFETY_CRITICAL = ("permission_gate", "evidence_verification", "structured_claim_verification", "memory_write_validation", "approval_required_for_high_risk", "enabled")
PROFILES = ("production", "development", "test")

DEFAULT_AGENT_CONTEXT = {"planner": 2500, "memory": 4000, "tool_selector": 4500, "report": 5000}


MIN_APPROVAL_TTL, MAX_APPROVAL_TTL = 60, 30 * 24 * 3600


class ConfigError(ValueError):
    pass


MIN_AUTH_SECRET_CHARS = 32


def load_auth_secret(env: Optional[Mapping[str, str]] = None) -> str:
    """The token-verification secret (CMF_AUTH_JWT_SECRET), or "" when none is configured.  Deliberately NOT a field of HarnessConfig, so it can never end up in a
    repr, an asdict() dump, a trace or an evaluation report."""
    env = os.environ if env is None else env
    secret = (env.get("CMF_AUTH_JWT_SECRET") or "").strip()
    if secret and len(secret) < MIN_AUTH_SECRET_CHARS:
        raise ConfigError(f"CMF_AUTH_JWT_SECRET must be at least {MIN_AUTH_SECRET_CHARS} characters")
    return secret


def is_loopback_host(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def check_bind(cfg: "HarnessConfig", host: str) -> None:
    """The development/test profiles grant unauthenticated local access, so they may only listen on a loopback address."""
    if cfg.profile != "production" and not is_loopback_host(host):
        raise ConfigError(f"HARNESS_PROFILE={cfg.profile} grants unauthenticated access and may only bind to a loopback address (127.0.0.1, ::1 or localhost), not {host!r}. "
                          f"To listen on other interfaces use HARNESS_PROFILE=production with CMF_AUTH_JWT_SECRET set.")


@dataclass(frozen=True)
class HarnessConfig:
    profile: str = "production"
    enabled: bool = True
    # ---- shared request budget (one per request, shared by all four agents)
    max_llm_calls: int = 20
    max_retries: int = 6                    # validation retries across the whole request
    max_retries_per_operation: int = 2      # retries of one structured-output operation
    max_tool_calls: int = 12
    max_tool_calls_per_agent: int = 12
    max_tool_iterations: int = 5
    max_input_tokens: int = 60_000
    max_output_tokens: int = 10_000
    max_total_tokens: int = 70_000
    max_runtime_seconds: float = 600.0
    max_tool_seconds: float = 90.0
    max_model_call_seconds: float = 180.0
    # ---- context
    max_context_tokens: int = 6144          # matches the Ollama default CMF_LLM_NUM_CTX
    reserved_output_tokens: int = 1000
    agent_context_tokens: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_AGENT_CONTEXT))
    memory_context_tokens: int = 2500       # budget for retrieved memories handed downstream
    compression_enabled: bool = True
    compression_threshold: float = 0.85     # compress when the prompt exceeds this fraction of its limit
    # ---- cache
    cache_enabled: bool = True
    cache_ttl_seconds: float = 900.0
    cache_max_entries: int = 256
    cache_agents: tuple = ("planner", "memory", "report")   # tool_selector is never cached: it is stateful across iterations
    # ---- governance
    evidence_verification: bool = True
    structured_claim_verification: bool = True   # claims are read into value/unit/polarity/modality/period and compared exactly; False = legacy lexical check (development only)
    temporal_conflict_detection: bool = True
    permission_gate: bool = True
    memory_write_validation: bool = True
    approval_required_for_high_risk: bool = True
    max_context_recoveries: int = 1         # HARNESS_MAX_CONTEXT_RECOVERIES: compress-and-retry attempts after the PROVIDER rejects a prompt as too long (0 = never retry; max 3)
    context_recovery_shrink: float = 0.6    # each recovery targets this fraction of the rejected prompt's size
    max_provider_workers: int = 32          # HARNESS_MAX_PROVIDER_WORKERS: PROCESS-WIDE cap on live provider workers (running + abandoned) across all requests
    max_abandoned_workers: int = 8          # HARNESS_MAX_ABANDONED_WORKERS: PROCESS-WIDE cap on abandoned workers whose provider call has not exited yet
    max_cancel_workers: int = 4             # HARNESS_MAX_CANCEL_WORKERS: PROCESS-WIDE cap on concurrent best-effort cancel_call threads
    cancel_timeout_seconds: float = 2.0     # HARNESS_CANCEL_TIMEOUT_SECONDS: a cancel_call still running after this is reported as timed out (its thread keeps its slot until it exits)
    approval_ttl_seconds: int = 14400       # HARNESS_APPROVAL_TTL_SECONDS: an approval request (and the approval it becomes) is valid this long from creation; 60 s .. 30 days
    approval_database_url: str = ""         # CMF_APPROVAL_DATABASE_URL: sqlite:///path (default build/cmf_approvals.db); production refuses an in-memory store
    approval_sensitive_memory_topics: tuple = ()   # semantic memory topics whose durable change needs a human decision, e.g. ("risk_tolerance",)
    stale_after_days: int = 180
    allow_asserted_identity: bool = False   # accept an identity the transport merely asserts (no authentication available)
    allow_anonymous_dev: bool = False       # development only: unauthenticated requests get a clearly labelled dev identity (loopback peers only)
    auth_trusted_gateway: bool = False      # declares that identities arrive as signed tokens; requires CMF_AUTH_JWT_SECRET (checked at start-up)
    auth_issuer: str = ""                   # CMF_AUTH_ISSUER: when set, tokens must carry this `iss`
    auth_audience: str = ""                 # CMF_AUTH_AUDIENCE: when set, tokens must carry this `aud`
    auth_leeway_seconds: int = 0            # clock-skew tolerance for exp / nbf
    api_docs_enabled: bool = False          # CMF_API_DOCS_ENABLED: production serves /docs and /openapi.json only when true AND the caller presents a verified token
    # ---- observability / integration
    trace_enabled: bool = True
    trace_retention_days: int = 30
    trace_max_files: int = 2000
    trace_store_prompts: bool = False       # prompts are hashed, never stored raw, unless this is switched on
    strands_execution: bool = True          # route model calls through a Strands Agent (hooks + invocation limits)
    evaluation_enabled: bool = True
    warnings: tuple = ()

    # ------------------------------------------------------------------ factories
    @classmethod
    def development(cls, **kw) -> "HarnessConfig":
        base = dict(profile="development", allow_asserted_identity=True, allow_anonymous_dev=True)
        base.update(kw)
        return cls(**base)

    @classmethod
    def for_test(cls, **kw) -> "HarnessConfig":
        base = dict(profile="test", allow_asserted_identity=True, allow_anonymous_dev=True, trace_enabled=False)
        base.update(kw)
        return cls(**base)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "HarnessConfig":
        env = os.environ if env is None else env
        profile = (env.get("HARNESS_PROFILE") or "production").strip().lower()
        if profile not in PROFILES:
            raise ConfigError(f"HARNESS_PROFILE must be one of {PROFILES}, got {profile!r}")
        relaxed = profile != "production"
        warnings: list[str] = []
        vals: dict = {"profile": profile}

        def boolean(name: str, field_name: str) -> None:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return
            v = raw.strip().lower()
            if v not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                raise ConfigError(f"{name} must be true/false, got {raw!r}")
            on = v in ("1", "true", "yes", "on")
            if not on and field_name in SAFETY_CRITICAL and not relaxed:
                warnings.append(f"{name}=false ignored: '{field_name}' is safety-critical and cannot be disabled in the production profile")
                return
            vals[field_name] = on

        def number(name: str, field_name: str, cast, minimum=1) -> None:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return
            try:
                v = cast(raw)
            except ValueError:
                raise ConfigError(f"{name} must be a number, got {raw!r}") from None
            if v < minimum:
                raise ConfigError(f"{name} must be >= {minimum}, got {v}")
            vals[field_name] = v

        for name, f in [("HARNESS_ENABLED", "enabled"), ("HARNESS_COMPRESSION_ENABLED", "compression_enabled"), ("HARNESS_CACHE_ENABLED", "cache_enabled"),
                        ("HARNESS_EVIDENCE_VERIFICATION", "evidence_verification"), ("HARNESS_STRUCTURED_CLAIM_VERIFICATION", "structured_claim_verification"), ("HARNESS_TEMPORAL_CONFLICT_DETECTION", "temporal_conflict_detection"),
                        ("HARNESS_PERMISSION_GATE", "permission_gate"), ("HARNESS_MEMORY_WRITE_VALIDATION", "memory_write_validation"),
                        ("HARNESS_APPROVAL_REQUIRED_FOR_HIGH_RISK", "approval_required_for_high_risk"), ("HARNESS_TRACE_ENABLED", "trace_enabled"),
                        ("HARNESS_EVALUATION_ENABLED", "evaluation_enabled"), ("HARNESS_STRANDS_EXECUTION", "strands_execution"),
                        ("HARNESS_TRACE_STORE_PROMPTS", "trace_store_prompts")]:
            boolean(name, f)
        for name, f in [("HARNESS_MAX_LLM_CALLS", "max_llm_calls"), ("HARNESS_MAX_RETRIES", "max_retries"), ("HARNESS_MAX_RETRIES_PER_OPERATION", "max_retries_per_operation"),
                        ("HARNESS_MAX_TOOL_CALLS", "max_tool_calls"), ("HARNESS_MAX_TOOL_CALLS_PER_AGENT", "max_tool_calls_per_agent"),
                        ("HARNESS_MAX_TOOL_ITERATIONS", "max_tool_iterations"), ("HARNESS_MAX_INPUT_TOKENS", "max_input_tokens"),
                        ("HARNESS_MAX_OUTPUT_TOKENS", "max_output_tokens"), ("HARNESS_MAX_TOTAL_TOKENS", "max_total_tokens"),
                        ("HARNESS_MAX_CONTEXT_TOKENS", "max_context_tokens"), ("HARNESS_RESERVED_OUTPUT_TOKENS", "reserved_output_tokens"),
                        ("HARNESS_MEMORY_CONTEXT_TOKENS", "memory_context_tokens"), ("HARNESS_CACHE_MAX_ENTRIES", "cache_max_entries"),
                        ("HARNESS_STALE_AFTER_DAYS", "stale_after_days"), ("HARNESS_APPROVAL_TTL_SECONDS", "approval_ttl_seconds"), ("HARNESS_TRACE_RETENTION_DAYS", "trace_retention_days"),
                        ("HARNESS_TRACE_MAX_FILES", "trace_max_files")]:
            number(name, f, int)
        for name, f in [("HARNESS_MAX_RUNTIME_SECONDS", "max_runtime_seconds"), ("HARNESS_MAX_TOOL_SECONDS", "max_tool_seconds"),
                        ("HARNESS_MAX_MODEL_CALL_SECONDS", "max_model_call_seconds"), ("HARNESS_CACHE_TTL_SECONDS", "cache_ttl_seconds")]:
            number(name, f, float, minimum=0.001)
        vals["approval_database_url"] = (env.get("CMF_APPROVAL_DATABASE_URL") or "").strip()
        number("HARNESS_MAX_CONTEXT_RECOVERIES", "max_context_recoveries", int, minimum=0)
        for name, f in [("HARNESS_MAX_PROVIDER_WORKERS", "max_provider_workers"), ("HARNESS_MAX_ABANDONED_WORKERS", "max_abandoned_workers"), ("HARNESS_MAX_CANCEL_WORKERS", "max_cancel_workers")]:
            number(name, f, int)
        number("HARNESS_CANCEL_TIMEOUT_SECONDS", "cancel_timeout_seconds", float, minimum=0.01)
        raw_topics = (env.get("HARNESS_APPROVAL_SENSITIVE_MEMORY_TOPICS") or "").strip()
        if raw_topics:
            vals["approval_sensitive_memory_topics"] = tuple(t.strip() for t in raw_topics.split(",") if t.strip())
        if relaxed:
            boolean("HARNESS_ALLOW_ASSERTED_IDENTITY", "allow_asserted_identity")
            boolean("HARNESS_ALLOW_ANONYMOUS_DEV", "allow_anonymous_dev")
            vals.setdefault("allow_asserted_identity", True)
            vals.setdefault("allow_anonymous_dev", True)
        else:
            for name in ("HARNESS_ALLOW_ANONYMOUS_DEV", "HARNESS_ALLOW_ASSERTED_IDENTITY"):
                if (env.get(name) or "").strip().lower() in ("1", "true", "yes", "on"):
                    warnings.append(f"{name} ignored in the production profile")
        # ---- identity: signed tokens only.  The old behaviour (trusting X-CMF-Actor-* headers when CMF_AUTH_TRUSTED_GATEWAY=true) no longer exists.
        secret = load_auth_secret(env)
        gw = (env.get("CMF_AUTH_TRUSTED_GATEWAY") or "").strip().lower()
        if gw not in ("", "1", "true", "yes", "on", "0", "false", "no", "off"):
            raise ConfigError(f"CMF_AUTH_TRUSTED_GATEWAY must be true/false, got {gw!r}")
        vals["auth_trusted_gateway"] = gw in ("1", "true", "yes", "on")
        if vals["auth_trusted_gateway"] and not secret:
            raise ConfigError(f"CMF_AUTH_TRUSTED_GATEWAY=true requires CMF_AUTH_JWT_SECRET (at least {MIN_AUTH_SECRET_CHARS} characters) so identity tokens can be verified; "
                              f"request headers alone are never accepted as authentication")
        vals["auth_issuer"] = (env.get("CMF_AUTH_ISSUER") or "").strip()
        vals["auth_audience"] = (env.get("CMF_AUTH_AUDIENCE") or "").strip()
        number("CMF_AUTH_LEEWAY_SECONDS", "auth_leeway_seconds", int, minimum=0)
        boolean("CMF_API_DOCS_ENABLED", "api_docs_enabled")
        cfg = cls(**vals, warnings=tuple(warnings))
        cfg.validate()
        return cfg

    # ------------------------------------------------------------------ checks
    def validate(self) -> None:
        if self.max_total_tokens < max(self.max_input_tokens, self.max_output_tokens):
            raise ConfigError("max_total_tokens must be >= max_input_tokens and max_output_tokens")
        if self.reserved_output_tokens >= self.max_context_tokens:
            raise ConfigError("reserved_output_tokens must be smaller than max_context_tokens")
        if not 0.1 <= self.compression_threshold <= 1.0:
            raise ConfigError("compression_threshold must be within [0.1, 1.0]")
        if not 0 <= self.max_context_recoveries <= 3:
            raise ConfigError(f"HARNESS_MAX_CONTEXT_RECOVERIES must be between 0 and 3, got {self.max_context_recoveries}")
        if min(self.max_provider_workers, self.max_abandoned_workers, self.max_cancel_workers) < 1 or self.cancel_timeout_seconds <= 0:
            raise ConfigError("HARNESS_MAX_PROVIDER_WORKERS, HARNESS_MAX_ABANDONED_WORKERS and HARNESS_MAX_CANCEL_WORKERS must be >= 1 and HARNESS_CANCEL_TIMEOUT_SECONDS > 0")
        if not 0.1 <= self.context_recovery_shrink <= 0.95:
            raise ConfigError("context_recovery_shrink must be within [0.1, 0.95]")
        if not MIN_APPROVAL_TTL <= self.approval_ttl_seconds <= MAX_APPROVAL_TTL:
            raise ConfigError(f"HARNESS_APPROVAL_TTL_SECONDS must be between {MIN_APPROVAL_TTL} and {MAX_APPROVAL_TTL}, got {self.approval_ttl_seconds}")
        if self.approval_database_url and "://" in self.approval_database_url and not self.approval_database_url.startswith("sqlite:///"):
            raise ConfigError("CMF_APPROVAL_DATABASE_URL must be sqlite:///path (SQLite only)")
        if self.profile == "production" and not all(getattr(self, f) for f in SAFETY_CRITICAL):
            raise ConfigError("production profile requires every safety-critical control to be enabled")

    @property
    def input_limit_per_call(self) -> int:
        return self.max_context_tokens - self.reserved_output_tokens

    def agent_limit(self, agent: str) -> int:
        """Prompt-token ceiling for one agent: its own limit, never above what the context window leaves after reserved output."""
        return min(int(self.agent_context_tokens.get(agent, self.input_limit_per_call)), self.input_limit_per_call)

    def with_(self, **kw) -> "HarnessConfig":
        return replace(self, **kw)
