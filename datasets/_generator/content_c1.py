"""CLIENT-001 (Kenji Tanaka) / ADVISOR-001 (Priya Nair). Authored synthetic content.

Codes: A = advisor, C = client, HP = Hannah Park (Kenji's CPA, named in INT-046/049/107/120).
"""
CLIENT = 1

MEETINGS = [
    dict(key="c1_m1", client=1, start="2026-04-28T08:00", platform="Teams",
         subject="Retirement age: 63 versus 65 with the retention bonus",
         refs=["INT-074", "INT-082"],
         turns="""
A: Morning, Kenji. I've updated the scenarios since our April 22 call to include the retention bonus. Can I walk you through them, and then ask what you and Mariko have worked out?
C: Please. I read your summary, but I'd like to hear it. Is the bonus counted at the full hundred and fifty thousand?
A: Pre-tax, yes, but only in the 63 scenario, paid in installments for as long as you stay. I haven't counted it at 60, because on the terms as I read them you'd forfeit it. I still haven't seen the retention agreement itself, so treat those terms as provisional.
C: I have the agreement. I'll upload it to the portal today.
A: Thank you. With the bonus, 63 funds the fifteen thousand a month target with a reasonable margin. 65 has a comfortable margin, as before. My suggestion is that 63 is the age to test, rather than 60.
C: That's fair. 60 is off the table in my head unless Northgate is sold and the role disappears. But I'm not ready to say 63 is the answer.
A: Understood. So what I'll hold today is this: 63 is under serious consideration, and the formal planning date is still 65.
C: Yes. Mariko and I talked on Sunday. She wants me out before 65, but she's nervous about the healthcare gap. She'd be happier with 63 than 65.
A: What worries her most about healthcare?
C: The two thousand eight hundred a month you estimated for private cover until Medicare. She wants to see that number in the plan, not just in my head.
A: It's in the 63 scenario for the full two years. I can show it as its own line so she can see it.
C: Please do. My brother also called. He thinks I'm crazy to stay on for a bonus at Northgate.
A: What do you think?
C: I think the bonus is real money and I'd be here until 63 anyway. I'm not letting him decide this.
A: Then here is what I'd suggest. You and Mariko take a few days. If you reach a decision, tell me on our May 1 call and I'll change the planning date then, not before.
C: Agreed. I'll tell you on Friday. Nothing changes in the file until I say so.
""".strip()),

    dict(key="c1_m2", client=1, start="2026-06-10T08:00", platform="Teams",
         subject="Deferred compensation: reviewing the election form",
         refs=["INT-120", "THR:c1_t3"],
         turns="""
A: Good morning, Kenji. I've read the election form. Shall I go through my comments before you submit it?
C: Yes. I've decided on five installments starting at 63, so I mostly want a second pair of eyes.
A: Two comments. The form asks for a beneficiary if you die before the installments finish, and it's blank. And the start-date field says age 63, but the plan defines it as the first January after your 63rd birthday. Check that matches what Hannah assumed in her estimate.
C: I don't know what she assumed. I'll ask her.
A: Please ask her to confirm it in writing so it's on file. On the beneficiary, who do you want?
C: Mariko first, then Yuki and Ren equally. I'd like to confirm that against the estate documents, though.
A: That makes sense, and I'm collecting your wills and designations anyway. Do you want Mariko on the form now, with the contingent names to follow?
C: Yes. Mariko as primary now. Contingent names after the documents review.
A: For the record, neither payout option was my recommendation. I said I wouldn't choose until we saw Hannah's numbers. Five installments is your decision, and I think it's a reasoned one.
C: Good. The administrator's deadline is June 26. I'd like to submit by the 19th.
A: That works. Send me the signed copy afterwards so it's on file.
C: Will do.
A: Separately, the fourteen thousand for Ren's semester is still earmarked. Have you heard about his acceptance?
C: Nothing yet. They said end of June.
A: Then I'll leave the earmark as it is and we'll check at the half-year review.
C: Good.
""".strip()),
]

EMAILS = [
    dict(key="c1_t1", client=1, subject="Early retirement scenarios - summary of Monday's call",
         refs=["INT-027"],
         msgs=[
             dict(t="2026-02-10T09:05", f="A", to="C", att="scenario_summary_2026-02-09.pdf", body="""Dear Kenji,

Thank you for your time yesterday. A short summary of the three scenarios I ran:

- Retire at 65: the plan funds $15,000 a month with a comfortable margin, even with modest returns.
- Retire at 63: it still works, but the margin is thinner.
- Retire at 60: it only works if the portfolio performs near its historical average. The gap is mostly healthcare (about $2,800 a month for private cover for the two of you until Medicare) and five fewer years of contributions.

I described 60 as feasible with trade-offs and not recommended on current numbers. As discussed, you'd like to retire at 60, so the next step is to price what part-time consulting income would need to be against your spending target.

Best regards,
Priya"""),
             dict(t="2026-02-10T18:40", f="C", to="A", body="""Priya,

Thanks. The healthcare gap is bigger than I had assumed. I'd like to see what part-time consulting at 60 would have to earn. Let me think about it over the next few weeks.

Kenji"""),
             dict(t="2026-02-11T08:20", f="A", to="C", body="""Kenji,

Happy to model that. I'll send a range of consulting-income levels before our next meeting. No decision is needed from you now.

Priya"""),
         ]),
    dict(key="c1_t2", client=1, subject="Cash earmark for Ren's semester in Seoul",
         refs=["INT-091", "INT-114"],
         msgs=[
             dict(t="2026-05-05T10:30", f="A", to="C", body="""Dear Kenji,

Confirming what we agreed on May 4. I've earmarked $14,000 from your cash for Ren's possible semester in Seoul next spring. If he isn't accepted, the money returns to your general reserve. I've recorded the earmark as additional tuition for the semester.

When the program sends its cost breakdown, please forward it to me.

Best regards,
Priya"""),
             dict(t="2026-05-05T19:15", f="C", to="A", body="""Priya, thanks. I'll forward the breakdown as soon as the program office sends it. Ren is still waiting to hear whether he's accepted.

Kenji"""),
             dict(t="2026-05-29T14:05", f="A", to="C", body="""Dear Kenji,

A correction to my May 5 email. The $14,000 is not additional tuition. As you explained on today's call, it covers the program fee, flights and housing; Ren's normal tuition is paid separately and is already in your cash flow. I've corrected the file note and dated it today. My May 5 email is unchanged in the record.

Best regards,
Priya"""),
             dict(t="2026-05-29T16:05", f="C", to="A", body="""That's right. The program's cost breakdown still hasn't arrived. I'll forward it when it does.

Kenji"""),
         ]),
    dict(key="c1_t3", client=1, subject="Deferred compensation payout - request for tax comparison",
         refs=["INT-107", "INT-120"],
         msgs=[
             dict(t="2026-05-20T09:30", f="A", to="HP", cc="C", att="deferred_comp_options.pdf", body="""Dear Hannah,

Kenji asked me on our May 19 call to ask you to compare the tax cost of his three deferred-compensation payout options: (a) a lump sum at 63, (b) five equal annual payments, or (c) ten equal annual payments. The plan summary is attached. I have shared only what you need for this comparison, and not his portfolio details.

Please base the estimate on retirement at 63. Kenji has not yet chosen an option, so please treat this as a comparison only.

Best regards,
Priya Nair"""),
             dict(t="2026-06-01T16:10", f="HP", to="A", cc="C", body="""Dear Priya, dear Kenji,

Estimates, based on a retirement age of 63 and the income shown on the 2025 return:

- Lump sum at 63: pushes the year into a high bracket.
- Five annual installments: keeps you in a lower bracket than the lump sum.
- Ten annual installments: saves a little more tax than five, but the balance stays with the company for longer.

These are estimates only; nothing is final until the election is filed.

Hannah Park"""),
             dict(t="2026-06-01T20:05", f="C", to="A", cc="HP", body="""Thank you both. I'm leaning toward five installments, but I'll confirm on Wednesday's call. Please don't treat it as final yet.

Kenji"""),
             dict(t="2026-06-03T12:30", f="A", to="C", cc="HP", body="""Dear Kenji,

Following this morning's call: your decision to elect five annual installments starting at 63 is recorded. Please send me the draft election form so I can comment before you submit it. Hannah, no further action is needed beyond keeping your estimate on file.

Best regards,
Priya"""),
         ]),
]

CHATS = [
    dict(key="c1_c1", client=1, refs=["INT-049"], msgs="""
2026-03-05T07:50|C|Priya, thanks for fixing the note yesterday. Can you confirm it says 65 is the planning date?|
2026-03-05T08:10|A|Confirmed. Formal planning date is 65; 60 is recorded as something you're exploring. The clarification is dated Mar 4 and the earlier note stays in the history.|1
2026-03-05T08:12|C|Good. And Hannah, she said April, right?|
2026-03-05T08:20|A|Yes, you told me she'd come back in April. I'll chase you, not her.|3
""".strip()),
    dict(key="c1_c2", client=1, refs=["INT-114", "THR:c1_t2"], msgs="""
2026-06-01T07:45|C|Priya, did the Seoul note get fixed? It's program fee, flights and housing. Not extra tuition.|
2026-06-01T07:58|A|Corrected on May 29, and I emailed the correction the same day.|1
2026-06-01T08:01|C|Great. The program office says acceptance letters go out in late June.|
2026-06-01T08:05|A|Okay. The earmark stays. We'll look at it again at the half-year review.|3
""".strip()),
    dict(key="c1_c3", client=1, refs=["INT-120", "MTG:c1_m2"], msgs="""
2026-06-12T18:20|C|Hannah confirmed in writing that her estimate assumed the first January after my 63rd birthday. I'll forward it.|
2026-06-12T18:31|A|Good, thank you. That matches the plan's definition.|1
2026-06-12T18:33|C|I'll submit the form on Monday. Mariko as primary beneficiary. Contingent names later.|
2026-06-12T18:40|A|Fine. Please send me the signed copy once it's in.|3
2026-06-12T18:41|C|Will do.|4
""".strip()),
]

SCENARIOS = [
    dict(id="SCN-C1-01", client=1, title="Retirement age: 65, exploring 60, then 63",
         dims="context|certainty|currency|provenance",
         arc="Kenji explores 60, the advisor's summary overstates it, the note is corrected on Mar 4, and 63 is considered, then decided on May 1.",
         records=[("INT-027", "origin: scenario results; 60 feasible but not recommended"),
                  ("c1_t1", "advisor email overstates exploration as a wish to retire at 60; client reply is tentative"),
                  ("INT-049", "client corrects: exploring only, 65 is still the planning date"),
                  ("c1_c1", "client asks for confirmation the corrected note says 65"),
                  ("INT-074", "63 raised as a maybe after the retention bonus"),
                  ("c1_m1", "63 under serious consideration; planning date still 65; decision promised for May 1"),
                  ("INT-090", "client decides 63")]),
    dict(id="SCN-C1-02", client=1, title="Ren's $14,000 semester abroad",
         dims="context|certainty|currency|provenance",
         arc="Earmark agreed May 4; advisor email mislabels it additional tuition; client corrects on May 29.",
         records=[("INT-091", "origin: earmark of $14,000"),
                  ("c1_t2", "advisor email labels it additional tuition; later corrected in same thread"),
                  ("INT-114", "client corrects the purpose"),
                  ("c1_c2", "client checks the correction was made; acceptance still pending")]),
    dict(id="SCN-C1-03", client=1, title="Deferred compensation payout election",
         dims="certainty|currency|governance|provenance",
         arc="Undecided on May 19; CPA estimates; tentative email on Jun 1; decision on Jun 3; form review and submission pending.",
         records=[("INT-107", "origin: options listed, no decision, client asks for CPA estimate"),
                  ("c1_t3", "advisor shares plan summary with the client's CPA; tentative client lean; decision recorded Jun 3"),
                  ("INT-120", "client decides five installments from 63"),
                  ("c1_m2", "form review; beneficiary partly open; submission pending"),
                  ("c1_c3", "CPA confirmation forwarded; submission planned; signed copy pending")]),
]
