"""Read-only view of the multi-channel source data (datasets/*.csv) for the "Learn about the data" page.

Pure CSV loading, no business logic and no auth: api/main.py owns authorization and client scoping.  Everything here is synthetic.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

DS = Path(__file__).resolve().parents[1] / "datasets"
CHANNELS = [  # (channel, platform, what one source-native record is)
    ("Meetings", "OneDrive and Teams", "turn-level transcript of a recorded meeting"),
    ("Calls", "Telephony platform", "turn-level transcript of a phone call"),
    ("Email", "Exchange mailbox", "threaded messages with headers (from, to, cc, subject)"),
    ("Chat", "Messaging platform", "short messages in a conversation"),
]
MAX_TEXT = 2500


def _rows(rel: str) -> list[dict]:
    with open(DS / rel, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=None)
def _cached(rel: str) -> tuple:
    return tuple(_rows(rel))


def _clip(t: str) -> str:
    return t if len(t) <= MAX_TEXT else t[:MAX_TEXT] + "…"


@lru_cache(maxsize=1)
def _scenarios() -> dict:
    return {r["scenario_id"]: r for r in _cached("evaluation/scenarios.csv")}


def scenarios(allowed: set | None) -> list[dict]:
    return [s for s in _scenarios().values() if allowed is None or s["client_id"] in allowed]


def scenario_client(scenario_id: str) -> str | None:
    s = _scenarios().get(scenario_id)
    return s["client_id"] if s else None


def overview() -> dict:
    manifest = _cached("source_manifest.csv")
    chans = []
    counts = {
        "Meetings": (sum(1 for r in manifest if r["assigned_channel"] == "Meetings") + len(_cached("meetings/meetings.csv")), "meetings"),
        "Calls": (sum(1 for r in manifest if r["assigned_channel"] == "Calls"), "calls"),
        "Email": (len(_cached("emails/email_threads.csv")), "threads"),
        "Chat": (len(_cached("chats/chat_conversations.csv")), "conversations"),
    }
    items = {"Email": len(_cached("emails/emails.csv")), "Chat": len(_cached("chats/chats.csv"))}
    turns = sum(int(r["turn_count"]) for r in manifest)
    for ch, plat, what in CHANNELS:
        n, unit = counts[ch]
        chans.append(dict(channel=ch, platform=plat, what=what, records=n, unit=unit, messages=items.get(ch)))
    crm = []
    for art in ("crm_meetings", "crm_calls", "crm_email", "crm_chat"):
        rows = _cached(f"crm/{art}.csv")
        crm.append(dict(artifact=art + ".csv", total=len(rows),
                        automatic=sum(1 for r in rows if r["transfer_method"].startswith("automatic")),
                        manual=sum(1 for r in rows if r["transfer_method"].startswith("manual"))))
    return dict(channels=chans, crm=crm, original_turns=turns,
                classification=[dict(original=k, assigned=v) for k, v in (("video", "Meetings"), ("in-person", "Meetings"), ("telephone", "Calls"))])


def _source(source_id: str, conv) -> dict:
    """Raw content of one source record: {title, meta, messages:[{who, role, at, text}]}."""
    p = source_id[:3]
    if p == "INT":
        m = conv.meeting(source_id)
        return dict(title=m[0]["meeting_context"], meta=f'{m[0]["channel"]} · {len(m)} turns',
                    messages=[dict(who=t["speaker"], role=t["speaker"], at=t["interaction_datetime"], text=_clip(t["utterance"]), turn=int(t["turn_index"])) for t in m])
    if p == "THR":
        th = next(r for r in _cached("emails/email_threads.csv") if r["thread_id"] == source_id)
        ms = [r for r in _cached("emails/emails.csv") if r["thread_id"] == source_id]
        return dict(title=th["subject"], meta=f'email thread · {len(ms)} messages',
                    messages=[dict(who=r["sender_name"], role=f'to {r["to_emails"]}' + (f' · cc {r["cc_emails"]}' if r["cc_emails"] else ""), at=r["timestamp"], text=_clip(r["body"]), turn=int(r["message_seq"])) for r in ms])
    if p == "CHT":
        ms = [r for r in _cached("chats/chats.csv") if r["chat_conversation_id"] == source_id]
        return dict(title="Chat conversation", meta=f"chat · {len(ms)} messages",
                    messages=[dict(who=r["sender_name"], role=r["sender_role"], at=r["timestamp"], text=_clip(r["message_text"]), turn=int(r["message_seq"])) for r in ms])
    mt = next(r for r in _cached("meetings/meetings.csv") if r["meeting_id"] == source_id)
    ms = [r for r in _cached("meetings/meeting_turns.csv") if r["meeting_id"] == source_id]
    return dict(title=mt["subject"], meta=f'meeting · {len(ms)} turns · {mt["meeting_platform"]}',
                messages=[dict(who=r["speaker_name"], role=r["speaker_role"], at=r["turn_timestamp"], text=_clip(r["utterance"]), turn=int(r["turn_index"])) for r in ms])


def scenario_detail(scenario_id: str, conv) -> dict | None:
    sc = _scenarios().get(scenario_id)
    if sc is None:
        return None
    links = sorted((r for r in _cached("evaluation/scenario_links.csv") if r["scenario_id"] == scenario_id), key=lambda r: int(r["sequence_no"]))
    crm = [r for a in ("meetings", "calls", "email", "chat") for r in _cached(f"crm/crm_{a}.csv")]
    steps = []
    for l in links:
        sid = l["source_id"]
        records = [dict(crm_record_id=c["crm_record_id"], type=c["record_type"], created_at=c["created_at"], subject=c["subject"], text=c["text"], transfer=c["transfer_method"])
                   for c in crm if sid in c["source_refs"].split("|")]
        steps.append(dict(sequence=int(l["sequence_no"]), channel=l["channel"], source_id=sid, timestamp=l["source_timestamp"], origin=l["record_origin"],
                          link_basis=l["link_basis"], note=l["storyline_note"], source=_source(sid, conv), crm=records))
    return dict(scenario=sc, steps=steps)


# ---- raw table browser: every CSV the page talks about, one key per table ---------------------------------------------------------------------------------------
CONV = Path(__file__).resolve().parents[1] / "data" / "conversations.csv"
TABLES = {  # key: (file relative to repo root, row filter or None, label)
    "meetings_original": ("data/conversations.csv", ("channel", {"video", "in-person"}), "Original meeting transcripts (video + in-person)"),
    "meetings_generated": ("datasets/meetings/meeting_turns.csv", None, "Generated follow-up meetings (turn level)"),
    "calls": ("data/conversations.csv", ("channel", {"telephone"}), "Call transcripts (telephone)"),
    "emails": ("datasets/emails/emails.csv", None, "Email messages"),
    "chats": ("datasets/chats/chats.csv", None, "Chat messages"),
    "crm_meetings": ("datasets/crm/crm_meetings.csv", None, "CRM: meetings"),
    "crm_calls": ("datasets/crm/crm_calls.csv", None, "CRM: calls"),
    "crm_email": ("datasets/crm/crm_email.csv", None, "CRM: email"),
    "crm_chat": ("datasets/crm/crm_chat.csv", None, "CRM: chat"),
}
ROOT = DS.parent


@lru_cache(maxsize=None)
def _table(key: str) -> tuple[list[str], tuple]:
    rel, flt, _ = TABLES[key]
    with open(ROOT / rel, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        rows = [r for r in rd if flt is None or r[flt[0]] in flt[1]]
        return list(rd.fieldnames or []), tuple(rows)


def table_keys() -> list[dict]:
    return [dict(key=k, file=v[0], label=v[2]) for k, v in TABLES.items()]


def table_page(key: str, allowed: set | None, client_id: str | None, offset: int, limit: int) -> dict | None:
    if key not in TABLES:
        return None
    cols, rows = _table(key)
    sel = [r for r in rows if (allowed is None or r["client_id"] in allowed) and (not client_id or r["client_id"] == client_id)]
    return dict(key=key, file=TABLES[key][0], label=TABLES[key][2], columns=cols, total=len(sel), offset=offset, rows=[list(r[c] for c in cols) for r in sel[offset:offset + limit]])
