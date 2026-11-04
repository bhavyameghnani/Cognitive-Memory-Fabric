"""Authored concept explanations (client asks, advisor explains)."""

EXPLAIN = {
"sequence_risk": """
C: People keep mentioning sequence of returns risk. What does it actually mean for me?
A: It's about the order of returns, not just the average. If markets fall early in retirement while you're withdrawing, you sell more shares at low prices, and the portfolio has less left to recover with. The same average return in a different order can leave you with very different amounts.
C: So what do people do about it?
A: Common tools are holding a cash or short-term bond reserve for the first few years of spending, using flexible withdrawals, and not being too aggressive close to the retirement date. None of that removes the risk. It reduces the chance of being forced to sell at the worst time.
""",
"concentrated_stock": """
C: My shares in one company have grown a lot. Why does everyone keep telling me that's a problem?
A: It isn't a problem while the price rises. The issue is that your income, your reputation and a big part of your savings then depend on one business. If it stumbles, you could lose all three at once.
C: But I know that business better than any fund.
A: You may well know it better. The difficulty is that even insiders are surprised by events, and a single company can fall fifty percent when the broad market doesn't. Many people trim in stages, to manage taxes and regret, rather than selling everything at once.
""",
"healthcare_bridge": """
C: What happens with health insurance if someone retires before Medicare starts at 65?
A: You'd need to cover the gap yourself. The usual routes are COBRA for up to eighteen months, a plan from the marketplace, or coverage through a spouse's employer. Premiums depend heavily on age, income and where you live.
C: Is that a big number?
A: It can be. I tend to plan with a conservative figure, and I'd rather you were pleasantly surprised than short. Subsidies on marketplace plans also depend on your taxable income, which interacts with how you draw money from your accounts.
""",
"education_funding": """
C: Is it smarter to pay for graduate school from savings or to let the student borrow?
A: There isn't one right answer. Graduate loans cost more than many people expect, and parents' savings are often also retirement savings. A common compromise is to set a firm annual cap on what you contribute and let the student cover the rest through work, scholarships or a modest loan.
C: And does it affect my retirement?
A: Every dollar spent on tuition is a dollar not compounding. I can show the effect on your retirement date for each cap, so the decision is informed rather than emotional.
""",
"social_security": """
C: When should I claim Social Security? Everyone has a different opinion.
A: The honest answer is that it depends. Claiming earlier gives smaller checks for longer, and claiming later gives larger checks for a shorter period. The break-even is usually in the late seventies or early eighties, but that doesn't capture your health, your spouse's benefit or your other income.
C: So what do you look at?
A: Your life expectancy, how much you'd rely on the money, whether a spouse would inherit the larger benefit, and your taxes. I'd rather model it than guess.
""",
"rmd_qcd": """
C: I get letters about required minimum distributions. How do those work?
A: Once you reach the required age, the IRS requires you to withdraw a minimum percentage each year from most pre-tax retirement accounts. It's calculated from your balance at the end of the prior year and a life-expectancy factor, and the withdrawal is taxable.
C: And if I give to charity anyway?
A: A qualified charitable distribution lets you send money directly from an IRA to a charity. It can count toward the required amount without being added to your taxable income. The rules and limits matter, so I'd check them with your accountant before the first gift.
""",
"irregular_reserve": """
C: My income arrives in lumps. How do I plan around that?
A: The usual answer is to pay yourself a steady amount from a holding account. In good months, the surplus goes into the account. In slow months, you draw from it. Separately, you need a reserve to cover taxes, because nobody is withholding for you.
C: How big should the reserve be?
A: With variable income I like to see at least nine to twelve months of essential spending in cash, plus the next tax payments set aside separately. It sounds like a lot until a bad quarter proves it isn't.
""",
"survivor_rollover": """
C: I've been told I can roll Samuel's retirement account into my own, or keep it as an inherited account. What's the difference?
A: As a surviving spouse, you can treat it as your own. That gives you the most flexibility on timing, including how the required withdrawals work later. If you keep it as an inherited account, the withdrawal rules follow Samuel's circumstances and your options differ.
C: Which is better?
A: You're past 59 and a half, so the early-withdrawal penalty isn't the deciding factor for you. The more important questions are how soon you will need the money and how the choice affects your taxes. I'd rather not rush it. There is no deadline that forces a choice this month.
""",
"sale_proceeds": """
C: If I do sell, what happens to the money?
A: It depends on how the deal is structured. Part might be paid in cash at closing, part might be held back in escrow, and part might depend on future performance. Taxes are a major factor, because the way each piece is characterised changes the rate.
C: So the headline price isn't what I'd keep.
A: Correct. I'd want to see the net after tax, after fees, after escrow, and after any earn-out risk, before we call it a good number.
""",
"term_life": """
C: How much life insurance do I need, and what type?
A: For a young family, term life insurance is often the right tool. It covers a fixed period, such as twenty or thirty years, while your dependants rely on your income. A common starting point is ten to fifteen times annual income, adjusted for debts, childcare and your partner's earnings.
C: Why not whole life?
A: Permanent policies have a place, mainly in estate planning, but they cost much more. For most families at your stage, buying term and investing the difference is simpler. I'm not an insurance broker, so I'd refer you to a licensed one for quotes.
""",
"ccrc": """
C: What exactly is a continuing care retirement community?
A: It's a community that offers a range of living options, from independent apartments to assisted living and nursing care, usually for an entrance fee and a monthly charge. The attraction is that care is available on the same campus if your needs change.
C: Is the entrance fee lost?
A: It depends on the contract. Some refund a portion to your estate, others don't. The financial strength of the operator also matters, because you're making a long-term commitment. I'd want to read the contract and the operator's financial statements before we model it.
""",
"estimated_tax": """
C: Why do I have to pay taxes quarterly? Nobody does that for employees.
A: Employees have tax withheld from each paycheck. When no one withholds, the tax system expects you to pay as you earn, in four instalments. If you pay too little, there can be an underpayment penalty.
C: How do I decide how much?
A: A common method is to pay at least the lesser of ninety percent of this year's expected tax or the safe-harbour amount based on last year's. Your accountant can pick the approach that fits an uneven year.
""",
"bond_ladder": """
C: You mentioned a bond ladder. How does that work?
A: You buy bonds that mature in different years, for example one, two, three, four and five years. As each one matures, you use the money for spending or buy a new one at the far end. It gives you predictable cash for the near term, and you aren't forced to sell if prices fall.
C: Is it safe?
A: It reduces the risk of having to sell at the wrong moment, but it isn't risk-free. Inflation can erode what the bonds pay, and the issuer could default, which is why quality matters.
""",
}


LEAD = {
"sequence_risk": "One term worth understanding properly is sequence-of-returns risk.",
"concentrated_stock": "I want to say something about holding one company's shares.",
"healthcare_bridge": "Let's talk about health coverage before Medicare begins.",
"education_funding": "There's a funding question hiding in the tuition numbers.",
"social_security": "We haven't talked about when to claim Social Security yet.",
"rmd_qcd": "Required distributions will start to matter, so let me explain the rules.",
"irregular_reserve": "Variable income needs a different kind of cash planning.",
"survivor_rollover": "There is a decision about the retirement account that has no deadline but needs explaining.",
"sale_proceeds": "Before the numbers go further, let's separate the headline price from what you would actually keep.",
"term_life": "We should talk about protecting your family if something happened to you.",
"ccrc": "Let me explain what these retirement communities actually are.",
"estimated_tax": "Quarterly tax payments are going to matter for you.",
"bond_ladder": "I want to describe how the short-term bond ladder works.",
}
