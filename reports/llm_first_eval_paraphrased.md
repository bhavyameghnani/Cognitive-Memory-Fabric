# LLM-first vs rules ingestion: evaluation

60 conversations from `build/eval/paraphrased_conversations.csv` · extractor `gpt-4o-mini` · verifier `gpt-4.1-mini` · generated 2026-11-22 13:42

Answer key: the generator's authored outcomes (`reports/generation_manifest.json`). Matching: a blind LLM judge (`gpt-4.1`, used by neither pipeline) sees the key and a shuffled, unlabelled mix of both pipelines' memories. Identical for both pipelines. Unmatched memories are not counted as errors: the key lists outcomes, not every fact.

| Metric | rules | llm_first | rules_raw | llm_first_raw |
|---|---|---|---|---|
| Recall of outcomes | 0.662 | 0.743 | 0.703 | 0.851 |
| Certainty accuracy (matched) | 0.425 | 0.792 | 0.419 | 0.788 |
| Over-confident (tentative → confirmed) | 0 | 0 | 0 | 0 |
| Under-confident (decision → weak) | 15 | 2 | 16 | 2 |
| Memories kept per conversation | 2.25 | 2.08 | 2.4 | 2.32 |
| Kept but matching no outcome | 86 | 71 | 92 | 77 |
| LLM failures | 0 | 0 | 0 | 0 |
| recall: decision | 15/22 | 21/22 | 16/22 | 21/22 |
| recall: plan | 4/10 | 5/10 | 5/10 | 6/10 |
| recall: tentative | 13/14 | 12/14 | 13/14 | 14/14 |
| recall: correction | 8/11 | 10/11 | 9/11 | 11/11 |
| recall: deferred | 9/17 | 7/17 | 9/17 | 11/17 |
| certainty: decision | 3/15 | 20/21 | 4/16 | 21/21 |
| certainty: plan | 1/4 | 4/5 | 1/5 | 4/6 |
| certainty: tentative | 13/13 | 12/12 | 13/13 | 14/14 |
| certainty: correction | 0/8 | 2/10 | 0/9 | 2/11 |

## Agreement buckets (llm_first): measured certainty accuracy

| Bucket | n | accuracy |
|---|---|---|
| agree_all | 18 | 0.944 |
| agree_llms | 25 | 0.84 |
| not_target | 2 | 0.0 |
| regex_hedge | 1 | 0.0 |
| verifier_higher | 3 | 0.667 |
| verifier_lower | 3 | 0.333 |

## rules: sample of outcomes it missed

- `INT-118` decision: to pay the second-quarter estimate at the safe-harbor amount
- `INT-150` deferred: any charitable gift until the sale numbers are final
- `INT-010` plan: a growth-oriented portfolio at about 75 percent equities
- `INT-050` correction: my February summary that the survivor payment was about half of Samuel's pension was imprecise; the form says sixty percent for life, with a lump-sum alternative not yet evaluated
- `INT-144` deferred: any Roth conversion until the second-half review
- `INT-126` decision: to pay the lawyer's retainer and the accountant's advance, about fifty-five thousand dollars, from cash and not from investments
- `INT-147` deferred: asking for a four-day work week until next year
- `INT-010` decision: to proceed with the agreed portfolio review
- `INT-015` plan: treating your mother's care, about six thousand dollars a month from you, as a fixed obligation in all projections
- `INT-142` correction: my assumption that your salary would stop at closing was wrong; the draft agreement keeps you on as a paid consultant for twelve months after closing
- `INT-079` decision: to apply for a one and a half million dollar twenty-year term policy for you and a one million dollar policy for Mateo
- `INT-118` deferred: the annualized-installment method for estimated taxes until the third quarter's income is clearer
- `INT-076` plan: the April tax shortfall paid, with about twenty-two thousand dollars from invoices and the rest from the cash account
- `INT-003` plan: preserving capital while covering the gap between about six thousand three hundred in pension and Social Security and spending of about eight thousand nine hundred a month
- `INT-103` deferred: any charitable gift until the sale numbers are firm

## rules: sample of kept memories matching no outcome

- `INT-066` exploratory · investment_trade: The client has not made any decisions beyond considering the bond allocation.
- `INT-040` unconfirmed · retirement_age: You might qualify for Social Security survivor benefits at age 60.
- `INT-078` exploratory · business_sale: Rebecca is considering waiting two years before selling her business to potentially get a better price.
- `INT-148` unconfirmed · liquidity_reserve: You aimed for a nine-month reserve after shifting your strategy.
- `INT-010` unconfirmed · asset_allocation: Kenji is comfortable holding around seventy-five percent equities and can handle a sizable dip.
- `INT-015` unconfirmed · care_costs: Her doctors indicated that she might need to stay in care for years.
- `INT-128` recommendation · investment_trade: The advisor will contact Grace if her stock position drops more than ten percent before the July 1 buy.
- `INT-016` recommendation · liquidity_reserve: The advisor suggests that the client should have closer to nine months of cash set aside due to unpredictable income.
- `INT-071` confirmed · housing_property: Sofia has decided to wait until autumn to start house hunting.
- `INT-144` exploratory · retirement_age: The client is considering a Roth conversion before turning 63.
- `INT-014` exploratory · investment_trade: The client is considering purchasing an additional $50,000 worth of Vantora stock.
- `INT-119` recommendation · liquidity_reserve: The advisor suggests having closer to 24 months saved up for expenses due to Frank's care and potential medical needs.
- `INT-050` exploratory · investment_trade: The client is considering whether they really need the annuity given their existing guaranteed income sources.
- `INT-005` exploratory · investment_trade: The client is considering whether to keep the intermediate bond fund.
- `INT-015` exploratory · retirement_age: David is considering retiring from teaching in a couple of years.

## llm_first: sample of outcomes it missed

- `INT-066` deferred: repositioning your personal portfolio until the deal terms are final
- `INT-040` deferred: claiming the Social Security survivor benefit while you are still working
- `INT-076` plan: the April tax shortfall paid, with about twenty-two thousand dollars from invoices and the rest from the cash account
- `INT-118` deferred: the annualized-installment method for estimated taxes until the third quarter's income is clearer
- `INT-010` decision: to proceed with the agreed portfolio review
- `INT-098` correction: my note that you would help Brian with a few hundred dollars toward equipment was too strong; it was only a thought and nothing has been offered
- `INT-144` deferred: any Roth conversion until the second-half review
- `INT-135` deferred: any decision about selling the house until at least November, a year after Samuel's death
- `INT-103` deferred: any charitable gift until the sale numbers are firm
- `INT-005` plan: taking no large irreversible financial decisions for the first six to twelve months unless something forces them
- `INT-150` deferred: any charitable gift until the sale numbers are final
- `INT-090` plan: a formal retirement date of 63, to be revisited if your health changes or Northgate is sold
- `INT-150` tentative: relocating to the Maine coast in a couple of years
- `INT-046` deferred: selling part of the vested Northgate shares until the tax estimate is in
- `INT-124` tentative: holding Folake's share in a trust until she is older

## llm_first: sample of kept memories matching no outcome

- `INT-122` confirmed · family_support: Grace indicated that she wants to give some of the insurance payout to Tobi as a gift, but only up to fifteen.
- `INT-010` unconfirmed · retirement_age: Kenji noted that he is about seven years out from retirement.
- `INT-066` hypothetical · investment_trade: Rebecca agreed to send the signed exclusivity agreement and her lawyer's summary to Daniel.
- `INT-010` confirmed · risk_tolerance: Kenji confirmed that he would rather err on the side of taking more risk than playing it safe.
- `INT-046` recommendation · investment_trade: The advisor suggests that Kenji should sell some Northgate shares at each vest and spread the money out.
- `INT-089` confirmed · risk_tolerance: Sofia indicated that her perspective on risk has changed for the long haul after having a child.
- `INT-127` exploratory · investment_trade: Lucas is still mulling over what to do with the rest of Vantora.
- `INT-046` unconfirmed · education_funding: Kenji mentioned that Ren is switching his major to engineering, which probably means he'll need to add another semester.
- `INT-077` confirmed · business_sale: Rebecca confirmed that a cash at closing of roughly $12.3 million would take care of her mom and the reserve.
- `INT-010` unconfirmed · education_funding: Kenji will send the summary for his Northgate deferred comp plan when he gets it.
- `INT-089` exploratory · risk_tolerance: Sofia decided to shift her retirement investments from about 90% stocks to 75% due to her changed comfort level after Lucia was born.
- `INT-049` exploratory · retirement_age: Priya will update her notes to reflect that Kenji is considering age 60 for retirement, but that 65 is still the official goal.
- `INT-079` unconfirmed · insurance_estate: Sofia will kick off the application process for the term life insurance this week.
- `INT-126` exploratory · liquidity_reserve: Rebecca mentioned that they’re aiming for late June to sign, with closing supposed to be in September.
- `INT-114` correction · education_funding: Kenji clarified that the $14,000 is for the program fee, flights, and housing on top of his usual tuition, which is already in the budget.
