"""Revision 2, CLIENT-003 Sofia Alvarez (fast, anxious, new to insurance and estate terms).
Thin meetings 9, 12, 23, 24, 25; use-of-funds correction in step 23."""
from .beats import Check, G, S
from .revisions import APPEND, REPLACE

REPLACE[(2, 9)] = ("Last check-in before the baby and keeping the employer match", [
    S("""
A: How are you feeling? Any signs yet?
C: Not yet, but the doctor says any day now. I'm at thirty-nine weeks and I keep refreshing everything, including my account, which is the worst idea, because the market's been awful.
A: Let's put the account down for a minute. Is everything ready at work?
C: My handover doc is done. Leave starts the day after the baby, whenever that is. I get twelve weeks at sixty percent pay, and I'm trying not to panic about the money.
A: What worries you most?
C: That I'll stop contributing to my 401(k) and lose the match. Should I pause everything during leave?
A: Not the match. That's free money, so I'd keep contributing the six percent that earns it, even at sixty percent pay. The extra brokerage contributions are already redirected to cash.
C: Okay. So keep the six percent going. Yes, keep it.
A: Good. You also mentioned your manager might let you move to four days after leave?
C: Maybe. She hinted. I'd lose about twenty percent of my salary, and honestly I might want that.
A: I'd keep it as an idea until it's in writing. I won't plan around it.
C: Agreed. I'm not asking until I'm back.
""", log=[("decision", "to keep contributing the six percent that earns the full employer match through parental leave"),
          ("tentative", "a four-day work week after leave, which would cut your salary by about twenty percent")],
      tags=("confirmed_decision", "uncertain_intention", "life_event")),
    G("liquidity"),
], None)

REPLACE[(2, 12)] = ("Insurance estimate and how much cover is enough", [
    Check("s_ins", "done", "Based on your income and Mateo's, I'd suggest roughly one and a half million in twenty-year term cover for you and about a million for Mateo, and I've sent the broker referral."),
    S("""
C: One and a half million? That feels like a lot. Mateo thinks a million is plenty.
A: Fair challenge. I get there by adding up what the money would have to do if you died: replace your income while Mateo raises Lucia, clear debts, and keep the house plan alive.
C: Wait, if I died would the student loans just disappear?
A: Federal loans are generally discharged at death. Private ones may not be. Which are yours?
C: Federal. And what's an elimination period? The broker used that on the disability quote and I nodded like I knew.
A: It's the waiting time before disability benefits begin, say ninety days. A longer wait means a cheaper premium. You have twelve weeks of leave pay and cash behind you, so ninety days is probably fine.
C: Okay, that I get. But I'm stuck on the amount. Can we decide next time? I want to talk to Mateo properly.
A: Of course. It's better decided together.
""", log=[("tentative", "whether one and a half million dollars of term life cover is more than you need")],
      tags=("uncertain_intention", "concept_explained", "procedure")),
], None)

REPLACE[(2, 23)] = ("The house fund and the childcare budget", [
    S("""
A: One thing in my notes needs checking. When we rebuilt your budget around childcare, I treated the forty thousand in the house fund as a cushion for the gap. Was that right?
C: No. Please don't. That forty thousand is house money. We postponed the search, we didn't cancel it. If childcare is a squeeze I'd rather cut my brokerage contributions than touch the house fund.
A: Understood. That was my assumption, not what you told me. You said in May it was for the house, and I'll correct my note so it says so.
C: Because if we raid it for diapers, we'll never get there.
A: Fair. Then how do we cover the childcare gap?
C: My brokerage contributions were going to cash until we reached fifty thousand, and we're there. So I can cut them by about eight hundred a month and not feel it.
A: That works, and I'd keep the 401(k) match untouched.
C: Yes, match stays. House fund stays. Trim the brokerage money.
""", log=[("correction", "my note treating the forty thousand dollars in the house fund as a childcare cushion was wrong; it remains house money"),
          ("decision", "to cover the childcare gap by cutting brokerage contributions by about eight hundred dollars a month and leave the house fund and the 401(k) match untouched")],
      tags=("correction", "misunderstanding", "use_of_funds", "confirmed_decision")),
    G("liquidity"),
], None)

REPLACE[(2, 24)] = ("Mid-year checkpoint on the retirement allocation", [
    S("""
C: Markets are better now. Part of me thinks I overreacted and should go back to ninety percent.
A: Let's pull that apart. What's different now, the market or you?
C: The market. I still hate watching it drop with Lucia asleep on me, but I read that everyone who sold in March missed the bounce.
A: Many did. But we moved because the swings were hard for you to live with. Has that part changed?
C: ...No. It was harder than I let myself say.
A: Then I'd keep seventy-five. It's not a punishment. It's the level you chose with a clear head.
C: Okay. Keep seventy-five. I reserve the right to complain if there's a big rally.
A: Granted. Let's look at the house and the college account again in the autumn.
C: Yes. And the will, which I know I keep saying.
""", log=[("decision", "to keep retirement equities at 75 percent rather than go back to 90 percent after the recovery, because the March swings were harder to live with than you first admitted")],
      tags=("confirmed_decision", "rationale", "changing_preference")),
    G("portfolio"),
], None)

APPEND[(2, 25)] = [S("""
A: Before we finish: in March you said you might ask for a four-day week. Is that still on the table?
C: I'm not asking. We looked at the childcare quote and the budget works at five days. I'd rather keep the salary until the house is settled.
A: So that's a no?
C: A not now. Maybe next year.
A: I'll record it as not now.
""", log=[("deferred", "asking for a four-day work week until next year")], tags=("deferred_decision", "history_recall"))]
