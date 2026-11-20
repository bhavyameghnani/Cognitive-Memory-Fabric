"""CLIENT-002 (Rebecca Holloway) / ADVISOR-002 (Daniel Okafor). Authored synthetic content."""
CLIENT = 2

MEETINGS = [
    dict(key="c2_m1", client=2, start="2026-05-05T11:00", platform="Teams",
         subject="Ethan's college funding and the 529 question",
         refs=["INT-038", "INT-083"],
         turns="""
A: Rebecca, thanks for making time. One topic today: Ethan's college. On February 19 you said there's about eighty-five thousand in the account David started. Is that still right?
C: As of the April statement, yes. About eighty-five. Ethan applies this autumn.
A: Do you have a sense of what the schools on his list cost?
C: A mix of private and state. I don't want him choosing a school on price.
A: Understood, and I'll plan for that. Say private at about eighty thousand a year all-in. Four years is three hundred twenty thousand. The account covers roughly a quarter of it. The rest comes from income and, later, from the sale proceeds.
C: After the sale closes. Not before. Don't plan Ethan's college on money that isn't in my account yet.
A: Fair. I'll show two versions: one funded from income only, and one that includes the proceeds.
C: Do that.
A: Separately, a suggestion: a 529 plan could hold part of what you set aside. With a start date this close, the tax benefit is smaller, so I wouldn't rush it.
C: What's the downside?
A: Less flexibility if he gets scholarships or goes a different route, though there are exceptions.
C: I'm not sure I want to open one. Ask me again once I know where he's applying.
A: Then it stays open. I'll hold the 529 as a suggestion and I won't set anything up.
C: Right. David thinks state schools are fine. I'm the one pushing private. We'll sort that out at home.
A: Understood. I'll model both so you have the numbers when you do.
C: Good. My mother's reserve next time. I want to keep this short today.
""".strip()),

    dict(key="c2_m2", client=2, start="2026-06-15T11:00", platform="Teams",
         subject="Life after closing: first pass of the cash-flow model",
         refs=["INT-130", "INT-126"],
         turns="""
A: Rebecca, I've built the post-closing model. Let me walk you through it. It assumes you stop drawing a salary when the deal closes, so your spending comes from a spending account seeded from the proceeds.
C: Okay.
A: Spending is about fourteen thousand five hundred a month, and I've sized the account at eighteen months of that. Your mother's care reserve sits separately.
C: Fine, fine. That's what I thought.
A: One thing I need from you: please read the actual assumptions page. I'm relying on your reading as my check.
C: I'll try. The lawyers are fighting about the escrow amount and I have three calls after this.
A: Then I'll record that you've seen the model, not that you've reviewed it.
C: That's accurate.
A: The main output is that the portfolio starts funding your spending right at closing. Everything else follows from that.
C: Okay. Send it today.
A: I will. And a reminder: the retention pool is still an idea, and the model doesn't include it.
C: Correct. Not decided. I haven't asked Lakeshore.
A: Anything else before you run?
C: No. Escrow. It's always escrow.
""".strip()),
]

EMAILS = [
    dict(key="c2_t1", client=2, subject="Lakeshore - first offer, summary and next steps",
         refs=["INT-052", "INT-058"],
         msgs=[
             dict(t="2026-03-12T10:10", f="A", to="C", body="""Rebecca,

Summary of yesterday's meeting on the first Lakeshore offer. The headline is $24 million under the proposed letter of intent. I still owe you a model of the cash you'd receive at closing alone, which I'll send as soon as it's done.

Following your acceptance of the offer, the immediate next steps are:
1. Your lawyer finishes the redlines on the letter of intent.
2. I update your suitability assessment, because reinvested proceeds would change your risk profile.
3. We pencil in a meeting once the redlines are back.

Daniel"""),
             dict(t="2026-03-12T17:30", f="C", to="A", body="""Daniel, thanks. My lawyer says the redlines will take another week. Send me the cash-at-closing model as soon as you have it.

Rebecca"""),
             dict(t="2026-03-24T14:30", f="A", to="C", att="cash_at_closing_model.xlsx", body="""Rebecca,

A correction to my March 12 email, where I wrote "following your acceptance of the offer". That was wrong. You told me you are inclined to accept; you have not signed the letter of intent and your lawyer is still negotiating. I have corrected my record today.

The model is attached. Cash at closing is about $13.5 million after tax and fees.

Daniel"""),
             dict(t="2026-03-24T15:02", f="C", to="A", body="""Thanks for fixing that. Keep it that way until I sign: inclined, not accepted.

Rebecca"""),
         ]),
    dict(key="c2_t2", client=2, subject="Retention pool for key employees - an idea, not a decision",
         refs=["INT-121"],
         msgs=[
             dict(t="2026-06-03T14:20", f="A", to="C", body="""Rebecca,

Following today's call. The idea you raised is a retention pool of $1 million to $1.2 million, taken from your proceeds, for Luis and the six supervisors. On my rough estimate the after-tax cost to you is about $700,000; please have your accountant check that figure.

You have not raised it with Lakeshore. My suggestion is to take it to your lawyer before the staff announcement so nobody is surprised. This is an idea, not a decision.

Daniel"""),
             dict(t="2026-06-04T07:40", f="C", to="A", body="""Agreed, it's an idea. I haven't decided and I haven't spoken to Lakeshore. My lawyer is away until Monday.

Rebecca"""),
             dict(t="2026-06-04T09:15", f="A", to="C", body="""Understood. I'll keep it as an open idea. If Lakeshore agreed to fund part of it, your cost would fall. Let's hear your lawyer first.

Daniel"""),
         ]),
    dict(key="c2_t3", client=2, subject="Post-closing cash-flow model",
         refs=["INT-130", "INT-142"],
         msgs=[
             dict(t="2026-06-15T16:30", f="A", to="C", att="post_closing_cashflow_v1.xlsx", body="""Rebecca,

As promised, the first version of the post-closing model. It assumes your salary stops at closing and that the portfolio funds your spending from that point. The spending account is sized at 18 months of spending.

Please read the assumptions page; that's where I'd most like a second pair of eyes.

Daniel"""),
             dict(t="2026-06-16T08:10", f="C", to="A", body="""Thanks. I haven't opened it properly. The escrow dispute is eating my week.

Rebecca"""),
             dict(t="2026-06-24T09:00", f="A", to="C", att="post_closing_cashflow_v2.xlsx", body="""Rebecca,

A correction after yesterday's call. Version 1 assumed your salary stops at closing. You told me section six of the draft keeps you in a paid consulting role for the twelve months after closing, at your current salary. Version 2 reflects that: portfolio withdrawals begin only after those twelve months, and the spending account is larger than it needs to be.

My recommendation is to resize the spending account. I haven't changed anything.

Daniel"""),
             dict(t="2026-06-24T11:25", f="C", to="A", body="""Good. Resize it after the escrow is settled, not before.

Rebecca"""),
         ]),
]

CHATS = [
    dict(key="c2_c1", client=2, refs=["INT-078"], msgs="""
2026-04-20T18:45|C|Daniel. I keep going back and forth. Is it crazy to wait two years?|
2026-04-20T19:02|A|Not crazy. It's a legitimate option and I'd rather you choose it than drift into it. What changed since this morning?|1
2026-04-20T19:05|C|Nothing. Just twenty-two years of me.|
2026-04-20T19:10|A|Understood. Let's use our Wednesday 11:15 slot to go through it properly.|3
2026-04-20T19:11|C|Okay. I'm not deciding by text.|
""".strip()),
    dict(key="c2_c2", client=2, refs=["INT-121", "THR:c2_t2"], msgs="""
2026-06-09T12:10|C|Quick one. My lawyer thinks the retention pool is worth raising with Lakeshore. Should I?|
2026-06-09T12:25|A|My view is that it's worth raising as a request, ideally with Lakeshore co-funding. It's still your call. Your after-tax cost is the number to keep in mind.|1
2026-06-09T12:31|C|I haven't decided how much.|
2026-06-09T12:40|A|Then don't put a number in writing yet.|3
2026-06-09T12:41|C|Fine. Holding it.|
""".strip()),
    dict(key="c2_c3", client=2, refs=["INT-126"], msgs="""
2026-06-12T09:20|C|Paid both this morning from the cash account. The lawyer's forty thousand retainer and Mei Lin's fifteen thousand advance.|
2026-06-12T09:48|A|Thanks. So the fifty-five thousand has now left your cash, as we agreed on Monday. I'll update my records.|1
2026-06-12T09:50|C|Fine. No surprises, I hope.|
""".strip()),
]

SCENARIOS = [
    dict(id="SCN-C2-01", client=2, title="Lakeshore offer: inclined, not accepted; second thoughts; decision to sell",
         dims="context|certainty|currency|provenance",
         arc="Advisor email says accepted; client was only inclined; corrected on Mar 24; second thoughts on Apr 20; firm decision on Apr 22.",
         records=[("INT-052", "origin: first offer"),
                  ("c2_t1", "advisor email says offer accepted; correction and client confirmation of 'inclined' on Mar 24"),
                  ("INT-058", "client corrects the record: inclined, not signed"),
                  ("INT-077", "walk-away floor of 22M stated as current position, not decision"),
                  ("INT-078", "second thoughts about timing"),
                  ("c2_c1", "client wavers by chat; refuses to decide by text"),
                  ("INT-083", "client decides to sell")]),
    dict(id="SCN-C2-02", client=2, title="Retention pool for key employees",
         dims="certainty|governance|provenance",
         arc="Raised as an idea on Jun 3; client and advisor both stress it is undecided; lawyer's view arrives; still no number.",
         records=[("INT-121", "origin: idea of a $1.0-1.2M pool, undecided"),
                  ("c2_t2", "advisor email restates idea; client agrees it is undecided"),
                  ("c2_c2", "lawyer thinks worth raising; client undecided on amount; advisor suggests no number in writing"),
                  ("c2_m2", "both restate that the pool is not decided and not in the model")]),
    dict(id="SCN-C2-03", client=2, title="Post-closing income: salary stops vs 12-month consulting role",
         dims="context|currency|provenance",
         arc="Advisor assumes no salary after closing; client does not catch it; corrected Jun 23-24.",
         records=[("INT-130", "advisor states income stops at closing"),
                  ("c2_m2", "model walkthrough; client says 'seen', not 'reviewed'"),
                  ("c2_t3", "v1 model assumes no salary; correction and v2 model"),
                  ("INT-142", "client corrects: paid consultant for 12 months")]),
    dict(id="SCN-C2-04", client=2, title="Ethan's college funding and the 529 suggestion",
         dims="context|certainty|governance",
         arc="About $85k in an existing account; client does not want price to drive the choice; 529 is an advisor suggestion, left open.",
         records=[("INT-038", "origin: $85k account, funding gap"),
                  ("c2_m1", "two funding versions requested; 529 suggestion left open")]),
    dict(id="SCN-C2-05", client=2, title="Pre-signing costs paid from cash",
         dims="context|currency|provenance",
         arc="$55k of pre-signing costs (lawyer $40k retainer + accountant Mei Lin's $15k advance) are to be paid from cash; client confirms payment by chat on Jun 12.",
         records=[("INT-126", "origin: $55k pre-signing costs and source of funds"),
                  ("c2_c3", "client confirms both payments were made from the cash account")]),
]
