"""Revision 2, CLIENT-006 Peg Sullivan (plain-spoken, wary of jargon, drifts to Frank's care; advisor warm).
Thin meetings 2, 6, 8, 14, 17, 20, 23, 24, 25; corrections in 17 (Brian) and 20 (aide days)."""
from .beats import Check, G, S
from .revisions import APPEND, REPLACE

REPLACE[(5, 2)] = ("How withdrawals work and what happens to income later", [
    S("""
C: You said the bonds pay for the next five years. I don't understand that. Do they just sit there?
A: Let me say it a different way. Each month you take about twenty-six hundred from the portfolio to cover what the pension and Social Security don't. Instead of selling stocks for that money, we keep several years of it in short-term bonds that mature one after another. When one matures, it turns into cash.
C: So I never sell stocks.
A: You sell them when the market is up and we're topping the bonds back up. You don't have to sell when the market is down. That's the point of it.
C: And if Frank passes first... I'm sorry, it's what I think about. Does my income go down?
A: I'm glad you asked, because a plan should answer that. Do you know what happens to Frank's pension if he dies first?
C: I think I get about half. I'm not sure. I never read the booklet.
A: That's worth knowing, and it affects how much you can safely spend. For now I'll record that the survivor amount is unconfirmed, and we'll come back to it when we look at the care costs.
C: All right. I don't like the fancy words, but that I followed.
""", log=[("plan", "treating the survivor amount of Frank's pension as unconfirmed, with you believing it is about half")],
      tags=("concept_explained", "family_context", "unresolved")),
    G("portfolio"),
], None)

REPLACE[(5, 6)] = ("A denied medical bill and the cash reserve", [
    S("""
C: I got a bill for nineteen hundred dollars from the physical therapy people, and the insurance says they won't pay. I don't know why. I just want it gone.
A: Let's look at it. Did you get an explanation from Medicare?
C: A letter. Something about not medically necessary. I didn't read the rest.
A: That's the summary notice. It usually says you can appeal within a certain time, and Frank's therapist might support it.
C: I haven't the energy for an appeal. I'd rather pay and be done. Is that stupid?
A: It isn't stupid. It's nineteen hundred dollars against your time and stress, and you know what you can carry. I only want you to know the option exists.
C: Then I'll pay it. Out of the cash account. And if it happens again, we'll see.
A: If it happens again, tell me and we'll look at the pattern together.
""", log=[("decision", "to pay the nineteen hundred dollar physical therapy bill from the cash account instead of appealing the denial, to avoid the time and stress")],
      tags=("confirmed_decision", "rationale", "family_context", "advisor_procedure")),
    G("liquidity"),
], None)

from .clients import c6 as _c6  # noqa: E402

REPLACE[(5, 8)] = (_c6.STEPS[7][0], [_c6.STEPS[7][1][0], S("""
C: Fourteen years, and then what?
A: Then the money would be gone, in the stress case. That case assumes Frank needs full-time nursing starting now and costs rise eight percent a year. It's a bad realistic case, not the expected one.
C: Should I be planning for the worst one?
A: For big decisions you can't undo, like lending a large sum or buying into a retirement community, I'd use the stress case to judge whether you can afford the risk. For monthly spending I'd use the expected case.
C: That makes sense. So for Brian's money, the stress case.
A: Yes. I'm not telling you what to do about Brian, only which yardstick to use.
C: All right. The stress case for the big ones.
""", log=[("decision", "to use the full-time-care stress case, and not the expected case, to judge large decisions such as lending money or buying into a retirement community")],
   tags=("confirmed_decision", "scenario_result"))], None)

REPLACE[(5, 14)] = ("Required distribution and the gift to church", [
    S("""
A: Dennis Hall has confirmed the numbers for the church gift. Your required distribution this year is about thirty-one thousand. A qualified charitable distribution of six thousand counts toward it, and it isn't added to your taxable income.
C: So I don't pay tax on the six thousand? And the church gets all of it?
A: Yes, if it goes straight from the IRA to the church. If you took the money out yourself and then wrote the church a check, it would be taxable.
C: Then do it the right way. Send the six thousand to the church from the IRA. The treasurer needs it by the end of June for their budget.
A: Confirming your instruction: a six-thousand-dollar direct gift from the IRA to your church, counted toward this year's required distribution. I'll arrange it with the custodian.
C: Yes, that's it.
""", log=[("decision", "to send six thousand dollars directly from your IRA to your church as a qualified charitable distribution, counted toward this year's required distribution"),
          ("a_action", "arrange the direct gift to your church with the IRA custodian")],
      tags=("confirmed_decision", "explicit_instruction", "tax")),
    G("portfolio"),
], None)

REPLACE[(5, 17)] = ("A note about Brian, and the markets", [
    S("""
A: Before the markets, I need to check a note. I have it recorded that you'd help Brian with a few hundred dollars toward his equipment. Is that where you landed?
C: No, no. That was only something I thought I might do. I haven't decided. I called him the weekend after we talked and told him no on the loan, and he went quiet. He didn't ask for anything else, and I didn't offer.
A: I see. Thank you. So helping with equipment is still only a thought, and nothing has been offered.
C: Right. I feel bad about it.
A: That's understandable. You made the decision because of Frank's care, and a small gift isn't ruled out for later.
C: Maybe after things settle. Not now.
""", log=[("correction", "my note that you would help Brian with a few hundred dollars toward equipment was too strong; it was only a thought and nothing has been offered")],
      tags=("correction", "misunderstanding", "family_context")),
    G("market"),
], None)

REPLACE[(5, 20)] = ("Care costs update", [
    S("""
A: I'm updating your care costs. My model still has the aide four days a week, about fifty-two hundred a month. Is that still right?
C: No, it's five days now. Since Frank's fall in March the agency moved us up, and the invoice for May was sixty-four hundred.
A: Thank you. Then my figure was out of date. That's twelve hundred more a month, and it puts your spending at about ten thousand one hundred.
C: I should have told you sooner. I get the bills and I just pay them.
A: Don't apologise, that's what this meeting is for. It also means the portfolio covers a bigger gap, about thirty-eight hundred a month and not twenty-six hundred.
C: Is that a problem?
A: Not an emergency. The portfolio supports it, but the margin is thinner than in February, and the stress case is shorter, closer to twelve years.
C: Twelve frightens me.
A: It's the worst case, as we said. It's one more reason to keep a big commitment like Maplewood on hold until we know more.
C: That makes sense. I'd rather wait on that.
A: One loose end from January. We said we'd come back to what Frank's pension pays you if he dies first, because it affects this stress case. Have you had a chance to look at the booklet?
C: I haven't. I know I should. Every time I pick it up there's something else.
A: Then I'll keep it recorded as unconfirmed, and in the stress case I'll use your own estimate of about half.
""", log=[("correction", "my model's assumption of the aide four days a week at about fifty-two hundred dollars a month was out of date; since Frank's fall the aide comes five days, at about sixty-four hundred a month"),
          ("plan", "monthly spending of about ten thousand one hundred dollars, with the portfolio covering about thirty-eight hundred"),
          ("plan", "using your own estimate of about half for the survivor income from Frank's pension in the stress case, until you have checked the booklet")],
      setF={"spend": 10_100}, tags=("correction", "outdated_assumption", "family_context", "scenario_result")),
    G("liquidity"),
], None)

REPLACE[(5, 23)] = ("The church gift and taking your required distribution", [
    S("""
C: The church treasurer called to say the gift arrived. Thank you for sorting that out.
A: The custodian sent it directly, so it counts toward your required distribution as we intended.
C: Good. Now the rest of the amount. Do I have to take it all at once?
A: No. You must take the full amount by December thirty-first, but you can take it in one go or in installments. Many people take it monthly.
C: I don't like big lumps. With the aide costs up, I'd rather it came monthly, like a pension.
A: That works. You can also have federal tax withheld each month, so there's no surprise in April.
C: Withhold whatever Dennis said. I don't understand it.
A: Dennis said twelve percent. Is that what you want?
C: If he said it, yes.
A: Then the remaining required amount, about twenty-five thousand, goes out monthly from July through December with twelve percent withheld. Is that your instruction?
C: Yes, that's what I want.
""", log=[("decision", "to take the remaining required distribution of about twenty-five thousand dollars in monthly installments from July through December, with twelve percent withheld for federal tax")],
      tags=("confirmed_decision", "explicit_instruction", "tax", "follow_up")),
], None)

REPLACE[(5, 24)] = ("What a maturing bond means for the ladder", [
    S("""
A: One of the short-term bonds in your ladder matures next month. I wanted to explain what that means before it happens.
C: Matures. That sounds like it ends. Do I lose it?
A: No, the opposite. The bond pays back its face value in cash, about fifty thousand dollars. The question is what to do with the cash.
C: Spend it?
A: You could, but its job is to cover your withdrawals over the next few years. The usual approach is to buy a new bond at the far end of the ladder, so there's always about five years of cover ahead.
C: Is that safe?
A: As safe as short Treasuries, yes. The risk is that rates are lower than when you bought the old one, so the new bond pays a little less.
C: That's not so bad.
A: Alternatively, since you've gone up to thirty percent in stocks, we could use this to add to the stock side. That's the more aggressive option, and I wouldn't suggest it after the spring you've had.
C: No. Put it back in the ladder. Do the safe thing.
""", log=[("decision", "to reinvest the maturing bond at the far end of the short-term ladder instead of adding to stocks")],
      tags=("confirmed_decision", "concept_explained", "rationale")),
], None)

APPEND[(5, 25)] = [S("""
A: Two more things before I let you go. Frank's aide: is five days enough, or is the agency talking about more?
C: Five is enough for now. The therapist says if he gets worse at night we'd need someone overnight, and I can't think about that yet.
A: Then I'll leave it, and we have the stress case for that. And Maplewood: do you want it off the list, or kept as a someday?
C: Someday. Frank wants to stay home, but I don't know what I'll want if things change.
A: Understood. I'll keep it as something to revisit if his needs change.
""", log=[("tentative", "moving to a retirement community such as Maplewood Commons at some point, if Frank's needs change")],
   tags=("uncertain_intention", "family_context", "unresolved"))]
