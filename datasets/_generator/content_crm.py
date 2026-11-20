"""CRM layer (Notes, Summaries, Activity records): derived, advisor-typed or system-logged, NOT original source evidence.

The brief: channels feed a CRM that holds one flat artifact per channel; important information is transferred manually
and there is little reconciliation. So:
  * ACTIVITY records are logged for only a share of the source records (ACTIVITY_COVERAGE, deterministic sampling);
  * NOTES / SUMMARIES are short, lossy, and restate only what an existing source record says. Some copy an advisor
    assertion that the source later corrects, and the CRM is then NOT updated (CRM-wide non-reconciliation), except
    where the source itself says the advisor corrected the file note (Kenji: Mar 4 and May 29).
Each entry names the source records it was transferred from (refs); every ref is in one channel and one client.
Forms: INT-### | THR:key | MTG:key | CHT:key (keys are those used in content_c*.py).
"""
ACTIVITY_COVERAGE = {"Meetings": 85, "Calls": 70, "Email": 50, "Chat": 25}   # percent of source records with an Activity row

NOTES = [
    # ---- CLIENT-001 (Kenji): the file note is corrected twice, as the advisor's own emails/chats say
    dict(key="n1", t="2026-02-10T17:30", type="Note", refs=["INT-027"], subject="Retirement scenarios",
         text="Kenji would like to retire at 60. Three scenarios (65, 63, 60) sent by email. Next: model part-time consulting income."),
    dict(key="n2", t="2026-03-04T16:00", type="Note", refs=["INT-049"], subject="Clarification of 10 Feb note",
         text="Clarification of the 10 Feb note: retiring at 60 is only being explored. Formal planning date is 65. Earlier note retained in history."),
    dict(key="n3", t="2026-05-05T11:00", type="Note", refs=["THR:c1_t2"], subject="Earmark for Ren, Seoul",
         text="$14,000 earmarked from cash for Ren's possible semester in Seoul next spring, recorded as additional tuition. Returns to general reserve if he is not accepted."),
    dict(key="n4", t="2026-05-29T15:30", type="Note", refs=["INT-114"], subject="Correction to 5 May note",
         text="Corrects the 5 May note: the $14,000 is the program fee, flights and housing, not additional tuition. Normal tuition is paid separately."),
    dict(key="n5", t="2026-04-28T09:00", type="Summary", refs=["MTG:c1_m1"], subject="Retirement age review, 28 Apr",
         text="63 is under serious consideration; formal planning date remains 65. Healthcare cost to be shown as its own line for Mariko. Kenji to tell me on the 1 May call if he decides."),
    dict(key="n6", t="2026-03-05T08:30", type="Note", refs=["CHT:c1_c1"], subject="Chat: planning date",
         text="Confirmed to Kenji by chat that the planning date is 65 and 60 is recorded as exploring."),
    # ---- CLIENT-002 (Rebecca): the advisor's mistaken 'acceptance' is copied into the CRM and never corrected there
    dict(key="n7", t="2026-03-12T10:40", type="Summary", refs=["THR:c2_t1"], subject="Lakeshore first offer",
         text="First Lakeshore offer: $24 million under the proposed LOI. Client accepted the offer. Next: redlines, suitability update, meeting."),
    dict(key="n8", t="2026-06-15T12:00", type="Summary", refs=["MTG:c2_m2"], subject="Post-closing model v1 walkthrough",
         text="Walked Rebecca through the post-closing model. It assumes salary stops at closing. Rebecca has seen the model but not reviewed it. Retention pool not included; still an idea."),
    # ---- CLIENT-004 (Grace): the 'about half' figure from the Feb summary is carried over
    dict(key="n9", t="2026-02-10T11:00", type="Summary", refs=["THR:c4_t1"], subject="Papers review summary",
         text="Death certificates in hand. Pension letter still outstanding. Survivor pension about half of Samuel's pension (from company summary). Retirement-account options comparison to follow."),
    # ---- CLIENT-006 (Peg)
    dict(key="n10", t="2026-03-31T11:30", type="Summary", refs=["THR:c6_t1"], subject="Brian: loan decision",
         text="Peg decided not to lend Brian $80,000 because of the cost of Frank's care. She will call him. She will also help with equipment, a few hundred dollars."),
    dict(key="n11", t="2026-06-03T10:30", type="Note", refs=["CHT:c6_c3"], subject="Aide days",
         text="Aide now comes five days (model had four). Model corrected. May invoice about $6,400."),
    dict(key="n12", t="2026-06-08T16:00", type="Note", refs=["THR:c6_t2"], subject="Maplewood Commons",
         text="Peg decided against Maplewood Commons for now because Frank wants to stay at home. No deposit. May revisit if his needs change."),
    dict(key="n13", t="2026-06-15T11:30", type="Summary", refs=["MTG:c6_m2"], subject="Care budget review, 15 Jun",
         text="Care model rerun with five aide days. Survivor pension still unconfirmed; documents requested via the secure portal. Peg has decided nothing about the equipment."),
]
