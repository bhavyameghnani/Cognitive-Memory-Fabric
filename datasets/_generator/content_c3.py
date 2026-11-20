"""CLIENT-003 (Sofia Alvarez) / ADVISOR-003 (Elena Rossi). Authored synthetic content."""
CLIENT = 3

MEETINGS = [
    dict(key="c3_m1", client=3, start="2026-04-27T08:00", platform="Teams",
         subject="Going back to work and the four-day-week idea",
         refs=["INT-045", "INT-071"],
         turns="""
A: Hi Sofia, how is Lucia?
C: Amazing and exhausting. She's nearly sleeping five hours in a row, which feels like a miracle.
A: That's wonderful. Today I'd like to talk about going back to work. When we last spoke you said your manager had floated a four-day week for when you return from leave.
C: He hinted at it, in passing, in a one-to-one. He said "we could look at four days when you're back." Nothing formal.
A: What would that do to your income?
C: About twenty percent less salary. I don't want to do the maths.
A: I can do it for you. A fifth off salary is a big hole next to childcare.
C: Right. So I don't want to ask for it. I might. Part of me wants Fridays with Lucia.
A: Both of those are valid. I'll model it as a scenario so you can see it, and I'll treat it as a possibility only.
C: Yes. Please don't write "Sofia is going to four days."
A: I'll record it as: your manager hinted, you haven't asked, and you're undecided.
C: Perfect.
A: One practical thing. Have you got a childcare place yet?
C: We have one lined up for when I'm back. I'll send you the fees when I have them.
A: Please do. That's the number I need most.
""".strip()),

    dict(key="c3_m2", client=3, start="2026-06-15T08:00", platform="Teams",
         subject="Childcare fees: first pass at the reworked budget",
         refs=["INT-131", "INT-100"],
         turns="""
A: Sofia, thanks for sending the childcare fees on Friday. I've put them in the budget draft. Before our Wednesday call, I'd like to show you how the gap looks.
C: Okay. I'm scared of this slide.
A: The gap is manageable. There are three ways to cover it: trim your brokerage contributions, use part of the house fund, or reduce your 401(k) contributions. I'd rule out touching the 401(k) match.
C: Agreed. The match stays.
A: Between the other two, my instinct is that the house fund is the cleaner cushion for a short period, because it's sitting in cash.
C: Hmm. I guess? Let me see the numbers on Wednesday.
A: Of course. I'm suggesting, not deciding.
C: Okay. Don't put that in as done.
A: I won't. I'll show it as one option among three.
C: Thank you. Mateo says he's still deciding about that interview, by the way. Nothing yet.
A: Understood. I won't plan around it until there's an actual offer.
""".strip()),
]

EMAILS = [
    dict(key="c3_t1", client=3, subject="Buying a home - summary of Friday's call",
         refs=["INT-017", "INT-071"],
         msgs=[
             dict(t="2026-01-26T11:00", f="A", to="C", body="""Hi Sofia,

Following Friday's call about buying a home and where the down payment should sit. You've decided to buy within two years, so I'd suggest we build a separate house fund and keep it in cash or short-term holdings rather than in equities. Your 401(k) and the employer match stay as they are.

Warm regards,
Elena"""),
             dict(t="2026-01-26T20:30", f="C", to="A", body="""Thanks Elena!! Reading on my phone, will go through it properly this weekend. Quick question: it's fine to leave the 401k exactly as is in the meantime?

Sofia"""),
             dict(t="2026-01-27T08:45", f="A", to="C", body="""Yes, the 401(k) and the match stay exactly as they are.

Elena"""),
             dict(t="2026-04-13T10:20", f="A", to="C", body="""Hi Sofia,

A correction after Friday's call. My January 26 email said you had decided to buy within two years. That overstated what you told me. As you explained on April 10, you are considering a purchase within about two years, with no commitment, and you have decided not to start a house search until the autumn. I have recorded both.

Warm regards,
Elena"""),
             dict(t="2026-04-13T12:05", f="C", to="A", body="""Yes, that's right. Though honestly the "autumn" part is soft. Mateo has been approached about a role elsewhere, and if that goes anywhere everything changes. I'll tell you more on Thursday.

Sofia"""),
         ]),
    dict(key="c3_t2", client=3, subject="Reworked budget with childcare",
         refs=["INT-134", "INT-139"],
         msgs=[
             dict(t="2026-06-18T09:30", f="A", to="C", att="reworked_budget_2026-06.xlsx", body="""Hi Sofia,

Following Wednesday's call, the reworked monthly budget is attached. I've used the $40,000 in the house fund as a cushion for the childcare gap while we see how the fees settle. Your 401(k) match is untouched.

Warm regards,
Elena"""),
             dict(t="2026-06-18T21:15", f="C", to="A", body="""Thanks Elena, I'll look at it over the weekend. Lucia has a cold!!

Sofia"""),
             dict(t="2026-06-22T11:10", f="A", to="C", body="""Hi Sofia,

A correction to my June 18 email. The $40,000 in the house fund is not a childcare cushion; it remains house money. The childcare gap will be covered by trimming your brokerage contributions by about $800 a month, with the 401(k) match untouched.

I can't change the contribution instruction until you confirm it by reply.

Warm regards,
Elena"""),
             dict(t="2026-06-22T13:40", f="C", to="A", body="""Confirming: reduce my brokerage contributions by $800 a month starting in July. The match stays. The house fund stays.

Sofia"""),
         ]),
]

CHATS = [
    dict(key="c3_c1", client=3, refs=["INT-017", "THR:c3_t1"], msgs="""
2026-03-24T22:40|C|Elena sorry for the late msg. Feeding at 3am makes me think about the house thing.|
2026-03-24T22:41|C|Not asking you to do anything|
2026-03-25T07:55|A|No apology needed! I hope you're getting some sleep. We can talk about house timing whenever you're ready. Nothing is due.|1
2026-03-25T08:20|C|Ok. Maybe after my leave settles? Idk|
2026-03-25T08:31|A|That's perfectly fine. I'll bring it up next call only if you want me to.|4
""".strip()),
    dict(key="c3_c2", client=3, refs=["INT-071"], msgs="""
2026-04-22T12:15|C|Mateo has a recruiter call tomorrow. Could be nothing|
2026-04-22T12:40|A|Thanks for telling me. I don't need numbers until there is an actual offer.|1
2026-04-22T12:42|C|Right. I'll keep you posted|
""".strip()),
    dict(key="c3_c3", client=3, refs=["MTG:c3_m1"], msgs="""
2026-04-27T20:30|C|Not asking my manager about four days yet. Please don't put it down as a plan|
2026-04-27T20:55|A|Recorded as something he hinted at, not something you've requested.|1
2026-04-27T20:56|C|Thank you. Fridays with Lucia are tempting though|
""".strip()),
    dict(key="c3_c4", client=3, refs=["INT-108"], msgs="""
2026-05-21T21:10|C|I looked at 529s and there are SO many options. Can you just tell me which one?|
2026-05-21T21:40|A|My suggestion, not a decision: a low-fee plan from your own state. I can send two options to compare. You decide.|1
2026-05-21T21:44|C|Let's hold off until the autumn. I'm drowning right now|
2026-05-21T21:48|A|Understood. Deferred until the autumn. I'll note it as open.|3
""".strip()),
]

SCENARIOS = [
    dict(id="SCN-C3-01", client=3, title="House purchase: possibility, mis-recorded decision, then postponement",
         dims="context|certainty|currency|provenance",
         arc="Jan 23 'might buy'; advisor email says decided; corrected Apr 10-13; search postponed to autumn; client flags softness.",
         records=[("INT-017", "origin: might buy in a couple of years"),
                  ("c3_t1", "advisor email states decision; correction and softened autumn plan on Apr 13"),
                  ("c3_c1", "late-night musing about the house; nothing due"),
                  ("INT-071", "client corrects; decides no house search until autumn"),
                  ("INT-147", "half-year review restates the arc")]),
    dict(id="SCN-C3-02", client=3, title="Four-day week and Mateo's job possibility",
         dims="certainty|currency|governance",
         arc="Manager hinted at a four-day week; Mateo has a possible interview; both stay possibilities.",
         records=[("INT-045", "origin: manager hint about four days"),
                  ("INT-075", "Mateo's job possibility and relocation"),
                  ("c3_c2", "recruiter call: exploratory only"),
                  ("c3_m1", "four-day week as scenario only; childcare fees to follow"),
                  ("c3_c3", "client asks not to record four days as a plan"),
                  ("INT-100", "Mateo still deciding whether to interview")]),
    dict(id="SCN-C3-03", client=3, title="House fund vs childcare gap",
         dims="certainty|currency|provenance",
         arc="Advisor floats the house fund as a cushion on Jun 15; it becomes 'used' in a Jun 18 email; client corrects Jun 22 and confirms a brokerage trim.",
         records=[("INT-131", "back to work and childcare costs"),
                  ("c3_m2", "advisor suggests house fund as one option; client says do not record as done"),
                  ("INT-134", "reworked budget"),
                  ("c3_t2", "advisor email treats house fund as cushion; correction and client's explicit instruction"),
                  ("INT-139", "client corrects: house fund remains house money")]),
    dict(id="SCN-C3-04", client=3, title="529 plan for Lucia",
         dims="certainty|currency",
         arc="Advisor suggests a low-fee plan; client defers to autumn.",
         records=[("INT-108", "origin: saving for Lucia's education"),
                  ("c3_c4", "advisor suggestion; client defers"),
                  ("INT-147", "529 decision listed as open item")]),
]
