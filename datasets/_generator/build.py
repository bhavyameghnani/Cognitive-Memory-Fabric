"""Build the four-channel synthetic dataset from the authored content modules.

Writes ONLY under datasets/. Reads data/conversations.csv (read-only) for call metadata.
Usage:  python -I datasets/_generator/build.py [--overwrite-generated]
No LLM, no network, standard library only.
"""
import csv
import hashlib
import importlib
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent.parent
OUT = ROOT / "datasets"
CALLS_SRC = ROOT / "data" / "conversations.csv"

CH = {
    "meetings": ("Meetings", "OneDrive and Teams"),
    "email": ("Email", "Exchange mailbox"),
    "calls": ("Calls", "Telephony platform"),
    "chat": ("Chat", "Messaging platform"),
}
BATCH = "multichannel-v1"

# ----------------------------------------------------------------------------- people
CLIENTS = {
    1: ("Kenji", "Tanaka"), 2: ("Rebecca", "Holloway"), 3: ("Sofia", "Alvarez"),
    4: ("Grace", "Adeyemi"), 5: ("Lucas", "Moreau"), 6: ("Peg", "Sullivan"),
}
ADVISORS = {1: ("Priya", "Nair"), 2: ("Daniel", "Okafor"), 3: ("Elena", "Rossi")}
ADVISOR_OF = {1: 1, 2: 2, 3: 3, 4: 1, 5: 2, 6: 3}  # verified against data/conversations.csv by the validator
EXTERNAL = {  # existing named third parties, one per client scope
    "HP": dict(id="EXT-001", first="Hannah", last="Park", client=1, email="hannah.park@cpa.example",
               role="third_party_professional (CPA of CLIENT-001, per INT-046)"),
    "DH": dict(id="EXT-002", first="Dennis", last="Hall", client=6, email="dennis.hall@accountancy.example",
               role="third_party_professional (accountant of CLIENT-006, per INT-018)"),
}


_DOC = "relationship stated or implied in the utterances; confirmed in docs/dataset_design.md"
MENTIONED_ONLY = [  # (first name, display name, client, role, role basis) - people the calls mention but who never speak
    ("Mariko", "Mariko", 1, "family: spouse of CLIENT-001", _DOC), ("Yuki", "Yuki", 1, "family: daughter of CLIENT-001", _DOC),
    ("Ren", "Ren", 1, "family: son of CLIENT-001", _DOC),
    ("David", "David", 2, "family: husband of CLIENT-002", _DOC), ("Ethan", "Ethan", 2, "family: son of CLIENT-002", _DOC),
    ("Dorothy", "Dorothy", 2, "family: mother of CLIENT-002", _DOC),
    ("Mei", "Mei Lin Chu", 2, "third_party_professional: accountant of CLIENT-002", "stated in INT-032/INT-070/INT-078 ('your accountant Mei Lin Chu')"),
    ("Luis", "Luis", 2, "other: plant manager at CLIENT-002's business", "stated in INT-121 ('Luis, the plant manager')"),
    ("Mateo", "Mateo", 3, "family: partner of CLIENT-003", _DOC), ("Lucia", "Lucia", 3, "family: daughter of CLIENT-003", _DOC),
    ("Samuel", "Samuel", 4, "family: late husband of CLIENT-004", _DOC), ("Tobi", "Tobi", 4, "family: son of CLIENT-004", _DOC),
    ("Folake", "Folake", 4, "family: daughter of CLIENT-004", _DOC),
    ("Camille", "Camille", 5, "family: partner of CLIENT-005", _DOC), ("Amélie", "Amélie", 5, "family: daughter of CLIENT-005", _DOC),
    ("Frank", "Frank", 6, "family: husband of CLIENT-006", _DOC), ("Colleen", "Colleen", 6, "family: daughter of CLIENT-006", _DOC),
    ("Brian", "Brian", 6, "family: son of CLIENT-006", _DOC),
]


# Original conversations.csv: the `channel` column is the only modality field (source_type is the generic
# label "synthetic_meeting" on every row, so it cannot distinguish a call from a meeting).
MODALITY = {
    "telephone": ("Calls", "high", "channel='telephone': a voice-only call"),
    "video": ("Meetings", "high", "channel='video': a video meeting (not a voice-only call)"),
    "in-person": ("Meetings", "high", "channel='in-person': a face-to-face meeting"),
}


def classify(orig_channel):
    return MODALITY.get(orig_channel, ("Unclassified", "none", f"channel={orig_channel!r} is not a recognised modality"))


def cid(n): return f"CLIENT-{n:03d}"
def aid(n): return f"ADVISOR-{n:03d}"


def coverage(source_id, pct):
    """Deterministic sampling: the CRM never logs everything (manual transfer)."""
    return int(hashlib.sha1(source_id.encode()).hexdigest(), 16) % 100 < pct


def person(code, client):
    """code in A/C/HP/DH -> (id, display name, email)."""
    if code == "A":
        a = ADVISOR_OF[client]
        f, l = ADVISORS[a]
        return aid(a), f"{f} {l}", f"{f.lower()}.{l.lower()}@advisory.example"
    if code == "C":
        f, l = CLIENTS[client]
        return cid(client), f"{f} {l}", f"{f.lower()}.{l.lower()}@client.example"
    e = EXTERNAL[code]
    if e["client"] != client:
        raise ValueError(f"{code} not permitted for client {client}")
    return e["id"], f"{e['first']} {e['last']}", e["email"]


def rank(x):
    """Append-only ID tier: 0 storyline records, 1 first routine batch, 2 routine batch added in the governance pass."""
    return x.get("tier", 1 if x.get("routine") else 0)


def parse_iso(s): return datetime.fromisoformat(s)
def iso(d): return d.strftime("%Y-%m-%dT%H:%M:%S")


LOCKED = []


def write_csv(path, header, rows, allow):
    path.parent.mkdir(parents=True, exist_ok=True)
    import io
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    data = buf.getvalue().encode("utf-8")
    if path.exists():
        if path.read_bytes() == data:      # unchanged: do not touch it (it may be open in another program)
            return len(rows)
        if not allow:
            raise SystemExit(f"refusing to overwrite existing file: {path}")
    try:
        path.write_bytes(data)
    except PermissionError:
        LOCKED.append(str(path))           # open in another program (e.g. Excel): reported at the end, nothing is forced
    return len(rows)


def main(allow):
    mods = [importlib.import_module(f"content_c{i}") for i in range(1, 7)]
    meetings = [dict(m) for mod in mods for m in mod.MEETINGS]
    rout = importlib.import_module("content_routine")
    threads = [dict(t) for mod in mods + [rout] for t in mod.EMAILS]
    chats = [dict(c) for mod in mods + [rout] for c in mod.CHATS]
    scenarios = [dict(s) for mod in mods for s in mod.SCENARIOS]

    # ---------------------------------------------------------------- call index (read only)
    with open(CALLS_SRC, encoding="utf-8", newline="") as f:
        call_rows = list(csv.DictReader(f))
    calls = {}
    for r in call_rows:
        c = calls.setdefault(r["interaction_id"], dict(r, turns=0))
        c["turns"] += 1
    sha = hashlib.sha256(CALLS_SRC.read_bytes()).hexdigest()

    # ---------------------------------------------------------------- id assignment (chronological)
    meetings.sort(key=lambda m: (m["start"], m["client"]))
    for i, m in enumerate(meetings, 1): m["id"] = f"MTG-{i:03d}"
    threads.sort(key=lambda t: (rank(t), t["msgs"][0]["t"], t["client"]))
    for i, t in enumerate(threads, 1): t["id"] = f"THR-{i:03d}"
    chats.sort(key=lambda c: (rank(c), c["msgs"].splitlines()[0].split("|")[0], c["client"]))
    for i, c in enumerate(chats, 1): c["id"] = f"CHT-{i:03d}"
    keymap = {}
    for m in meetings: keymap[("MTG", m["key"])] = m["id"]; keymap[m["key"]] = m["id"]
    for t in threads: keymap[("THR", t["key"])] = t["id"]; keymap[t["key"]] = t["id"]
    for c in chats: keymap[("CHT", c["key"])] = c["id"]; keymap[c["key"]] = c["id"]

    def resolve(ref):
        if ref.startswith("INT-"): return ref
        kind, key = ref.split(":")
        return keymap[(kind, key)]

    # ---------------------------------------------------------------- meetings
    mrows, trows, mdrows = [], [], []
    start_of = {}   # source id -> (first ts, last ts)
    for m in meetings:
        n = m["client"]; adv = ADVISOR_OF[n]
        start = parse_iso(m["start"])
        t = start
        lines = [ln for ln in m["turns"].splitlines() if ln.strip()]
        for idx, ln in enumerate(lines, 1):
            role, text = ln.split(": ", 1)
            sid, sname, _ = person(role, n)
            trows.append(dict(
                turn_id=f"{m['id']}-T{idx:02d}", meeting_id=m["id"], client_id=cid(n), turn_index=idx,
                turn_timestamp=iso(t), speaker_role="Advisor" if role == "A" else "Client",
                speaker_id=sid, speaker_name=sname, utterance=text,
                channel=CH["meetings"][0], source_platform=CH["meetings"][1],
                source_type="synthetic_meeting_transcript", consent_status="synthetic"))
            t += timedelta(seconds=int(12 + 0.45 * len(text.split())))
        end = t + timedelta(seconds=30)
        dur = math.ceil((end - start).total_seconds() / 60)
        start_of[m["id"]] = (start, end)
        teams = m["platform"] == "Teams"
        base = f"onedrive://synthetic/CMF/{cid(n)}/Meetings/{m['id']}"
        mrows.append(dict(
            meeting_id=m["id"], client_id=cid(n), advisor_id=aid(adv), organizer_id=aid(adv),
            meeting_start_datetime=iso(start), meeting_end_datetime=iso(end), duration_minutes=dur,
            subject=m["subject"], channel=CH["meetings"][0], source_platform=CH["meetings"][1],
            meeting_platform=m["platform"] if teams else "OneDrive (uploaded recording)",
            turn_count=len(lines), source_type="synthetic_meeting_transcript", consent_status="synthetic"))
        mdrows.append(dict(
            meeting_id=m["id"], client_id=cid(n), channel=CH["meetings"][0], source_platform=CH["meetings"][1],
            source_type="synthetic_meeting_transcript", consent_status="synthetic",
            participant_ids=f"{aid(adv)}|{cid(n)}",
            participant_names=f"{ADVISORS[adv][0]} {ADVISORS[adv][1]}|{CLIENTS[n][0]} {CLIENTS[n][1]}",
            organizer_id=aid(adv), organizer_name=f"{ADVISORS[adv][0]} {ADVISORS[adv][1]}",
            recording_store="OneDrive",
            recording_ref=f"{base}.{'mp4' if teams else 'm4a'}",
            transcript_ref=(f"teams://synthetic/meetings/{m['id']}/transcript.vtt" if teams
                            else f"{base}.transcript.txt"),
            references_source_ids="|".join(resolve(r) for r in m["refs"]),
            client_scope=cid(n), data_classification="client-confidential",
            includes_third_party_participant="false", synthetic="true", generation_batch=BATCH))

    # ---------------------------------------------------------------- emails
    erows, throws = [], []
    allmsgs = []
    for th in threads:
        for seq, msg in enumerate(th["msgs"], 1):
            allmsgs.append((msg["t"], th["id"], seq, th, msg))
    allmsgs.sort(key=lambda x: (rank(x[3]), x[0], x[1], x[2]))
    eid_of = {}
    for i, (_, tid, seq, th, msg) in enumerate(allmsgs, 1): eid_of[(tid, seq)] = f"EML-{i:03d}"
    for th in threads:
        n = th["client"]; adv = ADVISOR_OF[n]
        mids = []
        ts = [m["t"] for m in th["msgs"]]
        parts, ext = [], False
        for seq, msg in enumerate(th["msgs"], 1):
            eid = eid_of[(th["id"], seq)]
            mid = f"<{eid}.{th['id']}@exchange.synthetic.example>"
            f_id, f_name, f_mail = person(msg["f"], n)
            to_id, to_name, to_mail = person(msg["to"], n)
            ccs = [person(c, n) for c in msg.get("cc", "").split(",") if c]
            for pid in [f_id, to_id] + [c[0] for c in ccs]:
                if pid not in parts: parts.append(pid)
            third = any(c in EXTERNAL for c in [msg["f"], msg["to"]] + msg.get("cc", "").split(","))
            ext = ext or third
            erows.append(dict(
                email_id=eid, thread_id=th["id"], message_id=mid, client_id=cid(n), advisor_id=aid(adv),
                message_seq=seq, timestamp=msg["t"] + ":00",
                sender_id=f_id, sender_name=f_name, sender_email=f_mail,
                to_ids=to_id, to_emails=to_mail,
                cc_ids="|".join(c[0] for c in ccs), cc_emails="|".join(c[2] for c in ccs),
                subject=th["subject"] if seq == 1 else "RE: " + th["subject"],
                body=msg["body"], in_reply_to=mids[-1] if mids else "", references=" ".join(mids),
                attachment_refs=msg.get("att", ""),
                channel=CH["email"][0], source_platform=CH["email"][1],
                source_type="synthetic_email", consent_status="synthetic",
                data_classification="client-confidential", includes_third_party="true" if third else "false"))
            mids.append(mid)
        start_of[th["id"]] = (parse_iso(ts[0]), parse_iso(ts[-1]))
        throws.append(dict(
            thread_id=th["id"], client_id=cid(n), advisor_id=aid(adv), subject=th["subject"],
            first_message_timestamp=ts[0] + ":00", last_message_timestamp=ts[-1] + ":00",
            message_count=len(th["msgs"]), participant_ids="|".join(parts),
            references_source_ids="|".join(resolve(r) for r in th["refs"]),
            includes_third_party="true" if ext else "false",
            channel=CH["email"][0], source_platform=CH["email"][1], source_type="synthetic_email",
            consent_status="synthetic", synthetic="true", generation_batch=BATCH))
    erows.sort(key=lambda r: r["email_id"])

    # ---------------------------------------------------------------- chats
    crows, cvrows = [], []
    allc = []
    for c in chats:
        for seq, ln in enumerate([l for l in c["msgs"].splitlines() if l.strip()], 1):
            ts, who, text, reply = ln.split("|")
            allc.append((ts, c["id"], seq, c, who, text, reply))
    allc.sort(key=lambda x: (rank(x[3]), x[0], x[1], x[2]))
    mid_of = {(x[1], x[2]): f"CHM-{i:04d}" for i, x in enumerate(allc, 1)}
    count, firstlast = {}, {}
    for ts, cidx, seq, c, who, text, reply in allc:
        n = c["client"]; adv = ADVISOR_OF[n]
        sid, sname, _ = person(who, n)
        rid, _, _ = person("C" if who == "A" else "A", n)
        crows.append(dict(
            message_id=mid_of[(cidx, seq)], chat_conversation_id=cidx, client_id=cid(n), advisor_id=aid(adv),
            message_seq=seq, timestamp=ts + ":00", sender_id=sid, sender_name=sname,
            sender_role="Advisor" if who == "A" else "Client", recipient_id=rid,
            participant_ids=f"{aid(adv)}|{cid(n)}", message_text=text,
            reply_to_message_id=mid_of[(cidx, int(reply))] if reply else "",
            channel=CH["chat"][0], source_platform=CH["chat"][1], source_type="synthetic_chat",
            consent_status="synthetic", data_classification="client-confidential"))
        count[cidx] = count.get(cidx, 0) + 1
        firstlast.setdefault(cidx, [ts, ts])[1] = ts
    crows.sort(key=lambda r: r["message_id"])
    for c in chats:
        n = c["client"]; adv = ADVISOR_OF[n]
        f, l = firstlast[c["id"]]
        start_of[c["id"]] = (parse_iso(f), parse_iso(l))
        cvrows.append(dict(
            chat_conversation_id=c["id"], client_id=cid(n), advisor_id=aid(adv),
            participant_ids=f"{aid(adv)}|{cid(n)}", first_message_timestamp=f + ":00",
            last_message_timestamp=l + ":00", message_count=count[c["id"]],
            references_source_ids="|".join(resolve(r) for r in c["refs"]),
            channel=CH["chat"][0], source_platform=CH["chat"][1], source_type="synthetic_chat",
            consent_status="synthetic", synthetic="true", generation_batch=BATCH))

    # ---------------------------------------------------------------- scenarios
    for iid, c in calls.items():
        d = parse_iso(c["interaction_datetime"]); start_of[iid] = (d, d)
    srows, lrows = [], []
    refmap = {}   # generated record -> the records it explicitly cites (references_source_ids)
    for rows_, key_ in ((mdrows, "meeting_id"), (throws, "thread_id"), (cvrows, "chat_conversation_id")):
        for r_ in rows_: refmap[r_[key_]] = {x for x in r_["references_source_ids"].split("|") if x}
    ref_count = {}
    for s in scenarios:
        recs = []
        for key, note in s["records"]:
            sid = key if key.startswith("INT-") else keymap[key]
            if key.startswith("INT-") and key not in calls:
                raise SystemExit(f"{s['id']}: unknown call {key}")
            if key.startswith("INT-") and calls[key]["client_id"] != cid(s["client"]):
                raise SystemExit(f"{s['id']}: call {key} belongs to {calls[key]['client_id']}")
            recs.append((start_of[sid][0], sid, note))
        recs.sort(key=lambda x: (x[0], x[1]))
        chans = set()
        seen_ids = []
        for seq, (ts, sid, note) in enumerate(recs, 1):
            basis = ("origin" if seq == 1 else
                     "explicit_reference" if refmap.get(sid, set()) & set(seen_ids) else "shared_topic_only")
            seen_ids.append(sid)
            ch = (classify(calls[sid]["channel"])[0] if sid.startswith("INT-") else "Meetings" if sid.startswith("MTG-")
                  else "Email" if sid.startswith("THR-") else "Chat")
            chans.add(ch)
            lrows.append(dict(scenario_id=s["id"], client_id=cid(s["client"]), sequence_no=seq, channel=ch,
                              source_id=sid, record_origin="original" if sid.startswith("INT-") else "generated",
                              source_timestamp=iso(ts), link_basis=basis, storyline_note=note))
        srows.append(dict(scenario_id=s["id"], client_id=cid(s["client"]), advisor_id=aid(ADVISOR_OF[s["client"]]),
                          title=s["title"], test_dimensions=s["dims"], arc_summary=s["arc"],
                          record_count=len(recs), channels_present="|".join(sorted(chans))))
    # calls referenced by new records (explicit references_source_ids + scenario links)
    refd = {}
    for rows in (mdrows, throws, cvrows):
        for r in rows:
            for x in r["references_source_ids"].split("|"):
                if x.startswith("INT-"): refd.setdefault(x, set()).add("ref")
    for l in lrows:
        if l["source_id"].startswith("INT-"): refd.setdefault(l["source_id"], set()).add("scn")
    scn_of = {}
    for l in lrows: scn_of.setdefault(l["source_id"], []).append(l["scenario_id"])

    # ---------------------------------------------------------------- source manifest + original-interaction metadata
    clrows, omrows, manifest = [], [], []
    for iid, c in calls.items():
        chan, conf, basis = classify(c["channel"])
        plat = {"Calls": CH["calls"][1], "Meetings": CH["meetings"][1]}.get(chan, "")
        plat_basis = ("channel taxonomy: voice-only interaction" if chan == "Calls" else
                      "assumed from the channel taxonomy; the capture tool (Teams, a OneDrive upload, or other) is not recorded in the source"
                      if chan == "Meetings" else "")
        linked = "|".join(sorted(set(scn_of.get(iid, []))))
        manifest.append(dict(
            interaction_id=iid, client_id=c["client_id"], advisor_id=c["advisor_id"], interaction_datetime=c["interaction_datetime"],
            turn_count=c["turns"], original_channel_value=c["channel"], original_source_type=c["source_type"],
            original_meeting_context=c["meeting_context"], assigned_channel=chan, classification_basis=basis,
            classification_confidence=conf, assigned_source_platform=plat, platform_basis=plat_basis,
            source_file="data/conversations.csv", source_ref_pattern=f"{iid}#T<turn_index>",
            referenced_by_generated_records="true" if iid in refd else "false", linked_scenario_ids=linked))
        row = dict(
            call_source_id=iid, client_id=c["client_id"], advisor_id=c["advisor_id"],
            interaction_datetime=c["interaction_datetime"], turn_count=c["turns"],
            first_turn_ref=f"{iid}#T1", last_turn_ref=f"{iid}#T{c['turns']}",
            meeting_context=c["meeting_context"], original_channel_value=c["channel"],
            original_source_type=c["source_type"], original_consent_status=c["consent_status"],
            channel=chan, source_platform=plat, platform_basis=plat_basis,
            source_file="data/conversations.csv", referenced_by_new_records="true" if iid in refd else "false",
            linked_scenario_ids=linked, synthetic="true", generation_batch="existing-conversations.csv")
        if chan == "Calls": clrows.append(row)
        elif chan == "Meetings": omrows.append(row)

    # ---------------------------------------------------------------- CRM layer (derived, never original evidence)
    crm_mod = importlib.import_module("content_crm")
    chan_of = {i: classify(c["channel"])[0] for i, c in calls.items()}
    client_of = {i: c["client_id"] for i, c in calls.items()}
    ctx_of = {i: c["meeting_context"] for i, c in calls.items()}
    for m in meetings: chan_of[m["id"]] = "Meetings"; client_of[m["id"]] = cid(m["client"]); ctx_of[m["id"]] = m["subject"]
    for t in threads: chan_of[t["id"]] = "Email"; client_of[t["id"]] = cid(t["client"]); ctx_of[t["id"]] = t["subject"]
    for c in chats: chan_of[c["id"]] = "Chat"; client_of[c["id"]] = cid(c["client"]); ctx_of[c["id"]] = "short message exchange"
    pending = []   # (created_dt, channel, type, source_ids, client, author, subject, text, method)
    for n in crm_mod.NOTES:
        refs = [resolve(r) for r in n["refs"]]
        chs = {chan_of[r] for r in refs}
        clients = {client_of[r] for r in refs}
        if len(chs) != 1 or len(clients) != 1: raise SystemExit(f"CRM note {n['key']} mixes channels/clients: {chs} {clients}")
        cl = next(iter(clients))
        pending.append((parse_iso(n["t"]), next(iter(chs)), n["type"], refs, cl, aid(ADVISOR_OF[int(cl[-3:])]),
                        n["subject"], n["text"], "manual (advisor-typed)"))
    for sid, (st, en) in start_of.items():
        chn = chan_of[sid]
        if not coverage(sid, crm_mod.ACTIVITY_COVERAGE[chn]): continue
        label = {"Meetings": "Meeting", "Calls": "Call", "Email": "Email thread", "Chat": "Chat conversation"}[chn]
        cl = client_of[sid]
        pending.append((en + timedelta(hours=1), chn, "Activity", [sid], cl, aid(ADVISOR_OF[int(cl[-3:])]),
                        f"{label} logged", f"{label} with client logged: {ctx_of[sid]}", "automatic activity log"))
    tier2 = {x["id"] for x in threads + chats if rank(x) == 2}
    # CRM rows for the tier-2 routine records are numbered after all earlier CRM rows, so earlier CRM IDs do not move
    pending.sort(key=lambda x: (any(s in tier2 for s in x[3]), x[0], x[3]))
    CRM_FILES = {"Meetings": "crm_meetings.csv", "Email": "crm_email.csv", "Calls": "crm_calls.csv", "Chat": "crm_chat.csv"}
    CRM_TAG = {"Meetings": "MTG", "Email": "EML", "Calls": "CAL", "Chat": "CHT"}
    crm_rows, seqn = {k: [] for k in CRM_FILES}, {k: 0 for k in CRM_FILES}
    for dt, chn, typ, srcs, cl, au, subj, text, how in pending:
        seqn[chn] += 1
        crm_rows[chn].append(dict(
            crm_record_id=f"CRM-{CRM_TAG[chn]}-{seqn[chn]:04d}", crm_artifact=CRM_FILES[chn], record_layer="crm_derived",
            record_type=typ, channel=chn, client_id=cl, advisor_id=au, author_id=au,
            created_at=iso(dt), subject=subj, text=text, source_refs="|".join(srcs), transfer_method=how,
            is_original_source_evidence="false", consent_status="synthetic", synthetic="true", generation_batch=BATCH))

    # ---------------------------------------------------------------- registry
    reg = []
    for n, (f, l) in CLIENTS.items():
        reg.append(dict(character_id=cid(n), display_name=f"{f} {l}", first_name=f, role="client",
                        client_scope=cid(n), advisor_scope=aid(ADVISOR_OF[n]), email=f"{f.lower()}.{l.lower()}@client.example",
                        used_as_participant_in_new_channels="true",
                        role_basis="speaker label 'Client' + advisor greets by first name in the client's interactions",
                        name_source="first name: data/conversations.csv utterances; surname: docs/data_dictionary.md, docs/dataset_design.md"))
    for n, (f, l) in ADVISORS.items():
        reg.append(dict(character_id=aid(n), display_name=f"{f} {l}", first_name=f, role="advisor",
                        client_scope="|".join(cid(k) for k, v in ADVISOR_OF.items() if v == n), advisor_scope=aid(n),
                        email=f"{f.lower()}.{l.lower()}@advisory.example", used_as_participant_in_new_channels="true",
                        role_basis="advisor_id column + speaker label 'Advisor'; self-introduces by first name",
                        name_source="first name: data/conversations.csv utterances; surname: docs/data_dictionary.md"))
    for code, e in EXTERNAL.items():
        reg.append(dict(character_id=e["id"], display_name=f"{e['first']} {e['last']}", first_name=e["first"],
                        role=e["role"], client_scope=cid(e["client"]), advisor_scope=aid(ADVISOR_OF[e["client"]]),
                        email=e["email"], used_as_participant_in_new_channels="true",
                        role_basis="named and described by the client in the calls; never a speaker in the calls",
                        name_source="data/conversations.csv utterances (full name)"))

    for first, full, n, role, basis in MENTIONED_ONLY:
        reg.append(dict(character_id=f"MENT-{len([r for r in reg if r['character_id'].startswith('MENT-')]) + 1:03d}",
                        display_name=full, first_name=first, role=role, client_scope=cid(n), advisor_scope=aid(ADVISOR_OF[n]),
                        email="", used_as_participant_in_new_channels="false", role_basis=basis,
                        name_source="data/conversations.csv utterances"))
    for r in reg:
        if r["display_name"] == "Samuel":
            r["role_basis"] = ("relationship inferred from the utterances ('where things stand after Samuel's death'); design docs say "
                               "CLIENT-004 was widowed and final_dataset_acceptance.md refers to Samuel's pension; "
                               "docs/dataset_design.md does not name him")

    # ---------------------------------------------------------------- attribution status (evidence computed from the CSV)
    texts = {}
    for r in call_rows: texts.setdefault(r["interaction_id"], []).append(r["utterance"])
    docs = ""
    for dn in ("dataset_design.md", "final_dataset_acceptance.md", "data_dictionary.md"):
        p = ROOT / "docs" / dn
        if p.exists(): docs += p.read_text(encoding="utf-8", errors="ignore")

    def mention_ids(name):
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        return {i for i, us in texts.items() if any(pat.search(u) for u in us)}

    STATED = {"Dorothy": "'my mother. Dorothy is eighty-one' (INT-015)", "Mei": "'My accountant, Mei Lin Chu' (INT-032)",
              "Luis": "'The plant manager, Luis' (INT-121)"}
    for r in reg:
        cat = r["character_id"].split("-")[0]
        if cat in ("CLIENT", "ADVISOR"):
            col = "client_id" if cat == "CLIENT" else "advisor_id"
            own = {i for i, c in calls.items() if c[col] == r["character_id"]}
            m = mention_ids(r["first_name"])
            hit, other = m & own, m - own
            share = len(hit) / len(own)
            conf = "high" if share >= 0.8 and not other else "medium" if hit and not other and share >= 0.2 else "low"
            r.update(person_category="client" if cat == "CLIENT" else "advisor", id_status="established_in_csv",
                     name_status="first_name_inferred_from_conversation_context; surname_from_repository_docs",
                     role_status="role_from_csv_column", attribution_confidence=conf,
                     attribution_evidence=(f"ID is a column in data/conversations.csv; the first name {r['first_name']} appears in {len(hit)} of "
                                           f"{len(own)} of this person's interactions and in {len(other)} interactions of anyone else; "
                                           f"the speaker column only says '{'Client' if cat == 'CLIENT' else 'Advisor'}'"))
        elif cat == "EXT":
            r.update(person_category="external_correspondent", id_status="registry_assigned",
                     name_status="full_name_stated_in_source", role_status="role_stated_in_source",
                     attribution_confidence="high",
                     attribution_evidence=("named with a role by the client in the calls (" + r["role"].split("per ")[-1].rstrip(")") +
                                           "); never a speaker; appears only as an email correspondent in generated records; ID assigned by the dataset"))
        else:
            n_ment = len(mention_ids(r["display_name"] if r["first_name"] == "Mei" else r["first_name"]))
            stated = r["first_name"] in STATED
            corro = r["first_name"] in docs if r["first_name"] not in ("Mei", "Luis") else False
            conf = "high" if stated else "medium" if (corro and n_ment >= 5) else "low"
            r.update(person_category="mentioned_only", id_status="registry_assigned", name_status="name_stated_in_source",
                     role_status="role_stated_in_source" if stated else "role_inferred_from_source",
                     attribution_confidence=conf,
                     attribution_evidence=(f"mentioned in {n_ment} interactions of one client, never a speaker or participant; "
                                           + (f"relationship stated: {STATED[r['first_name']]}" if stated else
                                              "relationship never stated outright in the calls; inferred from context"
                                              + ("; the repository design docs agree" if corro else "; not corroborated by the repository docs"))
                                           + "; ID assigned by the dataset"))

    # ---------------------------------------------------------------- source-control metadata (synthetic) + access scenarios
    gov = importlib.import_module("content_governance")
    ctl = []
    ctl_info = {}   # record_id -> dict(channel, client, advisor, platform, start, layer, kind, access, retention)
    plat = {"Meetings": CH["meetings"][1], "Email": CH["email"][1], "Calls": CH["calls"][1], "Chat": CH["chat"][1]}
    ACC = {"Meetings": "SYN-ACC-MTG", "Calls": "SYN-ACC-CAL", "Email": "SYN-ACC-EML", "Chat": "SYN-ACC-CHT"}
    RET = {"Meetings": "SYN-RET-MTG", "Calls": "SYN-RET-CAL", "Email": "SYN-RET-EML", "Chat": "SYN-RET-CHT"}
    KIND = {"Meetings": "meeting", "Email": "email_thread", "Chat": "chat_conversation"}
    for sid, (st, en) in start_of.items():
        chn = chan_of[sid]
        kind = "original_interaction" if sid.startswith("INT-") else KIND[chn]
        ctl_info[sid] = dict(kind=kind, layer="original_evidence", orig="true", chn=chn, plat=plat[chn], client=client_of[sid],
                             start=en, first=st, acc=ACC[chn], ret=RET[chn],
                             restr="SYN-ACC-RESTRICTED" if sid in gov.RESTRICTED else "")
    for chn, rows_ in crm_rows.items():
        for r in rows_:
            note = r["record_type"] != "Activity"
            ctl_info[r["crm_record_id"]] = dict(kind="crm_record", layer="crm_derived", orig="false", chn=chn, plat="CRM (derived layer)",
                                                client=r["client_id"], start=parse_iso(r["created_at"]), first=parse_iso(r["created_at"]),
                                                restr="", acc="SYN-ACC-CRM-NOTE" if note else "SYN-ACC-CRM-ACT",
                                                ret="SYN-RET-CRM-NOTE" if note else "SYN-RET-CRM-ACT")
    for rid in gov.RESTRICTED:
        if rid not in ctl_info: raise SystemExit(f"restricted record {rid} does not exist")
    for rid, v in sorted(ctl_info.items(), key=lambda kv: (kv[1]["layer"], kv[0])):
        days = gov.RETENTION[v["ret"]][1]
        ctl.append(dict(record_id=rid, record_kind=v["kind"], record_layer=v["layer"], is_original_source_evidence=v["orig"],
                        channel=v["chn"], controlling_platform=v["plat"], client_scope=v["client"],
                        responsible_advisor_id=aid(ADVISOR_OF[int(v["client"][-3:])]), access_policy_id=v["acc"],
                        restriction_policy_id=v["restr"],
                        retention_policy_id=v["ret"], retention_start_date=v["start"].strftime("%Y-%m-%d"),
                        review_date=(v["start"] + timedelta(days=days)).strftime("%Y-%m-%d"),
                        access_inheritance="none: governed by its own access policy" if v["layer"] == "crm_derived" else "not_applicable",
                        restriction_basis=gov.RESTRICTED.get(rid, ""), control_basis=gov.ASSUMPTION, synthetic="true"))
    ret_rows = [dict(retention_policy_id=k, applies_to=v[0], review_after_days=v[1], policy_label=v[2],
                     expiry_modelled="no: review date only", assumption=gov.ASSUMPTION) for k, v in gov.RETENTION.items()]
    acc_rows = [dict(access_policy_id=k, applies_to=v[0], permitted_roles=v[1], agent_access=v[2], third_party_grants=v[3],
                     note=v[4], assumption=gov.ASSUMPTION) for k, v in gov.ACCESS.items()]
    grant_rows = []
    for g in gov.GRANTS:
        info = ctl_info[g["record_id"]]
        vf = info["first"].strftime("%Y-%m-%d") if g["valid_from"] == "@record" else g["valid_from"]   # a grant starts when the record starts
        grant_rows.append(dict(grant_id=g["grant_id"], record_id=g["record_id"], principal_id=g["principal_id"],
                               principal_type="external_correspondent", permission="read", client_scope=info["client"],
                               basis_source_ids=g["basis"], basis_note=g["note"], valid_from=vf, valid_until=g["valid_until"],
                               status=g["status"], revoked_at=g["revoked_at"], assumption=gov.ASSUMPTION, synthetic="true"))
    acc_scn = []
    ids_known = set(ctl_info)
    for (sid_, cat, who, typ, act, rec, d, exp, reason, why, support, grants_) in gov.SCENARIOS:
        for x in [rec] + support.split("|"):
            if x not in ids_known: raise SystemExit(f"{sid_}: unknown record {x}")
        acc_scn.append(dict(scenario_id=sid_, gap_category=cat, requester_id=who, requester_type=typ, acting_for_id=act,
                            target_record_id=rec, request_date=d, expected_decision=exp, expected_reason_code=reason,
                            rationale=why, supporting_source_ids=support, supporting_grant_ids=grants_))

    # ---------------------------------------------------------------- write
    O = OUT
    out = {}
    out["meetings"] = write_csv(O / "meetings/meetings.csv", list(mrows[0]), mrows, allow)
    out["meeting_turns"] = write_csv(O / "meetings/meeting_turns.csv", list(trows[0]), trows, allow)
    out["meeting_metadata"] = write_csv(O / "meetings/meeting_metadata.csv", list(mdrows[0]), mdrows, allow)
    out["emails"] = write_csv(O / "emails/emails.csv", list(erows[0]), erows, allow)
    out["email_threads"] = write_csv(O / "emails/email_threads.csv", list(throws[0]), throws, allow)
    out["chats"] = write_csv(O / "chats/chats.csv", list(crows[0]), crows, allow)
    out["chat_conversations"] = write_csv(O / "chats/chat_conversations.csv", list(cvrows[0]), cvrows, allow)
    out["calls_metadata"] = write_csv(O / "calls/calls_metadata.csv", list(clrows[0]), clrows, allow)
    out["original_meetings_metadata"] = write_csv(O / "meetings/original_meetings_metadata.csv", list(omrows[0]), omrows, allow)
    out["source_manifest"] = write_csv(O / "source_manifest.csv", list(manifest[0]), manifest, allow)
    for k, fn in CRM_FILES.items():
        out["crm/" + fn] = write_csv(O / "crm" / fn, list(crm_rows[k][0]), crm_rows[k], allow)
    # evaluation ground truth lives in its own folder, outside every source path
    rr = [dict(record_id=x["id"], channel="Email" if x["id"].startswith("THR-") else "Chat", client_id=cid(x["client"]),
               note="routine administrative message (logistics, thanks, scheduling, receipt): no decision, correction, preference, figure or new commitment; in no scenario")
          for x in threads + chats if x.get("routine")]
    out["evaluation/routine_records"] = write_csv(O / "evaluation/routine_records.csv", list(rr[0]), rr, allow)
    out["evaluation/scenarios"] = write_csv(O / "evaluation/scenarios.csv", list(srows[0]), srows, allow)
    out["evaluation/scenario_links"] = write_csv(O / "evaluation/scenario_links.csv", list(lrows[0]), lrows, allow)
    out["evaluation/access_scenarios"] = write_csv(O / "evaluation/access_scenarios.csv", list(acc_scn[0]), acc_scn, allow)
    out["governance/source_control"] = write_csv(O / "governance/source_control.csv", list(ctl[0]), ctl, allow)
    out["governance/retention_policies"] = write_csv(O / "governance/retention_policies.csv", list(ret_rows[0]), ret_rows, allow)
    out["governance/access_policies"] = write_csv(O / "governance/access_policies.csv", list(acc_rows[0]), acc_rows, allow)
    out["governance/access_grants"] = write_csv(O / "governance/access_grants.csv", list(grant_rows[0]), grant_rows, allow)
    out["character_registry"] = write_csv(O / "character_registry.csv", list(reg[0]), reg, allow)
    lock = O / "calls/conversations.sha256"
    if lock.exists() and not allow:
        raise SystemExit(f"refusing to overwrite {lock}")
    lock.write_text(f"{sha}  data/conversations.csv\nrows={len(call_rows)} interactions={len(calls)}\n", encoding="utf-8")
    for k, v in out.items(): print(f"{k}: {v} rows")
    if LOCKED:
        print("NOT WRITTEN (file is open in another program; close it and re-run):", *LOCKED, sep="\n  ")
    print("calls referenced by new records:", len(refd), "of", len(calls))


if __name__ == "__main__":
    main("--overwrite-generated" in sys.argv)
