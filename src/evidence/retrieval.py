"""Evidence retrieval over the structured store.  These are EVIDENCE interfaces: they return what was said or written, with provenance.
They are not memory retrieval and create nothing.

Every method takes a Requester and checks each container against the synthetic governance fixtures (datasets/governance, via the reference
evaluator governance_check.decide: deny by default, a record-level restriction can only narrow, a derived CRM record has its own policy, an agent holds
only its delegator's rights).  Rows the requester may not read are omitted, never described.  Direct lookups of a denied id raise AccessDenied.
Reads use the store's read-only handle.  The governance evaluator is a validation aid, not production access control."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .sources import DATASETS
from .store import EvidenceStore

if str(DATASETS) not in sys.path:
    sys.path.insert(0, str(DATASETS))
import governance_check as gc  # noqa: E402


class AccessDenied(PermissionError):
    def __init__(self, record_id: str, reason: str) -> None:
        super().__init__(f"access to {record_id} denied: {reason}")
        self.record_id, self.reason = record_id, reason


class NotFound(KeyError):
    pass


@dataclass(frozen=True)
class Requester:
    principal_id: str
    principal_type: str                  # client | advisor | external | agent
    acting_for: str = ""                 # an agent's delegator

    @classmethod
    def advisor(cls, advisor_id: str) -> "Requester":
        return cls(advisor_id, "advisor")

    @classmethod
    def client(cls, client_id: str) -> "Requester":
        return cls(client_id, "client")


def _fts_query(text: str) -> str:
    toks = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text) if len(t) > 2]
    return " OR ".join('"' + t.replace('"', "") + '"' for t in toks)


def _d(row) -> dict:
    return dict(row) if row is not None else None


class EvidenceRetriever:
    def __init__(self, store: EvidenceStore, requester: Requester, on: Optional[date] = None, datasets_dir=None) -> None:
        self.store, self.who = store, requester
        self.on = (on or date.today()).isoformat()
        self.ctx = gc.load(datasets_dir) if datasets_dir else gc.load()
        self.db = store.reader()
        self._memo: dict[str, tuple] = {}

    def close(self) -> None:
        self.db.close()

    # ---- access ---------------------------------------------------------------------------------------------------------------------------
    def check(self, record_id: str) -> tuple[bool, str]:
        if record_id not in self._memo:
            self._memo[record_id] = gc.decide(self.ctx, self.who.principal_id, self.who.principal_type, self.who.acting_for, record_id, self.on)
        d, reason = self._memo[record_id]
        return d == "allow", reason

    def _require(self, record_id: str) -> None:
        ok, reason = self.check(record_id)
        if not ok:
            raise AccessDenied(record_id, reason)

    def _container_of(self, id_: str) -> str:
        """Resolve a container, turn, segment or evidence id to its container id; NotFound if unknown."""
        for sql in ("SELECT container_id FROM containers WHERE container_id=?", "SELECT container_id FROM turns WHERE turn_id=?",
                    "SELECT container_id FROM segments WHERE segment_id=?", "SELECT container_id FROM evidence_spans WHERE evidence_id=?"):
            r = self.db.execute(sql, (id_,)).fetchone()
            if r:
                return r[0]
        raise NotFound(id_)

    def _allowed(self, rows: Iterable[dict], key: str = "container_id") -> list[dict]:
        return [r for r in rows if self.check(r[key])[0]]

    # ---- 1. containers for a client --------------------------------------------------------------------------------------------------------
    def containers_for_client(self, client_id: str, channels: Optional[list] = None, start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
        sql, p = "SELECT * FROM containers WHERE client_id=?", [client_id]
        if channels:
            sql += f" AND channel IN ({','.join('?' * len(channels))})"
            p += channels
        if start:
            sql += " AND substr(started_at,1,10) >= ?"
            p.append(start[:10])
        if end:
            sql += " AND substr(started_at,1,10) <= ?"
            p.append(end[:10])
        rows = [dict(r) for r in self.db.execute(sql + " ORDER BY started_at, container_id", p)]
        return self._allowed(rows)

    # ---- 2. every turn of a container ------------------------------------------------------------------------------------------------------
    def turns_in_container(self, container_id: str) -> list[dict]:
        self._require(container_id)
        out = [dict(r) for r in self.db.execute("SELECT * FROM turns WHERE container_id=? ORDER BY seq", (container_id,))]
        if not out:
            raise NotFound(container_id)
        for t in out:
            e = self.db.execute("SELECT * FROM email_message_details WHERE turn_id=?", (t["turn_id"],)).fetchone()
            if e:
                t["email"] = dict(e)
            c = self.db.execute("SELECT * FROM chat_message_details WHERE turn_id=?", (t["turn_id"],)).fetchone()
            if c:
                t["chat"] = dict(c)
        return out

    def container(self, container_id: str) -> dict:
        self._require(container_id)
        r = self.db.execute("SELECT * FROM containers WHERE container_id=?", (container_id,)).fetchone()
        if not r:
            raise NotFound(container_id)
        d = dict(r)
        d["metadata"] = json.loads(d["metadata"])
        d["segments"] = [dict(x) for x in self.db.execute("SELECT * FROM segments WHERE container_id=? ORDER BY seg_index", (container_id,))]
        return d

    # ---- 3. context segments by client and topic -------------------------------------------------------------------------------------------
    def segments(self, client_id: str, topic: Optional[str] = None, channels: Optional[list] = None) -> list[dict]:
        sql = ("SELECT s.*, c.channel, c.started_at FROM segments s JOIN containers c ON c.container_id = s.container_id WHERE s.client_id=?")
        p: list = [client_id]
        if topic:
            sql += " AND s.topic_label = ?"
            p.append(topic)
        if channels:
            sql += f" AND c.channel IN ({','.join('?' * len(channels))})"
            p += channels
        rows = self._allowed([dict(r) for r in self.db.execute(sql + " ORDER BY c.started_at, s.container_id, s.seg_index", p)])
        for r in rows:
            r["turn_ids"] = [x[0] for x in self.db.execute("SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", (r["segment_id"],))]
        return rows

    def topics_for_client(self, client_id: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.segments(client_id):
            out[s["topic_label"]] = out.get(s["topic_label"], 0) + 1
        return out

    # ---- 4. evidence spans by source, speaker, date, topic, text --------------------------------------------------------------------------
    def find_spans(self, client_id: str, *, container_id: Optional[str] = None, speaker_id: Optional[str] = None, channels: Optional[list] = None,
                   start: Optional[str] = None, end: Optional[str] = None, topic: Optional[str] = None, text: Optional[str] = None,
                   dialogue_act: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Exact-text evidence spans for one client.  With `text`, ranked by full-text relevance (bm25); otherwise chronological.
        A date filter uses the turn's own time when the source has one, else the container's start (calls and original meetings carry no turn time)."""
        when = "COALESCE(t.ts, c.started_at)"
        sql = ("SELECT e.evidence_id, e.turn_id, e.container_id, e.client_id, e.span_kind, e.exact_text, e.start_offset, e.end_offset, e.source_ref, "
               f"t.speaker_id, t.speaker_name, t.speaker_role, t.attribution_status, t.attribution_confidence, t.seq, {when} AS when_ts, c.channel, c.subject "
               "FROM evidence_spans e JOIN turns t ON t.turn_id = e.turn_id JOIN containers c ON c.container_id = e.container_id")
        join_fts = bool(text) and self.store.has_fts and _fts_query(text)
        if join_fts:
            sql = sql.replace("SELECT e.evidence_id", "SELECT bm25(turns_fts) AS score, e.evidence_id", 1) + " JOIN turns_fts ON turns_fts.turn_id = t.turn_id"
        where, p = ["e.client_id = ?", "e.span_kind = 'full_turn'"], [client_id]
        if container_id:
            where.append("e.container_id = ?"); p.append(container_id)
        if speaker_id:
            where.append("t.speaker_id = ?"); p.append(speaker_id)
        if channels:
            where.append(f"c.channel IN ({','.join('?' * len(channels))})"); p += channels
        if start:
            where.append(f"substr({when},1,10) >= ?"); p.append(start[:10])
        if end:
            where.append(f"substr({when},1,10) <= ?"); p.append(end[:10])
        if topic:
            where.append("EXISTS (SELECT 1 FROM segment_turns st JOIN segments s ON s.segment_id = st.segment_id WHERE st.turn_id = t.turn_id AND s.topic_label = ?)"); p.append(topic)
        if dialogue_act:
            where.append("EXISTS (SELECT 1 FROM annotations a WHERE a.target_type='turn' AND a.target_id = t.turn_id AND a.kind='dialogue_act' AND a.value = ?)"); p.append(dialogue_act)
        if text:
            if join_fts:
                where.append("turns_fts MATCH ?"); p.append(_fts_query(text))
            else:
                for tok in [x for x in re.findall(r"\w+", text.lower()) if len(x) > 2]:
                    where.append("lower(e.exact_text) LIKE ?"); p.append(f"%{tok}%")
        order = "score, t.turn_id" if join_fts else f"{when}, e.container_id, t.seq"
        rows = []
        for r in self.db.execute(f"{sql} WHERE {' AND '.join(where)} ORDER BY {order}", p):
            d = dict(r)
            if self.check(d["container_id"])[0]:
                d.pop("score", None)
                rows.append(d)
                if len(rows) >= limit:
                    break
        return rows

    # ---- 5. the complete context around a span -----------------------------------------------------------------------------------------------
    def span_context(self, id_: str, window: int = 2) -> dict:
        """The span's turn, its neighbours, its segment(s), the whole container's turn list and its source references."""
        cid = self._container_of(id_)
        self._require(cid)
        tid = id_
        sp = self.db.execute("SELECT turn_id FROM evidence_spans WHERE evidence_id=?", (id_,)).fetchone()
        if sp:
            tid = sp[0]
        t = self.db.execute("SELECT * FROM turns WHERE turn_id=?", (tid,)).fetchone()
        if not t:
            raise NotFound(id_)
        seq = t["seq"]
        turns = self.turns_in_container(cid)
        segs = [dict(r) for r in self.db.execute(
            "SELECT s.* FROM segments s JOIN segment_turns st ON st.segment_id = s.segment_id WHERE st.turn_id=? ORDER BY s.seg_index", (tid,))]
        for s in segs:
            s["turn_ids"] = [x[0] for x in self.db.execute("SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", (s["segment_id"],))]
        rels = [dict(r) for r in self.db.execute("SELECT from_turn_id, to_turn_id, rel_type, basis, provenance FROM turn_relations "
                                                "WHERE from_turn_id=? OR to_turn_id=? ORDER BY rel_type, from_turn_id", (tid, tid))]
        return dict(turn=next(x for x in turns if x["turn_id"] == tid),
                    before=[x for x in turns if seq - window <= x["seq"] < seq], after=[x for x in turns if seq < x["seq"] <= seq + window],
                    segments=segs, relations=rels, container=self.container(cid), container_turns=turns, source_references=self.source_references(tid))

    # ---- 6. chronological communications across channels ------------------------------------------------------------------------------------
    def timeline(self, client_id: str, channels: Optional[list] = None, start: Optional[str] = None, end: Optional[str] = None,
                 include_derived: bool = False) -> list[dict]:
        """Containers of every channel in time order (container start, then id).  CRM records, if asked for, appear marked layer='derived'."""
        items = [dict(layer="original", record_id=c["container_id"], channel=c["channel"], kind=c["container_kind"], ts=c["started_at"], subject=c["subject"],
                      turn_count=c["turn_count"]) for c in self.containers_for_client(client_id, channels, start, end)]
        if include_derived:
            sql, p = "SELECT * FROM derived_artifacts WHERE client_id=?", [client_id]
            if channels:
                sql += f" AND channel IN ({','.join('?' * len(channels))})"; p += channels
            for r in self.db.execute(sql, p):
                if self.check(r["crm_record_id"])[0] and (not start or r["created_at"][:10] >= start[:10]) and (not end or r["created_at"][:10] <= end[:10]):
                    items.append(dict(layer="derived", record_id=r["crm_record_id"], channel=r["channel"], kind=r["artifact_type"], ts=r["created_at"], subject=r["subject"],
                                      turn_count=0))
        return sorted(items, key=lambda x: (x["ts"] or "", x["record_id"]))

    # ---- 7. the original sources behind a derived CRM record ----------------------------------------------------------------------------------
    def derived_support(self, crm_record_id: str) -> dict:
        """The CRM record (derived, as written) and, SEPARATELY, the original containers it cites that THIS requester may read.
        Access to the CRM record never grants access to its sources; unreadable sources are counted, not shown."""
        self._require(crm_record_id)
        d = self.db.execute("SELECT * FROM derived_artifacts WHERE crm_record_id=?", (crm_record_id,)).fetchone()
        if not d:
            raise NotFound(crm_record_id)
        refs = [dict(r) for r in self.db.execute("SELECT * FROM derived_sources WHERE crm_record_id=? ORDER BY source_ref", (crm_record_id,))]
        sources, withheld, unresolved = [], 0, []
        for r in refs:
            if r["resolution"] != "container":
                unresolved.append(r["source_ref"]); continue
            if self.check(r["resolved_container_id"])[0]:
                sources.append(self.container(r["resolved_container_id"]) | dict(turns=self.turns_in_container(r["resolved_container_id"])))
            else:
                withheld += 1
        return dict(derived=dict(d) | dict(layer="derived"), original_sources=sources, withheld_source_count=withheld, unresolved_refs=unresolved)

    def derived_for_container(self, container_id: str) -> list[dict]:
        """CRM records that cite a container (derived records about it), each readable only under its own policy."""
        self._require(container_id)
        rows = [dict(r) for r in self.db.execute(
            "SELECT d.* FROM derived_artifacts d JOIN derived_sources s ON s.crm_record_id = d.crm_record_id WHERE s.resolved_container_id=? ORDER BY d.created_at", (container_id,))]
        return [r | dict(layer="derived") for r in rows if self.check(r["crm_record_id"])[0]]

    # ---- 8. all source references of a parsed record --------------------------------------------------------------------------------------------
    def source_references(self, id_: str) -> dict:
        """Provenance of any container, turn, segment, evidence span or CRM record: file, native key, hashes, revision and (for a CRM record) its sources."""
        d = self.db.execute("SELECT * FROM derived_artifacts WHERE crm_record_id=?", (id_,)).fetchone()
        if d:
            self._require(id_)
            a = dict(self.db.execute("SELECT * FROM source_artifacts WHERE artifact_id=?", (id_,)).fetchone())
            refs = [dict(r) for r in self.db.execute("SELECT source_ref, resolved_container_id, resolution FROM derived_sources WHERE crm_record_id=? ORDER BY source_ref", (id_,))]
            return dict(record_id=id_, kind="derived_crm_record", layer="derived", source_file=a["source_file"], source_ref=a["source_ref"], revision=a["revision"],
                        content_hash=a["content_hash"], parser_version=a["parser_version"], provenance=json.loads(a["provenance"]), cites=refs)
        cid = self._container_of(id_)
        self._require(cid)
        a = dict(self.db.execute("SELECT * FROM source_artifacts WHERE artifact_id=?", (cid,)).fetchone())
        out = dict(record_id=id_, container_id=cid, layer=a["layer"], channel=a["channel"], source_file=a["source_file"], container_source_ref=a["source_ref"],
                   source_ts=a["source_ts"], ingested_at=a["ingested_at"], revision=a["revision"], content_hash=a["content_hash"], parser_version=a["parser_version"],
                   provenance=json.loads(a["provenance"]))
        t = self.db.execute("SELECT turn_id, seq, source_ref FROM turns WHERE turn_id=?", (id_,)).fetchone()
        if t:
            out |= dict(kind="turn", turn_source_ref=t["source_ref"], seq=t["seq"])
        elif self.db.execute("SELECT 1 FROM segments WHERE segment_id=?", (id_,)).fetchone():
            out |= dict(kind="segment", constituent_turns=[x[0] for x in self.db.execute("SELECT turn_id FROM segment_turns WHERE segment_id=? ORDER BY position", (id_,))])
        elif self.db.execute("SELECT 1 FROM evidence_spans WHERE evidence_id=?", (id_,)).fetchone():
            r = self.db.execute("SELECT turn_id, source_ref, span_kind, start_offset, end_offset FROM evidence_spans WHERE evidence_id=?", (id_,)).fetchone()
            out |= dict(kind="evidence_span", turn_id=r["turn_id"], turn_source_ref=r["source_ref"], span_kind=r["span_kind"], start_offset=r["start_offset"], end_offset=r["end_offset"])
        else:
            out |= dict(kind="container")
        return out
