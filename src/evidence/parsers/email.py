"""Email parser.  Primary unit: the thread.  Every message stays individually addressable; the body is stored whole and ALSO split into
newly authored text and quoted earlier text, but only where an explicit quoting marker identifies the quote.  Nothing is inferred."""
from __future__ import annotations

import re

from ..model import ParsedContainer, ParsedTurn, ParseError
from ..sources import Sources

# explicit quote markers.  A tail marker begins a quoted tail that runs to the end of the body; '>' lines are quoted wherever they occur.
_TAIL = [("on_wrote", re.compile(r"^\s*On .{5,200}wrote:\s*$", re.I)),
         ("original_message", re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I)),
         ("forwarded", re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I)),
         ("header_block", re.compile(r"^\s*From:\s.+$"))]
_QUOTE_LINE = re.compile(r"^\s*>")
_HEADER_FOLLOW = re.compile(r"^\s*(Sent|Date|To|Subject):")


def split_quoted(body: str) -> tuple[str, str, str]:
    """(authored_text, quoted_text, quote_basis).  authored + quoted partition the body's lines; a body with no marker is all authored."""
    lines = body.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        for name, rx in _TAIL:
            if rx.match(ln.rstrip("\r\n")) and (name != "header_block" or any(_HEADER_FOLLOW.match(x) for x in lines[i + 1:i + 4])):
                return "".join(lines[:i]).rstrip(), "".join(lines[i:]), name
    q = [ln for ln in lines if _QUOTE_LINE.match(ln)]
    if q:
        return "".join(ln for ln in lines if not _QUOTE_LINE.match(ln)).strip(), "".join(q), "angle_bracket_lines"
    return body, "", "no_quote_markers"


def _ids(s: str) -> str:
    return "|".join(x for x in re.split(r"[|,;\s]+", s or "") if x)


def parse_email_threads(src: Sources) -> tuple[list, list]:
    msgs = src.grouped("emails.csv", "thread_id")
    out, failed = [], []
    for t in src.rows("email_threads.csv"):
        tid = t["thread_id"]
        try:
            out.append(_parse_thread(src, tid, t, msgs.get(tid, [])))
        except (ParseError, KeyError, ValueError) as e:
            failed.append((tid, "Email", t.get("client_id", ""), f"{type(e).__name__}: {e}"))
    return out, failed


def _parse_thread(src: Sources, tid: str, t: dict, rows: list) -> ParsedContainer:
    if not rows:
        raise ParseError(f"thread {tid} has no messages")
    rows = sorted(rows, key=lambda r: int(r["message_seq"]))
    seqs = [int(r["message_seq"]) for r in rows]
    if seqs != list(range(1, len(rows) + 1)):
        raise ParseError(f"thread {tid}: message_seq is not 1..n: {seqs}")
    if int(t["message_count"]) != len(rows):
        raise ParseError(f"thread {tid}: message_count {t['message_count']} != {len(rows)} messages")
    if {r["client_id"] for r in rows} != {t["client_id"]}:
        raise ParseError(f"thread {tid}: messages disagree about the client")
    ts = [r["timestamp"] for r in rows]
    by_msgid = {r["message_id"]: r["email_id"] for r in rows}
    turns = []
    for r in rows:
        authored, quoted, basis = split_quoted(r["body"])
        irt = r["in_reply_to"]
        turns.append(ParsedTurn(
            turn_id=r["email_id"], seq=int(r["message_seq"]), ts=r["timestamp"], ts_basis="message", speaker_id=r["sender_id"],
            speaker_name=r["sender_name"], speaker_role=None, attribution_status="source_stated", original_text=r["body"],
            source_ref=r["email_id"],
            email=dict(message_id=r["message_id"], subject=r["subject"], from_id=r["sender_id"], from_email=r["sender_email"],
                       to_ids=_ids(r["to_ids"]), to_emails=_ids(r["to_emails"]), cc_ids=_ids(r["cc_ids"]), cc_emails=_ids(r["cc_emails"]),
                       in_reply_to=irt, in_reply_to_turn_id=by_msgid.get(irt) if irt else None,
                       reference_ids=r["references"], attachment_refs=_ids(r["attachment_refs"]),
                       authored_text=authored, quoted_text=quoted, quote_basis=basis)))
    return ParsedContainer(
        container_id=tid, layer="original", channel="Email", kind="email_thread", client_id=t["client_id"], advisor_id=t["advisor_id"],
        subject=t["subject"], started_at=ts[0], ended_at=ts[-1], participant_ids=t["participant_ids"], source_platform=t["source_platform"],
        source_file=src.rel("emails.csv"), source_ref=tid, classification_basis=None,
        metadata=dict(references_source_ids=t["references_source_ids"], includes_third_party=t["includes_third_party"],
                      sequence_matches_timestamps=ts == sorted(ts), source_type=t["source_type"], consent_status=t["consent_status"]),
        turns=turns, email_thread=dict(includes_third_party=t["includes_third_party"], references_source_ids=t["references_source_ids"]),
        provenance=dict(files=[src.rel("emails.csv"), src.rel("email_threads.csv")], keys=["thread_id", "email_id"]))
