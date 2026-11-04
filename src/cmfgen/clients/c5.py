"""CLIENT-005 Lucas Moreau: entrepreneur, irregular income, concentrated positions, thin liquidity.
Moves from 'double down' to diversification and a larger cash reserve after losing a major customer."""
from ..beats import S, G, Check

PROFILE = dict(
    client_id="CLIENT-005", advisor_id="ADVISOR-002",
    name="Lucas Moreau", adv_name="Daniel Okafor",
    channels=[("video", 0.55), ("telephone", 0.3), ("in-person", 0.15)],
    hours=[9, 12, 16, 19], beta=1.4,
    months=[1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6],
    facts=dict(first="Lucas", adv_first="Daniel", spend=6500,
               income_desc="lumpy consulting and software revenue, anywhere from four thousand to forty thousand in a month",
               buffer_target=9,
               liq_reason="your irregular income and the tax bills that nobody withholds for you"),
    state=dict(value=1_150_000, equity=95, anxiety="low", cash=31_000),
    holdings=[
        ("your Vantora Systems shares", "they are about a third of the liquid portfolio, and a single company can fall very far, so they're the position I think about most", (1, 14)),
        ("your Vantora Systems shares", "you're selling the position down in three planned steps from its starting size of about a third of the liquid portfolio", (15, 20)),
        ("your Vantora Systems shares", "two of the three planned sales are done, and you've chosen to keep the rest through the third-quarter earnings", (21, 23)),
        ("your Vantora Systems shares", "after the three sales it's down to roughly a sixth of the liquid portfolio, and you've chosen to hold the rest until after the third-quarter earnings", (24, 25)),
        ("the small crypto holding", "it's about five percent, it's extremely volatile, and I'd treat it as money you could lose", (1, 17)),
        ("the total-market index fund", "it's the diversified core and it's the part I'd want to grow over time"),
        ("the cash account", "it holds your tax set-asides and operating reserve, which are not meant to earn much"),
    ],
    pressures=[
        "a client paid sixty days late and I had to cover payroll for my contractor from savings",
        "Camille and I upgraded our apartment lease",
        "I paid for conference travel and software licences up front",
        "Amélie's after-school program costs went up",
    ],
    casual=[
        """
C: Amélie lost her first tooth. She's thrilled, and so is the tooth fairy's budget.
A: Congratulations to her. Dental costs and everything else, I assume, are fine?
C: Yes, all fine.
""",
        """
C: Camille is thinking of switching jobs. Nothing is certain, but she's interviewing.
A: Thanks for telling me. Her income matters a great deal for the household buffer, so please let me know if it changes.
""",
        """
C: I've been working weekends again. I know, I know.
A: I'm not going to lecture you, but the plan assumes you stay healthy. Let's make sure your insurance reflects that.
""",
        """
C: I might relocate to Lisbon in a couple of years. Camille's family has connections there, and the cost of living is lower.
A: Interesting. I'll note it as a possibility. It would bring tax and residency questions, but nothing to solve yet.
""",
        """
C: My co-founder wants to hire two more developers. I'm not convinced we can afford it.
A: That's a business decision, but it affects your income, so I'd like to hear how it goes.
""",
        """
C: I finally took a week off. Went hiking with Amélie. Phone off.
A: Excellent. How was it?
C: Hard at first. Then great.
""",
        """
C: A friend from university is launching a new company and asked me to advise for equity.
A: Flattering. Equity from advising rarely turns into cash quickly. I'd treat it as zero in your plan.
""",
        """
C: We got a puppy. Please don't ask what it cost.
A: I won't, but I'll assume it's in the budget now.
""",
    ],
)

STEPS = [
 ("Your income, your shares and your plans", [
  S("""
A: Lucas, let's start from the top. Walk me through how the money comes in.
C: Moreau Labs sells design software to small agencies, and I do consulting on the side. Some months I make four thousand, some months forty. I own most of Moreau Labs with my co-founder. Outside of that I hold Vantora Systems stock, some crypto, and an index fund.
A: And what's your thinking about the stock?
C: I believe in Vantora. I think it could double. I'd like to put more in. I'm aggressive, and I'd rather concentrate where I have conviction than diversify into mediocrity.
A: I understand the logic. I'll note it, but I also want to point out that your business and your Vantora stock both depend on the technology sector. If it turns, they'd turn together.
C: I know, but I like that risk.
""", log=[("plan", "an aggressive, concentrated approach with conviction in Vantora Systems")], tags=("risk_preference_baseline", "stated_intention")),
  G("portfolio"),
 ], "video"),
 ("Taxes when nobody withholds", [
  G("explain", concept="estimated_tax"),
  G("doc", doc="your 2025 profit and loss statement and your estimated tax payment records", why="so I can see how much you've really set aside and what's owed", id="l_pl", due="by the end of the month"),
 ], "video"),
 ("Adding to the Vantora position", [
  S("""
C: Vantora dropped a few percent this week. I want to buy another fifty thousand.
A: Where would the money come from?
C: Mostly from the cash account.
A: That account holds your tax set-asides and your operating reserve. Using it for a stock purchase would leave you with very little cash during a slow month.
C: I could cover it with the next big invoice.
A: Perhaps, but invoices arrive late. I'd advise against it. I can't stop you, but I'll note that I recommended against, and why.
C: I hear you. Let's not do it today. I might do it later when I have extra cash.
""", log=[("deferred", "adding fifty thousand dollars to the Vantora position until there is spare cash beyond the reserve")],
   tags=("deferred_decision", "advisor_caution", "uncertain_intention")),
  G("suit", topic="adding to a single stock"),
 ], "telephone"),
 ("Cash flow and a quarterly tax payment", [
  G("cashflow"),
  G("liquidity"),
 ], "video"),
 ("Your reserve, in months", [
  Check("l_pl", "pending", "My bookkeeper hasn't finished the final numbers, and some of the invoices were paid in January."),
  G("explain", concept="irregular_reserve"),
  S("""
C: Nine months sounds like overkill. Four or five should be fine, because I can always raise money.
A: Can you? From where?
C: From selling stock, or borrowing against it.
A: That depends on markets being kind at the moment you need the money. I'd rather the reserve not depend on that. But it's your decision, and I'd record that you currently prefer a smaller reserve.
C: Yes. Let's keep it as it is for now.
""", log=[("decision", "to keep the cash reserve at about five months for now, against the advisor's recommendation of nine")],
   tags=("confirmed_decision", "advisor_disagreement")),
 ], "video"),
 ("Tender offer for your startup shares", [
  S("""
C: Moreau Labs got a secondary offer. An investor would buy up to a hundred and fifty thousand dollars of founders' shares. The deadline is in April.
A: Would you sell?
C: I might sell some, but I haven't decided. I like owning the company, but it's most of what I have.
A: That's the key point. The liquid portfolio is about one point one five million, but the startup stake is a large part of your net worth and you can't easily sell it. A secondary can reduce that.
C: Right. I'd want to understand the tax.
A: I'll run a comparison of selling nothing, half, and the full amount, and ask your accountant about the tax treatment.
""", log=[("tentative", "selling some Moreau Labs founder shares in the secondary offer"), ("a_action", "compare selling none, half or all of the offered founder shares, including the tax")],
   open=[("l_sec", "A", "compare selling none, half or all of the offered founder shares, including the tax")], tags=("uncertain_intention", "advisor_commitment")),
 ], "video"),
 ("A friend's restaurant", [
  S("""
C: My friend Hugo is opening a restaurant. He asked for sixty thousand for a share of the profits.
A: Do you want to invest?
C: I'd like to support him. But I'm torn. It could be a good return.
A: Restaurants are high risk and illiquid. Sixty thousand is a large part of your reserve. I'd want to see the business plan and a written agreement before answering.
C: He promised to send it.
""", log=[("tentative", "investing sixty thousand dollars in Hugo's restaurant"), ("c_action", "ask Hugo for the business plan and a written agreement")],
   open=[("l_hugo", "C", "ask Hugo for the business plan and a written agreement")], tags=("uncertain_intention",)),
  G("market"),
 ], "telephone"),
 ("Markets and tax numbers", [
  Check("l_pl", "done", "I got the profit and loss from the bookkeeper. Net income was about one hundred ninety thousand, and I paid about thirty-five thousand in estimates."),
  G("market"),
  G("portfolio"),
 ], "video"),
 ("Losing a major customer", [
  S("""
C: Bad news. Our biggest customer cancelled. It was thirty percent of our revenue.
A: I'm sorry. What does it mean for your income over the next few months?
C: I'm not sure. Maybe half of what I expected, for at least two quarters.
A: This is the scenario the reserve exists for. With five months, you have some time. If the shortfall lasts, you could be forced to sell investments.
C: Yes. I was wrong about the reserve. Now I'm worried.
A: Don't be too hard on yourself. The question is what to do. Would you consider building to nine months?
C: Yes. Let's do that. I've decided: build the reserve to nine months.
""", log=[("decision", "to build the cash reserve to nine months of expenses after losing the largest customer"), ("a_action", "set a plan for building the reserve to nine months")],
   open=[("l_res", "A", "set a plan for building the reserve to nine months")], set={"anxiety": "mid"},
   tags=("changing_preference", "contradiction", "confirmed_decision", "rationale")),
 ], "telephone"),
 ("The restaurant decision", [
  Check("l_hugo", "done", "Hugo sent a plan. It shows break-even in eighteen months, and he wants the money in two weeks."),
  S("""
A: With the customer loss, the reserve target and the tender decision, do you still want to invest sixty thousand?
C: No. I've decided not to invest in the restaurant. I need the cash for the reserve and the taxes, and I can't afford to have it locked in a business I don't control.
A: I'll note that, and your reasoning. Would you like me to help you tell Hugo?
C: No, I'll call him. He'll understand.
""", log=[("decision", "not to invest in Hugo's restaurant, because you need the cash for the reserve and taxes and cannot lock it in a business you don't control")],
   tags=("confirmed_decision", "rationale", "rejected_option")),
 ], "video"),
 ("Vantora falls", [
  S("""
C: Vantora is down almost thirty percent since January. I don't want to sell at a loss.
A: I understand, but the purchase price shouldn't decide the sale. The question is whether you'd buy it today at this size.
C: Honestly, no. Not at this size.
A: Then let's talk about selling in stages. A loss can also offset gains elsewhere. That's called tax-loss harvesting. I'd want your accountant to confirm.
C: Fine. But I'm not ready to commit to anything today.
""", log=[("tentative", "selling part of the Vantora position in stages")], set={"anxiety": "mid"}, tags=("changing_preference", "uncertain_intention")),
  Check("l_res", "done", "I built a plan to put about four thousand a month into the reserve until it reaches nine months."),
 ], "video"),
 ("Clearing up the tender decision", [
  S("""
A: A moment ago I looked at my notes, and they say you intend to sell your shares in the tender. Is that right?
C: No. I said I might. I haven't decided. I'm waiting for your comparison.
A: Thank you. I'll correct my record. You haven't decided, and I owe you the comparison.
""", log=[("correction", "my note that you intended to sell shares in the tender offer was wrong; you were considering it and had not decided")],
   tags=("correction", "misunderstanding")),
  Check("l_sec", "pending", "I'm still waiting on your accountant's answer about how the sale would be taxed."),
  G("cashflow"),
 ], "telephone"),
 ("Decision on the secondary offer", [
  S("""
A: Here's the comparison. Selling half, about seventy-five thousand, would cover your reserve and the next tax payments and leave most of your stake intact. Selling all would raise a lot of cash but cut your ownership meaningfully.
C: Then I've decided. I'll sell half, seventy-five thousand. I need the liquidity, but I don't want to give up the company.
A: Understood. The deadline is close. I'll let you sign the paperwork and send it to the company.
""", close=["l_sec"], log=[("decision", "to sell half, about seventy-five thousand dollars, of the offered founder shares to raise liquidity while keeping most of your ownership"),
         ("c_action", "sign and return the secondary sale paperwork before the deadline")],
   open=[("l_sale", "C", "sign and return the secondary sale paperwork before the deadline")], set={"cash": 52_000}, tags=("confirmed_decision", "rationale", "scenario_result")),
 ], "video"),
 ("Estimated taxes and proceeds", [
  Check("l_sale", "done", "I signed it Monday, and the company confirmed receiving it."),
  G("liquidity"),
  G("cashflow"),
 ], "telephone"),
 ("Trimming Vantora", [
  S("""
C: I've thought about Vantora. I'd like to reduce it. Sell forty percent over three steps, and put the proceeds into the index fund.
A: That's a clear instruction. Is the reasoning that the position is too large for your risk?
C: Yes. After the customer loss I don't want two big bets in the same sector.
A: Understood. I'll sell in three equal parts over six weeks and send you confirmations.
""", log=[("decision", "to sell forty percent of the Vantora position in three steps and move the proceeds to the index fund, because a business and a stock in the same sector is too much concentration"),
         ("a_action", "sell the Vantora shares in three equal steps and send confirmations")],
   set={"equity": 90}, tags=("changing_preference", "contradiction", "confirmed_decision", "rationale", "explicit_instruction")),
  G("market"),
 ], "video"),
 ("Moving to Lisbon?", [
  S("""
C: Camille and I have been talking about Lisbon again. Maybe in two years.
A: Is this a plan, or a daydream?
C: Somewhere between. I'm not asking you to plan on it. I just want you to know.
A: Noted as a possibility. If it becomes real, it affects your tax residency and the way we hold some investments.
""", tags=("uncertain_intention", "casual_context")),
  G("portfolio"),
 ], "telephone"),
 ("Crypto holding", [
  S("""
C: In January I said I wanted to hold crypto. I've changed my mind. I want to sell it. It's too wild, and I don't need more of that.
A: That's a change. Is it a lasting one?
C: Yes. After the customer loss, I want things I can understand.
A: Then I'll record the instruction. Do you want to sell all of it?
C: All of it. Put the money into the reserve.
""", log=[("decision", "to sell the entire crypto holding and put the money into the cash reserve, because after the customer loss you want simpler, steadier assets"),
         ("a_action", "sell the crypto holding and move the proceeds to the cash reserve")],
   set={"cash": 62_000}, tags=("changing_preference", "confirmed_decision", "explicit_instruction", "rationale")),
 ], "video"),
 ("Insurance for a self-employed founder", [
  G("explain", concept="term_life"),
  G("doc", doc="your current health and disability insurance policy summaries", why="so I can check what protects the household if you can't work", id="l_ins", due="before our next meeting"),
 ], "telephone"),
 ("Checking the reserve", [
  G("liquidity"),
  G("portfolio"),
  Check("l_ins", "pending", "I couldn't find the disability policy. I think I only have health."),
 ], "video"),
 ("Taxes for the second quarter", [
  G("cashflow"),
  G("market"),
 ], "video"),
 ("Vantora steps and market", [
  G("portfolio"),
  G("market"),
 ], "telephone"),
 ("Amélie's future and college saving", [
  G("explain", concept="education_funding"),
  S("""
C: Should I be putting money away for Amélie's education?
A: Eventually yes, but I'd start after the reserve is full. Your income isn't steady enough to commit to monthly contributions yet.
C: Fine. Let's revisit in the fall.
""", log=[("deferred", "a college savings plan for Amélie until the reserve is full and income is steadier")], tags=("deferred_decision",)),
 ], "video"),
 ("Disability cover follow-up", [
  Check("l_ins", "pending", "I contacted the broker, but he says it will take three weeks to get quotes."),
  G("liquidity"),
  G("market"),
 ], "telephone"),
 ("Reserve progress and the last Vantora step", [
  S("""
A: The final Vantora sale went through, and the proceeds went into the index fund. Between the tender money and the crypto sale, the reserve now covers a little over nine months, which meets the target you set in March.
C: That's a relief. Revenue is partly back too, since a new client signed.
A: Good. I wouldn't loosen the target because of one contract, though. I'd keep nine months as the minimum and put anything above it to work in the index fund. Do you agree?
C: I agree. Nine months is the floor, and anything above it goes into the index fund.
""", log=[("decision", "to keep nine months of expenses as the minimum cash reserve despite the new client, and to invest anything above it in the index fund")], set={"cash": 62_000}, tags=("confirmed_decision",)),
  G("liquidity"),
 ], "video"),
 ("Half-year review", [
  S("""
A: Lucas, let me recap your six months. In January you wanted to concentrate on Vantora and you wanted crypto. After the customer loss in March you decided to build a nine-month reserve, declined the restaurant, sold half the secondary shares, trimmed Vantora by forty percent and sold the crypto.
C: When you say it that way, it's a big change in attitude.
A: It is. Open items: the disability insurance quotes, and the college savings, which we deferred.
C: And Lisbon, which isn't a plan.
A: Correct, I have it as a possibility only.
""", tags=("summary", "history_recall", "changing_preference")),
  G("portfolio"),
 ], "video"),
]


PROFILE["casual_windows"] = [(1, 25), (1, 12), (1, 25), (3, 14), (1, 25), ("2026-04-20", "2026-06-30"), (1, 25), (6, 25)]
