"""Revision 2, CLIENT-004 Grace Adeyemi (hesitant, needs plain explanations; advisor analytical).
Thin meetings 9, 18, 21, 22, 23, 24, 25; two corrections (pension figure in 9, intended use of funds in 21).
Also fixes a v1 timeline slip: the first investing step is June 1, as agreed in May."""
from .beats import Check, G, S
from .revisions import APPEND, REPLACE

APPEND[(3, 9)] = [S("""
A: The annuity discussion sends me back to something I told you in February. I described the pension survivor payment as about half of Samuel's pension. I have now read the actual form, and I was imprecise.
C: Oh. Is it less?
A: More. The form says sixty percent for life, or a lump-sum alternative that we haven't evaluated yet. I had read the company's summary too loosely.
C: Sixty. Is that, I mean, is that good?
A: It is a real income, guaranteed by the pension plan. It matters for your annuity question, because it already provides some of the guaranteed income the salesman was offering.
C: Oh. So I might not need the annuity.
A: I'm not saying that yet. But it changes the comparison. What I told you in February stays in my notes as I said it; I'm adding today's figure next to it.
""", log=[("correction", "my February summary that the survivor payment was about half of Samuel's pension was imprecise; the form says sixty percent for life, with a lump-sum alternative not yet evaluated")],
   tags=("correction", "outdated_assumption", "pension"))]

REPLACE[(3, 18)] = ("Nerves before the first investing step", [
    S("""
C: Priya, the first step is on June first, isn't it? I keep thinking I should call it off.
A: It is on June first. What's making you want to call it off?
C: It's silly. Every time I read the paper there's something. And it's Samuel's money, in a way. If I lose some of it...
A: It isn't silly. Let me be concrete. The first step is one sixth of the amount heading into stocks, about twenty-four thousand dollars. If that fell twenty percent, you'd be down about forty-eight hundred.
C: Forty-eight hundred. I could bear that. It's the idea of it.
A: What would make it feel safer?
C: Knowing I can stop. Could I stop after the first one, if it went badly?
A: Yes. Each step is a separate instruction. You can pause before any step and nothing is locked in.
C: Then... go ahead as planned. I'll be nervous, but go ahead with the first one.
""", log=[("decision", "to keep the plan as agreed, with the first step into stocks on June 1 and the right to pause before any later step")],
      tags=("confirmed_decision", "changing_preference", "rationale")),
    G("market"),
], None)

REPLACE[(3, 21)] = ("Reading the first statement after investing", [
    S("""
C: I got a statement and there's a column of numbers I don't understand. There's a line that says purchases. Is that the first step?
A: Yes. On June first we bought about twenty-four thousand dollars of the global equity fund. It shows as a purchase, and your money-market balance falls by the same amount.
C: And it already went down, the little number's red.
A: It's down about one percent since Monday, a few hundred dollars. That's ordinary noise over a few days, and it tells us nothing.
C: Oh. So I shouldn't look every day.
A: Once a week at most. Now let me ask about how I've recorded your money. In my notes the insurance payout is labelled as reserved for paying off the mortgage. Is that right?
C: No... I mean, I said in February I wished the house were paid off. But then we decided to keep the mortgage. The money isn't for that.
A: I see. I treated a wish as a purpose. What is the money for?
C: Folake's tuition. Part of it so I feel safe. And some for Tobi, only as a gift, up to fifteen. I don't have a neat word for it.
A: Then I'll relabel it: Folake's tuition bucket, your safety reserve, the gift allowance, and the remainder invested gradually. The February note stays in the file as written, with today's correction beside it.
""", log=[("correction", "my note labelling the insurance payout as reserved for the mortgage was wrong; your February comment about wanting the house paid off was a wish, and the money is for Folake's tuition, your safety reserve and the gift allowance for Tobi")],
      set={"equity": 17}, tags=("correction", "misunderstanding", "use_of_funds", "explainer")),
], None)

REPLACE[(3, 22)] = ("Beneficiary forms and Folake's inheritance", [
    Check("g_benef", "done", "I settled it. Tobi and Folake will be equal beneficiaries and I'm sending the forms back."),
    S("""
C: I did decide. But then I kept thinking about Folake. She's nineteen. If something happened to me next year, she'd get half of everything at once. I'm not sure she could manage it.
A: That's a legitimate worry. If Folake inherits at nineteen, she controls the money outright. Some people leave a young beneficiary's share in a trust until a chosen age.
C: A trust. That sounds expensive. And complicated.
A: It adds cost and paperwork, and it needs an estate attorney, which I'm not. I can't tell you it's necessary, only that it's one way to address your worry.
C: What would you do?
A: I'd raise it with an attorney when you review your own will, which is still on your list. In the meantime the beneficiary forms stand as they are.
C: Then I'll leave the forms for now. I don't want to decide this today.
""", log=[("tentative", "holding Folake's share in a trust until she is older")],
      tags=("uncertain_intention", "family_context", "concept_explained")),
], None)

REPLACE[(3, 23)] = ("Second thoughts after a down day", [
    S("""
C: The market dropped on Monday, and I looked. I know, you said once a week. The little number was red again. Do we stop?
A: What did you see?
C: Down about twelve hundred dollars on the stocks. That's more than the few hundred it showed on <<ago:5>>.
A: And what did we say a bad outcome might look like?
C: Forty-eight hundred. So it's within. But it isn't the number, it's...
A: The feeling that it's your money going. We agreed you could pause. Do you want to?
C: Part of me does. But if I stop at the first dip, why did I start?
A: That's the right question. There's no correct answer, only the one you can live with. The next step is scheduled for July first.
C: Keep going. The second step on July first. But if it's down by a lot by then, I'd like you to call me first.
A: Agreed. If the stock holding is down more than ten percent since the first purchase, I'll call you before I place the second.
""", log=[("decision", "to continue with the second investing step on July 1"),
          ("a_action", "call you before the July 1 purchase if the stock holding is down more than ten percent since the first purchase")],
      tags=("confirmed_decision", "changing_preference", "advisor_commitment")),
], None)

REPLACE[(3, 24)] = ("Whether to keep the house", [
    S("""
C: Priya, I've been thinking about the house. It's too big. Samuel and I bought it for the children. Tobi will go, Folake will go, and it will be me.
A: Are you thinking of selling?
C: At night I think so. In the morning I can't imagine it. Samuel painted every room.
A: Both feelings are legitimate. A practical question: what does the house cost you each year beyond the mortgage?
C: Property tax is about nine thousand, insurance two, and the roof, the gutters... there's always something.
A: So fifteen thousand or more. But I'd rather you not decide this in the first year. In January you said no large irreversible decisions for twelve months. That runs to November.
C: I remember. I'm not deciding. I just wanted to say it out loud.
A: Then I'll record it as a thought. When you're ready, we can compare the cost of staying with the proceeds of selling, a smaller place, and what that does to your retirement date.
C: And Folake still needs a home to come back to.
A: That's a good reason to wait as well.
""", log=[("tentative", "selling the house and downsizing in a few years"), ("deferred", "any decision about selling the house until at least November, a year after Samuel's death")],
      tags=("uncertain_intention", "deferred_decision", "rationale", "family_context")),
], None)

APPEND[(3, 25)] = [S("""
A: Looking ahead, one family matter is still open: Tobi. How is he doing?
C: He sent me the business plan. Eleven pages. I read it twice. I'm not sure what to think. It looks organised?
A: Is the fifteen thousand gift still what you offered?
C: That's the limit. But I haven't said yes to the plan itself. I want to ask him some questions first.
A: What questions?
C: Who his customers are. What happens if it doesn't work. He's confident, and I'm his mother.
""", log=[("tentative", "giving Tobi the fifteen thousand dollar gift, once you have asked him about his customers and what happens if the business fails")],
   tags=("uncertain_intention", "family_context", "unresolved"))]
