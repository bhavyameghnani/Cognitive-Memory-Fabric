"""Meeting parser.  Primary unit: the meeting session.  Two source shapes: generated meetings (meetings.csv + meeting_turns.csv, with turn
timestamps and speaker ids) and the original video / in-person interactions (shared reader in call.py, no per-turn time)."""
from __future__ import annotations

from ..model import ParsedContainer, ParsedTurn, ParseError
from ..sources import Sources
from .call import parse_original_interactions


def parse_meetings(src: Sources) -> tuple[list, list]:
    turns_by = src.grouped("meeting_turns.csv", "meeting_id")
    meta = {r["meeting_id"]: r for r in src.rows("meeting_metadata.csv")}
    out, failed = [], []
    for m in src.rows("meetings.csv"):
        mid = m["meeting_id"]
        try:
            out.append(_parse_generated(src, mid, m, meta[mid], turns_by.get(mid, [])))
        except (ParseError, KeyError, ValueError) as e:
            failed.append((mid, "Meetings", m.get("client_id", ""), f"{type(e).__name__}: {e}"))
    o, f = parse_original_interactions(src, "Meetings")
    return out + o, failed + f


def _parse_generated(src, mid, m, md, rows) -> ParsedContainer:
    rows = sorted(rows, key=lambda r: int(r["turn_index"]))
    if [int(r["turn_index"]) for r in rows] != list(range(1, len(rows) + 1)):
        raise ParseError(f"{mid}: turn_index is not 1..n")
    if int(m["turn_count"]) != len(rows):
        raise ParseError(f"{mid}: turn_count {m['turn_count']} != {len(rows)} turns")
    if {r["client_id"] for r in rows} != {m["client_id"]}:
        raise ParseError(f"{mid}: turns disagree about the client")
    turns = [ParsedTurn(turn_id=r["turn_id"], seq=int(r["turn_index"]), ts=r["turn_timestamp"], ts_basis="turn", speaker_id=r["speaker_id"],
                        speaker_name=r["speaker_name"], speaker_role=r["speaker_role"], attribution_status="source_stated",
                        original_text=r["utterance"], source_ref=r["turn_id"]) for r in rows]
    return ParsedContainer(
        container_id=mid, layer="original", channel="Meetings", kind="meeting", client_id=m["client_id"], advisor_id=m["advisor_id"],
        subject=m["subject"], started_at=m["meeting_start_datetime"], ended_at=m["meeting_end_datetime"], participant_ids=md["participant_ids"],
        source_platform=m["source_platform"], source_file=src.rel("meetings.csv"), source_ref=mid, classification_basis=None,
        metadata=dict(organizer_id=m["organizer_id"], meeting_platform=m["meeting_platform"], duration_minutes=m["duration_minutes"],
                      recording_ref=md["recording_ref"], transcript_ref=md["transcript_ref"], references_source_ids=md["references_source_ids"],
                      includes_third_party_participant=md["includes_third_party_participant"], consent_status=m["consent_status"],
                      audio_or_diarization_available=False),
        turns=turns,
        provenance=dict(files=[src.rel("meetings.csv"), src.rel("meeting_turns.csv"), src.rel("meeting_metadata.csv")], keys=["meeting_id", "turn_id"]))
