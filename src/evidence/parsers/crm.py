"""Derived-artifact (CRM) parser.  CRM rows are advisor-written or automatically logged records ABOUT communications.  They are parsed on their
own pathway and never become turns, segments or evidence spans.  source_refs are container-level ids in this dataset; no turn-level support is invented."""
from __future__ import annotations

from ..model import ParsedCrm, ParseError
from ..sources import Sources

CRM_FILES = ["crm_meetings.csv", "crm_email.csv", "crm_calls.csv", "crm_chat.csv"]


def parse_crm(src: Sources) -> tuple[list, list]:
    out, failed = [], []
    for fname in CRM_FILES:
        for r in src.rows(fname):
            try:
                if r["record_layer"] != "crm_derived" or r["is_original_source_evidence"].lower() != "false":
                    raise ParseError("record is not marked as a derived artifact")
                refs = [x for x in r["source_refs"].split("|") if x]
                if not refs:
                    raise ParseError("no source_refs")
                out.append(ParsedCrm(r["crm_record_id"], src.rel(fname), r["record_type"], r["channel"], r["client_id"], r["advisor_id"] or None,
                                     r["author_id"] or None, r["created_at"], r["subject"] or None, r["text"], refs, r["transfer_method"] or None,
                                     r["record_layer"], False))
            except (ParseError, KeyError) as e:
                failed.append((r.get("crm_record_id", "?"), r.get("channel", ""), r.get("client_id", ""), f"{type(e).__name__}: {e}"))
    return out, failed
