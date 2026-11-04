"""Revision 2, CLIENT-001 Kenji Tanaka (decisive, analytical).  Thin meeting 4; correction in step 20; review in step 25."""
from .beats import Check, G, S
from .revisions import APPEND, REPLACE

REPLACE[(0, 4)] = ("Cash reserve, the mortgage and what a layoff would mean", [
    Check("k_defcomp", "done", "Yes, I finally got the statement from the administrator. The balance is around six hundred forty thousand, and it lets me choose between a lump sum and installments."),
    S("""
A: Since the reorganisation news, I'd like to test your cash position against a bad outcome and not just the likely one. If your role were eliminated, how long could you cover your spending without selling investments?
C: I don't expect that to happen. Nobody has told me anything.
A: I hope it doesn't. But a plan that only covers the outcome you expect hasn't been tested. You hold about a hundred and fifty thousand in cash. What would Northgate pay a senior executive in severance?
C: I don't know the number. There's a policy somewhere. Six months of base, maybe. I'm not going to ask HR. If I start asking about severance, people notice.
A: That's a reasonable instinct. I'll use a conservative working assumption of six months of base salary and replace it if you ever learn the real terms. On that basis, cash plus severance would cover well over a year of your current spending, without touching the portfolio.
C: So the cash is fine.
A: For that scenario, yes. What I'd watch is bonus and vesting income, because both would stop on the same day. One question people in your position often ask is whether to pay down the mortgage as a precaution.
C: Mariko asked exactly that. She thinks we should pay it down while we have the cash, as insurance against a layoff.
A: I'd argue the opposite. Paying it down turns cash into home equity, which you can't spend if the paycheck stops. Keeping the cash is the better insurance, and the loan rate is low.
C: That's a fair argument. We keep the mortgage as it is and keep the cash. I'll tell Mariko.
""", log=[("plan", "a working assumption of six months of base salary as severance for liquidity planning, to be replaced if the real terms become known"),
          ("decision", "to keep the mortgage as it is and hold the cash as a cushion, rather than pay the mortgage down early")],
      tags=("confirmed_decision", "rationale", "scenario_discussion")),
    G("liquidity"),
], None)

APPEND[(0, 20)] = [S("""
A: Before we finish, I want to check a note. The fourteen thousand we set aside for Ren's semester in Seoul: in my file I have it recorded as extra tuition. Is that right?
C: Not quite. He pays his normal tuition to his own university either way, and that's already in your numbers. The fourteen is the program fee, flights and housing on top of that.
A: Thank you. Then I had the purpose wrong, and I'd have double-counted tuition in your cash flow. I'll correct the note so it says the money covers the cost of being abroad. What you told me in May stays on record as you said it.
C: And if he doesn't get in, it goes back into the reserve, like you said.
A: Yes, that part doesn't change.
""", log=[("correction", "my note that the fourteen thousand dollars for Ren was extra tuition was wrong; it covers the Seoul program fee, flights and housing, and his tuition is already in the cash-flow plan")],
   tags=("correction", "misunderstanding", "use_of_funds"))]

APPEND[(0, 25)] = [S("""
A: One topic before we close, because it's the main open question about family spending. Has Yuki decided between the program and the job?
C: Not yet. She has until the end of July to confirm the place. The nonprofit job got through to the final round, so now she's genuinely torn. I told her the thirty thousand is there if she goes, and I'm not pushing either way.
A: And how would you feel if she took the job?
C: Honestly, fine. Mariko would be disappointed. I'd be a little relieved on cash flow, and I'd say that to Yuki. But it's her call.
A: Then I'll record that the thirty-thousand-dollar cap stands if she goes, and that her choice is still open.
""", log=[("tentative", "whether Yuki takes up her graduate place or the nonprofit job, with the thirty thousand dollar a year cap standing if she goes")],
   tags=("uncertain_intention", "family_context", "unresolved"))]
