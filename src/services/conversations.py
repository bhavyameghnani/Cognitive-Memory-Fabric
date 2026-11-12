"""Read-only access to data/conversations.csv (the frozen source evidence) + the client directory derived from it."""
from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = ROOT / "data" / "conversations.csv"
_NAME_RES = [re.compile(r"\b(?:Hi|Hello|Hey|Good (?:morning|afternoon|evening)),?(?: there,?)?\s+([A-Z][a-z]+)\b"),
             re.compile(r"^(?:It's [A-Z][a-z]+\.\s*)?([A-Z][a-z]+), (?:thanks|good to|good morning)")]
_NOT_NAMES = {"There", "Good", "Thanks", "Take", "Shall", "Agreed"}


class Conversations:
    def __init__(self, path: Path = CSV_PATH) -> None:
        self.path = path
        with open(path, newline="", encoding="utf-8") as f:
            self.rows = list(csv.DictReader(f))
        for r in self.rows:
            r["turn_index"] = int(r["turn_index"])
        self.by_turn = {(r["interaction_id"], r["turn_index"]): r for r in self.rows}
        self.meetings: dict[str, list[dict]] = defaultdict(list)
        for r in self.rows:
            self.meetings[r["interaction_id"]].append(r)
        for v in self.meetings.values():
            v.sort(key=lambda r: r["turn_index"])
        self._directory: Optional[dict] = None

    # ---- lookups
    def interactions_for(self, client_id: str) -> list[str]:
        ids = {r["interaction_id"] for r in self.rows if r["client_id"] == client_id}
        return sorted(ids, key=lambda i: self.meetings[i][0]["interaction_datetime"])

    def meeting(self, interaction_id: str) -> list[dict]:
        return self.meetings[interaction_id]

    def turn(self, interaction_id: str, turn_index: int) -> Optional[dict]:
        return self.by_turn.get((interaction_id, turn_index))

    def transcript_hash(self, interaction_id: str) -> str:
        h = hashlib.sha256()
        for r in self.meetings[interaction_id]:
            h.update(f"{r['turn_index']}|{r['speaker']}|{r['utterance']}\n".encode())
        return h.hexdigest()

    def verify_excerpt(self, client_id: str, interaction_id: str, turn_index: int, excerpt: str) -> bool:
        r = self.turn(interaction_id, turn_index)
        return bool(r and r["client_id"] == client_id and norm(excerpt) in norm(r["utterance"]))

    # ---- client directory (derived from the data, not hard-coded)
    def directory(self) -> dict[str, dict]:
        if self._directory is None:
            names: dict[str, Counter] = defaultdict(Counter)
            advisor: dict[str, str] = {}
            for r in self.rows:
                advisor[r["client_id"]] = r["advisor_id"]
                if r["speaker"] == "Advisor" and r["turn_index"] <= 3:
                    for rx in _NAME_RES:
                        for m in rx.finditer(r["utterance"][:120]):
                            if m.group(1) not in _NOT_NAMES:
                                names[r["client_id"]][m.group(1)] += 1
            self._directory = {c: dict(first_name=(names[c].most_common(1)[0][0] if names[c] else None), advisor_id=advisor[c],
                                       interactions=len(self.interactions_for(c))) for c in sorted(advisor)}
        return self._directory

    def resolve_clients(self, text: str) -> tuple[list[str], list[str]]:
        """Return (client_ids, notes). Matches CLIENT-00N or first names (whole word, case-insensitive)."""
        found = []
        for m in re.finditer(r"CLIENT-\d{3}", text, re.I):
            cid = m.group(0).upper()
            if cid in self.directory() and cid not in found:
                found.append(cid)
        for cid, d in self.directory().items():
            if d["first_name"] and re.search(rf"\b{re.escape(d['first_name'])}(?:'s)?\b", text, re.I) and cid not in found:
                found.append(cid)
        return found, []

    def explicit_client_ids(self, text: str) -> list[str]:
        """Every CLIENT-nnn id the text names, whether or not it exists (same pattern as resolve_clients).  Used to treat an id that does not exist exactly like one the caller may not use."""
        out: list[str] = []
        for m in re.finditer(r"CLIENT-\d{3}", text, re.I):
            if m.group(0).upper() not in out:
                out.append(m.group(0).upper())
        return out


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


@lru_cache(maxsize=1)
def shared() -> Conversations:
    return Conversations()
