"""Revision 2, CLIENT-002 Rebecca Holloway (decisive, blunt).  Thin meetings 2, 6, 12, 20, 21, 25; correction in step 24."""
from .beats import Check, G, S
from .revisions import APPEND, REPLACE

REPLACE[(1, 2)] = ("Whether the personal portfolio should offset the business risk", [
    S("""
A: Your personal portfolio is about two point one million. The business, at today's rough numbers, is worth several times that. So the first question is whether this portfolio should be run like a normal one, or as a counterweight to the company.
C: I don't follow. Why does it matter next to the company?
A: Because if the business has a bad year, your whole net worth drops at once. If the portfolio is also sixty percent stocks, it adds to that. Some owners in your spot hold safer assets personally to balance it.
C: I'm in stocks because the business doesn't give me anything I can count on. And I'm not changing course on a hunch. Is there actually a link between my plant and the stock market?
A: A recession would hit both. That's the link.
C: So you want me to sell stocks.
A: I'd lean toward a more cautious mix. But you might sell the company, and that changes everything. So, do you want to adjust now, or wait?
C: Wait. If I sell, the whole picture changes. Don't touch it until I know.
A: Understood. I'll note that I raised a more cautious mix, and you chose to hold until you know whether you're selling.
""", log=[("deferred", "any change to the personal portfolio's 60 percent equity level until it is clear whether you will sell the company")],
      tags=("deferred_decision", "advisor_recommendation", "rationale")),
    G("portfolio"),
], None)

REPLACE[(1, 6)] = ("Ethan's college costs and the sale", [
    S("""
C: Ethan's list is down to five schools. Three private, two state. The private ones run about eighty thousand a year all in.
A: That's over three hundred thousand across four years. How are you thinking of paying?
C: David started a college account when Ethan was born. There's about eighty-five in it. The rest comes out of income. I don't want Ethan choosing a school on price.
A: Understood. One thing to know: if you sell the company, the proceeds could weigh heavily on financial aid forms, depending on timing.
C: We won't get aid either way. We make too much.
A: Probably true. I mention it because which years the schools look at can matter, and I'd rather you hear it now than in a form in October.
C: Do I need to time the sale around Ethan's applications?
A: No. Don't run a multi-million-dollar decision around a form. I'd just tell you if a closing date firms up, so we can look at it.
C: Fine. And let's not decide how to cover the gap now. Ethan hasn't been accepted anywhere.
""", log=[("tentative", "how to fund Ethan's college costs beyond the existing college account")],
      tags=("uncertain_intention", "family_context")),
    G("market"),
], None)

REPLACE[(1, 12)] = ("Walk-away price and the risk of a lower offer", [
    S("""
C: Daniel, I've been lying awake over one thing. What if Lakeshore finds something in due diligence and cuts the price?
A: It happens often enough that you should plan for it. Buyers sometimes re-trade after diligence. The usual triggers are customer concentration, equipment condition, and the earn-out numbers.
C: Our biggest customer is nineteen percent of revenue. That's no secret. They know.
A: Then it's mostly priced in. The question is the number where you'd walk away.
C: Twenty-two. Below twenty-two I walk.
A: Is that firm, or is it where you are today?
C: It's where I am today. Ask me again when I've seen the paperwork.
A: Fair. I'll record it as your current floor, not a decision. Let me test it: at twenty-two instead of twenty-four, cash at closing falls by about one point two million after tax, to roughly twelve point three. Does that still do the job?
C: It covers my mother and the reserve, yes. It just wouldn't feel good.
A: Which is worth knowing. The number that feels good and the number that works are different numbers.
""", log=[("tentative", "walking away from the sale if the price falls below about twenty-two million dollars")],
      tags=("uncertain_intention", "advisor_caution", "scenario_discussion")),
], None)

REPLACE[(1, 20)] = ("Staff announcement and key-employee retention", [
    S("""
C: Lakeshore wants to announce to the staff next month. I feel sick about it. What if they leave?
A: That's a natural reaction. Buyers expect some turnover, and retention pools help. How many people actually matter to the operation?
C: Luis, the plant manager, and six supervisors. If they go, the customers go.
A: Then ask for a retention pool for those seven. How much were you thinking?
C: A million, a million two. Out of the price. I'm not taking it from the staff to pay the staff.
A: Out of your proceeds, you mean. After tax that's roughly seven hundred thousand less in your pocket. Can you live with that?
C: Dorothy's reserve is covered, so probably yes. I haven't asked Lakeshore yet.
A: Then it's an idea, not a decision. They may offer to fund part of it. I'd raise it with your lawyer before the announcement, so it isn't a surprise to anyone.
C: Fine. Not decided.
A: And how are you sleeping?
C: Badly. David's been good about it.
""", log=[("tentative", "funding a retention pool of up to roughly one point two million dollars for Luis and six supervisors out of the sale proceeds")],
      tags=("uncertain_intention", "personal_stress", "rationale")),
], None)

REPLACE[(1, 21)] = ("Signing timeline and cash needs", [
    Check("r_returns", "done", "The accountant finally released the 2025 return and I uploaded it."),
    S("""
A: Let's talk timing. When's signing?
C: Late June, they say. Closing in September, after the regulatory waiting period. Not that I believe dates anymore.
A: Between now and closing your salary continues, but you'll have costs. What are they?
C: The lawyer wants a forty-thousand retainer on top of what I've paid. Mei Lin wants fifteen up front.
A: Fifty-five thousand out of your own pocket before the deal pays for itself. Where's it coming from?
C: I assumed I'd sell something.
A: You don't need to. You hold two hundred twenty thousand in cash. I'd pay from cash and not sell investments right after a rough spring.
C: Then do it from cash. Pay the retainer and Mei Lin's advance from the cash account.
A: Confirming your instruction: both from cash, nothing from investments. And if signing slips?
C: Then it slips. The retainer's spent either way.
""", log=[("decision", "to pay the lawyer's retainer and the accountant's advance, about fifty-five thousand dollars, from cash and not from investments")],
      set={"cash": 165_000}, tags=("confirmed_decision", "explicit_instruction", "rationale")),
], None)

APPEND[(1, 24)] = [S("""
A: One thing in the planning I did when we looked at life after closing. I told you your income would stop at closing, with no salary, and I built the spending account on that. Is that still right?
C: No, and I should have said so. The draft keeps me on as a paid consultant for twelve months after closing, at my current salary. Lakeshore wants me for the handover. It's in section six.
A: That changes the model. Salary continues for a year, so the portfolio draw doesn't start until after that, and the spending account is oversized.
C: Right. Escrow is the part still being argued over. Section six isn't.
A: Thank you. I'll correct the planning note. My earlier statement was an assumption of mine and not something you told me, so I'll mark it as superseded without deleting it.
""", log=[("correction", "my assumption that your salary would stop at closing was wrong; the draft agreement keeps you on as a paid consultant for twelve months after closing")],
   tags=("correction", "outdated_assumption", "income"))]

APPEND[(1, 25)] = [S("""
A: Let me add the things that aren't open tasks but are still unsettled. The charitable gift: where are you?
C: Parked until the numbers are final. I'm not committing to a scholarship amount until I see the closing statement.
A: And Maine?
C: Not a plan. We looked at the cottage again and decided it's too soon. It stays a maybe for a couple of years.
A: Then I'll keep it as a possibility only.
""", log=[("deferred", "any charitable gift until the sale numbers are final"), ("tentative", "relocating to the Maine coast in a couple of years")],
   tags=("deferred_decision", "uncertain_intention"))]
