# LLM-first vs rules ingestion: evaluation

60 conversations from `data/conversations.csv` · extractor `gpt-4o-mini` · verifier `gpt-4.1-mini` · generated 2026-11-23 10:58

Answer key: the generator's authored outcomes (`reports/generation_manifest.json`). Matching: a blind LLM judge (`gpt-4.1`, used by neither pipeline) sees the key and a shuffled, unlabelled mix of both pipelines' memories. Identical for both pipelines. Unmatched memories are not counted as errors: the key lists outcomes, not every fact.

| Metric | rules | llm_first | rules_raw | llm_first_raw |
|---|---|---|---|---|
| Recall of outcomes | 0.851 | 0.77 | 0.851 | 0.892 |
| Certainty accuracy (matched) | 0.648 | 0.723 | 0.667 | 0.75 |
| Over-confident (tentative → confirmed) | 0 | 0 | 0 | 1 |
| Under-confident (decision → weak) | 9 | 4 | 9 | 4 |
| Memories kept per conversation | 2.38 | 1.98 | 2.52 | 2.23 |
| Kept but matching no outcome | 80 | 63 | 88 | 69 |
| LLM failures | 0 | 0 | 0 | 0 |
| recall: decision | 22/22 | 20/22 | 22/22 | 22/22 |
| recall: plan | 8/10 | 6/10 | 8/10 | 7/10 |
| recall: tentative | 13/14 | 12/14 | 13/14 | 14/14 |
| recall: correction | 11/11 | 9/11 | 11/11 | 9/11 |
| recall: deferred | 9/17 | 10/17 | 9/17 | 14/17 |
| certainty: decision | 15/22 | 19/20 | 15/22 | 21/22 |
| certainty: plan | 6/8 | 3/6 | 6/8 | 4/7 |
| certainty: tentative | 12/13 | 11/12 | 12/13 | 13/14 |
| certainty: correction | 2/11 | 1/9 | 3/11 | 1/9 |

## Agreement buckets (llm_first): measured certainty accuracy

| Bucket | n | accuracy |
|---|---|---|
| agree_all | 26 | 0.923 |
| agree_llms | 17 | 0.765 |
| not_target | 2 | 0.0 |
| regex_hedge | 3 | 0.333 |
| verifier_higher | 3 | 0.333 |
| verifier_lower | 1 | 0.0 |

## rules: sample of outcomes it missed

- `INT-022` plan: a working assumption of six months of base salary as severance for liquidity planning, to be replaced if the real terms become known
- `INT-066` deferred: repositioning your personal portfolio until the deal terms are final
- `INT-150` deferred: any charitable gift until the sale numbers are final
- `INT-118` deferred: the annualized-installment method for estimated taxes until the third quarter's income is clearer
- `INT-005` plan: taking no large irreversible financial decisions for the first six to twelve months unless something forces them
- `INT-103` deferred: any charitable gift until the sale numbers are firm
- `INT-135` deferred: any decision about selling the house until at least November, a year after Samuel's death
- `INT-144` deferred: any Roth conversion until the second-half review
- `INT-052` tentative: accepting Lakeshore's letter of intent
- `INT-116` deferred: any decision on Maplewood Commons until you've talked to Frank and an elder-law attorney has reviewed the contract
- `INT-147` deferred: asking for a four-day work week until next year

## rules: sample of kept memories matching no outcome

- `INT-068` exploratory · retirement_age: Kenji wants to protect their retirement date while Ren has three more years of tuition.
- `INT-035` exploratory · housing_property: The client might relocate to the coast of Maine in a couple of years.
- `INT-103` recommendation · tax_planning: The advisor mentioned that a gift in the year of the sale can offset some of the tax.
- `INT-014` exploratory · investment_trade: The client wants to buy another fifty thousand in Vantora.
- `INT-015` unconfirmed · care_costs: It costs about nine thousand eight hundred a month for her care, and I'm covering what her income doesn't.
- `INT-144` exploratory · tax_planning: The client is considering a Roth conversion before age 63.
- `INT-016` unconfirmed · liquidity_reserve: The client prefers to have a cushion to avoid selling investments in a bad market.
- `INT-078` unconfirmed · care_costs: The advisor reminded Rebecca that her mother's care costs are a pressing concern.
- `INT-014` unconfirmed · liquidity_reserve: The advisor cautioned that using the account for a stock purchase would leave very little cash during a slow month.
- `INT-139` recommendation · liquidity_reserve: I'd want closer to 9 months of liquidity reserve to avoid selling investments in a bad market to pay bills.
- `INT-049` exploratory · retirement_age: The advisor will correct the record to reflect that Kenji is exploring retiring at 60 while planning for 65.
- `INT-005` unconfirmed · insurance_estate: There was a life insurance payout, his retirement account, and my own savings.
- `INT-015` exploratory · retirement_age: David is thinking about retiring from teaching in a couple of years.
- `INT-114` exploratory · liquidity_reserve: If the drawdown passes a threshold, the client wants it documented in advance.
- `INT-106` recommendation · asset_allocation: The advisor proposes a lower-risk portfolio for the sale proceeds, suggesting forty percent in equities.

## llm_first: sample of outcomes it missed

- `INT-098` correction: my note that you would help Brian with a few hundred dollars toward equipment was too strong; it was only a thought and nothing has been offered
- `INT-076` plan: the April tax shortfall paid, with about twenty-two thousand dollars from invoices and the rest from the cash account
- `INT-124` tentative: holding Folake's share in a trust until she is older
- `INT-146` tentative: giving Tobi the fifteen thousand dollar gift, once you have asked him about his customers and what happens if the business fails
- `INT-127` decision: to complete the third Vantora sale as planned and keep the remaining position
- `INT-147` deferred: asking for a four-day work week until next year
- `INT-150` deferred: any charitable gift until the sale numbers are final
- `INT-004` deferred: any change to the personal portfolio's 60 percent equity level until it is clear whether you will sell the company
- `INT-119` correction: my model's assumption of the aide four days a week at about fifty-two hundred dollars a month was out of date; since Frank's fall the aide comes five days, at about sixty-four hundred a month
- `INT-137` decision: to reinvest the maturing bond at the far end of the short-term ladder instead of adding to stocks
- `INT-090` plan: a formal retirement date of 63, to be revisited if your health changes or Northgate is sold
- `INT-003` plan: preserving capital while covering the gap between about six thousand three hundred in pension and Social Security and spending of about eight thousand nine hundred a month
- `INT-103` deferred: any charitable gift until the sale numbers are firm
- `INT-118` deferred: the annualized-installment method for estimated taxes until the third quarter's income is clearer
- `INT-127` deferred: any further reduction of the Vantora position until after third-quarter earnings

## llm_first: sample of kept memories matching no outcome

- `INT-004` recommendation · asset_allocation: The advisor raised a more cautious mix for Rebecca's personal portfolio.
- `INT-039` unconfirmed · business_sale: Lucas will ask Hugo for the business plan and a written agreement.
- `INT-003` unconfirmed · income_cashflow: Peg stated that their spending is around eight thousand nine hundred, including Frank's care.
- `INT-079` confirmed · insurance_estate: Sofia will start the application for the term life insurance this week.
- `INT-139` recommendation · liquidity_reserve: The advisor recommended that Sofia should aim for closer to 9 months of liquidity given her situation.
- `INT-015` confirmed · care_costs: Rebecca is covering about six thousand dollars a month for her mother's care, which will probably grow.
- `INT-046` unconfirmed · tax_planning: I'll send your accountant a summary of the vesting, and you'll ask your accountant for the estimated-tax figure.
- `INT-066` exploratory · investment_trade: Rebecca is considering moving her personal portfolio into bonds but has decided to hold off for now.
- `INT-015` recommendation · care_costs: Daniel will include six thousand a month, rising with inflation, in all projections.
- `INT-078` unconfirmed · care_costs: Daniel reminded Rebecca that her mother's care costs don't wait with her.
- `INT-014` exploratory · liquidity_reserve: Lucas is considering using the cash account for a stock purchase but decided against it for now.
- `INT-089` confirmed · risk_tolerance: Sofia has decided she doesn't want to be this aggressive anymore due to the swings in March and wants to feel more secure with Lucia.
- `INT-050` exploratory · insurance_estate: The client is considering whether they might not need the annuity due to the guaranteed income provided by the pension survivor payment.
- `INT-147` confirmed · asset_allocation: Sofia decided on seventy-five percent for retirement.
- `INT-114` confirmed · education_funding: Kenji agreed that if Ren doesn't get into the program, the fourteen thousand dollars will go back into the reserve.
