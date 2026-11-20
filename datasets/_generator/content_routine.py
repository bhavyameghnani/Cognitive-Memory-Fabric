"""Routine, non-memory-bearing correspondence (logistics, thanks, log-in trouble).

Added because every original generated thread/chat sat inside a storyline. These records carry no decision, correction,
preference, figure or commitment, cite no earlier record, and belong to no scenario (they are listed in
evaluation/routine_records.csv). They get IDs after the existing ones so earlier IDs do not move.
"""
EMAILS = [
    dict(key="r_t1", client=3, routine=True, subject="Secure portal link", refs=[],
         msgs=[
             dict(t="2026-05-12T09:10", f="A", to="C", body="""Hi Sofia,

The secure-portal link I sent earlier has expired. I have sent a fresh one in a separate message. Nothing else is needed from you.

Elena"""),
             dict(t="2026-05-12T09:42", f="C", to="A", body="""Got it, thank you. I was able to log in.

Sofia"""),
         ]),
    dict(key="r_t2", client=1, routine=True, subject="Teams link", refs=[],
         msgs=[
             dict(t="2026-04-22T11:40", f="A", to="C", body="""Dear Kenji,

The Teams link for our video meetings is unchanged, in case you need it again. No reply needed.

Priya"""),
             dict(t="2026-04-22T12:05", f="C", to="A", body="""Thanks Priya, I have it.

Kenji"""),
         ]),
    dict(key="r_t3", client=5, routine=True, subject="Out of the office Monday", refs=[],
         msgs=[
             dict(t="2026-06-19T16:20", f="A", to="C", body="""Lucas,

A quick note that I am out of the office on Monday and back on Tuesday. I will answer messages then.

Daniel"""),
             dict(t="2026-06-19T16:48", f="C", to="A", body="""No problem. Thanks for letting me know.

Lucas"""),
         ]),
    # ---- tier 2 (added in the governance refinement pass): appended, so earlier IDs do not move
    dict(key="r_t4", client=2, routine=True, tier=2, subject="Next review", refs=[],
         msgs=[
             dict(t="2026-06-26T10:20", f="A", to="C", body="""Rebecca,

I would like to find a time for our next review. Would the week of July 6 suit you?

Daniel"""),
             dict(t="2026-06-26T11:05", f="C", to="A", body="""That week works for me. Please send whichever day suits you.

Rebecca"""),
         ]),
    dict(key="r_t5", client=4, routine=True, tier=2, subject="Scanned pages", refs=[],
         msgs=[
             dict(t="2026-05-14T15:05", f="C", to="A", body="""Dear Priya,

I scanned the pages and uploaded them to the portal this afternoon. Could you let me know that they arrived?

Grace"""),
             dict(t="2026-05-14T15:40", f="A", to="C", body="""Hello Grace,

Yes, they arrived and they are readable. Thank you.

Priya"""),
         ]),
    dict(key="r_t6", client=6, routine=True, tier=2, subject="Our next call", refs=[],
         msgs=[
             dict(t="2026-06-19T17:30", f="C", to="A", body="""Elena dear,

Something may come up and I may need to move our next call a little. May I tell you nearer the time?

Peg"""),
             dict(t="2026-06-19T18:05", f="A", to="C", body="""Of course, Peg. Just let me know when you can.

Elena"""),
         ]),
]

CHATS = [
    dict(key="r_c1", client=2, routine=True, refs=[], msgs="""
2026-04-14T10:52|C|Daniel, are you free for a quick question later today?|
2026-04-14T10:58|A|Yes, after two o'clock.|1
2026-04-14T11:00|C|Great, I'll message then.|2
""".strip()),
    dict(key="r_c2", client=1, routine=True, refs=[], msgs="""
2026-05-18T07:35|C|Priya, the portal wouldn't let me log in this morning.|
2026-05-18T07:50|A|Please use the password reset link on the login page and tell me if it still fails.|1
2026-05-18T08:05|C|It worked. Thanks.|2
""".strip()),
    dict(key="r_c3", client=6, routine=True, refs=[], msgs="""
2026-05-21T14:05|C|Elena dear did my last message arrive|
2026-05-21T14:20|A|Yes it did, Peg. Thank you.|1
""".strip()),
    # ---- tier 2 (governance refinement pass)
    dict(key="r_c4", client=3, routine=True, tier=2, refs=[], msgs="""
2026-06-15T08:42|C|Hi Elena, the document is in the portal now.|
2026-06-15T08:55|A|Received, thank you Sofia.|1
""".strip()),
    dict(key="r_c5", client=4, routine=True, tier=2, refs=["INT-122"], msgs="""
2026-06-05T16:20|C|Thank you for this morning, Priya. It helped to go through it slowly.|
2026-06-05T16:35|A|I am glad, Grace. Message me whenever you like.|1
""".strip()),
    dict(key="r_c6", client=5, routine=True, tier=2, refs=["INT-127"], msgs="""
2026-06-09T09:50|C|Thanks Daniel, that was clear.|
2026-06-09T10:05|A|Glad it was useful, Lucas. Speak soon.|1
""".strip()),
]
