"""CLIENT-004 Grace Adeyemi: recently widowed (Samuel died Nov 2025); inheritance, dependants, security.
Gradual shift from near-all-cash to a cautious growth allocation; rejects an annuity; keeps the mortgage."""
from ..beats import S, G, Check

PROFILE = dict(
    client_id="CLIENT-004", advisor_id="ADVISOR-001",
    name="Grace Adeyemi", adv_name="Priya Nair",
    channels=[("in-person", 0.4), ("telephone", 0.4), ("video", 0.2)],
    hours=[9, 13, 14, 16], beta=0.8,
    months=[1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6, 6],
    facts=dict(first="Grace", adv_first="Priya", spend=6200,
               income_desc="your salary as a hospital administrator, which is now your only earned income",
               buffer_target=12,
               liq_reason="Folake's tuition, the mortgage and the recent change in your household"),
    state=dict(value=1_450_000, equity=15, anxiety="high", cash=330_000),
    holdings=[
        ("the money-market and short-term Treasury holdings", "they hold the life insurance payout in a safe and accessible form while you decide what to do next"),
        ("the account you inherited from Samuel", "it's invested conservatively for now, and we haven't changed anything while you decide how to title it", (1, 10)),
        ("the retirement account you rolled into your own name", "it's still invested conservatively, and it's the long-term money that the gradual move into stocks draws on", (11, 25)),
        ("your own 401(k) at the hospital", "you keep contributing there, and the match makes it worthwhile whatever else changes"),
        ("the intermediate bond fund", "it pays more than cash and moves less than stocks, which suits where you are right now"),
    ],
    pressures=[
        "Folake's housing deposit for next year came earlier than I expected",
        "there are still bills coming in under Samuel's name",
        "the car needed new tires and repairs",
        "I've been paying for the funeral costs in pieces",
    ],
    casual=[
        """
C: I've started going to a grief support group on Thursday evenings. It's helped more than I expected.
A: I'm glad you found it. Please don't hesitate to tell me if you need to move a meeting.
""",
        """
C: Tobi has moved back in with me for now. It's nice to have someone in the house.
A: That sounds like a comfort. Is he working or still studying?
C: Finishing his degree. He graduates in May.
""",
        """
C: Folake has called me three times in the last few days, mostly to ask if I'd eaten. The roles are reversed.
A: That sounds like she's looking after you. How are you finding it?
C: Loving it, and a little embarrassed.
""",
        """
C: Samuel's birthday was on April 11. I made his jollof rice and we told stories.
A: That sounds like a lovely way to remember him.
""",
        """
C: I've been thinking I might sell the house and downsize in a few years. Not now, just thinking.
A: Understood. I'll note it as a thought and not a plan. It would affect the mortgage question.
""",
        """
C: My sister keeps telling me to travel more. Maybe a trip to Lagos next year.
A: That could be wonderful. Let me know if you decide, and I'll include it in your spending.
""",
        """
C: I've been sleeping through the night more often than not.
A: That's good to hear.
""",
        """
C: Work has asked if I'd stay on past 65 as a consultant. I told them I'd think about it.
A: That's a flattering offer. We can look at it when we revisit your retirement timing.
""",
    ],
)

STEPS = [
 ("Where things stand after Samuel's death", [
  S("""
A: Grace, thank you for coming. Before anything else, how are you doing?
C: Some days are better than others. Samuel died in November. We'd been married thirty-one years. I'm still finding paperwork he handled that I never saw.
A: I'm very sorry. There's no rush to decide anything big today. My role for now is to organise what has to be done and protect you from hurried choices.
C: Please. That's what I need. I'm not sure what we have. There was a life insurance payout, his retirement account, and my own savings.
A: We'll build a clear list together. As a general principle, I'd advise against large irreversible decisions in the first six to twelve months, unless something forces them. Are you comfortable with that approach?
C: Yes. I feel like I can't think straight yet.
""", log=[("plan", "taking no large irreversible financial decisions for the first six to twelve months unless something forces them")],
   tags=("life_event", "advisor_procedure")),
 ], "in-person"),
 ("Estate paperwork and survivor benefits", [
  G("doc", doc="five certified copies of Samuel's death certificate and the letter from his employer about the pension survivor election", why="so I can retitle accounts and claim the survivor options correctly", id="g_estate", due="in the next three weeks"),
  S("""
C: His company sent a form about a survivor election. I don't understand it.
A: That's one of the items with a deadline, so I'd treat it carefully. Don't sign anything until I've reviewed it with you. I'll ask the company for a plain summary of the options.
""", log=[("a_action", "ask Samuel's employer for a plain summary of the pension survivor options")],
   open=[("g_pension", "A", "ask Samuel's employer for a plain summary of the pension survivor options")], tags=("advisor_commitment", "procedure")),
 ], "telephone"),
 ("Where the insurance money sits and your comfort with risk", [
  S("""
A: Right now the insurance payout is in money-market funds and short Treasuries. Do you want to talk about whether that's right?
C: I don't want any risk at all. I can't stomach the idea of losing any of it. It's all I have of him, in a sense.
A: I understand. That feeling is real, and I'm not going to argue with it. I'd record that you want capital preserved for now.
C: Yes. Please keep it safe.
A: One thing I'll add gently: safe isn't the same as risk-free, because inflation erodes cash over years. We don't need to solve that now, but we'll come back to it.
""", log=[("decision", "to keep the insurance payout in safe, accessible investments for now"), ("plan", "a very conservative portfolio, about 15 percent equities")],
   tags=("risk_preference_baseline", "confirmed_decision")),
 ], "in-person"),
 ("Documents and the inherited retirement account", [
  Check("g_estate", "pending", "I have two death certificates and I've ordered more, but the pension letter hasn't arrived."),
  G("explain", concept="survivor_rollover"),
  Check("g_pension", "done", "The company sent a summary. It gives you a lifetime survivor payment of about half his pension, or a lump sum option. I'll walk through it with you next time."),
 ], "telephone"),
 ("Paying off the mortgage?", [
  S("""
C: I keep thinking about the mortgage. We owe about a hundred forty thousand. I'd sleep better if the house were paid off.
A: I understand the wish, and it's a very common one after a loss. The rate is three point one percent, which is low. Paying it off would use a large part of your cash.
C: But I wouldn't owe anyone anything.
A: True. My advice is to wait. You're still in the first year of grief, you're still sorting the estate, and Folake's tuition is a large demand on your cash. Paying off the mortgage can't be undone.
C: How long should I wait?
A: At least until the estate is settled and we've decided how to title the retirement account. Shall we agree to revisit it in April?
C: All right. Let's revisit it in April.
""", log=[("deferred", "paying off the mortgage until the estate is settled, to be revisited in April")],
   tags=("deferred_decision", "advisor_advice_delay", "rationale")),
 ], "in-person"),
 ("Folake's tuition and cash flow", [
  G("cashflow"),
  S("""
A: Folake's tuition is about thirty-two thousand a year, and she has two and a half years left. Does she have any scholarships?
C: A small one. I'd like to pay it all, so she doesn't carry debt after losing her father.
A: Then I'd create a dedicated tuition bucket. We can hold about eighty thousand in short-term bonds so tuition doesn't depend on the stock market.
C: That sounds sensible. Yes, please set that up.
""", log=[("decision", "to set up a dedicated tuition bucket of about eighty thousand dollars in short-term bonds for Folake"),
         ("a_action", "set up the tuition bucket for Folake")], tags=("confirmed_decision", "family_context")),
 ], "telephone"),
 ("Survivor benefits from Social Security", [
  G("explain", concept="social_security"),
  S("""
C: Could I claim a survivor benefit now?
A: You may be eligible from age 60, but the amount is reduced if you claim early, and while you work before full retirement age the earnings limit could reduce it further. I'd want to check your exact numbers with the agency.
C: I'm working, so I'd rather wait.
A: That's reasonable. I'll note it as postponed, not decided forever.
""", log=[("deferred", "claiming the Social Security survivor benefit while you are still working")], tags=("deferred_decision",)),
 ], "in-person"),
 ("Tobi's business idea", [
  S("""
C: Tobi wants to start a food-delivery business with a friend. He asked me to lend him forty thousand.
A: How do you feel about it?
C: I might lend him some. I love that he has a plan. But I don't want to take money from Folake's tuition. I'm not sure.
A: Then we should treat it as a possibility only. Before any money moves, I'd want to see a written plan and decide what you can afford to lose.
C: Yes. Can you help me think about that?
A: I'll prepare a short note on how much you could lend without touching the tuition bucket or your emergency reserve.
""", log=[("tentative", "lending Tobi forty thousand dollars for his food-delivery business"), ("a_action", "prepare a note on how much you could lend Tobi without touching the tuition bucket or emergency reserve")],
   open=[("g_loan", "A", "prepare a note on how much you could lend Tobi without touching the tuition bucket or emergency reserve")],
   tags=("uncertain_intention", "family_context", "advisor_commitment")),
  G("market"),
 ], "telephone"),
 ("An annuity offer", [
  S("""
C: A man called me from an insurance company about an annuity. He said it would guarantee me income for life, and that I'd never have to worry again.
A: Did you give him any details?
C: No. But I'm tempted. A guarantee sounds comforting.
A: I understand. Annuities can have a role. The issues are cost, surrender charges, and loss of access to the money. What would you want the annuity to do?
C: Make me feel safe.
A: Let's make sure that's the right tool. For example, you have a pension survivor option and your salary. I'd want to see the product details before saying yes or no.
""", tags=("uncertain_intention", "advisor_caution")),
  G("suit", topic="an annuity"),
 ], "in-person"),
 ("Decision on the annuity and the market", [
  S("""
C: I read the brochure and thought about it. I've decided not to buy the annuity. The money would be locked up for years, and I need access for Folake's tuition and for Tobi, if something happens.
A: That's a sound reason. I'll record it as a decision with that rationale.
C: Yes. If I change my mind, I'll tell you.
""", log=[("decision", "not to buy the annuity, because you need access to the money for Folake's tuition and family needs")],
   tags=("confirmed_decision", "rationale", "rejected_option")),
  G("market"),
 ], "telephone"),
 ("Retitling the inherited account", [
  Check("g_estate", "done", "I have all five death certificates now, and the pension letter came too."),
  S("""
A: Let's turn to the retirement account. Have you thought about the choice we discussed?
C: Yes. I want to treat it as my own. I like having one account with my name on it.
A: That's a legitimate choice, and it gives you the most flexibility on timing. It's also hard to reverse, so I want to be sure. Is that your instruction?
C: It is. Please roll it over into my own account.
""", log=[("decision", "to roll Samuel's retirement account into an account in your own name"), ("a_action", "start the spousal rollover into your own account")],
   tags=("confirmed_decision", "explicit_instruction", "procedure")),
 ], "in-person"),
 ("Revisiting the mortgage", [
  S("""
A: As we agreed, I'd like to revisit the mortgage. Here is the picture: the three point one percent rate, the amount of cash you'd use, and the effect on your emergency reserve.
C: I've been thinking about it. In February I wanted it gone. Now I think I'd rather keep the mortgage.
A: What changed?
C: Seeing the numbers helped. If I pay it off, I'd lose too much flexibility for Folake. And three point one percent isn't much.
A: Then I'll record that you've decided to keep the mortgage for now, with the reason.
""", log=[("decision", "to keep the mortgage rather than pay it off, because the three point one percent rate is low and you want flexibility for Folake")],
   tags=("contradiction", "confirmed_decision", "rationale")),
 ], "telephone"),
 ("Tobi's loan decision", [
  Check("g_loan", "done", "You could lend about fifteen thousand without touching the tuition bucket or the emergency reserve. Anything above that would come from long-term savings."),
  S("""
C: Then I've decided. I won't lend forty thousand. I'll offer up to fifteen thousand, as a gift, if Tobi writes a business plan I can read.
A: A gift and not a loan?
C: Yes, I don't want to nag him about repayment, and I don't want family tension about money.
A: That's a thoughtful way to handle it. I'd suggest you tell him the limit clearly so there are no surprises.
""", log=[("decision", "not to lend Tobi forty thousand dollars, but to offer up to fifteen thousand as a gift if he provides a written business plan")],
   tags=("confirmed_decision", "rationale", "family_context")),
 ], "in-person"),
 ("Thinking about when to retire", [
  S("""
C: At work they asked whether I'd stay past 65. But lately I've been wondering whether I should retire earlier, maybe at 62. I'm not sure yet.
A: What's behind that?
C: I want more time. But I also don't want to be alone in an empty house.
A: Both are real. I'll record that you're thinking about 62, not deciding it. When we look at it, I'd like to see what 62 and 65 mean for your income.
""", log=[("tentative", "retiring at 62 rather than 65")], set={"retire_age": 65},
   tags=("uncertain_intention", "stated_intention")),
  G("portfolio"),
 ], "telephone"),
 ("Feeling ready to invest a little", [
  S("""
C: I've been looking at the account, and it feels like the money is just sitting there. A part of me wants to put some of it to work. Not a lot.
A: That's a different feeling from January. What has changed?
C: I feel steadier. And I realise I can't live on fear forever.
A: I'm glad you can say that. How would you feel if part of the money went down for a while?
C: Uncomfortable. But I could live with a small amount. Maybe a quarter in stocks?
A: Let's study it properly rather than just doing it. I'll prepare a gradual plan, with the possible ups and downs.
""", log=[("tentative", "raising stock exposure to about a quarter of the portfolio"), ("a_action", "prepare a gradual plan to raise stock exposure")],
   open=[("g_grow", "A", "prepare a gradual plan to raise stock exposure")], set={"anxiety": "mid"},
   tags=("changing_preference", "uncertain_intention")),
  G("suit", topic="raising your stock exposure"),
 ], "in-person"),
 ("The gradual investing plan", [
  S("""
A: Here's the plan. We would move money in six equal steps over six months, so that you don't make a single large bet on one day. The target is about 25 percent in stocks, with the tuition bucket and the cash reserve untouched.
C: And if the market falls after the first step?
A: Then you'd have bought at a lower price in the next steps, but you would also see losses. I can't promise how it turns out. It's about reducing regret.
C: Okay. I've decided. Let's do it, starting next month, with the target of 25 percent.
""", close=["g_grow"], log=[("decision", "to raise stock exposure to about 25 percent in six monthly steps, leaving the tuition bucket and cash reserve untouched"),
         ("a_action", "carry out the first monthly step toward the 25 percent target")],
   tags=("changing_preference", "confirmed_decision", "explicit_instruction")),
 ], "telephone"),
 ("Tobi's graduation and beneficiaries", [
  G("doc", doc="your will and the beneficiary designations on your own accounts", why="so I can update them now that Samuel is no longer your beneficiary", id="g_benef", due="in the next month"),
  G("cashflow"),
 ], "in-person"),
 ("Market check and first investing step", [
  G("market"),
  G("portfolio"),
 ], "telephone"),
 ("Funding Folake's tuition from the bucket", [
  G("explain", concept="bond_ladder"),
  G("cashflow"),
  Check("g_benef", "pending", "I started the forms, but I had trouble deciding between Tobi and Folake as the main beneficiary."),
 ], "in-person"),
 ("Retirement age decision", [
  S("""
C: I've decided to work until 63. Not 62, not 65. It's a compromise. Another year helps the numbers, and I'd like to finish a project at the hospital.
A: Thank you. In April you were considering 62, and before that 65. I'll record 63 and your reason.
C: Yes. And if Tobi's business needs support, I may revisit.
""", log=[("decision", "to work until 63, rather than 62 or 65, to strengthen the numbers and finish a hospital project")],
   set={"retire_age": 63}, tags=("contradiction", "confirmed_decision", "rationale")),
 ], "telephone"),
 ("Portfolio review", [
  G("portfolio"),
  G("market"),
 ], "in-person"),
 ("Beneficiary forms", [
  Check("g_benef", "done", "I settled it. Tobi and Folake will be equal beneficiaries and I'm sending the forms back."),
  G("cashflow"),
  G("market"),
 ], "telephone"),
 ("Second investing step", [
  G("portfolio"),
  G("suit", topic="your gradual move to a quarter in stocks"),
 ], "in-person"),
 ("Six months on", [
  S("""
C: It's almost seven months since Samuel died. I'm still sad, but I notice I'm handling things I couldn't before.
A: You are. When we met in January, you couldn't think straight. Now you've made a series of careful decisions.
C: I still miss having him to discuss them with.
A: Of course. I'm glad you have people. Shall we use this meeting to look at the whole picture?
C: Yes.
""", tags=("casual_context", "personal_reflection")),
  G("portfolio"),
 ], "in-person"),
 ("Half-year review", [
  S("""
A: Grace, let me read back where we are. In January you wanted no risk at all, and we kept the insurance money safe. You turned down the annuity because you needed access to the money, you rolled Samuel's account into your own, and you decided to keep the mortgage after first wanting to pay it off.
C: That's right. And I'm investing a little now.
A: Yes, in six steps to about a quarter in stocks. You also decided on a gift of up to fifteen thousand for Tobi if he writes a plan, and to work until 63.
C: That's all correct.
A: Open items: the survivor benefit claim is postponed, and the next investing steps continue.
C: Good. Let's keep going at this pace.
""", tags=("summary", "history_recall", "changing_preference")),
 ], "in-person"),
]


PROFILE["casual_windows"] = [(1, 6), (1, 12), (2, 25), ("2026-04-13", "2026-05-31"), (10, 23), (3, 25), (12, 25), (12, 19)]
