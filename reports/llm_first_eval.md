# LLM-first vs rules ingestion: evaluation

150 conversations from `data/conversations.csv` · extractor `gpt-4o-mini` · verifier `gpt-4.1-mini` · generated 2026-11-23 10:57

Answer key: the generator's authored outcomes (`reports/generation_manifest.json`). Matching: a blind LLM judge (`gpt-4.1`, used by neither pipeline) sees the key and a shuffled, unlabelled mix of both pipelines' memories. Identical for both pipelines. Unmatched memories are not counted as errors: the key lists outcomes, not every fact.

| Metric | rules | llm_first | rules_raw | llm_first_raw |
|---|---|---|---|---|
| Recall of outcomes | 0.911 | 0.837 | 0.911 | 0.941 |
| Certainty accuracy (matched) | 0.781 | 0.825 | 0.789 | 0.858 |
| Over-confident (tentative → confirmed) | 0 | 0 | 0 | 1 |
| Under-confident (decision → weak) | 13 | 9 | 13 | 7 |
| Memories kept per conversation | 1.93 | 1.96 | 2.05 | 2.2 |
| Kept but matching no outcome | 166 | 183 | 185 | 205 |
| LLM failures | 0 | 0 | 0 | 0 |
| recall: decision | 52/53 | 50/53 | 52/53 | 53/53 |
| recall: plan | 15/17 | 13/17 | 15/17 | 14/17 |
| recall: tentative | 36/37 | 31/37 | 36/37 | 37/37 |
| recall: correction | 11/11 | 9/11 | 11/11 | 9/11 |
| recall: deferred | 9/17 | 10/17 | 9/17 | 14/17 |
| certainty: decision | 42/52 | 45/50 | 42/52 | 50/53 |
| certainty: plan | 12/15 | 9/13 | 12/15 | 10/14 |
| certainty: tentative | 33/36 | 30/31 | 33/36 | 36/37 |
| certainty: correction | 2/11 | 1/9 | 3/11 | 1/9 |

## Agreement buckets (llm_first): measured certainty accuracy

| Bucket | n | accuracy |
|---|---|---|
| agree_all | 76 | 0.947 |
| agree_llms | 27 | 0.815 |
| not_target | 2 | 0.0 |
| regex_hedge | 3 | 0.333 |
| verifier_higher | 3 | 0.333 |
| verifier_lower | 2 | 0.5 |

## rules: sample of outcomes it missed

- `INT-116` deferred: any decision on Maplewood Commons until you've talked to Frank and an elder-law attorney has reviewed the contract
- `INT-022` plan: a working assumption of six months of base salary as severance for liquidity planning, to be replaced if the real terms become known
- `INT-052` tentative: accepting Lakeshore's letter of intent
- `INT-147` deferred: asking for a four-day work week until next year
- `INT-150` deferred: any charitable gift until the sale numbers are final
- `INT-005` plan: taking no large irreversible financial decisions for the first six to twelve months unless something forces them
- `INT-066` deferred: repositioning your personal portfolio until the deal terms are final
- `INT-118` deferred: the annualized-installment method for estimated taxes until the third quarter's income is clearer
- `INT-135` deferred: any decision about selling the house until at least November, a year after Samuel's death
- `INT-041` decision: to use the full-time-care stress case, and not the expected case, to judge large decisions such as lending money or buying into a retirement community
- `INT-103` deferred: any charitable gift until the sale numbers are firm
- `INT-144` deferred: any Roth conversion until the second-half review

## rules: sample of kept memories matching no outcome

- `INT-069` exploratory · housing_property: Grace is considering selling her house and downsizing in a few years.
- `INT-032` recommendation · business_sale: The advisor will calculate the net amount from a sale at twenty and twenty-four million after tax, fees, and escrow.
- `INT-088` exploratory · retirement_age: The advisor noted that Grace is still weighing the decision between retiring at 62 and 65.
- `INT-011` unconfirmed · income_cashflow: Sofia's income is about two hundred ten thousand with stock grants.
- `INT-015` exploratory · care_costs: If I sell, I want enough cash to be sure of her care.
- `INT-136` unconfirmed · liquidity_reserve: You currently hold about $62,000 in cash and short-term holdings, which covers roughly 10 months of spending.
- `INT-021` exploratory · business_sale: The client is considering buying a franchise and is seeking advice on it.
- `INT-078` unconfirmed · care_costs: The advisor reminded Rebecca that her mother's care costs are a pressing concern.
- `INT-144` recommendation · liquidity_reserve: The advisor recommends having closer to 15 months of liquidity to avoid selling investments in a bad market.
- `INT-014` exploratory · investment_trade: The client wants to buy another fifty thousand in Vantora.
- `INT-120` confirmed · retirement_income: Kenji prefers five annual deferred compensation installments to limit exposure to Northgate.
- `INT-047` exploratory · education_funding: Grace is concerned about not wanting to take money from Folake's tuition.
- `INT-009` unconfirmed · insurance_estate: The survivor amount of Frank's pension is currently unconfirmed and will be revisited when discussing care costs.
- `INT-017` exploratory · housing_property: The client is considering buying a house in a couple of years but is not yet sure.
- `INT-103` recommendation · tax_planning: The advisor mentioned that a gift in the year of the sale can offset some of the tax.

## llm_first: sample of outcomes it missed

- `INT-065` tentative: whether one and a half million dollars of term life cover is more than you need
- `INT-146` tentative: giving Tobi the fifteen thousand dollar gift, once you have asked him about his customers and what happens if the business fails
- `INT-149` tentative: whether Yuki takes up her graduate place or the nonprofit job, with the thirty thousand dollar a year cap standing if she goes
- `INT-096` tentative: relocating to Lisbon in about two years, which depends on Camille's employer allowing remote work and on Moreau Labs running without you on site
- `INT-119` correction: my model's assumption of the aide four days a week at about fifty-two hundred dollars a month was out of date; since Frank's fall the aide comes five days, at about sixty-four hundred a month
- `INT-150` deferred: any charitable gift until the sale numbers are final
- `INT-005` plan: taking no large irreversible financial decisions for the first six to twelve months unless something forces them
- `INT-103` deferred: any charitable gift until the sale numbers are firm
- `INT-003` plan: preserving capital while covering the gap between about six thousand three hundred in pension and Social Security and spending of about eight thousand nine hundred a month
- `INT-127` deferred: any further reduction of the Vantora position until after third-quarter earnings
- `INT-137` decision: to reinvest the maturing bond at the far end of the short-term ladder instead of adding to stocks
- `INT-098` correction: my note that you would help Brian with a few hundred dollars toward equipment was too strong; it was only a thought and nothing has been offered
- `INT-101` decision: to sell the entire crypto holding and put the money into the cash reserve, because after the customer loss you want simpler, steadier assets
- `INT-147` deferred: asking for a four-day work week until next year
- `INT-090` plan: a formal retirement date of 63, to be revisited if your health changes or Northgate is sold

## llm_first: sample of kept memories matching no outcome

- `INT-008` unconfirmed · tax_planning: Lucas needs to make quarterly tax payments since no one withholds taxes from his income.
- `INT-038` confirmed · education_funding: Rebecca does not want Ethan choosing a school based on price.
- `INT-007` recommendation · insurance_estate: Priya will ask Samuel's employer for a plain summary of the pension survivor options.
- `INT-119` unconfirmed · care_costs: The portfolio covers a bigger gap, about thirty-eight hundred a month.
- `INT-020` confirmed · risk_tolerance: Grace decided that she doesn't want any risk at all with the insurance payout and wants capital preserved for now.
- `INT-058` unconfirmed · risk_tolerance: The advisor stated they need to update Rebecca's suitability assessment before acting on reinvesting sale proceeds.
- `INT-093` unconfirmed · income_cashflow: Rebecca confirmed that her average monthly spending is roughly $14,500.
- `INT-021` unconfirmed · income_cashflow: Peg feels that her spending is about $8,900 a month on average, which is set against her pension and Social Security, with the portfolio covering the difference.
- `INT-115` exploratory · insurance_estate: Lucas could not find the disability policy and thinks he only has health insurance.
- `INT-018` exploratory · tax_planning: Peg gives about six thousand to the church every year and is considering doing that directly from the IRA through a qualified charitable distribution.
- `INT-122` unconfirmed · debt_mortgage: Grace clarified that the insurance payout is not reserved for paying off the mortgage, as they decided to keep the mortgage.
- `INT-067` unconfirmed · risk_tolerance: Kenji's real tolerance for volatility is lower than he told the advisor in January, and he wants smaller swings as retirement approaches.
- `INT-119` recommendation · liquidity_reserve: For you, given Frank's care costs and the chance of a sudden medical bill, I'd want closer to 24 months of liquidity.
- `INT-145` confirmed · asset_allocation: Peg decided to adjust her equity allocation to thirty percent after initially wanting thirty-five percent and later reducing it to twenty-five percent.
- `INT-029` exploratory · retirement_age: Grace is considering whether to roll Samuel's retirement account into her own or keep it as an inherited account.
