"""CLIENT-006 (Margaret "Peg" Sullivan) / ADVISOR-003 (Elena Rossi). Authored synthetic content.

DH = Dennis Hall, Peg's accountant (named in INT-018, INT-028, INT-085).
"""
CLIENT = 6

MEETINGS = [
    dict(key="c6_m1", client=6, start="2026-06-04T10:00", platform="Teams",
         subject="Maplewood Commons: questions for the elder-law attorney",
         refs=["INT-116", "INT-072"],
         turns="""
A: Peg, good morning. Take your time. Today I thought we'd go through the contract together and write down the questions for the attorney.
C: Yes, dear. I wrote some things on the back of an envelope.
A: Lovely. First, the entrance fee: three hundred eighty thousand dollars. The contract says eighty percent comes back to the estate.
C: So twenty percent is gone.
A: Yes. That's roughly seventy-six thousand that wouldn't come back.
C: That's a lot of money for a room.
A: It also buys the right to nursing care later. The question is whether you'd use it.
C: Frank wants to stay home. But he can't tell me what he'll want when he's worse.
A: That's the honest uncertainty. Second, the monthly fee can rise.
C: By how much?
A: That's a question for the attorney: whether there's a cap.
C: I'll ask. What else?
A: Whether a change in Frank's care needs would trigger a move to the nursing wing, and who decides.
C: I'll write that down.
A: I'm not recommending yes or no. The decision stays yours.
C: I know, dear. Frank and I will decide. I don't want to be rushed.
""".strip()),

    dict(key="c6_m2", client=6, start="2026-06-15T10:00", platform="Teams",
         subject="Care budget with five aide days; Frank's pension survivor amount",
         refs=["INT-119", "INT-009"],
         turns="""
A: Hello Peg. I've rerun the care model with five aide days instead of four.
C: Good. Five is what it is.
A: The aide was about sixty-four hundred dollars in May. Your spending is about ten thousand one hundred a month, and your pension and Social Security come to about sixty-three hundred, so the portfolio covers roughly thirty-eight hundred a month.
C: Is that a lot?
A: A year of that is about forty-six thousand dollars. The short-term bond ladder is built for exactly that.
C: So we're all right.
A: For the next few years, yes, on these numbers. The open question is Frank's pension. What survivor amount would you receive after he's gone?
C: I told you, I think about half.
A: You did. In January I recorded that amount as unconfirmed, and I'd like the actual statement.
C: Colleen will find it.
A: Could you ask her to send it to the secure portal? Until then I'll use half only as a stress case, and I won't treat it as a fact.
C: That's fine. Brian called on Sunday. We had a nice talk. He didn't ask for anything.
A: I'm glad.
C: I haven't decided anything about the equipment, dear.
A: Understood. I have it as only a thought.
""".strip()),
]

EMAILS = [
    dict(key="c6_t1", client=6, subject="Brian - your decision about the loan",
         refs=["INT-037", "INT-063", "INT-098"],
         msgs=[
             dict(t="2026-03-31T10:15", f="A", to="C", body="""Dear Peg,

Following our call yesterday, I'm writing it down in plain words so you have it.

1. You have decided not to lend Brian $80,000, because of the cost of Frank's care.
2. You will call Brian yourself this weekend.
3. You'd also like to help Brian with a few hundred dollars toward his equipment.

Kind regards,
Elena"""),
             dict(t="2026-03-31T16:30", f="C", to="A", body="""Dear Elena,

Thank you for writing it down. That is what we talked about. I will call Brian on Saturday.

Peg"""),
             dict(t="2026-05-08T11:50", f="A", to="C", body="""Dear Peg,

A correction to my March 31 email. Helping Brian with equipment is not something you decided. As you told me today, it was only a thought, and nothing has been offered. My March 31 email stays in the record as sent.

Kind regards,
Elena"""),
             dict(t="2026-05-08T14:15", f="C", to="A", body="""Thank you dear. It is only a thought. I may think about it again when things settle.

Peg"""),
         ]),
    dict(key="c6_t2", client=6, subject="Maplewood Commons - what the contract says",
         refs=["INT-116", "INT-125"],
         msgs=[
             dict(t="2026-05-29T15:20", f="A", to="C", body="""Dear Peg,

Following our call. A short summary of the Maplewood Commons contract:
- The entrance fee is $380,000, and eighty percent is refundable to the estate.
- The monthly fee can rise.
- My recommendation is that an elder-law attorney reviews it before you decide.

You told me you want to talk to Frank first. The decision is on hold until you have.

Kind regards,
Elena"""),
             dict(t="2026-05-30T09:30", f="C", to="A", body="""Dear Elena,

Thank you. I have not talked with Frank yet. He has had a tired week. Please send me the names of the attorneys.

Peg"""),
             dict(t="2026-06-01T09:00", f="A", to="C", body="""Dear Peg,

I'll mail you a short list of two elder-law attorneys today. The choice of whom to call is yours.

Elena"""),
             dict(t="2026-06-08T15:05", f="A", to="C", body="""Dear Peg,

Following today's call: you have decided against Maplewood Commons for now, because Frank wants to stay at home. You may revisit it if his needs change. No deposit will be paid.

Kind regards,
Elena"""),
             dict(t="2026-06-08T17:30", f="C", to="A", body="""That is right dear. Thank you.

Peg"""),
         ]),
    dict(key="c6_t3", client=6, subject="Required distribution and the church gift",
         refs=["INT-018", "INT-085", "INT-129"],
         msgs=[
             dict(t="2026-06-11T14:00", f="A", to="DH", cc="C", body="""Dear Dennis,

As Peg asked, I'm coordinating with you on this year's required distribution. My understanding, from our earlier discussions and today's call with Peg:

- The required distribution for 2026 is about $31,000.
- A $6,000 qualified charitable distribution to the church counts toward it.
- The remaining $25,000 would be taken monthly from July to December, with 12 percent withheld, as you suggested.

Could you confirm, and tell me anything that needs to happen in a particular order?

Elena Rossi"""),
             dict(t="2026-06-12T09:45", f="DH", to="A", cc="C", body="""Dear Elena, dear Peg,

Confirmed. Withholding at 12 percent is fine. The charitable distribution must go directly from the IRA custodian to the church, so please don't have Peg withdraw the cash first.

Dennis Hall"""),
             dict(t="2026-06-12T11:00", f="C", to="A", cc="DH", body="""Dear Dennis and Elena,

I want the six thousand to go to the church. And the rest monthly as you said.

Peg"""),
         ]),
]

CHATS = [
    dict(key="c6_c1", client=6, refs=["INT-072"], msgs="""
2026-04-11T10:05|C|Elena dear it is Peg. Colleen showed me how to do this on the phone|
2026-04-11T10:06|C|Colleen says Maplewood wants a deposit soon. Do I have to|
2026-04-11T10:30|A|Lovely to hear from you, Peg. No, nothing is due. You told me you are only looking.|2
2026-04-11T10:33|C|Yes only looking. Thank you dear|
""".strip()),
    dict(key="c6_c2", client=6, refs=["INT-092"], msgs="""
2026-05-04T17:10|C|Elena thank you for this morning. The garden was lovely but I felt rushed by the lady. Frank was tired afterwards|
2026-05-04T17:35|A|Thank you for telling me, Peg. There is nothing to decide. Send the contract when you can.|1
2026-05-04T17:38|C|Colleen will help me with it|
""".strip()),
    dict(key="c6_c3", client=6, refs=["INT-119"], msgs="""
2026-06-03T09:50|C|Did I say the aide comes five days now since Frank fell|
2026-06-03T10:15|A|You did, yesterday. I've corrected my model from four days to five. The May invoice was about $6,400.|1
2026-06-03T10:18|C|Yes that is right|
""".strip()),
    dict(key="c6_c4", client=6, refs=["MTG:c6_m2"], msgs="""
2026-06-16T16:40|C|Colleen found Frank's pension papers. Do I send them to you|
2026-06-16T16:58|A|Please use the secure portal, Peg, not a photo by text or email. Colleen can upload it.|1
2026-06-16T17:01|C|She will do it Sunday|
""".strip()),
]

SCENARIOS = [
    dict(id="SCN-C6-01", client=6, title="Brian's loan and the equipment thought",
         dims="context|certainty|currency|provenance",
         arc="Loan request is undecided on Feb 19, declined Mar 30; advisor email records equipment help as wanted; client reply is an ambiguous acknowledgement; corrected May 8 as a thought only.",
         records=[("INT-037", "origin: Brian's loan request, undecided"),
                  ("INT-063", "loan declined; equipment help only a thought"),
                  ("c6_t1", "email lists equipment help as a wish; ambiguous client acknowledgement; correction May 8"),
                  ("INT-098", "client confirms equipment help is only a thought"),
                  ("c6_m2", "client again says nothing decided about the equipment")]),
    dict(id="SCN-C6-02", client=6, title="Maplewood Commons: looking, reading the contract, deciding against for now",
         dims="context|certainty|currency",
         arc="Brochure Apr 10; deposit worry; visit May 4; contract May 28-29; attorney questions Jun 4; decision against for now Jun 8.",
         records=[("INT-072", "origin: only looking"),
                  ("c6_c1", "deposit worry; nothing due"),
                  ("INT-092", "visit; contract not read"),
                  ("c6_c2", "client felt rushed; nothing to decide"),
                  ("INT-116", "contract read; decision on hold pending Frank"),
                  ("c6_t2", "contract summary, hold, attorney list, final decision against for now"),
                  ("c6_m1", "questions for the attorney; advisor explicitly not recommending"),
                  ("INT-125", "client decides against for now")]),
    dict(id="SCN-C6-03", client=6, title="Care budget: four vs five aide days; unconfirmed survivor pension",
         dims="context|currency|provenance",
         arc="Model assumed four aide days; client corrects on Jun 2; survivor amount remains unconfirmed with documents pending.",
         records=[("INT-009", "origin: survivor amount recorded as unconfirmed"),
                  ("INT-119", "client corrects to five aide days"),
                  ("c6_c3", "client double-checks the correction"),
                  ("c6_m2", "model rerun; survivor amount is a stress case only"),
                  ("c6_c4", "pension papers to be uploaded via the secure portal"),
                  ("INT-145", "half-year review: survivor amount still unconfirmed")]),
    dict(id="SCN-C6-04", client=6, title="Required distribution and the church gift",
         dims="context|governance|provenance",
         arc="Advisor coordinates with the client's accountant, with the client copied; explicit client instruction in reply.",
         records=[("INT-018", "origin: client asks advisor to contact accountant"),
                  ("INT-085", "figures confirmed: about $31k, $6k via church"),
                  ("INT-129", "remaining amount monthly Jul-Dec, 12 percent withheld"),
                  ("c6_t3", "advisor-accountant-client thread; accountant confirms; client instructs")]),
]
