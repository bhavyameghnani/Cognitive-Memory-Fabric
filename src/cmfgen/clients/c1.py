"""CLIENT-001 Kenji Tanaka: senior executive, retirement timing 65 -> (explored 60) -> 63;
risk appetite 75% equity -> 60% after the March sell-off."""
from ..beats import S, G, Check

PROFILE = dict(
    client_id="CLIENT-001", advisor_id="ADVISOR-001",
    name="Kenji Tanaka", adv_name="Priya Nair",
    channels=[("video", 0.45), ("in-person", 0.3), ("telephone", 0.25)],
    hours=[8, 9, 10, 16], beta=1.0,
    months=[1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6, 6],
    facts=dict(first="Kenji", adv_first="Priya", spend=11800,
               income_desc="salary and bonus income that comfortably exceeds that",
               buffer_target=15,
               liq_reason="the reorganisation at Northgate and Ren's tuition over the next few years"),
    state=dict(value=3_400_000, equity=75, anxiety="low", cash=150_000),
    holdings=[
        ("the Northgate Freight shares from your equity awards", "they have grown to about nine percent of your investable assets because of vesting, which is more concentration in your employer than I'd normally like", (1, 11)),
        ("the Northgate Freight shares from your equity awards", "since you sold a third of the vested shares in the spring they are down to about six percent of your investable assets, which is easier to live with", (12, 25)),
        ("the global equity index funds", "they are the core growth engine of the portfolio and they are cheap to hold"),
        ("the municipal bond fund", "it is there for stability, and the interest is generally exempt from federal income tax, so we compare it against Treasuries whenever we rebalance"),
        ("the real estate fund", "it is a small diversifier at about five percent, although it is less liquid than the rest"),
    ],
    pressures=[
        "Ren's tuition went up again this year",
        "the kitchen renovation ran well over budget",
        "the travel we've been doing eats into the monthly number",
        "our property tax bill came in higher than I expected",
    ],
    casual=[
        """
C: Mariko has been picking up extra tutoring hours and she seems happier for it.
A: That's good to hear. Does it change household income at all?
C: Marginally. Mostly she enjoys it.
""",
        """
C: Yuki has been talking about a master's in public health. Nothing is decided, she's still looking at programs.
A: That's an exciting step for her. I'll keep it in mind as a possible cost without building it into the plan yet.
""",
        """
C: I've been trying to get back to running in the mornings. My doctor said my blood pressure was borderline.
A: Good for you. Your health matters for the retirement conversation too, particularly for coverage before Medicare.
""",
        """
C: We're thinking about a trip to Kyoto in the autumn to see Mariko's family.
A: Lovely. Is that a one-off, or the kind of trip you'd like to take every year? It affects how I model travel in retirement.
C: Probably every other year, if we can.
""",
        """
C: Ren decided to switch his major to engineering, which probably adds a semester.
A: Thanks for telling me. An extra semester is worth building into the tuition numbers.
""",
        """
C: My mother's apartment in Osaka needs repairs. My brother and I are splitting the cost.
A: Understood. Is that likely to be recurring, or a one-time contribution?
C: One-time, we think.
""",
        """
C: Mariko wants to redo the garden now that the weather has turned.
A: Sounds nice. Just flag anything over a few thousand dollars so I can plan liquidity around it.
""",
        """
C: I've been spending more time with my family lately. All the reorganisation noise made me realise I've been working too much.
A: That's an important thing to notice. We can make sure the plan leaves room for that.
""",
    ],
)

STEPS = [
 # 1 Jan
 ("Retirement timing and income needs", [
  S("""
A: Kenji, before we get into numbers, tell me in your own words what retirement looks like for you.
C: Honestly, the plan has always been 65. Mariko and I have said it for years. Travel, maybe some consulting, and being around while the kids settle into their lives.
A: What monthly income would make that comfortable? I'm asking about after-tax spending, not salary.
C: We spend about eleven thousand eight hundred a month now. In retirement I think we'd want fifteen thousand, with more travel and health costs I haven't budgeted for.
A: That's helpful. I'd like us to stress-test fifteen thousand rather than assume it. Does it include the mortgage? You have about two hundred ten thousand left, if I remember the paperwork.
C: The mortgage runs to 2032, so it's gone before we retire. The fifteen is mostly lifestyle.
A: Good. So the working assumption is retirement at 65, fifteen thousand a month after tax, mortgage cleared. I'll record that as your current plan, not a fixed decision. Plans change and I'd rather revisit it than lock it in.
C: That's fair.
""", log=[("plan", "retirement at 65 on about fifteen thousand a month after tax, with the mortgage cleared by 2032")],
   set={"retire_age": 65}, tags=("stated_intention",)),
  G("doc", doc="the plan summary for your Northgate deferred compensation account", why="so I can model how those payouts fit into your retirement income", id="k_defcomp", due="within the next two weeks"),
 ], "video"),
 # 2 Jan
 ("Portfolio review and risk profile", [
  Check("k_defcomp", "pending", "I started asking HR, but the plan administrator's site needed a password reset and I got locked out. I'll try again this week."),
  S("""
A: Last year's questionnaire put you as growth-oriented. Does that still describe you?
C: Very much. I've always said I'd rather be too aggressive than too cautious. With the years I have left before retiring, I want the portfolio to work hard.
A: You have roughly seven years to your planned retirement, and a lot of your income comes from the same company whose shares you hold. How would you feel about a twenty percent fall?
C: I'd hate it, but I'd sit tight. I did in 2022.
A: That history is useful. For the record, you're comfortable with about seventy-five percent in equities and you'd tolerate a significant drawdown. We'll check that as things change. Please proceed with the portfolio review we agreed, and I'll bring the numbers to you.
C: Yes, go ahead with the review.
""", log=[("decision", "to proceed with the agreed portfolio review"), ("plan", "a growth-oriented portfolio at about 75 percent equities")],
   tags=("risk_preference_baseline", "confirmed_decision")),
  G("portfolio"),
 ], "telephone"),
 # 3 Jan
 ("Company reorganisation and early retirement thoughts", [
  S("""
C: There's something I wanted to raise. Northgate announced a reorganisation yesterday and my division is being merged. Nobody has said my job is at risk, but the atmosphere is tense.
A: I'm sorry, that sounds unsettling. Has anything changed in your role or compensation?
C: Not yet. But it's got me thinking: what if I retired at 60 instead of 65? I might do it. I'm only thinking out loud, I haven't decided anything.
A: Understood, and thank you for putting it that way. I'll note it as something you're exploring. It has big consequences: five fewer years of salary and savings, five more years of withdrawals, and health coverage before Medicare starts at 65.
C: Can you show me what it would look like?
A: Yes. I'll run the plan at 60, 63 and 65 using your current numbers and conservative return assumptions, and bring it to our next meeting.
""", log=[("tentative", "retiring at 60"), ("a_action", "run the retirement-age scenarios for 60, 63 and 65")],
   open=[("k_scen", "A", "run the retirement-age scenarios for 60, 63 and 65")],
   tags=("uncertain_intention", "advisor_commitment")),
  G("cashflow"),
 ], "video"),
 # 4 Feb
 ("Cash flow, mortgage and liquidity", [
  Check("k_defcomp", "done", "Yes, I finally got the statement from the administrator. The balance is around six hundred forty thousand, and it lets me choose between a lump sum and installments."),
  G("cashflow"),
  G("liquidity"),
  S("""
A: One more thing on the mortgage. Some clients ask whether to pay it off early. At your rate, I don't see a strong reason, but it's your call.
C: Mariko asked the same thing. I'll leave it as it is for now.
""", tags=("casual_context",)),
 ], "in-person"),
 # 5 Feb
 ("Early retirement scenario results", [
  S("""
A: As promised, I ran the three scenarios. At 65, your plan funds fifteen thousand a month with a comfortable margin, even with modest returns. At 63, it still works but the margin is thinner. At 60, it only works if the portfolio performs near its long-run average, and a poor first five years would put the income at real risk.
C: That's sobering. I expected 60 to be tight, but not that tight.
A: The gap is mostly healthcare. Between 60 and 65 you'd be paying for private coverage, which I've estimated at about two thousand eight hundred a month for the two of you, and you'd lose five years of contributions.
C: Right. Still, part of me wants out of the reorg politics. If I lean toward 60, is it impossible?
A: Not impossible, but it would need a lower spending target, part-time consulting income, or both. I'd call it feasible with trade-offs, and not recommended on current numbers.
C: Okay. Let's say I'm leaning toward 60 for now, but I'm not committing to it. I want to see how the reorganisation shakes out.
A: That's a sensible way to treat it. I'll keep 65 as the formal planning date until you tell me otherwise.
""", close=["k_scen"], log=[("tentative", "leaning toward retiring at 60 while 65 stays the formal planning date")],
   set={"lean_age": 60}, tags=("uncertain_intention", "scenario_result", "advisor_commitment")),
  G("market"),
 ], "video"),
 # 6 Feb
 ("Market volatility and staying the course", [
  S("""
C: The market has been rough. I'm well down on paper from the January high, but I'm not going to panic. In fact I was thinking of buying more equities while prices are low.
A: I'd be careful with that. Your portfolio is already at the upper end of what the plan needs, and your Northgate shares add to the same risk.
C: But isn't this when you're supposed to buy?
A: Rebalancing toward target is reasonable. Raising the target in a falling market is a different thing. We're about 72 percent in equities now, so moving a small amount to get back to 75 is within the existing policy.
C: Okay. Go ahead and do that small rebalance. Nothing more.
A: Understood. I'll move about ninety thousand from bonds into the index funds and send confirmation. To be clear, that's an instruction to rebalance only to the 75 percent target, correct?
C: Correct.
""", log=[("decision", "to rebalance back to the 75 percent equity target, with no increase in overall risk"),
         ("a_action", "move about ninety thousand dollars from bonds into the equity index funds and send you confirmation")],
   tags=("confirmed_decision", "explicit_instruction")),
 ], "telephone"),
 # 7 Feb
 ("Yuki's graduate studies and funding", [
  S("""
C: Yuki heard back from two public health programs. She hasn't decided, and she's also interviewing for a job at a nonprofit. If she goes to school, it would be about forty-eight thousand a year for two years.
A: And is the expectation that you would cover it?
C: I'd like to help, but I haven't thought about how much. Mariko thinks we should pay all of it. I'm not so sure.
A: That's worth deciding deliberately rather than by default. The question isn't just affordability. It's the effect on your retirement date and on Ren's costs, which will continue for three more years.
C: Can you show me how forty-eight a year compares with thirty-something?
A: I can. I'll model full funding, a cap of thirty thousand a year, and a partly loan-based option, with the retirement scenarios still in view.
""", log=[("tentative", "how much to contribute if Yuki goes ahead with graduate school"),
         ("a_action", "model funding Yuki's graduate studies in full, capped at thirty thousand dollars a year, or partly through loans")],
   open=[("k_edu", "A", "model funding Yuki's graduate studies in full, capped at thirty thousand dollars a year, or partly through loans")],
   tags=("uncertain_intention", "family_context", "advisor_commitment")),
  G("explain", concept="education_funding"),
 ], "in-person"),
 # 8 Mar
 ("Share vesting and tax planning", [
  S("""
C: A large block of Northgate stock vested on <<ago:3>>. The tax withholding looked low to me.
A: It often is. Supplemental withholding is a flat rate that can sit below your marginal rate, so you may owe more in April.
C: I figured. What should I do with the shares?
A: Whether you sell at vesting or later, the tax on the vesting itself is the same, so the question is purely about risk. At about nine percent of your investable assets, I lean toward selling a portion at each vest and diversifying. That's a recommendation. The decision is yours.
C: Let me think about it. I like the company, and the reorganisation might make the stock fall or rise.
A: Fair. Let's not decide today. I'd like your accountant's view on the estimated payments first. I'll send her a summary of the vesting, and could you ask her for the estimated-tax figure?
C: Sure. Hannah Park is my CPA. I'll email her tonight.
""", log=[("deferred", "selling part of the vested Northgate shares until the tax estimate is in"),
         ("a_action", "send your accountant a summary of the vesting"), ("c_action", "ask your accountant for the estimated-tax figure")],
   open=[("k_cpa_note", "A", "send Hannah Park a summary of the vesting"), ("k_cpa_est", "C", "get the estimated-tax figure from Hannah Park")],
   tags=("deferred_decision", "tax", "advisor_commitment")),
  G("explain", concept="concentrated_stock"),
 ], "video"),
 # 9 Mar
 ("Correcting the retirement-age record", [
  S("""
A: Before we start, I want to check something in my notes. After our last discussion of retirement timing, I understood that you wanted to retire at 60. Is that right?
C: I should clarify that. I was only exploring the possibility. I said I was leaning that way, but I haven't made that decision. 65 is still the date we're planning against.
A: Thank you, that matters. The wording of my file note was too strong. I'll correct it so it reads that you are exploring 60 while the formal planning date remains 65.
C: Please do. I don't want someone looking at that note later and assuming I'd committed.
A: Agreed. The original discussion stays on record exactly as it happened. I'll add a clarification dated today.
""", log=[("correction", "my earlier note that you wanted to retire at 60 was too strong; you are exploring it, and 65 remains the planning date")],
   tags=("correction", "misunderstanding", "contradiction")),
  Check("k_cpa_note", "done", "I sent Hannah Park the summary on <<ago:1>>, and she has acknowledged it."),
  Check("k_cpa_est", "pending", "I emailed Hannah, but she's swamped until the filing deadline passes and said she'd get back to me in April."),
 ], "telephone"),
 # 10 Mar
 ("Comfort with portfolio risk", [
  S("""
C: There's something that's been bothering me. March was ugly. At one point the portfolio was down well over two hundred thousand from the January high, and I realised it wasn't just a number. I'm wondering whether seventy-five percent in stocks is still right for me at 58.
A: I appreciate you saying that, because it's different from what you told me in January, when you said you'd sit tight through a twenty percent fall.
C: I know. Saying it and watching it are different. With the reorganisation on top, I'd feel better with smaller swings.
A: Then let's take that seriously. Two questions: how much less risk, and is this a temporary reaction or a lasting change? If we cut risk after a fall, we lock in the loss. If the change is lasting, the questionnaire should reflect it.
C: I think it's lasting. Maybe sixty percent? I don't want to be too conservative either.
A: Sixty is reasonable to look at, but I won't act on 'maybe'. I'll prepare a comparison of 60-40 and 70-30 portfolios, including the effect on your retirement scenarios, and we can decide next time.
""", log=[("tentative", "reducing equity exposure from about 75 percent to around 60"),
         ("a_action", "prepare a comparison of 60 and 70 percent equity portfolios against your retirement scenarios")],
   open=[("k_alloc", "A", "prepare a comparison of 60 and 70 percent equity portfolios against your retirement scenarios")],
   set={"anxiety": "mid"}, tags=("changing_preference", "uncertain_intention", "advisor_commitment")),
  G("suit", topic="a lower equity allocation"),
 ], "video"),
 # 11 Mar
 ("Healthcare before Medicare and the vested shares", [
  G("explain", concept="healthcare_bridge"),
  Check("k_cpa_est", "done", "Hannah got back to me early. She estimates about twenty-eight thousand in additional tax for the year."),
  S("""
A: Given Hannah's number, do you want to sell some of the vested shares to cover it?
C: Yes. Sell enough to cover the tax, and let's sell about a third of what vested. The reorganisation has made me want less exposure to the company, and I don't want to be forced to sell later in a panic.
A: Understood, that's a clear instruction. I'll sell about a third of the vested Northgate shares, including enough to cover the estimated tax, and confirm the trade.
C: Yes, please proceed.
""", log=[("decision", "to sell about a third of the vested Northgate shares, including enough to cover the estimated tax, to reduce exposure to your employer"),
         ("a_action", "sell about a third of the vested Northgate shares and confirm the trade")],
   open=[("k_rsu_sale", "A", "sell about a third of the vested Northgate shares and confirm the trade")],
   tags=("confirmed_decision", "rationale", "explicit_instruction")),
 ], "in-person"),
 # 12 Apr
 ("Decision on the equity allocation", [
  Check("k_rsu_sale", "done", "The trade was completed, and the proceeds covered the estimated tax with a small amount left over, which is in your cash account."),
  S("""
A: Here's the comparison. A 60-40 mix lowers expected long-run growth, but your plan at 65 still works. At 63 the margin is narrower, and at 70-30 it's in between. The 60-40 mix would also have had a noticeably smaller fall in March.
C: Seeing that, I've decided. Let's go to sixty percent equities. Please proceed.
A: Thank you. Because this changes your risk profile, I need an updated investment policy statement signed by you. I'll implement the change in your retirement accounts first, so we avoid triggering capital gains in the brokerage account.
C: That makes sense. I'll sign it.
A: I'd also like to note your reason: the March decline showed you that your real tolerance is lower than you told me in January, and you want smaller swings as retirement approaches. Is that fair?
C: That's fair.
""", close=["k_alloc"], log=[("decision", "to reduce the equity allocation from about 75 percent to 60 percent because the March decline showed a lower tolerance for volatility"),
         ("a_action", "implement the move to 60 percent equities, using the retirement accounts first"), ("c_action", "sign the updated investment policy statement")],
   open=[("k_ips", "C", "sign the updated investment policy statement")], set={"equity": 60},
   tags=("changing_preference", "confirmed_decision", "rationale", "explicit_instruction", "procedure")),
  G("portfolio"),
 ], "video"),
 # 13 Apr
 ("Education funding decision", [
  Check("k_ips", "pending", "I opened the e-signature link but it had expired. Can you resend it?"),
  S("""
A: Here are the results for Yuki's studies. Funding all of it, about ninety-six thousand over two years, pushes your retirement date back by roughly a year at the 63 scenario. A thirty-thousand-dollar annual cap barely moves it. A loan-based option sits in between.
C: Yuki hasn't decided yet, but she'll probably take a place. I've decided: if she goes, we'll contribute up to thirty thousand a year. She can cover the rest with a part-time job or a loan. Mariko agrees.
A: Can you tell me the reasoning, so it's recorded properly?
C: I want to protect our retirement date, and Ren has three more years of tuition. I'd rather give a fixed amount than keep opening the wallet.
A: That's a clear and defensible position. I'll note it as conditional on Yuki attending.
""", close=["k_edu"], log=[("decision", "to contribute up to thirty thousand dollars a year if Yuki attends graduate school, to protect your retirement date and Ren's remaining tuition"),
         ("a_action", "resend the investment policy statement for signature")],
   tags=("confirmed_decision", "rationale", "scenario_result")),
 ], "telephone"),
 # 14 Apr
 ("Integration role and retirement age", [
  S("""
C: Big news. The CEO asked me to lead the integration of the acquisition through the end of 2028, with a retention bonus.
A: Congratulations. Do you know the size and the conditions of the bonus?
C: About a hundred and fifty thousand, paid in installments, conditional on staying. I'm not sure what happens if I leave early.
A: That's the key point. I'd like to see the retention agreement before we plan around it. This changes how I'd think about 60 as well.
C: It does. Honestly, 60 isn't realistic anymore if I take it. I'm thinking about 63 instead. Maybe. I haven't talked it through with Mariko fully.
A: Understood. I'll record 63 as something you're considering, not a decision. I'll update the scenarios to include the bonus and a retirement age of 63.
""", log=[("tentative", "retiring at 63 instead of 60"), ("a_action", "update the retirement scenarios to include the retention bonus and a retirement age of 63")],
   open=[("k_63", "A", "update the retirement scenarios to include the retention bonus and a retirement age of 63")],
   set={"lean_age": 63}, tags=("contradiction", "uncertain_intention", "advisor_commitment")),
  G("doc", doc="the retention bonus agreement from Northgate HR", why="so I can check the vesting dates and what happens if you leave early", id="k_ret"),
 ], "video"),
 # 15 Apr
 ("Retention bonus tax and the 63 scenario", [
  Check("k_ret", "done", "HR emailed it. The bonus vests in three installments, and I forfeit whatever hasn't vested if I leave before each date."),
  Check("k_ips", "done", "I signed the resent form yesterday."),
  S("""
A: With that, here is the updated scenario. At 63 with the retention bonus, your plan funds fifteen thousand a month with a comfortable margin, and the healthcare gap shrinks to two years.
C: That's much better than 60, as I feared.
A: Two cautions. First, the bonus is taxed as supplemental income, so I'd plan on keeping only part of each installment. Second, it's conditional, so I wouldn't count on it until it vests.
C: Understood. I'd like to talk it over with Mariko before I say it's final.
""", close=["k_63"], log=[("tentative", "retiring at 63, pending a conversation with Mariko")], tags=("scenario_result", "uncertain_intention")),
 ], "telephone"),
 # 16 May
 ("Retirement age decision", [
  S("""
C: Mariko and I talked it through over the weekend. We've decided: 63. The idea of 60 was mostly about escaping the reorganisation politics, and now my role has changed. Mariko wants to keep teaching until 62.
A: That's a clear decision. For the record, how does this compare with your original plan of 65?
C: Two years earlier than 65, three years later than the 60 I was toying with. It's the compromise we can both live with.
A: I'll change the formal planning date from 65 to 63. Are there circumstances that would make you revisit it?
C: If my health changes, or if Northgate is sold and the role disappears. Otherwise, I'd like to stick with 63.
A: Good. I'll note those two triggers and the reasoning, so that anyone looking at the plan later understands why it changed.
""", log=[("decision", "to retire at 63, replacing the earlier plan of 65, because the role changed and Mariko wants to teach until 62"),
         ("plan", "a formal retirement date of 63, to be revisited if your health changes or Northgate is sold")],
   set={"retire_age": 63}, tags=("contradiction", "confirmed_decision", "rationale")),
  G("portfolio"),
 ], "video"),
 # 17 May
 ("Ren's semester abroad and liquidity", [
  S("""
C: Ren wants to spend a semester in Seoul next spring. The program would cost about fourteen thousand beyond normal tuition.
A: Is that definite, or is it still a possibility?
C: He still has to be accepted, but I'd like to be ready. Should I sell investments to pay for it?
A: I'd avoid selling in a weak market. You have enough cash to cover it without hurting your emergency reserve, but only just. I'd suggest earmarking fourteen thousand from cash.
C: Then let's do that. Please set aside fourteen thousand in cash for it.
A: I'll earmark it. If he isn't accepted, the money simply returns to your general reserve.
""", log=[("decision", "to set aside fourteen thousand dollars in cash for Ren's possible semester in Seoul"),
         ("a_action", "earmark fourteen thousand dollars of your cash for the Seoul program")],
   tags=("confirmed_decision", "family_context")),
  G("liquidity"),
 ], "telephone"),
 # 18 May
 ("Social Security claiming strategy", [
  G("explain", concept="social_security"),
  S("""
C: Mariko earned less over the years, so I assume my benefit matters more.
A: It often does. If you die first, she could step up to your larger benefit, which is a reason people with a bigger earner sometimes delay claiming. I'd like to model claiming at 63, 67 and 70 with her record included.
C: Fine. Do you need anything from me?
A: Her most recent Social Security statement would help. I'll run the scenarios and bring results.
""", log=[("a_action", "model Social Security claiming at 63, 67 and 70 using both your records")],
   open=[("k_ss", "A", "model Social Security claiming at 63, 67 and 70 using both your records")], tags=("advisor_commitment",)),
  G("market"),
 ], "video"),
 # 19 May
 ("Deferred compensation payout options", [
  S("""
C: The plan administrator sent a notice. I have to elect how the deferred compensation is paid, and the deadline is the end of June.
A: What are the options?
C: A lump sum at 63, five annual installments, or ten annual installments.
A: The right answer depends heavily on your tax bracket in those years. A lump sum could push you into a high bracket in one year. Spreading it may reduce the tax, but it keeps the money exposed to the employer's credit.
C: I don't want to decide without seeing the tax estimate. Can Hannah run it?
A: She can. I'd ask her to compare the three options. I'm not comfortable choosing until we see those numbers.
""", log=[("deferred", "the deferred compensation payout election until the tax comparison is ready"), ("c_action", "ask Hannah Park to estimate the tax on the three payout options")],
   open=[("k_dc_tax", "C", "ask Hannah Park to estimate the tax on the three payout options")], tags=("deferred_decision", "tax", "pending_action")),
  G("cashflow"),
 ], "in-person"),
 # 20 May
 ("Portfolio review and estate documents", [
  G("portfolio"),
  G("doc", doc="your current will and the beneficiary designations on your retirement accounts", why="so I can check they still match your family situation", id="k_estate", due="in the next few weeks"),
  G("market"),
 ], "telephone"),
 # 21 Jun
 ("Deferred compensation election", [
  Check("k_dc_tax", "done", "Hannah says five annual installments from age 63 keep me in a lower bracket than the lump sum. The ten-year option saves a bit more tax but keeps the money with the company longer."),
  S("""
A: Do you have a preference?
C: I've decided on five annual installments starting at 63. The ten years saves a bit, but I don't want that much exposed to Northgate for that long.
A: That's a reasoned choice. You'll need to submit the election form to the plan administrator before the deadline. Do you want me to look at the form first?
C: Yes, please. Send me your comments, then I'll submit it.
""", log=[("decision", "to elect five annual deferred compensation installments starting at 63, rather than a lump sum or ten installments, to limit exposure to Northgate"),
         ("c_action", "submit the election form to the plan administrator before the end of June")],
   open=[("k_election", "C", "submit the election form to the plan administrator before the end of June")],
   tags=("confirmed_decision", "rationale")),
 ], "video"),
 # 22 Jun
 ("Social Security results", [
  S("""
A: I have the first version of the Social Security model. At 63 claiming is possible but locks in a smaller benefit for both of you. Delaying to 67 improves the survivor benefit materially. I haven't modelled 70 fully because Mariko's record is still missing.
C: So you can't finish without her statement?
A: I can give you a base case, but the survivor picture depends on her record. Could you ask her to download it?
C: I'll ask her tonight.
""", close=["k_ss"], log=[("c_action", "get Mariko's Social Security statement to complete the survivor analysis")],
   open=[("k_spouse_ss", "C", "get Mariko's Social Security statement to complete the survivor analysis")],
   tags=("scenario_result", "pending_action")),
  G("portfolio"),
 ], "telephone"),
 # 23 Jun
 ("Follow-ups on forms and estate documents", [
  Check("k_election", "done", "I submitted the election form on <<ago:5>>, and the administrator confirmed it."),
  Check("k_spouse_ss", "pending", "Mariko hasn't had a chance. She's grading final exams this week and then she will do it."),
  Check("k_estate", "pending", "I found the will, but the beneficiary forms are in a safe-deposit box and I haven't gone to the bank."),
  G("market"),
 ], "video"),
 # 24 Jun
 ("Cash flow and summer spending", [
  G("cashflow"),
  G("liquidity"),
  S("""
C: One more question. Should I be thinking about a Roth conversion at some point before 63?
A: It might be worth studying, particularly in the years between retirement and the start of your deferred compensation. I wouldn't start now. It depends on your future bracket and we have other items open. I'll put it on the list for the second-half review.
""", log=[("deferred", "any Roth conversion until the second-half review")], tags=("deferred_decision",)),
 ], "telephone"),
 # 25 Jun
 ("Half-year review", [
  S("""
A: Kenji, I'd like to read back how your plan has moved this year, to make sure it's accurate. In January you told me you'd retire at 65. In February you were exploring 60 and leaning that way, but you corrected me in March to say you hadn't decided. After the integration role, you considered 63 and in May, with Mariko, decided on 63.
C: That's accurate. It feels like a year of changes in six months.
A: On risk, you started with seventy-five percent in equities and said you'd sit tight. After the March decline, you decided on sixty, and I recorded why.
C: Yes. That was the right call for me. I sleep better.
A: Open items: Mariko's Social Security statement, and the beneficiary forms for your estate documents. Both are on your side.
C: I know. I'll chase them both this month.
""", tags=("summary", "history_recall", "contradiction")),
  G("portfolio"),
 ], "video"),
]


PROFILE["casual_windows"] = [(1, 25), (1, 6), (3, 25), (4, 25), (4, 25), (5, 25), (9, 25), (5, 25)]
