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
        self._index()

    def _index(self) -> None:
        self.by_turn = {(r["interaction_id"], r["turn_index"]): r for r in self.rows}
        self.meetings: dict[str, list[dict]] = defaultdict(list)
        for r in self.rows:
            self.meetings[r["interaction_id"]].append(r)
        for v in self.meetings.values():
            v.sort(key=lambda r: r["turn_index"])
        self._directory: Optional[dict] = None

    def add_rows(self, rows: list[dict]) -> None:
        """Append the rows of interactions that are NEW to this source (the demo simulation, services/simulation.py).  An interaction that already exists is refused:
        a source row, once read, never changes (the transcript hash of an ingested interaction must stay valid)."""
        iids = {r["interaction_id"] for r in rows}
        clash = sorted(iids & set(self.meetings))
        if clash:
            raise ValueError(f"interactions already present: {clash}")
        self.rows += [dict(r, turn_index=int(r["turn_index"])) for r in rows]
        self._index()

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


# ---- multichannel source adapter -------------------------------------------------------------------------------------------------------------------------
# The generated meetings, email threads and chat conversations of datasets/ are presented through the SAME interface as conversations.csv: one interaction
# per meeting / thread / chat (MTG-, THR-, CHT- ids), one "turn" per turn / message.  The memory integrity boundary, the harness write validator and the
# evidence checks therefore verify them with their existing code: an excerpt must exist in the cited message, the speaker must match, and the memory's
# observation time is the cited message's own timestamp (a thread spans weeks, so it is set per row, not per interaction).
#   * a third-party sender (e.g. the client's CPA) is kept as context with speaker "Third party"; such a message can never be cited as memory evidence
#   * records restricted from agent access in datasets/governance/source_control.csv (SYN-ACC-RESTRICTED) are not loaded at all
#   * the evaluation answer key and the CRM layer are never read here
DATASETS = ROOT / "datasets"
CHANNELS = ("meetings", "email", "chat")
THIRD_PARTY = "Third party"
_NO_AGENT_ACCESS = "SYN-ACC-RESTRICTED"


def _csv(rel: str) -> list[dict]:
    with open(DATASETS / rel, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _role(person_id: str) -> str:
    return "Client" if person_id.startswith("CLIENT-") else "Advisor" if person_id.startswith("ADVISOR-") else THIRD_PARTY


def restricted_records() -> set[str]:
    return {r["record_id"] for r in _csv("governance/source_control.csv") if r.get("restriction_policy_id") == _NO_AGENT_ACCESS}


def channel_rows(channels=CHANNELS) -> list[dict]:
    """Rows in the conversations.csv shape (plus speaker_name / speaker_id) for the requested extra channels, restricted records excluded."""
    blocked, out = restricted_records(), []

    def row(iid, client, advisor, ts, channel, turn, speaker_id, name, text, context, source_type, consent):
        return dict(interaction_id=iid, client_id=client, advisor_id=advisor, interaction_datetime=ts, channel=channel, turn_index=int(turn),
                    speaker=_role(speaker_id), speaker_id=speaker_id, speaker_name=name, utterance=text, meeting_context=context,
                    source_type=source_type, consent_status=consent)
    if "meetings" in channels:
        meta = {m["meeting_id"]: m for m in _csv("meetings/meetings.csv")}
        for t in _csv("meetings/meeting_turns.csv"):
            m = meta[t["meeting_id"]]
            if t["meeting_id"] not in blocked:
                out.append(row(t["meeting_id"], t["client_id"], m["advisor_id"], t["turn_timestamp"], "meeting", t["turn_index"], t["speaker_id"], t["speaker_name"],
                               t["utterance"], m["subject"], t["source_type"], t["consent_status"]))
    if "email" in channels:
        for e in _csv("emails/emails.csv"):
            if e["thread_id"] not in blocked:
                out.append(row(e["thread_id"], e["client_id"], e["advisor_id"], e["timestamp"], "email", e["message_seq"], e["sender_id"], e["sender_name"],
                               e["body"], e["subject"], e["source_type"], e["consent_status"]))
    if "chat" in channels:
        for c in _csv("chats/chats.csv"):
            if c["chat_conversation_id"] not in blocked:
                out.append(row(c["chat_conversation_id"], c["client_id"], c["advisor_id"], c["timestamp"], "chat", c["message_seq"], c["sender_id"], c["sender_name"],
                               c["message_text"], "chat conversation", c["source_type"], c["consent_status"]))
    return out


class MultiChannelConversations(Conversations):
    """conversations.csv plus the requested extra channels, behind the unchanged Conversations interface."""

    def __init__(self, path: Path = CSV_PATH, channels=CHANNELS) -> None:
        super().__init__(path)
        self.channels = tuple(channels)
        self.rows += channel_rows(self.channels)
        self._index()


def memory_channels(env=None) -> tuple:
    """CMF_MEMORY_CHANNELS: empty / 'conversations' (default) = data/conversations.csv only; 'all' or a comma list of meetings,email,chat adds those."""
    import os
    v = (os.environ if env is None else env).get("CMF_MEMORY_CHANNELS", "").strip().lower()
    if v in ("", "conversations"):
        return ()
    return CHANNELS if v == "all" else tuple(c for c in (x.strip() for x in v.split(",")) if c in CHANNELS)


@lru_cache(maxsize=1)
def shared() -> Conversations:
    extra = memory_channels()
    return MultiChannelConversations(channels=extra) if extra else Conversations()
