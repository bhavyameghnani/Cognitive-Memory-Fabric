"""Chat parser.  Primary unit: the chat conversation.  Every message keeps its own id, sender, timestamp and text; reply links are kept only where
the source states reply_to_message_id.  Grouping into context segments happens later (semantics.py) and never merges messages."""
from __future__ import annotations

from ..model import ParsedContainer, ParsedTurn, ParseError
from ..sources import Sources


def parse_chats(src: Sources) -> tuple[list, list]:
    msgs = src.grouped("chats.csv", "chat_conversation_id")
    out, failed = [], []
    for c in src.rows("chat_conversations.csv"):
        cid = c["chat_conversation_id"]
        try:
            out.append(_parse(src, cid, c, msgs.get(cid, [])))
        except (ParseError, KeyError, ValueError) as e:
            failed.append((cid, "Chat", c.get("client_id", ""), f"{type(e).__name__}: {e}"))
    return out, failed


def _parse(src, cid, c, rows) -> ParsedContainer:
    rows = sorted(rows, key=lambda r: int(r["message_seq"]))
    if [int(r["message_seq"]) for r in rows] != list(range(1, len(rows) + 1)):
        raise ParseError(f"{cid}: message_seq is not 1..n")
    if int(c["message_count"]) != len(rows):
        raise ParseError(f"{cid}: message_count {c['message_count']} != {len(rows)}")
    if {r["client_id"] for r in rows} != {c["client_id"]}:
        raise ParseError(f"{cid}: messages disagree about the client")
    ids = {r["message_id"] for r in rows}
    turns = []
    for r in rows:
        rt = r["reply_to_message_id"] or None
        if rt and rt not in ids:
            raise ParseError(f"{cid}: {r['message_id']} replies to {rt}, which is not in the conversation")
        turns.append(ParsedTurn(turn_id=r["message_id"], seq=int(r["message_seq"]), ts=r["timestamp"], ts_basis="message", speaker_id=r["sender_id"],
                                speaker_name=r["sender_name"], speaker_role=r["sender_role"], attribution_status="source_stated",
                                original_text=r["message_text"], source_ref=r["message_id"],
                                chat=dict(reply_to_turn_id=rt, recipient_id=r["recipient_id"])))
    return ParsedContainer(
        container_id=cid, layer="original", channel="Chat", kind="chat_conversation", client_id=c["client_id"], advisor_id=c["advisor_id"],
        subject=None, started_at=rows[0]["timestamp"], ended_at=rows[-1]["timestamp"], participant_ids=c["participant_ids"],
        source_platform=c["source_platform"], source_file=src.rel("chats.csv"), source_ref=cid, classification_basis=None,
        metadata=dict(references_source_ids=c["references_source_ids"], source_type=c["source_type"], consent_status=c["consent_status"]),
        turns=turns, provenance=dict(files=[src.rel("chats.csv"), src.rel("chat_conversations.csv")], keys=["chat_conversation_id", "message_id"]))
