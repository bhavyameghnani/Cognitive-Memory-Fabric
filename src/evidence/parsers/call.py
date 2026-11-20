"""Call parser, and the shared reader for the ORIGINAL interactions in data/conversations.csv.

Channel comes from the validated source manifest (62 Calls, 88 Meetings), never from the file's own synthetic_meeting source_type.
The original file has a Client/Advisor label per turn but no speaker identifier and no per-turn timestamp: speaker ids are derived from the
interaction's client/advisor ids and marked derived_from_role_label; turns carry no timestamp of their own.  No audio or diarization data
exists, and none is invented."""
from __future__ import annotations

from ..model import ParsedContainer, ParsedTurn, ParseError
from ..sources import Sources


def parse_original_interactions(src: Sources, want_channel: str) -> tuple[list, list]:
    manifest = {r["interaction_id"]: r for r in src.rows("source_manifest.csv")}
    meta_file = "calls_metadata.csv" if want_channel == "Calls" else "original_meetings_metadata.csv"
    meta = {r["call_source_id"]: r for r in src.rows(meta_file)}
    conv = src.grouped("conversations.csv", "interaction_id")
    reg = src.registry()
    out, failed = [], []
    for iid, m in manifest.items():
        if m["assigned_channel"] != want_channel:
            continue
        try:
            out.append(_parse(src, iid, m, conv[iid], reg, want_channel, meta_file))
        except (ParseError, KeyError, ValueError) as e:
            failed.append((iid, want_channel, m.get("client_id", ""), f"{type(e).__name__}: {e}"))
    return out, failed


def parse_calls(src: Sources) -> tuple[list, list]:
    return parse_original_interactions(src, "Calls")


def _parse(src, iid, m, rows, reg, channel, meta_file) -> ParsedContainer:
    rows = sorted(rows, key=lambda r: int(r["turn_index"]))
    if [int(r["turn_index"]) for r in rows] != list(range(1, len(rows) + 1)):
        raise ParseError(f"{iid}: turn_index is not 1..n")
    if int(m["turn_count"]) != len(rows):
        raise ParseError(f"{iid}: manifest turn_count {m['turn_count']} != {len(rows)} turns")
    client, advisor = rows[0]["client_id"], rows[0]["advisor_id"]
    if {r["client_id"] for r in rows} != {client} or m["client_id"] != client:
        raise ParseError(f"{iid}: client disagrees between turns and manifest")
    turns = []
    for r in rows:
        role = r["speaker"]
        if role not in ("Client", "Advisor"):
            raise ParseError(f"{iid} turn {r['turn_index']}: unexpected speaker label {role!r}")
        sid = client if role == "Client" else advisor
        turns.append(ParsedTurn(
            turn_id=f"{iid}#T{r['turn_index']}", seq=int(r["turn_index"]), ts=None, ts_basis="container_start_only", speaker_id=sid,
            speaker_name=reg.get(sid, {}).get("display_name"), speaker_role=role, attribution_status="derived_from_role_label",
            original_text=r["utterance"], source_ref=f"{iid}#T{r['turn_index']}"))
    kind = "call" if channel == "Calls" else "meeting"
    return ParsedContainer(
        container_id=iid, layer="original", channel=channel, kind=kind, client_id=client, advisor_id=advisor, subject=rows[0]["meeting_context"],
        started_at=rows[0]["interaction_datetime"], ended_at=None, participant_ids=f"{advisor}|{client}", source_platform=m["assigned_source_platform"],
        source_file="data/conversations.csv", source_ref=iid, classification_basis=m["classification_basis"],
        metadata=dict(original_channel_value=m["original_channel_value"], original_source_type=m["original_source_type"],
                      classification_confidence=m["classification_confidence"], platform_basis=m["platform_basis"],
                      consent_status=rows[0]["consent_status"], turn_timestamps_available=False, audio_or_diarization_available=False),
        turns=turns,
        provenance=dict(files=["data/conversations.csv", src.rel("source_manifest.csv"), src.rel(meta_file)], keys=["interaction_id", "turn_index"]))
