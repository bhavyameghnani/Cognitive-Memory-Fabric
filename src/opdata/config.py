"""Shared configuration for the synthetic operational data layer (stdlib only)."""
from __future__ import annotations

import csv
import hashlib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONVERSATIONS = ROOT / "data" / "conversations.csv"
CONVERSATIONS_MD5 = "03d14ce01d5f053bd843a1d9ec559f8f"  # frozen v2-final (see docs/final_dataset_acceptance.md)
DESIGN_DOC = "docs/dataset_design.md"
OUT_DIR = ROOT / "data" / "operational"
REPORT_DIR = ROOT / "reports"
SQL_DIR = ROOT / "sql"

SEED = 20260101  # same convention as the conversation generator
SCHEMA = "cmf_ops"
CURRENCY = "USD"
PROFILE_AS_OF = date(2026, 6, 30)
MONTH_ENDS = [date(2026, m, d) for m, d in ((1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30))]
BASE_DATE = date(2025, 12, 31)  # price-path origin only; no snapshot is emitted for it

CLIENT_IDS = [f"CLIENT-00{i}" for i in range(1, 7)]

# provenance vocabulary (data_provenance column)
PROV_SYNTH = "synthetic"
PROV_ANCHORED = "conversation_anchored"  # at least one field is taken or derived from conversations.csv


def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def load_conversations() -> list[dict]:
    with open(CONVERSATIONS, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["turn_index"] = int(r["turn_index"])
    return rows


class ConvIndex:
    """Read-only lookup into conversations.csv used to *verify* every evidence reference."""

    def __init__(self) -> None:
        self.rows = load_conversations()
        self.by_turn = {(r["interaction_id"], r["turn_index"]): r for r in self.rows}

    def turn(self, interaction_id: str, turn_index: int) -> dict:
        return self.by_turn[(interaction_id, turn_index)]

    def verify(self, client_id: str, interaction_id: str, turn_index: int, excerpt: str) -> dict:
        r = self.by_turn.get((interaction_id, turn_index))
        if r is None:
            raise KeyError(f"no such turn {interaction_id}.{turn_index}")
        if r["client_id"] != client_id:
            raise ValueError(f"{interaction_id}.{turn_index} belongs to {r['client_id']}, not {client_id}")
        if excerpt not in r["utterance"]:
            raise ValueError(f"excerpt not found in {interaction_id}.{turn_index}: {excerpt!r}")
        return r
