"""CLIENT-003 Sofia Alvarez: young professional; aggressive growth (Jan) -> discomfort with volatility (Mar)
-> more conservative approach for the house fund (May).  New baby Lucia in March."""
from ..beats import S, G, Check

PROFILE = dict(
    client_id="CLIENT-003", advisor_id="ADVISOR-003",
    name="Sofia Alvarez", adv_name="Elena Rossi",
    channels=[("video", 0.6), ("telephone", 0.3), ("in-person", 0.1)],
    hours=[8, 12, 17, 18], beta=1.1,
    months=[1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6],
    facts=dict(first="Sofia", adv_first="Elena", spend=7800,
               income_desc="your salary, which will dip while you're on parental leave",
               buffer_target=9,
               liq_reason="the baby, a possible home purchase and the drop in income during parental leave"),
    state=dict(value=410_000, equity=90, anxiety="low", cash=38_000),
    holdings=[
        ("the total-market index fund in your 401(k)", "it's the core of your retirement savings, and the employer match goes into it"),
        ("your vested company shares", "they are a meaningful slice, and your paycheck depends on the same company, so I'd keep trimming them over time"),
        ("the small-cap growth fund", "it's volatile and may lag for long stretches, but it fits a long horizon if you can stomach the swings"),
        ("the high-yield savings account", "it's where the house fund and emergency cash sit, because that money must not fall in value"),
    ],
    pressures=[
        "we've been buying baby equipment and it adds up fast",
        "childcare quotes are scarier than I'd imagined",
        "Mateo's classroom supplies come out of our pocket more than they should",
        "our rent renewal came with an increase",
    ],
    casual=[
        """
C: Mateo and I painted the spare room this weekend. It will be the nursery.
A: That's lovely. How are you feeling about the baby coming?
C: Excited and terrified, in about equal measure.
""",
        """
C: I've been so stressed about work that I barely sleep.
A: I'm sorry. Is it a temporary crunch or something deeper?
C: Temporary, I hope. We have a big product launch coming up.
""",
        """
C: My mother is flying in to help for the first month after the baby.
A: That will be a big help. Is she staying with you?
C: Yes, on the sofa bed. We'll see how that goes.
""",
        """
C: Mateo got an email about a teaching position in another district. He's not sure he'll apply.
A: Understood. That might change your commute, or where you want to live. I'll note it as a possibility only.
""",
        """
C: We might relocate in a couple of years, maybe closer to my parents, so the grandparents can help with childcare.
A: That's useful to know for the home purchase timeline. We'll treat it as a possibility for now.
""",
        """
C: Lucia smiled at me for the first time this morning. I cried at my desk.
A: What a wonderful moment. Congratulations again.
""",
        """
C: I started running again in the evenings, with the stroller.
A: Good for you. That sounds like a nice routine.
""",
        """
C: My brother asked if I could lend him money for a car. I said I'd think about it.
A: That's your call to make. If you do lend, I'd treat it as a gift in your budget and not as an asset.
""",
    ],
)

STEPS = [
 ("Goals, income and investment approach", [
  S("""
A: Sofia, let's start with the big picture. What do you want your money to do over the next ten or twenty years?
C: I want to retire comfortably, buy a home, and eventually have kids. I'm thirty-four, and Mateo and I are expecting a baby in March. Our income is good: I earn about two hundred ten thousand with stock grants, and Mateo teaches.
A: Congratulations. How would you describe your approach to investing?
C: Aggressive. I have thirty years until retirement. I want the maximum growth and I don't mind volatility. I'd even put a small slice in crypto if you think it makes sense.
A: I'll note growth as your priority and that you're comfortable with volatility. On crypto, I'd want to understand why before suggesting any. For now, we'll keep it as a conversation rather than a decision.
C: Fair enough.
""", log=[("plan", "an aggressive, growth-focused portfolio at about 90 percent equities")], tags=("risk_preference_baseline", "stated_intention")),
  G("portfolio"),
 ], "video"),
 ("Employer benefits and share grants", [
  G("doc", doc="your restricted stock grant agreements and the latest 401(k) statement", why="so I can map your vesting dates and check you're capturing the full employer match", id="s_grants", due="before our next meeting"),
  G("explain", concept="concentrated_stock"),
  G("cashflow"),
 ], "video"),
 ("Buying a home and where the down payment should sit", [
  S("""
C: We're renting, and we'd like to buy a house. I might buy in a couple of years, but I'm not sure. It depends on the baby, Mateo's job and the market.
A: Do you have a target price?
C: Around six hundred thousand. We'd want a hundred and twenty thousand down.
A: Then the down-payment money has a time horizon of about two years. That's too short to hold in aggressive investments. I'd keep it in a separate bucket from your retirement savings.
C: Isn't that giving up growth?
A: A bit. But if the market fell thirty percent a month before closing, you wouldn't be able to wait for it to recover. For money you need on a date, predictability matters more than growth.
C: I see. Let's talk about how much we should save for it.
""", log=[("tentative", "buying a home in about two years"), ("plan", "keeping any down-payment savings separate from retirement investments")],
   tags=("uncertain_intention", "stated_intention")),
 ], "telephone"),
 ("Student loans: prepay or invest", [
  S("""
C: I still owe about forty-eight thousand in student loans at five point two percent. Should I pay them off faster or invest?
A: Five point two is a guaranteed return for every dollar prepaid. Investments might earn more, but there's no guarantee. Your employer match is separate and should come first.
C: I'm capturing the full match. Could you compare extra payments of three hundred a month with investing that money?
A: Yes. I'll run both cases, including the risk that the market return is lower.
""", log=[("a_action", "compare extra student loan payments of three hundred dollars a month against investing that money")],
   open=[("s_loan", "A", "compare extra student loan payments of three hundred dollars a month against investing that money")], tags=("advisor_commitment",)),
  G("liquidity"),
 ], "video"),
 ("Loan comparison and parental leave planning", [
  Check("s_grants", "done", "I uploaded the grant agreements and the 401(k) statement last night."),
  S("""
A: The comparison: investing the three hundred a month has a higher expected result, but the prepayment is certain at five point two percent. The gap isn't large. Many people split it.
C: Given the baby, I'd rather keep the cash flexible. I've decided: minimum payments on the loan, and invest the extra. I'm capturing the employer match anyway.
A: That's reasonable. I'd also be honest that the loan is guaranteed money you aren't saving. If your income falls during leave, we may revisit.
C: Fine. And speaking of leave, I get twelve weeks at sixty percent pay.
""", close=["s_loan"], log=[("decision", "to keep minimum payments on the student loan and invest the extra money, because flexibility matters with a baby coming")],
   tags=("confirmed_decision", "rationale", "scenario_result")),
 ], "video"),
 ("Cash buffer before parental leave", [
  G("liquidity"),
  S("""
A: During leave, income drops by forty percent for twelve weeks. That's roughly twelve thousand less over the period.
C: So I should hold more cash.
A: I'd suggest building cash to about fifty thousand before the baby comes. Is that realistic?
C: I think so. I can redirect my brokerage contributions for a few weeks.
A: Let's make that a plan. It's your instruction to redirect, not my suggestion alone, so please say if you agree.
C: I agree. Redirect new contributions to cash until we reach fifty.
""", log=[("decision", "to redirect new brokerage contributions to cash until reserves reach fifty thousand dollars, ahead of parental leave"),
         ("a_action", "redirect your brokerage contributions to cash")], set={"cash": 50_000}, tags=("confirmed_decision", "explicit_instruction")),
 ], "telephone"),
 ("Life and disability insurance", [
  G("explain", concept="term_life"),
  S("""
C: How would I even start? I've never owned any life insurance.
A: I'd refer you to a licensed broker for quotes. In the meantime, I'll estimate the coverage you need: debts, childcare, the loss of either income, and your housing goal.
""", log=[("a_action", "estimate the life and disability coverage you and Mateo need and refer you to a licensed broker")],
   open=[("s_ins", "A", "estimate the life and disability coverage you and Mateo need and refer you to a licensed broker")], tags=("advisor_commitment", "procedure")),
  G("market"),
 ], "video"),
 ("Markets and buying the dip", [
  S("""
C: The sell-off is scary, but I'm thinking it's a gift. Shouldn't I put more of my cash into the market while it's low?
A: I'd resist that. The cash is for the baby and leave. If you move it into equities and the market falls further, you'd have no buffer.
C: But I'm young. I can wait out a downturn.
A: Your retirement money can. That's why it stays at ninety percent equities. The cash serves a different job.
C: Okay. I'll leave the cash alone.
""", log=[("decision", "to leave the cash reserve alone and keep investing for retirement as planned")], tags=("confirmed_decision",)),
  Check("s_ins", "pending", "The estimate is taking longer because I'm still waiting on the disability benefit details from your employer's plan."),
 ], "telephone"),
 ("Pre-birth check-in", [
  G("portfolio"),
  G("cashflow"),
  G("market"),
 ], "video"),
 ("Volatility and a new baby", [
  S("""
C: Lucia was born on <<ago:1>>. Mother and baby are doing well, and yes, I'm still in the hospital. I didn't want to cancel, because I'll be useless for weeks once we're home. And honestly, looking at my account has become painful. I'm down quite a bit from January.
A: Congratulations, and thank you for making time. How are you feeling about the investments?
C: Different, if I'm honest. In January I said I didn't mind volatility. Now I have a baby and watching a number fall feels personal.
A: That's an honest observation. Is it that you feel the loss more, or that you can't afford it anymore?
C: A bit of both. I'm not saying I want to change anything today. I'm too tired to decide.
A: Then we won't. I'll note that your comfort with volatility has changed, and we'll revisit when you have more energy.
""", log=[("tentative", "whether a 90 percent equity portfolio still suits you")], set={"anxiety": "mid"},
   tags=("changing_preference", "life_event", "uncertain_intention")),
 ], "telephone"),
 ("Guardianship and your will", [
  G("doc", doc="the name of the attorney you'd like to use for a will and guardianship documents", why="so I can coordinate the legal work with your financial plan", id="s_will", due="in the next month"),
  G("suit", topic="the plan for your daughter's financial security"),
  G("cashflow"),
 ], "video"),
 ("Insurance estimate and broker referral", [
  Check("s_ins", "done", "Based on your income and Mateo's, I'd suggest roughly one and a half million in twenty-year term cover for you and about a million for Mateo, and I've sent the broker referral."),
  G("portfolio"),
  G("market"),
 ], "video"),
 ("The home purchase timeline", [
  S("""
A: Last time we talked about a house, I understood you'd decided to buy within two years. Is that right?
C: I should clarify that. I was only saying I might. After the baby, I don't know. I haven't decided anything about the timing.
A: Thank you. I'll fix my note so it records that you're considering a purchase within two years, with no commitment.
C: Actually, now that Lucia's here, I think we should wait until after my leave and see how childcare costs work out. I've decided: no house search until the autumn.
A: Understood. That's a decision to hold off the search, with the goal still open. I'll record your reason, childcare costs and income uncertainty.
""", log=[("correction", "my earlier note that you had decided to buy a house within two years was too strong; you were only considering it"),
         ("decision", "to postpone any house search until the autumn, because childcare costs and income are uncertain")],
   tags=("correction", "misunderstanding", "confirmed_decision", "rationale")),
 ], "video"),
 ("Mateo's job possibility and relocation", [
  S("""
C: Mateo got an offer from a district about an hour away. It pays more, but we'd probably relocate.
A: Has he accepted?
C: No. He wants to know what it would do to our finances. If we move, we might buy sooner or later, I don't know.
A: I can compare staying and moving, but I'd need his actual salary, the benefits, and the likely housing costs.
C: He'll get you the details.
""", log=[("a_action", "compare staying where you are with Mateo's possible move"), ("c_action", "send Mateo's offer details")],
   open=[("s_offer", "C", "send Mateo's offer details")], tags=("uncertain_intention", "pending_action")),
  G("liquidity"),
 ], "telephone"),
 ("Term life decision", [
  S("""
C: I talked to the broker. I'd like to go ahead with a one point five million, twenty-year term policy for me, and one million for Mateo.
A: Before you apply, remember that I don't sell insurance. The broker will handle the application, and the underwriter might change the price. Please don't cancel anything until the new coverage is in force.
C: Understood. I'll start the application this week.
""", log=[("decision", "to apply for a one and a half million dollar twenty-year term policy for you and a one million dollar policy for Mateo"),
         ("c_action", "start the term life application with the broker")],
   open=[("s_app", "C", "start the term life application with the broker")], tags=("confirmed_decision", "procedure")),
  G("portfolio"),
 ], "video"),
 ("Asking for a more conservative approach", [
  S("""
C: I need to talk about risk again. I've decided I don't want to be this aggressive anymore. The swings in March were hard, and with Lucia I want to feel more secure.
A: Thank you. Let's be specific. In January you were at ninety percent equities and wanted maximum growth. What would you like to change?
C: I want the house fund safe, in cash and short bonds. And for retirement, maybe seventy-five percent in equities rather than ninety.
A: That's a meaningful change, and it deserves a suitability review. Do you consider it temporary, or lasting?
C: Lasting, I think. Having a child changed how I see risk.
A: I'll record the change and the reason. Please proceed with the investment policy update once I send it.
C: Yes, please proceed.
""", log=[("decision", "to move the house fund into cash and short-term bonds and cut retirement equities from about 90 percent to 75 percent, because having Lucia changed your comfort with risk"),
         ("a_action", "send the updated investment policy statement")],
   open=[("s_ips", "A", "send the updated investment policy statement")], set={"equity": 75, "anxiety": "mid"},
   tags=("changing_preference", "confirmed_decision", "rationale", "procedure")),
 ], "video"),
 ("Policy paperwork and open items", [
  Check("s_ips", "done", "The updated statement is in your portal for signature."),
  Check("s_offer", "pending", "Mateo hasn't sent them because he's still deciding whether he wants to interview."),
  Check("s_app", "done", "The broker started the application and the medical exam is next week."),
  G("cashflow"),
 ], "telephone"),
 ("Selling vested shares for the house fund", [
  S("""
C: A block of my shares vested. I'd like to sell enough to put about forty thousand into the house fund.
A: Selling at vest has no extra tax compared with holding, and it reduces concentration. I support it, but I'd put a note in the file that it's your instruction.
C: It is. Sell enough to net forty thousand into the house fund, which is the cash and short bond bucket.
""", log=[("decision", "to sell enough vested shares to move forty thousand dollars into the house fund"), ("a_action", "sell the vested shares and move forty thousand dollars into the house fund")],
   tags=("confirmed_decision", "explicit_instruction")),
  G("market"),
 ], "video"),
 ("Saving for Lucia's education", [
  G("explain", concept="education_funding"),
  S("""
C: Should I open a college savings account for Lucia?
A: A 529 plan can make sense, with tax-free growth for education. But it's the sixth priority on your list after the cash reserve and retirement. I'd say think about it, not decide yet.
C: Okay. Let's put it on hold until after we've seen childcare costs.
""", log=[("deferred", "opening a 529 college savings plan for Lucia until childcare costs are clear")], tags=("deferred_decision",)),
 ], "telephone"),
 ("Wills and guardianship follow-up", [
  Check("s_will", "pending", "I haven't called any attorneys. The baby takes all my time."),
  G("portfolio"),
  G("liquidity"),
 ], "video"),
 ("Back to work and childcare costs", [
  G("cashflow"),
  S("""
C: Childcare is going to be around two thousand eight hundred a month. That's much more than we thought.
A: That changes the budget. It also changes the house timeline. I'd rebuild the cash-flow plan with that figure.
C: Please do. I may need to cut some of my investment contributions.
A: I'd advise against reducing the employer match. The rest we can look at.
""", log=[("a_action", "rebuild your cash-flow plan with two thousand eight hundred dollars a month of childcare")], tags=("advisor_commitment",)),
 ], "video"),
 ("Reworked budget", [
  G("portfolio"),
  G("market"),
  Check("s_offer", "pending", "Mateo decided not to interview, so there are no details to send."),
 ], "telephone"),
 ("Signing the updated policy", [
  G("liquidity"),
  G("cashflow"),
  G("market"),
 ], "video"),
 ("Mid-year checkpoint", [
  G("portfolio"),
  G("suit", topic="the changes in your investment approach this year"),
  G("cashflow"),
 ], "video"),
 ("Half-year review", [
  S("""
A: Sofia, let me read back your year. In January you described yourself as aggressive, at ninety percent equities. By March, with Lucia's birth and the sell-off, you told me the swings were harder to bear. In May you decided on seventy-five percent for retirement, and a safe house fund.
C: Yes. It's a different me.
A: On the house, you were considering a purchase in about two years, I mistakenly recorded it as decided, you corrected it, and then you chose to wait until the autumn.
C: Right. That's still where we are.
A: Open items: the will and guardianship paperwork, and the 529 decision you deferred.
C: The will is embarrassing. I'll book an attorney this month.
""", tags=("summary", "history_recall", "changing_preference")),
  G("portfolio"),
 ], "video"),
]


PROFILE["casual_windows"] = [(1, 8), (1, 25), (1, 8), (3, 13), (4, 25), (13, 25), (14, 25), (2, 25)]
