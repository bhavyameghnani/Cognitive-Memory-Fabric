"""Stages 5-7: consolidation, association and temporal validation.

Semantic memory is written ONLY here, never by the model: a slot's episodes are folded in time order into short, versioned values.
Only confirmed/correction episodes that carry a value (numeric topics: a bare number) create or reinforce a version.
Deterministic and idempotent: re-running changes nothing; ids and relationships are derived from content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from agents.memory.config import NUMERIC_TOPICS
from agents.memory.integrity import HC, Principal, _canon
from agents.memory.store import MemoryStore, h


def _order(ep: dict) -> tuple:
    return (ep["event_ts"] or "", ep["interaction_id"] or "", ep["turn_index"] or 0)


def _slot(ep: dict) -> str:
    return f"{ep['topic']}|{ep['subject'] or ep['topic']}"


def eligible(ep: dict) -> bool:
    """May this episode create or reinforce a semantic value?"""
    v = ep["value_text"]
    if ep["certainty"] not in ("confirmed", "correction") or ep["status"] == "unconfirmed" or not v:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", v)) if ep["topic"] in NUMERIC_TOPICS else True


def _same(a: str, b: str) -> bool:
    return a == b or a in b or b in a


@dataclass
class Version:
    value: str
    valid_from: str
    creator: dict
    supports: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)


def fold(episodes: list[dict]) -> list[Version]:
    """Pure fold of one slot's eligible episodes (time ordered) into versions."""
    chain: list[Version] = []
    for ep in sorted((e for e in episodes if eligible(e)), key=_order):
        cur = chain[-1] if chain else None
        if cur is None:
            chain.append(Version(ep["value_text"], ep["event_date"], ep))
        elif ep["value_text"] == cur.value:
            cur.supports.append(ep)
        elif ep["event_date"] == cur.valid_from:      # conflicting confirmed statements on the same day = the same event, not a new version
            cur.conflicts.append(ep)
        else:
            chain.append(Version(ep["value_text"], ep["event_date"], ep))
    return chain


class Consolidator:
    def __init__(self, store: MemoryStore, validator=None) -> None:
        self.store = store
        self.validator = validator   # optional harness MemoryWriteValidator.approve_semantic(client_id, version, store) -> (ok, reason)

    def consolidate_client(self, client_id: str) -> dict:
        st = self.store
        out = dict(versions_created=0, relationships=0, superseded_episodes=0)
        with st.transaction():
            eps = st.records(client_id, "episodic")
            by_slot: dict[str, list[dict]] = {}
            for e in sorted(eps, key=_order):
                by_slot.setdefault(_slot(e), []).append(e)
            for slot, slot_eps in by_slot.items():
                self._semantic(client_id, slot, slot_eps, out)
                self._associate(client_id, slot, slot_eps, out)
        return out

    # ---------------------------------------------------------------- semantic versions
    def _semantic(self, client_id: str, slot: str, slot_eps: list[dict], out: dict) -> None:
        st = self.store
        pr = Principal.service("consolidation", [client_id])      # consolidation may only touch the ONE client being consolidated
        desired = fold(slot_eps)
        if self.validator is not None:   # semantic memory is still written only here; the validator can only REMOVE candidates versions
            kept = []
            for v in desired:
                ok, why = self.validator.approve_semantic(client_id, v, st)
                if ok:
                    kept.append(v)
                else:
                    out["rejected_versions"] = out.get("rejected_versions", 0) + 1
            desired = kept
        stored = sorted(st.records(client_id, "semantic", topics=[slot.split("|")[0]]), key=lambda r: r["version_no"] or 0)
        stored = [s for s in stored if s["slot_key"] == slot]
        pinned = [s for s in stored if s["source_class"] == HC and s["status"] in ("active", "disputed", "unconfirmed")]
        if pinned:             # PRECEDENCE: a live human correction outranks every automatic version.  Consolidation neither supersedes nor rebuilds around it
            self._respect_correction(client_id, desired, stored, pinned[-1], pr, out)
            return
        i = 0
        while i < min(len(desired), len(stored)) and (desired[i].value, desired[i].valid_from) == (stored[i]["value_text"], stored[i]["valid_from"]):
            i += 1
        for s in stored[i:]:   # out-of-order evidence changed history: stale tail is superseded (never deleted)
            if s["status"] in ("active", "disputed"):
                st.set_status(s["memory_id"], "superseded", "chain rebuilt after out-of-order evidence", "consolidation", valid_to=s["valid_from"], principal=pr)
        ids = [s["memory_id"] for s in stored[:i]]
        prev_id = ids[-1] if ids else (stored[-1]["memory_id"] if stored else None)
        next_no = (stored[-1]["version_no"] if stored else 0) + 1
        topic, subject = slot.split("|")
        for k in range(i, len(desired)):
            v = desired[k]
            mid = "MEM-SEM-" + h(client_id, slot, next_no, v.value, v.valid_from, n=12)
            ev = [dict(source_type="conversation", interaction_id=v.creator["interaction_id"], turn_index=v.creator["turn_index"], speaker=v.creator["speaker_role"],
                       excerpt=self._excerpt(v.creator), role="primary")]
            rec = dict(memory_id=mid, client_id=client_id, memory_type="semantic", topic=topic, subject=subject, title=f"{subject}: {v.value}",
                       content=f"{subject.replace('_', ' ')} = {v.value} (valid from {v.valid_from})", value_text=v.value, certainty="confirmed", source_role="System",
                       status="active", confidence=0.9, event_date=v.valid_from, valid_from=v.valid_from, version_no=next_no, supersedes_id=prev_id,
                       origin="consolidation", content_hash=h(client_id, slot, next_no, v.value, v.valid_from, n=40))
            det = dict(slot_key=slot, basis=f"{v.creator['certainty']} episode {v.creator['interaction_id']}.{v.creator['turn_index']}", supporting_episodes=1)
            _, created = st.insert_memory(rec, det, ev, reason=f"new version {next_no} from {v.creator['memory_id']}", principal=pr)
            if created:
                out["versions_created"] += 1
            if prev_id:
                prev = st.get(prev_id)
                if prev and prev["status"] in ("active", "disputed"):
                    st.set_status(prev_id, "superseded", f"newer confirmed value {v.value} (version {next_no})", "consolidation", related=mid, valid_to=v.valid_from, principal=pr)
                out["relationships"] += st.add_relationship(mid, prev_id, "supersedes", f"{v.value} replaces earlier value", principal=pr)
            ids.append(mid)
            prev_id, next_no = mid, next_no + 1
        # reinforcement (idempotent): supporting evidence copies, `supports`, `derived_from`, same-day conflicts
        for v, mid in zip(desired, ids):
            out["relationships"] += st.add_relationship(mid, v.creator["memory_id"], "derived_from", principal=pr)
            for ep in v.supports:
                st.add_evidence(mid, client_id, dict(source_type="conversation", interaction_id=ep["interaction_id"], turn_index=ep["turn_index"],
                                                     speaker=ep["speaker_role"], excerpt=self._excerpt(ep), role="supporting"), principal=pr)
                out["relationships"] += st.add_relationship(ep["memory_id"], mid, "supports", principal=pr)
            for ep in v.conflicts:
                out["relationships"] += st.add_relationship(ep["memory_id"], v.creator["memory_id"], "related_to",
                                                            f"conflict: same-day confirmed values {ep['value_text']} vs {v.value}", principal=pr)
            row = st.get(mid)
            if row and row["supporting_episodes"] != 1 + len(v.supports):
                st.set_semantic_support(mid, 1 + len(v.supports), principal=pr)

    def _respect_correction(self, client_id: str, desired: list, stored: list, pinned: dict, pr: Principal, out: dict) -> None:
        """A person's correction is the active version of this slot.  Evidence that AGREES with it, or that is not newer than it, changes nothing.  Newer evidence that DISAGREES is
        never allowed to replace it silently: it is recorded as an open `human_correction_vs_evidence` conflict (a review requirement) and the correction stays active."""
        for v in desired:
            for ep in [v.creator] + v.supports:          # every episode behind an automatic version: the creator and the ones that merely reinforced it
                if _canon(ep["value_text"]) == _canon(pinned["value_text"]) or (ep["event_date"] or "") <= (pinned["valid_from"] or ""):
                    continue
                made = self.store.boundary.record_conflict(pinned["memory_id"], ep["memory_id"], "human_correction_vs_evidence", "high",
                                                           f"newer confirmed value {ep['value_text']} (from {ep['event_date']}) disagrees with the human-corrected value {pinned['value_text']}",
                                                           principal=pr)
                out["conflicts_recorded"] = out.get("conflicts_recorded", 0) + int(made)

    def _excerpt(self, ep: dict) -> str:
        ev = self.store.evidence_map([ep["memory_id"]])[ep["memory_id"]]
        return ev[0].excerpt

    # ---------------------------------------------------------------- associations
    def _associate(self, client_id: str, slot: str, slot_eps: list[dict], out: dict) -> None:
        st = self.store
        pr = Principal.service("consolidation", [client_id])
        topic = slot.split("|")[0]
        versions = sorted((s for s in st.records(client_id, "semantic", topics=[topic]) if s["slot_key"] == slot), key=lambda r: r["version_no"])
        prev: Optional[dict] = None
        for ep in slot_eps:
            if prev is not None:
                out["relationships"] += st.add_relationship(ep["memory_id"], prev["memory_id"], "follows", principal=pr)
            prev = ep
            if ep["certainty"] in ("exploratory", "hypothetical", "recommendation"):
                tgt = [v for v in versions if v["valid_from"] <= ep["event_date"]]
                if tgt:
                    out["relationships"] += st.add_relationship(ep["memory_id"], tgt[-1]["memory_id"], "explores", principal=pr)
            elif ep["certainty"] == "correction":
                earlier = [e for e in slot_eps if _order(e) < _order(ep) and e["memory_id"] != ep["memory_id"]]
                target = next((e for e in reversed(earlier) if ep["corrects_value"] and e["value_text"] and _same(e["value_text"], ep["corrects_value"])), None)
                if target:
                    out["relationships"] += st.add_relationship(ep["memory_id"], target["memory_id"], "corrects", f"corrects value {ep['corrects_value']}", principal=pr)
                    # only a wrongly recorded CONFIRMED decision is superseded; explorations and statements stay as they were
                    if target["certainty"] == "confirmed" and target["status"] == "active":
                        st.set_status(target["memory_id"], "superseded", "wrongly recorded decision corrected", "consolidation", related=ep["memory_id"], principal=pr)
                        out["superseded_episodes"] += 1

    # ---------------------------------------------------------------- stage 7
    def validate_chains(self, client_id: str) -> list[str]:
        """Temporal validation: no overlapping windows, valid_to set on superseded, version never starts before its evidence."""
        st, problems = self.store, []
        sem = st.records(client_id, "semantic")
        ev = st.evidence_map([s["memory_id"] for s in sem])
        slots: dict[str, list[dict]] = {}
        for s in sem:
            slots.setdefault(s["slot_key"], []).append(s)
        for slot, vs in slots.items():
            vs.sort(key=lambda r: r["version_no"])
            for a, b in zip(vs, vs[1:]):
                if a["status"] == "superseded" and not a["valid_to"]:
                    problems.append(f"{a['memory_id']}: superseded without valid_to")
                if a["valid_to"] and a["valid_to"] > b["valid_from"]:
                    problems.append(f"{slot}: versions {a['version_no']} and {b['version_no']} overlap")
            if sum(1 for v in vs if v["status"] == "active") > 1:
                problems.append(f"{slot}: more than one active version")
            for v in vs:
                dates = [st.conv.turn(e.interaction_id, e.turn_index)["interaction_datetime"][:10] for e in ev[v["memory_id"]] if st.conv.turn(e.interaction_id, e.turn_index)]
                if dates and v["valid_from"] < min(dates):
                    problems.append(f"{v['memory_id']}: starts before its evidence")
        return problems
