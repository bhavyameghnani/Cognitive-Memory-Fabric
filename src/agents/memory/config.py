"""Memory Orchestrator configuration: filtering knobs, retrieval limits and the shared constants (no secrets here)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULT_DB_URL = "sqlite:///build/cmf_memory.db"
EXTRACTION_AGENT = "memory"          # agent name used with the shared LLM client (MockLLM handlers are keyed by it)
WORKING_TTL_HOURS = 4.0
EPISODE_CAP = 8
GLOBAL_CLIENT = "GLOBAL"

# Topics whose semantic value must be a bare number (a sentence would cause version churn).
NUMERIC_TOPICS = {"retirement_age", "asset_allocation"}
# Topics whose slot subject is always the topic itself (so "35% equities" and "equity allocation" are one slot).
FIXED_SUBJECT_TOPICS = {"retirement_age", "asset_allocation"}

# Higher = stronger claim.  A guard may only move certainty DOWN this ranking.
CERTAINTY_RANK = {"confirmed": 5, "correction": 5, "recommendation": 3, "exploratory": 2, "hypothetical": 2, "unconfirmed": 1, "n/a": 0}
# Ranking weight used by retrieval (blended with keyword overlap and embedding similarity in retrieve.relevance()).
CERTAINTY_WEIGHT = {"confirmed": 1.0, "correction": 0.9, "recommendation": 0.6, "exploratory": 0.5, "hypothetical": 0.35, "unconfirmed": 0.3, "n/a": 0.2}


def database_url() -> str:
    return os.environ.get("CMF_MEMORY_DATABASE_URL") or DEFAULT_DB_URL


@dataclass
class FilterConfig:
    """Stage 1 (salience) settings.  Every selection is logged with its reasons."""
    min_score: float = 2.0            # a sentence needs at least this score to be shown to the model
    max_turns: int = 12               # at most this many turns per meeting (highest score first, then chronological)
    max_snippet_chars: int = 700      # per turn
    min_sentence_chars: int = 25
    w_decision: float = 3.0
    w_correction: float = 3.0
    w_exploratory: float = 2.5
    w_recommendation: float = 2.0
    w_procedure: float = 2.0          # advisor wording that may propose a reusable procedure
    w_recap: float = 1.0              # extra weight for "for the record / let me read it back" recap sentences
    w_topic: float = 1.0
    w_figure: float = 1.0
    w_client_turn: float = 0.5
    p_template: float = 5.0           # penalty for routine templates (portfolio read-outs, spending averages)
    min_relevance: float = 1.0        # filter stage: reject candidates scoring below this


@dataclass
class RetrievalConfig:
    episode_cap: int = EPISODE_CAP
    unresolved_cap: int = 6
    history_cap: int = 6
    working_ttl_hours: float = WORKING_TTL_HOURS
    semantic_weight: float = 1.0      # weight of query-memory embedding cosine in episode ranking; 0 disables embeddings (keyword-only ranking)
    embedder: Optional[Any] = None    # agents.memory.embeddings.Embedder; None -> default_embedder() (env CMF_EMBED_BACKEND)


@dataclass
class OrchestratorConfig:
    filter: FilterConfig = field(default_factory=FilterConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
