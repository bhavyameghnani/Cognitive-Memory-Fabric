"""CLIENT-002 Rebecca Holloway: owner of Holloway Precision Packaging weighing a sale to Lakeshore Partners.
Rejects an employee-ownership route because she needs cash for her mother's memory-care costs."""
from ..beats import S, G, Check

PROFILE = dict(
    client_id="CLIENT-002", advisor_id="ADVISOR-002",
    name="Rebecca Holloway", adv_name="Daniel Okafor",
    channels=[("in-person", 0.5), ("video", 0.3), ("telephone", 0.2)],
    hours=[11, 13, 15, 17], beta=0.9,
    months=[1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6],
    facts=dict(first="Rebecca", adv_first="Daniel", spend=14500,
               income_desc="your salary and distributions from the company, which vary with the year",
               buffer_target=18,
               liq_reason="most of your net worth being inside the company and your mother's care costs"),
    state=dict(value=2_100_000, equity=60, anxiety="mid", cash=220_000),
    holdings=[
        ("the broad U.S. equity fund", "it is the largest holding and the simplest way to own the market at very low cost"),
        ("the short-term Treasury ladder", "it is your safety layer, and it is sized so a bad year in the business doesn't force sales of anything else"),
        ("the international equity fund", "it diversifies away from the U.S. and was lagging, which is the normal price of diversification"),
        ("the investment-grade corporate bond fund", "it pays more than Treasuries for taking modest credit risk, and I'd keep it modest"),
    ],
    pressures=[
        "your mother's care invoice rose again after the facility's annual adjustment",
        "I paid for new equipment out of my own pocket rather than the company's",
        "Ethan's campus visits and application fees add up faster than I expected",
        "David and I took on the roof repair we'd been postponing",
    ],
    casual=[
        """
C: Ethan is applying to engineering programs this fall. He wants to be an industrial designer, which is funny given what we make.
A: That's a nice connection. Do you want him to see the business in action before he applies?
C: He already works summers on the line.
""",
        """
C: David is thinking about retiring from teaching in a couple of years.
A: That's worth knowing, since it affects household income. Is it a firm plan?
C: No, he's only talking about it. I'll let you know if it firms up.
""",
        """
C: I might relocate to the coast of Maine in a couple of years. We've been looking at a cottage near David's sister.
A: Understood. I'll note it as a possibility, not a plan. If it becomes real, the timing will matter for taxes.
""",
        """
C: My mother had a good week. She recognised me and we looked at old photographs.
A: I'm glad. Those days matter. Thank you for telling me.
""",
        """
C: I took my first full Saturday off in months. I forgot what it was like not to check email.
A: That's a good sign. Founders often forget how tied they are to the business until they step back.
""",
        """
C: The plant manager, Luis, told me he's worried about what a sale would mean for the team.
A: That's a real consideration, and it's one that belongs in your decision alongside the price.
""",
        """
C: I was thinking of joining a board or two once I'm not running things day to day.
A: Interesting. Board fees are modest, but it could be a good use of your experience. Let's keep it as an idea for now.
""",
        """
C: The weather finally warmed up and David and I walked all the way around the lake. We hadn't done that in years.
A: That sounds wonderful.
""",
    ],
)

STEPS = [
 ("Business value and whether to sell", [
  S("""
A: Rebecca, you mentioned on the phone that someone had approached you. Tell me about it.
C: A private equity group called Lakeshore Partners contacted me out of the blue. They said they'd like to talk about acquiring Holloway Precision Packaging. I've run it for twenty-two years and I honestly don't know if I want to sell.
A: What's your gut feeling, if you had to guess today?
C: I'm curious, nothing more. I haven't decided whether to sell the business. It was never part of my plan. But I'm fifty-two, and everything we own is tied up in it.
A: That concentration is exactly why I'd like us to prepare, whatever you decide. Preparing doesn't commit you to selling. Roughly, what do you think the company is worth?
C: A broker friend said around twenty million. Revenue is eighteen million, and EBITDA is about three point two.
A: Then I'll treat twenty million as a rough figure, not a valuation, and we'll look at it after tax. I'll record that you are curious and undecided.
""", log=[("tentative", "whether to sell Holloway Precision Packaging"), ("plan", "preparing financially for a possible sale without committing to one")],
   tags=("uncertain_intention", "stated_intention")),
  G("doc", doc="the last three years of company financial statements and any valuation work your accountant has done", why="so I can build a realistic picture of what a sale might net after tax", id="r_fin", due="in the next two weeks"),
 ], "in-person"),
 ("Personal portfolio and concentration", [
  G("portfolio"),
  G("explain", concept="concentrated_stock"),
  G("liquidity"),
 ], "video"),
 ("Your mother's care costs", [
  S("""
C: I should tell you about my mother. Dorothy is eighty-one, and her memory has declined. She moved into a memory-care facility in the fall. It costs about nine thousand eight hundred a month, and I'm covering what her income doesn't.
A: I'm sorry, that's a lot to carry. How much of the cost falls to you each month?
C: About six thousand. It will probably grow. The doctors say she could be there for years.
A: Then that needs to be a planned liability, not a surprise. It also affects how I'd think about a sale, because you'd want certain money, not money that depends on the business performing.
C: Yes, that's what's on my mind. If I sell, I want enough cash to be sure of her care.
A: I'll include six thousand a month, rising with inflation, in all my projections.
""", log=[("plan", "treating your mother's care, about six thousand dollars a month from you, as a fixed obligation in all projections")],
   tags=("family_context", "rationale_seed")),
  G("cashflow"),
 ], "telephone"),
 ("Valuation documents and what a sale could net", [
  Check("r_fin", "done", "My accountant, Mei Lin Chu, sent three years of statements and a draft valuation range of nineteen to twenty-three million."),
  G("scenario", id="r_net", desc="calculate what a sale at twenty and twenty-four million would leave you after tax, fees and escrow"),
  G("explain", concept="sale_proceeds"),
 ], "in-person"),
 ("Net proceeds and the people side of a sale", [
  S("""
A: Here are the numbers I promised. At twenty million, after fees, escrow and tax, you'd keep roughly fourteen to fifteen million. At twenty-four, closer to seventeen to eighteen, assuming a typical structure. These are estimates and depend on the deal terms.
C: That's lower than I expected, but still a life-changing number.
A: It is. The more important question is what it would be like afterward. Does a sale solve your problems, or only change them?
C: Honestly, I'm worried about my employees. Eighty-five people. Some have been with me since the beginning. I don't want to hand them to someone who will cut jobs.
A: That's a legitimate factor. It can be negotiated, though no buyer can promise it forever. I'd keep it on your list of terms.
C: Okay. I still haven't decided. Let's keep working on the numbers.
""", close=["r_net"], log=[("tentative", "selling the company, while keeping employee protection as a condition")], tags=("uncertain_intention", "scenario_result")),
 ], "video"),
 ("Markets and Ethan's college plans", [
  G("market"),
  G("explain", concept="education_funding"),
 ], "telephone"),
 ("The first offer from Lakeshore", [
  S("""
C: Lakeshore sent a letter of intent. Twenty-four million, with twenty percent rolled over into the new company and an earn-out tied to the next two years.
A: That's a significant step. Have you signed anything?
C: Not yet. My lawyer is reading it. I'm inclined to say yes, but I'm nervous about the earn-out and the rollover.
A: Those two items are where the real risk sits. The rollover means twenty percent of your sale stays at risk with the same business, under new ownership. The earn-out depends on performance you won't control.
C: Should I accept the offer?
A: I can't make that call for you. I can say that I'd want to see the net cash at closing before I called it a good offer. Let's model the cash at closing alone.
""", log=[("tentative", "accepting Lakeshore's letter of intent"), ("a_action", "model the cash at closing alone under the Lakeshore offer")],
   open=[("r_cash", "A", "model the cash at closing alone under the Lakeshore offer")], tags=("uncertain_intention", "advisor_commitment")),
  G("portfolio"),
 ], "in-person"),
 ("Clarifying where the offer stands", [
  S("""
A: Before we begin, I want to confirm something. After our last meeting, my note says you had accepted the Lakeshore offer. Is that correct?
C: No, and I'm glad you asked. I said I was inclined to accept. My lawyer is still negotiating, and I haven't signed the letter of intent.
A: Thank you. That's an important distinction, and my note overstated it. I'll correct it so it says you are considering the offer and have not signed.
C: Please. If my accountant saw a note saying I'd accepted, it would confuse things.
""", log=[("correction", "my earlier note that you had accepted the Lakeshore offer was wrong; you are inclined to accept and have not signed")],
   tags=("correction", "misunderstanding")),
  Check("r_cash", "done", "Cash at closing is about thirteen point five million after tax and fees. The rest depends on the rollover and the earn-out."),
  G("suit", topic="reinvesting sale proceeds"),
 ], "video"),
 ("The employee-ownership alternative", [
  S("""
C: A friend suggested selling to an employee ownership plan instead. That would protect the people, and my lawyer says it can work with a seller note.
A: It can. The trade-off is that a seller note means you're paid over time, typically eight to ten years, from the company's future cash.
C: Ten years is a deal-breaker for me. I need cash now for my mother. If the business had a bad year, the payments could stall while her bills keep arriving.
A: That's a clear reason and I'll record it. So you're declining the employee-ownership route because you need certainty and early liquidity for your mother's care.
C: Yes. I like the idea for the employees, but I can't take the risk.
""", log=[("decision", "to decline the employee-ownership route, because a ten-year seller note would not give the certain cash needed for your mother's care")],
   tags=("confirmed_decision", "rationale", "rejected_option")),
  G("cashflow"),
 ], "in-person"),
 ("Exclusivity period and why to wait on personal moves", [
  G("delay", ask="Since the sale looks likely, should I move my personal portfolio now? Maybe put more into bonds?", reason="The terms aren't final, and if the deal changes or falls through, you'd have repositioned for a situation that never happened. Selling investments could also create taxes that the sale structure might offset.", until="the deal terms are final", item="repositioning your personal portfolio"),
  G("doc", doc="the signed exclusivity agreement and your lawyer's summary of the key terms", why="so I can match your personal planning to the actual deal", id="r_lawyer"),
 ], "telephone"),
 ("Tax planning around a sale", [
  S("""
A: Your accountant and I should speak before any agreement is final. How a sale is structured changes the tax a great deal.
C: Mei Lin said something about an installment structure, but I didn't follow.
A: It means some of the price is paid later, which can spread the tax. The trade-off is the buyer's credit risk and the delay in receiving cash. Given your mother's needs, we'd want enough at closing to cover care.
C: Please speak to Mei Lin. I'd like both of you to agree before I sign.
""", log=[("a_action", "arrange a call with your accountant Mei Lin Chu about the tax structure of the sale")],
   open=[("r_tax", "A", "arrange a call with your accountant Mei Lin Chu about the tax structure of the sale")], tags=("advisor_commitment", "tax")),
  Check("r_lawyer", "pending", "My lawyer still hasn't finished the summary because the buyer keeps changing the earn-out language."),
 ], "video"),
 ("Markets and a family weekend", [G("market"), G("portfolio"), G("liquidity")], "in-person"),
 ("Second thoughts about timing", [
  S("""
C: I've been thinking, and I'm not sure about selling now. Revenue is up this quarter, and maybe I should wait two years and sell for more.
A: That's possible, and it's a different position from where you were in March, when you were inclined to accept.
C: I know. Part of me is worried I'll regret selling at the wrong moment.
A: There's a risk either way. Waiting can mean a higher price, but also a lower one if the market for your industry changes. Your mother's care costs and your concentration risk don't wait with you.
C: Yes. I need to think about it more. I haven't made up my mind.
""", log=[("tentative", "waiting two years before selling")], tags=("contradiction", "uncertain_intention")),
  Check("r_tax", "done", "I spoke with Mei Lin this morning, just before our call. She prefers a mostly cash deal with a small deferred piece, and she'll send a summary."),
 ], "telephone"),
 ("Decision on the Lakeshore sale", [
  S("""
C: I've decided. I'm going to sell to Lakeshore. I want the rollover reduced to ten percent, and I want written protections for the staff for at least two years.
A: Let me repeat it to be sure. You've decided to proceed with the sale, subject to those two conditions, and you want me to treat that as a firm decision.
C: Yes. The reasons are simple. Dorothy's care needs certainty, and I've been carrying this business alone for twenty-two years. Waiting two years doesn't remove either problem.
A: Thank you for the reasoning. That reverses what you were saying at our last meeting. I'll record that you considered waiting and chose not to, and why.
C: Good. Please tell Mei Lin that's the plan.
""", log=[("decision", "to sell to Lakeshore with the rollover cut to ten percent and two years of written staff protections, because certainty for your mother's care mattered more than waiting for a higher price"),
         ("a_action", "tell Mei Lin Chu about your decision")],
   tags=("confirmed_decision", "rationale", "contradiction")),
 ], "in-person"),
 ("Due diligence and documents", [
  Check("r_lawyer", "done", "He finally sent the summary. It shows the rollover at ten percent and the staff protection language."),
  G("doc", doc="the last two years of personal tax returns", why="so I can estimate your tax position for the year of the sale", id="r_returns", due="within the next two weeks"),
  G("cashflow"),
 ], "video"),
 ("Setting up a care reserve for your mother", [
  S("""
A: Because your mother's care is a fixed obligation, I'd suggest setting a care reserve at closing. Six thousand a month, rising with inflation, for ten years comes to well over a million dollars.
C: Over ten years? She might not live that long.
A: She might not, but it's a conservative assumption, and money left over would simply return to your general assets. I'd rather plan for the longer case.
C: Then yes. Put aside six hundred thousand at closing for that reserve, in safe investments. I'd like to review it each year.
A: Six hundred is lower than my ten-year figure, so I'd call it a partial reserve. Are you comfortable with that?
C: Yes. The rest of my income can cover the gap.
""", log=[("decision", "to set aside six hundred thousand dollars of the proceeds at closing as a care reserve for your mother, invested conservatively and reviewed yearly")],
   tags=("confirmed_decision", "family_context")),
 ], "in-person"),
 ("Charitable giving ideas", [
  S("""
C: I'm thinking I might give some of the proceeds to a charity, maybe a scholarship for apprentices at the plant. I haven't worked out how.
A: A donor-advised fund can be efficient. A gift in the year of the sale can offset some of the tax. But I'd treat it as an idea until the numbers are firm.
C: Agreed. Let's not decide until the deal is closer.
""", log=[("deferred", "any charitable gift until the sale numbers are firm")], tags=("deferred_decision", "uncertain_intention")),
  G("portfolio"),
  G("market"),
 ], "video"),
 ("Investment approach for the sale proceeds", [
  G("suit", topic="how the sale proceeds are invested"),
  S("""
A: In the past, you described yourself as willing to take risk, mostly because the business was a risky asset. Once it is converted to cash, does that change?
C: It does. I've taken enough risk for twenty years. After the sale I'd like something much more conservative than what I have now.
A: Then I'd propose we design a lower-risk portfolio for the proceeds, with perhaps forty percent in equities, while your current portfolio stays as it is. I'll draft an investment policy for your approval.
C: Please do.
""", log=[("tentative", "a more conservative portfolio for the sale proceeds"), ("a_action", "draft an investment policy statement for the sale proceeds at about forty percent equities")],
   open=[("r_ips", "A", "draft an investment policy statement for the sale proceeds at about forty percent equities")], set={"anxiety": "low"}, tags=("changing_preference",)),
 ], "telephone"),
 ("Review of open items", [
  Check("r_returns", "pending", "My bookkeeper has the 2024 return but 2025 is still with the accountant."),
  Check("r_ips", "done", "I've drafted it. It has forty percent equities, a care reserve sleeve and a liquidity bucket, and I'll leave a copy in your portal."),
  G("liquidity"),
 ], "video"),
 ("Staff announcement and personal stress", [
  S("""
C: Lakeshore wants to announce to the staff next month. I feel sick about it. What if they leave?
A: That's a natural reaction. Buyers usually expect some turnover, and retention bonuses can help. I'm not your lawyer, but I'd raise a retention pool in the negotiation.
C: I'll ask. I just feel responsible for all of them.
A: You are, and you're doing the best you can. How are you sleeping?
C: Poorly. But David's been good about it.
""", tags=("casual_context", "personal_stress")),
  G("portfolio"),
 ], "telephone"),
 ("Signing timeline and cash needs", [
  G("liquidity"),
  Check("r_returns", "done", "The accountant finally released the 2025 return and I uploaded it."),
  G("market"),
 ], "in-person"),
 ("What changes in your plan after closing", [
  S("""
A: Let me outline how your financial plan will look if the sale closes as expected. Your income will no longer include a salary, so we'll move to drawing from a portfolio. Your spending is about fourteen thousand five hundred a month plus the care cost.
C: It sounds strange not to have a paycheck.
A: Many founders feel that way. We'll rehearse it with a monthly 'salary' from your accounts for a few months. Do you want the care reserve and spending account set up before closing?
C: Yes. I'd like it ready on the first day.
""", log=[("a_action", "set up the care reserve and a monthly spending account so they are ready on the day of closing")], tags=("advisor_commitment", "procedure")),
  G("doc", doc="the final purchase agreement draft", why="so I can check the cash at closing against our model", id="r_spa"),
 ], "video"),
 ("Estate and insurance after the sale", [
  G("explain", concept="sale_proceeds"),
  G("doc", doc="your current will and trust documents", why="so I can check they still make sense with the proceeds", id="r_estate"),
  G("cashflow"),
 ], "in-person"),
 ("Checking in before closing", [
  Check("r_spa", "pending", "My lawyer says the final draft isn't ready because of a dispute about the escrow amount."),
  G("portfolio"),
  G("market"),
 ], "telephone"),
 ("Half-year review", [
  S("""
A: Rebecca, let me summarise your year so far. In January you were curious but undecided about selling. You received Lakeshore's offer in March, and you corrected my note that you had accepted it. You declined the employee-ownership plan because you needed certain cash for your mother. In April you wavered about waiting two years, then decided to sell with a ten percent rollover.
C: That's the story. It feels like a long time ago that I was only curious.
A: Open items: the final purchase agreement draft, your estate documents, and the sale-proceeds investment policy for your signature.
C: I'll review the policy this week. The estate documents are waiting on my lawyer.
""", tags=("summary", "history_recall")),
  G("portfolio"),
 ], "video"),
]


PROFILE["casual_windows"] = [(1, 25), (1, 25), (4, 25), (4, 25), (2, 25), (7, 25), (15, 25), ("2026-04-15", "2026-06-30")]
