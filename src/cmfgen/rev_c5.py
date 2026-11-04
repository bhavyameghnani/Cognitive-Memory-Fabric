"""Revision 2, CLIENT-005 Lucas Moreau (informal, challenges the advisor, vague about numbers until asked).
Thin meetings 4, 8, 14, 16, 20, 21, 25."""
from .beats import Check, G, S
from .revisions import APPEND, REPLACE

REPLACE[(4, 4)] = ("How to set the 2026 estimated tax payments", [
    S("""
C: Quarterly taxes. I just pay what feels right, don't I? Last year I sent some round number. Thirty-five, I think. I don't see why I need a formula.
A: Because feels right is how people end up with a penalty and a surprise bill. How much did you earn last year?
C: No idea exactly. Lumpy. The bookkeeper's still finishing the P&L.
A: Then we can't be precise today. There's a safe harbor: if you pay at least a hundred and ten percent of last year's total tax, in four equal installments, there's no underpayment penalty however big this year turns out.
C: And if this year's bad I'm overpaying the IRS for a loan I never asked for.
A: True, it's a cash cost. The alternative is estimating this year's income, which with your swings is a guess. A wrong guess costs a penalty and maybe a cash crunch in April.
C: I hate paying more than I owe.
A: I'd rather you overpay and get a refund than guess low. Your cash reserve is thin, which makes a surprise bill more dangerous.
C: Fine. Safe harbor, equal installments. I'm not happy about it.
A: Noted. We'll size it from last year's return, and the P&L will help.
""", log=[("decision", "to base each quarterly estimated tax payment on the safe-harbor amount from last year's return, and not on a guess about this year's income")],
      tags=("confirmed_decision", "advisor_disagreement", "rationale", "tax")),
    G("liquidity"),
], None)

REPLACE[(4, 8)] = ("Tax numbers and the April shortfall", [
    Check("l_pl", "done", "I got the profit and loss from the bookkeeper. Net income was about one hundred ninety thousand, and I paid about thirty-five thousand in estimates."),
    S("""
A: So net was about one-ninety and you've paid thirty-five. What's the rough tax on one-ninety, self-employed?
C: I don't know. Fifty? I figured thirty-five was most of it.
A: Self-employment tax alone is around twenty-seven thousand, plus federal income tax, plus state. I'd expect fifty-five to sixty all in. So you're short by twenty to twenty-five, due in April.
C: What? Nobody told me.
A: The estimates were your own guess, and we're only just seeing the P&L. I'm telling you now, seven weeks out.
C: Where do I find twenty-five grand? The cash is operating money.
A: Options: pay from the reserve and rebuild it, sell some Vantora, or a bit of both. I'd rather not sell Vantora to pay taxes while it's down.
C: Not selling. I'll cover it from invoices. I'm owed about thirty.
A: Late invoices aren't cash yet. I'd want a plan B.
C: Plan B is the cash account. We'll see in April.
""", log=[("tentative", "covering a tax shortfall of about twenty to twenty-five thousand dollars in April from outstanding invoices, with the cash account as a fallback and without selling Vantora")],
      tags=("uncertain_intention", "new_information", "tax", "advisor_caution")),
    G("market"),
], None)

REPLACE[(4, 14)] = ("What to do with the secondary-sale proceeds", [
    Check("l_sale", "done", "I signed it Monday, and the company confirmed receiving it."),
    S("""
A: Before the proceeds: the April tax payment was due yesterday. How did you cover the shortfall?
C: Invoices came in, mostly. Twenty-two of the twenty-five thousand. I took the rest out of the cash account.
A: So the plan B was needed, but only a little. I'll record the shortfall as paid. Now, the secondary closed. Seventy-five thousand gross. What happens to it is what I want to settle. Founder shares have a very low basis, so most of it is taxable gain. Plan on about a quarter going to tax.
C: A quarter? So I clear fifty-six?
A: Roughly. After setting the tax aside, the reserve is at about eight months, and the target you set in March was nine.
C: Then the rest goes into Vantora. It's down. Lottery-ticket price.
A: I'd push back on that. This would put more of your liquid money in two things in the same sector, your company and that stock.
C: You sound like my accountant.
A: I'm being your advisor. You said in March the priority was the reserve.
C: Okay. Okay. Reserve first. And the leftover?
A: Index fund, not Vantora.
C: Fine. Fill the reserve to nine months, tax set-aside off the top, whatever's left goes in the index fund.
""", log=[("plan", "the April tax shortfall paid, with about twenty-two thousand dollars from invoices and the rest from the cash account"),
          ("decision", "to set aside about a quarter of the secondary-sale proceeds for tax, fill the cash reserve to nine months, and put anything left into the index fund and not into Vantora")],
      tags=("confirmed_decision", "advisor_disagreement", "rationale", "tax")),
], None)

REPLACE[(4, 16)] = ("Moving to Lisbon?", [
    S("""
C: Camille and I have been talking about Lisbon again. Maybe in two years.
A: Is that a plan, or a daydream?
C: Somewhere between. Her sister is there. Camille could work remotely and I could run Moreau Labs from anywhere. Amélie is six, so it's now or when she's a teenager.
A: When you say anywhere, where are your customers?
C: US mostly. Some Canada.
A: Then a few things before I'd call it real. You'd still be taxed by the U.S. wherever you live. The company is a U.S. entity. And Portuguese residency has its own tax rules that interact with ours.
C: I know. I'm not asking you to solve that.
A: I'm saying the move wouldn't simplify taxes, it would add a second system. What would the budget look like?
C: Rent's less, the rest is the same. Cheaper, I guess. Maybe.
A: Maybe is fine at this stage. What would turn it into a plan?
C: Camille's job saying yes to remote, and Moreau Labs not needing me in the room. Neither's true today.
A: Then I'll record it as a possibility, and I won't build it into your numbers.
""", log=[("tentative", "relocating to Lisbon in about two years, which depends on Camille's employer allowing remote work and on Moreau Labs running without you on site")],
      tags=("uncertain_intention", "family_context", "rationale")),
], None)

REPLACE[(4, 20)] = ("Second-quarter taxes and a lower income", [
    S("""
C: The Q2 estimate is due in two weeks. Revenue's way down since the customer left. Why am I paying the same as Q1?
A: Because the safe-harbor amount is based on last year's tax, not this year's income.
C: That's my point. Last year was good, this year's a mess. I'm overpaying.
A: Probably, yes. There's another method, annualized installments, that lets you pay based on income earned so far. It can cut a payment, but it's fiddly and your accountant would have to prepare the form.
C: How much would it save?
A: I can't say without your year-to-date numbers. If income is thirty percent below last year, maybe twenty or thirty percent off each payment.
C: That's real money right now.
A: It is. But if income recovers in the second half, you could owe a catch-up payment, and the reserve is still being built.
C: I don't want a catch-up. Fine, pay the safe harbor again. But if Q3 looks the same, we do the other thing.
A: Agreed. Q2 at the safe-harbor amount, and the annualized method reconsidered for Q3.
""", log=[("decision", "to pay the second-quarter estimate at the safe-harbor amount"),
          ("deferred", "the annualized-installment method for estimated taxes until the third quarter's income is clearer")],
      tags=("confirmed_decision", "deferred_decision", "tax", "rationale")),
], None)

REPLACE[(4, 21)] = ("Whether to finish selling Vantora", [
    S("""
C: Vantora's up eight percent from the low. We've done two of the three sales, and I'm already annoyed. Stop selling.
A: You decided on three steps. What's your reasoning now?
C: It rebounded. If I'd waited...
A: If you'd waited, it might have kept falling. You can't know. The reason for selling wasn't the price. It was concentration: your company and that stock in the same sector. That hasn't changed.
C: That is, annoyingly, what I said in April.
A: It is. I'm not saying you must finish. I'm saying don't change the plan because of two good weeks.
C: Finish the third sale. But I'm keeping the rest. Sixty percent stays, and I don't want to talk about trimming again until after Q3 earnings.
A: That's your call. I'll record that you finish the plan and leave the remaining position alone until after the third-quarter earnings.
""", log=[("decision", "to complete the third Vantora sale as planned and keep the remaining position"),
          ("deferred", "any further reduction of the Vantora position until after third-quarter earnings")],
      tags=("confirmed_decision", "deferred_decision", "rationale", "advisor_recommendation")),
], None)

APPEND[(4, 25)] = [S("""
C: One thing I never settled: the disability insurance. The broker told me three weeks, as I said when we spoke on <<ago:11>>, so there are no quotes yet.
A: That puts them around early July. Do you still want the cover?
C: I want it. I just keep not following up with him.
A: I'd check in before then, so he has everything he needs from you. Of everything we've discussed, a gap in income protection is the one that can really hurt you.
C: Yeah. I'll push him.
A: Then I'll record it as pending: you want the cover, the quotes aren't back yet, and nothing is bought until you've seen them.
C: Right. Nothing bought, nothing signed.
""", log=[("c_action", "check in with your broker so he has what he needs for the disability insurance quotes"),
          ("tentative", "buying disability cover once the broker's quotes arrive")],
   tags=("follow_up", "unresolved", "pending_follow_up", "advisor_recommendation"))]
