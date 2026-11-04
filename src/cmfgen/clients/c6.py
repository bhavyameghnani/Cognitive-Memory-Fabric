"""CLIENT-006 Margaret 'Peg' Sullivan: retired, healthcare costs for husband Frank, capital preservation.
Equity target 35% -> 25% (after Frank's hospitalisation) -> 30% (inflation worry)."""
from ..beats import S, G, Check

PROFILE = dict(
    client_id="CLIENT-006", advisor_id="ADVISOR-003",
    name="Margaret Sullivan", adv_name="Elena Rossi",
    channels=[("telephone", 0.6), ("video", 0.25), ("in-person", 0.15)],
    hours=[10, 11, 14, 15], beta=0.6,
    months=[1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6],
    facts=dict(first="Peg", adv_first="Elena", spend=8900,
               income_desc="your pension and Social Security, with the portfolio covering the difference",
               buffer_target=24,
               liq_reason="Frank's care costs and the chance of a sudden medical bill"),
    state=dict(value=1_450_000, equity=35, anxiety="mid", cash=210_000),
    holdings=[
        ("the short-term bond ladder", "it provides the next few years of withdrawals so you don't have to sell anything in a down market"),
        ("the dividend-focused equity fund", "it provides some growth and income, but it will still fall in a bad year"),
        ("the municipal bond fund", "it provides tax-friendly income, though its price can move when interest rates change"),
        ("the Treasury inflation-protected securities", "they adjust with inflation, which matters over a long retirement"),
    ],
    pressures=[
        "Frank's home aide asked for more hours and the invoice grew",
        "the supplemental insurance premium rose again",
        "we replaced the stair lift, which wasn't in the plan",
        "his new medication isn't fully covered",
    ],
    casual=[
        """
C: Colleen comes by every Tuesday now. She does the shopping and sits with Frank so I can go to church.
A: That sounds like a great support. How are you holding up?
C: Tired but fine.
""",
        """
C: Brian called from Seattle. He's thinking of buying a franchise and wants advice. I told him I'm not the right person.
A: That's wise. If he wants numbers, I'd be happy to look at them with him.
""",
        """
C: I've been doing the crossword with Frank in the evenings. His hands shake, but he still remembers every clue.
A: That's a lovely thing to share.
""",
        """
C: I might look at a retirement community in a couple of years, one with nursing care if we need it.
A: Understood. I'll note that as a possibility only. Let me know when you start visiting.
""",
        """
C: We've had to cancel our trip to Ireland again. Frank doesn't travel well.
A: I'm sorry. That was important to you both.
C: Maybe one day. Not this year.
""",
        """
C: The church asked if I'd chair the fundraising committee. I said I'd think about it.
A: That's a compliment. Just check how much time you can really give.
""",
        """
C: I'm learning to use a tablet to video-call my grandchildren. It only took three tries.
A: Well done.
""",
        """
C: Frank had a good week, and we sat on the porch every afternoon.
A: That's wonderful to hear.
""",
    ],
)

STEPS = [
 ("Income, care costs and capital preservation", [
  S("""
A: Peg, thank you for making time. Let's begin with where things are. How is Frank?
C: He's stable, but the Parkinson's is progressing. We have an aide four days a week, about five thousand two hundred a month. I'm managing, but it's a lot.
A: I'm sorry. Let's look at the money side. Your pension and Social Security together bring in about six thousand three hundred. Spending is around eight thousand nine hundred, including Frank's care, so the portfolio covers the gap.
C: That's what I understand. I don't want to run out of money. Preserving what we have is the main thing. I don't need big returns.
A: Right now about thirty-five percent of the portfolio is in equities. Is that still comfortable?
C: Yes, I think so. We've done fine with it.
""", log=[("plan", "preserving capital while covering the gap between about six thousand three hundred in pension and Social Security and spending of about eight thousand nine hundred a month"),
         ("plan", "keeping equities at about 35 percent of the portfolio")], tags=("risk_preference_baseline", "stated_intention", "family_context")),
 ], "telephone"),
 ("Withdrawals and the safety layer", [
  G("explain", concept="sequence_risk"),
  G("portfolio"),
 ], "telephone"),
 ("Required distributions and charity", [
  G("explain", concept="rmd_qcd"),
  S("""
C: I give about six thousand to the church every year. Could I do that directly from the IRA?
A: Potentially, yes, through a qualified charitable distribution. I'd want your accountant to confirm the details.
C: Our accountant is Dennis Hall. Can you call him?
A: I'll contact him with your permission.
C: You have my permission.
""", log=[("a_action", "contact your accountant Dennis Hall about giving to the church through a qualified charitable distribution")],
   open=[("p_qcd", "A", "contact your accountant Dennis Hall about giving to the church through a qualified charitable distribution")], tags=("advisor_commitment", "tax")),
 ], "telephone"),
 ("Medicare costs and insurance review", [
  G("doc", doc="the latest statements for Frank's Medicare supplement policy and the home care invoices", why="so I can model the true cost of his care over the next several years", id="p_care", due="in the next two weeks"),
  G("cashflow"),
 ], "video"),
 ("Frank's care budget", [
  Check("p_care", "done", "I put the statements and last quarter's invoices in the portal."),
  Check("p_qcd", "done", "I spoke with Dennis Hall. He said a direct gift from the IRA to the church can work, and he'll confirm the paperwork after he sees your required distribution amount."),
  S("""
A: From the statements, care costs have grown about eight percent a year. If that continues, in five years the aide alone could cost nearly eight thousand a month.
C: That frightens me.
A: It should be planned for, not feared. I'll model several levels of care and show you how long the money would last in each.
C: Please.
""", log=[("a_action", "model Frank's care costs at several levels and show how long the money lasts")],
   open=[("p_model", "A", "model Frank's care costs at several levels and show how long the money lasts")], tags=("advisor_commitment",)),
 ], "telephone"),
 ("Cash reserve and bills", [
  G("liquidity"),
  G("market"),
 ], "telephone"),
 ("Brian's request", [
  S("""
C: Brian asked me for a loan. Eighty thousand for the franchise.
A: How do you feel about it?
C: Torn. He's my son. But I might lend him something. I haven't decided.
A: I understand. The question is how much you could lose without harming Frank's care. Would repayment be dependable?
C: I don't know. He says it will be.
A: Let's treat it as undecided. I'd like to see what an eighty-thousand loan, and a smaller one, would do to the care model.
""", log=[("tentative", "lending Brian eighty thousand dollars for a franchise")], tags=("uncertain_intention", "family_context")),
  G("portfolio"),
 ], "video"),
 ("Care model results", [
  S("""
A: Here's the care model. With the aide four days a week and cost growth near eight percent, your money lasts well beyond your life expectancy. If Frank needs full-time nursing, the money lasts roughly fourteen years, and that's before an eighty-thousand loan.
C: Fourteen years sounds short.
A: It's a stress case, not a forecast. But yes, a loan would shorten it by about a year in that case.
C: A year isn't nothing. I don't want to risk it.
""", close=["p_model"], tags=("scenario_result", "family_context")),
  G("market"),
 ], "telephone"),
 ("Frank's hospitalisation", [
  S("""
C: Frank was in the hospital for five days after a fall. He's home now, but it frightened me terribly. And watching the markets drop at the same time...
A: I'm so sorry, Peg. How is he?
C: Weaker. The doctors say he'll need more help. I can't take a loss on top of this. I want to reduce the stock part. Maybe down to twenty-five percent.
A: That's a significant change from thirty-five. Let me ask whether this is a response to the past two weeks, or something you've concluded about your needs.
C: Both. I'd rather earn less and sleep.
A: I'll write down your request. Before I act, I'd like to update your suitability profile, because a reduction like this changes your income and growth expectations.
""", log=[("tentative", "reducing equities from 35 percent to about 25 percent after Frank's hospitalisation"), ("a_action", "update your suitability profile before changing the allocation")],
   set={"anxiety": "high"}, tags=("changing_preference", "life_event", "uncertain_intention")),
 ], "telephone"),
 ("Decision to cut equities", [
  S("""
A: I've updated your profile. Going to twenty-five percent equities reduces expected growth, and the portfolio would be more exposed to inflation. Do you still want to proceed?
C: Yes. I'm decided. Twenty-five percent. Please do it.
A: I'll sell from the dividend fund and move the proceeds into the short-term ladder, so the near-term cash is covered for about five years.
C: That's what I want.
""", log=[("decision", "to cut the equity allocation from 35 percent to 25 percent after Frank's hospitalisation, with proceeds going to the short-term bond ladder"),
         ("a_action", "sell part of the dividend fund and add the proceeds to the short-term bond ladder")],
   set={"equity": 25}, tags=("changing_preference", "confirmed_decision", "rationale", "explicit_instruction")),
 ], "telephone"),
 ("Brian's loan decision", [
  S("""
C: I've decided about Brian. I'm not lending him eighty thousand. It would cut into what I need for Frank, and I can't take that chance.
A: Did you tell him?
C: Not yet. I'll call him this weekend. I might offer to help in some small way, like paying a few hundred toward his equipment. But no loan.
A: I'll record the decision and the reason. The small help is only a thought, right?
C: Right. It's only a thought.
""", log=[("decision", "not to lend Brian eighty thousand dollars, because you need the money for Frank's care"), ("tentative", "offering Brian a few hundred dollars toward equipment")],
   tags=("confirmed_decision", "rationale", "rejected_option", "family_context")),
  G("portfolio"),
 ], "video"),
 ("Retirement community brochure", [
  G("explain", concept="ccrc"),
  S("""
C: Colleen found a place called Maplewood Commons. The entrance fee is three hundred eighty thousand.
A: Have you visited?
C: Not yet. I'm only looking.
A: Then I'll treat it as an interest and nothing more. If you visit, bring the contract and the financial statements so we can study them.
""", log=[("tentative", "moving to Maplewood Commons, with a three hundred eighty thousand dollar entrance fee")], tags=("uncertain_intention",)),
 ], "telephone"),
 ("Questions about inflation", [
  G("market"),
  S("""
C: I read that prices keep climbing. I wonder if I went too far in cutting stocks.
A: That's a fair question. Lower equity means less growth, and inflation can erode a conservative portfolio over fifteen or twenty years.
C: What are my options?
A: We can keep the current mix, add inflation-protected bonds, or raise stocks modestly. I'd want to model each before suggesting anything.
""", log=[("a_action", "model keeping equities at 25 percent versus adding inflation-protected bonds or raising equities modestly")],
   open=[("p_infl", "A", "model keeping equities at 25 percent versus adding inflation-protected bonds or raising equities modestly")], tags=("changing_preference", "advisor_commitment")),
 ], "telephone"),
 ("Required distribution and the gift to church", [
  G("cashflow"),
  G("portfolio"),
 ], "video"),
 ("Inflation results and a second thought", [
  S("""
A: The model shows that at twenty-five percent in stocks you're fine on current spending but have less cushion if care costs keep rising faster than your income. Thirty percent, with more inflation-protected bonds, improves the long-range numbers without a big increase in risk.
C: I did go too far, I think. Let me say it clearly: thirty percent, not twenty-five. And more of the inflation-protected bonds.
A: Thank you. That reverses part of the change you made in March. I'll record that you've chosen thirty percent, and why.
C: Yes. The inflation worry is real.
""", close=["p_infl"], log=[("decision", "to raise equities from 25 percent to 30 percent and add inflation-protected bonds, because inflation could erode a very conservative portfolio over a long retirement"),
         ("a_action", "make the shift to 30 percent equities and add inflation-protected bonds")],
   set={"equity": 30, "anxiety": "mid"}, tags=("changing_preference", "contradiction", "confirmed_decision", "rationale")),
 ], "telephone"),
 ("Maplewood Commons visit", [
  S("""
C: Colleen and I visited Maplewood. It was nicer than I expected. But three hundred eighty thousand is a lot, and I'd be leaving the house.
A: Did you bring the contract?
C: They gave me a copy. I haven't read it.
A: Please send it to me. I'd also like their latest financial statements before saying anything.
""", log=[("c_action", "send the Maplewood Commons contract"), ("a_action", "request the community's financial statements")],
   open=[("p_contract", "C", "send the Maplewood Commons contract")], tags=("pending_action", "document_request")),
 ], "video"),
 ("Market check", [G("market"), G("portfolio"), G("liquidity")], "telephone"),
 ("Estate documents", [
  G("doc", doc="your power of attorney and health care proxy documents", why="so I can confirm that Colleen can act for you and for Frank if needed", id="p_poa", due="in the next month"),
  Check("p_contract", "pending", "Colleen is scanning it. The copy is thick, with many pages."),
 ], "telephone"),
 ("Reviewing the Maplewood contract", [
  Check("p_contract", "done", "Colleen sent the whole contract yesterday."),
  S("""
A: I've read the contract. Eighty percent of the entrance fee is refundable to the estate, which is better than many. But the monthly fee can rise, and I'd want an elder-law attorney to review it.
C: I'm not ready to decide. I want to talk to Frank first, when he has a good day.
A: Of course. Let's put the decision on hold until you've talked.
""", log=[("deferred", "any decision on Maplewood Commons until you've talked to Frank and an elder-law attorney has reviewed the contract")], tags=("deferred_decision",)),
 ], "telephone"),
 ("Care costs update", [
  G("cashflow"),
  G("liquidity"),
 ], "video"),
 ("Power of attorney follow-up", [
  Check("p_poa", "pending", "I can only find Frank's. Mine is somewhere in the attic."),
  G("portfolio"),
  G("market"),
 ], "telephone"),
 ("Elder-law attorney and the Maplewood decision", [
  S("""
C: Frank had a good day and I told him about Maplewood. He said he'd rather stay home as long as we can manage.
A: Thank you for asking him. Does that change your thinking?
C: It does. I'm not going to put down the deposit. If things get worse, we'll revisit. Right now, I've decided against it.
A: I'll record that you've decided against Maplewood for now, because Frank wants to stay home, and that you may revisit if his needs change.
""", log=[("decision", "not to pursue Maplewood Commons for now, because Frank wants to stay at home, with the option to revisit if his needs change")],
   tags=("confirmed_decision", "rationale", "rejected_option")),
  G("market"),
 ], "telephone"),
 ("Annual distribution and the church gift", [
  G("explain", concept="rmd_qcd"),
  G("cashflow"),
  G("liquidity"),
 ], "video"),
 ("Bond ladder maintenance", [
  G("explain", concept="bond_ladder"),
  G("portfolio"),
 ], "telephone"),
 ("Half-year review", [
  S("""
A: Peg, let me recap. In January you wanted to preserve capital with about thirty-five percent in equities. After Frank's fall you asked to reduce to twenty-five. A month later, worried about inflation, you chose thirty. You decided not to lend Brian the eighty thousand, and after talking with Frank you decided against Maplewood Commons for now.
C: That's accurate. I changed my mind more than I'd like.
A: That's not a failing. You reacted to what happened and corrected when the facts changed. The open item is your power of attorney document.
C: I'll find that document this week.
""", tags=("summary", "history_recall", "changing_preference")),
  G("portfolio"),
 ], "telephone"),
]


PROFILE["casual_windows"] = [(1, 25), (1, 6), (1, 25), (1, 11), (1, 25), (1, 25), (1, 25), (1, 25)]
