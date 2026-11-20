"""CLIENT-004 (Grace Adeyemi) / ADVISOR-001 (Priya Nair). Authored synthetic content."""
CLIENT = 4

MEETINGS = [
    dict(key="c4_m1", client=4, start="2026-03-18T09:30", platform="OneDrive",
         subject="Survivor pension form in plain language; the lump-sum option",
         refs=["INT-050", "INT-029"],
         turns="""
A: Grace, thank you for coming in. Today I want to go through the pension form line by line, in plain language, and decide nothing. Is that all right?
C: Yes please. I brought the form. I don't understand half of it.
A: This line says survivor benefit: sixty percent of Samuel's monthly pension, paid to you for life.
C: For life. And it doesn't go down?
A: Not unless the plan itself changes, which would be unusual. The form doesn't mention increases, so I'd plan on it staying level. I'll ask the plan administrator to confirm that.
C: Level. So prices go up and it stays the same.
A: Yes. That's partly why I raised the annuity. Someone might offer to cover that. I'd like you to understand the trade before choosing.
C: The man says guaranteed. Guaranteed is a nice word.
A: It is. It's only as good as the company and the contract, and it costs you access to the money.
C: And this part, the lump sum?
A: The form offers a lump sum instead of the monthly payments. I haven't evaluated it and I'm not recommending it. I'll compare the two and bring you the numbers.
C: I think I'd rather have the monthly money. I like knowing it comes. But please don't write that down as final.
A: Understood. I'll note a preference for monthly income, not a decision, until my comparison is done.
C: And the annuity. I'm feeling like no. But I don't know.
A: You don't have to know today. Let's set a decision call for the twenty-sixth.
C: The twenty-sixth. Good. Thank you for not rushing me.
""".strip()),

    dict(key="c4_m2", client=4, start="2026-06-15T09:30", platform="Teams",
         subject="Folake's share: held in trust or outright",
         refs=["INT-124", "THR:c4_t3"],
         turns="""
A: Grace, thank you for making time. Last week we raised whether Folake's share should be held in a trust. Today I'll explain the idea and nothing is decided. Is that all right?
C: Yes. Tobi called afterwards and said, "Mum, don't make it complicated."
A: Fair. The idea is simple. Money left to Folake outright means she controls it at once. A trust lets you set conditions or timing. A trust costs more and is less flexible.
C: Why would I want conditions? Folake is sensible.
A: Sensible people still get married, divorced or sued. I don't mean to alarm you. For many families the reason is protection and timing, not mistrust.
C: I don't want her to feel I don't trust her.
A: That's a real concern, and it's yours to weigh. One option is outright for now, and revisit it with an attorney.
C: Maybe. I think I'd like to hear what an attorney says.
A: I'd suggest speaking to an estate attorney before you decide. I can give you two names.
C: Yes, please. But I'm not deciding this month.
A: Understood. I'll record it as open, and I'll send you the two names by Friday.
C: And Tobi's gift. Up to fifteen. Did I say that right?
A: Up to $15,000, only as a gift, from the insurance payout. You haven't set a date.
C: Correct. Not yet.
""".strip()),
]

EMAILS = [
    dict(key="c4_t1", client=4, subject="Samuel's pension and the inherited account - what we covered",
         refs=["INT-029", "INT-050"],
         msgs=[
             dict(t="2026-02-10T10:00", f="A", to="C", body="""Dear Grace,

Thank you for speaking with me yesterday. I know some of those papers are hard to face, and there's no rush beyond what we agreed.

A short summary:
1. Death certificates: you have two and have ordered more.
2. The pension letter from Samuel's employer hasn't arrived yet. I need it for the next calculation.
3. Samuel's retirement account: you can roll it into your own or keep it as an inherited account. There is no deadline, but I'll prepare a plain-English comparison.
4. From the company's summary, the pension survivor payment looks like about half of Samuel's pension.

Kind regards,
Priya"""),
             dict(t="2026-02-10T16:45", f="C", to="A", body="""Thank you Priya. You are very patient. I am worried that half might not be enough. I will look for the letter again this weekend.

Grace"""),
             dict(t="2026-02-11T08:30", f="A", to="C", body="""Grace, please don't worry about the number yet. It comes from a summary, not from the plan's own letter. Once the letter arrives we will know.

Priya"""),
             dict(t="2026-03-10T09:40", f="A", to="C", att="survivor_form_summary.pdf", body="""Dear Grace,

A correction to my February 10 email. I wrote that the survivor payment looked like about half of Samuel's pension. I had read the company's summary too loosely. The form actually gives sixty percent, payable for life, plus an optional lump sum that I haven't evaluated yet. A one-page summary of the form is attached.

Kind regards,
Priya"""),
             dict(t="2026-03-10T14:20", f="C", to="A", body="""Oh thank you Priya. That is better news. I will tell Folake tonight. Does it mean I do not need the annuity?

Grace"""),
             dict(t="2026-03-11T09:05", f="A", to="C", body="""Grace, good question, and I would rather answer it in person. The survivor pension is one input. I haven't recommended for or against an annuity, and the decision is yours. We'll talk before you decide.

Priya"""),
         ]),
    dict(key="c4_t2", client=4, subject="Paying off the mortgage - the numbers",
         refs=["INT-030", "INT-084", "INT-122"],
         msgs=[
             dict(t="2026-02-12T09:30", f="A", to="C", body="""Dear Grace,

Following yesterday's conversation about the mortgage. Paying it off would use a large part of the insurance payout and leave less in reserve. For now I have set the payout aside, labelled as reserved for paying off the mortgage, while we look at the numbers together.

Kind regards,
Priya"""),
             dict(t="2026-02-12T15:20", f="C", to="A", body="""Thank you Priya. I wish it were paid off. It is all I think about at night. But I do not want to do anything yet.

Grace"""),
             dict(t="2026-04-24T14:10", f="A", to="C", body="""Dear Grace,

Following today's call: you have decided to keep the mortgage in place for now. The insurance payout stays in the money-market and short-term Treasury holdings while we plan.

Kind regards,
Priya"""),
             dict(t="2026-04-25T09:00", f="C", to="A", body="""Yes that is right. Thank you.

Grace"""),
             dict(t="2026-06-08T11:30", f="A", to="C", body="""Dear Grace,

A correction. The insurance payout is not reserved for paying off the mortgage; my February 12 email labelled it that way when what you had expressed was a wish. As you described on June 5, the money is for Folake's tuition (part of it, so you feel safe) and for a gift to Tobi of up to $15,000. That amount is a ceiling, not an instruction.

Kind regards,
Priya"""),
             dict(t="2026-06-08T15:40", f="C", to="A", body="""Yes. Thank you for fixing it. I will think about how to explain it to Tobi.

Grace"""),
         ]),
    dict(key="c4_t3", client=4, subject="Beneficiary forms and Folake's share",
         refs=["INT-124"],
         msgs=[
             dict(t="2026-06-08T15:20", f="A", to="C", body="""Dear Grace,

Following today's call about beneficiary forms. Two things:
1. You told me Tobi and Folake will be equal beneficiaries and that you are sending the signed forms back. Once they arrive I will check them against your wishes.
2. Whether Folake's share should be held in a trust is an open question. I'm not recommending either way yet; I'd like an estate attorney's view first.

Kind regards,
Priya"""),
             dict(t="2026-06-09T10:05", f="C", to="A", body="""Dear Priya, I posted two of the three forms this morning. I cannot find the third. I will keep looking.

Grace"""),
             dict(t="2026-06-09T11:00", f="A", to="C", body="""Grace, that's fine. If you can't find it, I can send you a request form to sign so we can get a copy from the provider. Please tell me which account it belongs to.

Priya"""),
         ]),
]

CHATS = [
    dict(key="c4_c1", client=4, refs=["INT-050", "MTG:c4_m1"], msgs="""
2026-03-25T19:20|C|Priya I cannot stop thinking about the annuity man. He phoned again.|
2026-03-25T19:34|A|You don't have to speak to him. Would you like me to handle it?|1
2026-03-25T19:40|C|No no. I will tell him I am thinking.|
2026-03-25T19:44|A|That's fine. We talk tomorrow at 9:15. Nothing to sign before then.|3
""".strip()),
    dict(key="c4_c2", client=4, refs=["INT-097", "INT-110"], msgs="""
2026-06-01T10:45|C|Priya did it happen today? the first step|
2026-06-01T11:20|A|Yes. About $24,000 went into the global equity fund this morning. It is the first of six steps.|1
2026-06-01T11:24|C|Thank you. I feel sick and excited at the same time|
2026-06-01T11:30|A|That is a normal mix. We'll read the statement together on Friday.|3
""".strip()),
    dict(key="c4_c3", client=4, refs=["INT-128", "THR:c4_t3"], msgs="""
2026-06-10T20:15|C|Priya I looked again. It is down more. Should I stop the plan|
2026-06-10T20:40|A|We talked about exactly this this morning. You don't need to decide anything tonight. The next step is July 1, and I'll call you first if the holding is down more than ten percent from the first purchase.|1
2026-06-10T20:44|C|Thank you. I will try not to look|
2026-06-10T20:50|A|Good. Message me if you want to talk before then.|3
""".strip()),
]

SCENARIOS = [
    dict(id="SCN-C4-01", client=4, title="Survivor pension: about half, then 60 percent; annuity decision",
         dims="context|certainty|currency|provenance",
         arc="Advisor's Feb summary says about half; form says 60 percent; corrected Mar 9-10; client prefers monthly income tentatively; annuity decided on Mar 26.",
         records=[("INT-029", "origin: pension letter outstanding"),
                  ("c4_t1", "advisor email says about half; later correction to 60 percent"),
                  ("INT-050", "advisor corrects 60 percent; annuity question raised"),
                  ("c4_m1", "form walkthrough; tentative preference for monthly income; lump sum not evaluated"),
                  ("c4_c1", "client anxious about annuity salesman; no action"),
                  ("INT-059", "annuity decision call")]),
    dict(id="SCN-C4-02", client=4, title="Gradual investing plan: first step and nerves",
         dims="certainty|currency",
         arc="Decision on May 8, first purchase Jun 1, second-thoughts after a down day, conditional advisor call before Jul 1.",
         records=[("INT-097", "decision: six steps to a 25 percent target"),
                  ("INT-110", "first step quantified (about $24,000)"),
                  ("c4_c2", "first step executed on Jun 1"),
                  ("INT-122", "first statement reviewed"),
                  ("INT-128", "second thoughts; conditional call before Jul 1"),
                  ("c4_c3", "client asks to stop; advisor restates no decision needed and the conditional call")]),
    dict(id="SCN-C4-03", client=4, title="Purpose of the insurance payout: a wish treated as a purpose",
         dims="context|certainty|currency|provenance",
         arc="Feb wish about the mortgage is labelled a purpose in an advisor email; mortgage kept in April; label corrected in June.",
         records=[("INT-030", "origin: wish to pay off the mortgage"),
                  ("c4_t2", "advisor email labels payout as reserved for mortgage; client's reply states a wish, not an action; later keep-mortgage confirmation; June correction"),
                  ("INT-084", "decision to keep the mortgage"),
                  ("INT-122", "client states actual purposes")]),
    dict(id="SCN-C4-04", client=4, title="Folake's share: trust or outright",
         dims="certainty|governance",
         arc="Open question; attorney suggested; client declines to decide this month; one beneficiary form missing.",
         records=[("INT-124", "origin: beneficiary forms and Folake's inheritance"),
                  ("c4_t3", "forms requested; one form missing"),
                  ("c4_m2", "trust explained; attorney suggested; open"),
                  ("INT-146", "half-year review lists it as open")]),
]
