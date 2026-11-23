# LLM-first vs rules ingestion: evaluation

25 conversations from `data/simulation/sim_conversations.csv` · extractor `gpt-4o-mini` · verifier `gpt-4.1-mini` · generated 2026-11-23 10:58

Answer key: the generator's authored outcomes (`reports/generation_manifest.json`). Matching: a blind LLM judge (`gpt-4.1`, used by neither pipeline) sees the key and a shuffled, unlabelled mix of both pipelines' memories. Identical for both pipelines. Unmatched memories are not counted as errors: the key lists outcomes, not every fact.

| Metric | rules | llm_first | rules_raw | llm_first_raw |
|---|---|---|---|---|
| Recall of outcomes | 0.234 | 0.558 | 0.234 | 0.636 |
| Certainty accuracy (matched) | 0.438 | 0.568 | 0.438 | 0.512 |
| Over-confident (tentative → confirmed) | 0 | 0 | 0 | 2 |
| Under-confident (decision → weak) | 5 | 11 | 5 | 12 |
| Memories kept per conversation | 1.24 | 2.44 | 1.36 | 2.84 |
| Kept but matching no outcome | 14 | 20 | 17 | 24 |
| LLM failures | 0 | 0 | 0 | 0 |
| recall: decision | 5/12 | 11/12 | 5/12 | 12/12 |
| recall: plan | 1/26 | 13/26 | 1/26 | 14/26 |
| recall: tentative | 7/17 | 7/17 | 7/17 | 10/17 |
| recall: correction | 3/12 | 6/12 | 3/12 | 7/12 |
| recall: deferred | 2/10 | 6/10 | 2/10 | 6/10 |
| certainty: decision | 1/5 | 6/11 | 1/5 | 6/12 |
| certainty: plan | 0/1 | 7/13 | 0/1 | 8/14 |
| certainty: tentative | 6/7 | 7/7 | 6/7 | 7/10 |
| certainty: correction | 0/3 | 1/6 | 0/3 | 1/7 |

## Agreement buckets (llm_first): measured certainty accuracy

| Bucket | n | accuracy |
|---|---|---|
| agree_all | 8 | 0.75 |
| agree_llms | 23 | 0.565 |
| not_target | 2 | 0.0 |
| regex_hedge | 3 | 0.0 |
| verifier_higher | 5 | 0.6 |
| verifier_lower | 2 | 0.0 |

## rules: sample of outcomes it missed

- `SIM-011` plan: Sofia and Mateo are still aiming to start the house search in autumn, with no active viewings yet despite browsing listings.
- `SIM-002` decision: Yuki has chosen to take the nonprofit job, so Kenji's thirty-thousand-dollar cap for graduate school support is no longer applicable.
- `SIM-006` plan: Rebecca's target equity allocation post-sale will be 55% for now, to be revisited in six months.
- `SIM-002` plan: Next review meeting is deferred until September unless document issues arise.
- `SIM-014` deferred: Decision about approaching estate attorneys for Folake's share is postponed until after the client's birthday in August.
- `SIM-011` plan: Brokerage contributions remain trimmed by about $800/month to cover childcare, with the setup unchanged for now.
- `SIM-001` correction: Client clarifies that while the thirty-thousand cap is available for Yuki, he is not encouraging her to choose grad school solely for the money.
- `SIM-014` plan: Next investing step is penciled in for July 12 but will only proceed after a check-in on July 11 if markets remain stable.
- `SIM-003` plan: Mariko’s Social Security benefit of $2,050/month at age 67 is now built into the survivor analysis.
- `SIM-005` deferred: Portfolio allocation and family support adjustments deferred until after September review or if client flags an issue.
- `SIM-016` tentative: The $15,000 gift to Tobi remains on hold with no set figure or commitment; client does not want to specify a lower amount yet.
- `SIM-021` plan: Advisor will send application forms and a checklist for Option B to Lucas and Camille.
- `SIM-015` decision: Grace decides to pause the next investment tranche and reassess after her birthday in August.
- `SIM-022` plan: Advisor will proceed with the bond reinvestment and notify Peg when the trade is complete.
- `SIM-022` correction: Peg found an outdated 2017 power of attorney, not the updated version Dennis drafted.

## rules: sample of kept memories matching no outcome

- `SIM-012` exploratory · housing_property: The client is considering running scenarios for their house situation if the numbers work.
- `SIM-007` recommendation · asset_allocation: The advisor is proposing a 55% equity allocation for the client's investment strategy.
- `SIM-013` unconfirmed · liquidity_reserve: Sofia's house fund is available within 1 business day, while brokerage assets would take 3-5 days to liquidate and settle.
- `SIM-024` exploratory · housing_property: If something changes with Frank or we decide on Maplewood, I’ll call you first.
- `SIM-006` unconfirmed · asset_allocation: Rebecca noted that the current allocation is 55% equities instead of the expected 60%.
- `SIM-006` recommendation · asset_allocation: The advisor mentioned that they can revise the allocation to 60% if Rebecca is more comfortable with that.
- `SIM-013` unconfirmed · education_funding: The advisor will leave the 529 plan as deferred until Sofia finalizes the backup guardian/trustee situation.
- `SIM-025` unconfirmed · housing_property: If Frank's care needs increase or if you decide to consider Maplewood, you will notify the advisor in advance.
- `SIM-008` confirmed · investment_trade: Rebecca requested to prepare the updated policy for her to sign after closing.
- `SIM-020` unconfirmed · insurance_estate: Lucas is considering a disability insurance plan that provides $5,000 monthly to age 65 without a partial payout option.
- `SIM-013` exploratory · care_costs: Sofia mentioned that if childcare costs change, they might need to reassess their financial situation.
- `SIM-025` unconfirmed · risk_tolerance: The client is not comfortable making changes to the portfolio without a significant reason, especially given Frank's current situation.
- `SIM-025` unconfirmed · asset_allocation: Your current equity allocation is at 30%, which was set as your comfort level after last year's volatility.
- `SIM-007` recommendation · asset_allocation: The advisor is suggesting a fixed income blend of 30% Treasuries, 10% investment-grade corporates, and 5% tax-exempt municipal bonds.

## llm_first: sample of outcomes it missed

- `SIM-008` plan: Client expects closing first week of August but will notify advisor if the date changes.
- `SIM-004` tentative: No immediate need for a portfolio performance report; client prefers to wait for the upcoming review unless there is a significant issue.
- `SIM-009` decision: Rebecca agrees to proceed with the investment policy as discussed: 55% equities, 30% Treasuries, 10% corporates, no muni fund.
- `SIM-025` tentative: Revisiting the equity allocation is an option for the year-end review, but not before unless the client raises it or urgent market conditions arise.
- `SIM-002` plan: Next review meeting is deferred until September unless document issues arise.
- `SIM-012` plan: House search remains unofficial; if Sofia and Mateo get serious about a listing, they'll ask Elena to run mortgage and expense numbers before making a move.
- `SIM-017` tentative: Grace is considering a $5,000 gift for Tobi, but is not committing and wants to see how he manages his first client.
- `SIM-001` deferred: Scheduling the next review meeting is deferred until after Yuki makes her decision on July 20.
- `SIM-015` tentative: The $15,000 gift for Tobi remains open; Grace is considering a smaller amount but has not decided.
- `SIM-001` tentative: Thirty-thousand-dollar cap for Yuki's grad program is a plan, but the client is not pushing her either way; support is flexible.
- `SIM-015` plan: No approach to Folake about a trust will be made until Grace requests it.
- `SIM-023` correction: Advisor checks if there are any other items to discuss, but client clarifies nothing is needed until the paperwork is settled, correcting any assumption of new agenda items.
- `SIM-011` plan: Brokerage contributions remain trimmed by about $800/month to cover childcare, with the setup unchanged for now.
- `SIM-021` correction: Lucas softens his earlier indecision, now stating he is comfortable proceeding with Option B, noting Camille is not thrilled but accepts it.
- `SIM-009` plan: Advisor will remind Rebecca about the charitable gift decision in September.

## llm_first: sample of kept memories matching no outcome

- `SIM-024` confirmed · insurance_estate: Peg confirmed that Dennis’s office emailed the new power of attorney document, which she uploaded to the portal and it is all signed.
- `SIM-015` confirmed · investment_trade: Grace decided to hold off on investing more in stocks for now due to market volatility.
- `SIM-005` confirmed · family_support: Kenji stated that Mariko is still part-time with no changes to her benefits.
- `SIM-008` confirmed · investment_trade: Rebecca agreed to prepare the updated investment policy for her signature after the closing.
- `SIM-020` exploratory · insurance_estate: Lucas wants one more look at the exclusions and the claims process before he signs anything.
- `SIM-010` confirmed · insurance_estate: Sofia has booked an appointment with an attorney for the will and guardianship next Friday.
- `SIM-016` exploratory · investment_trade: Grace stated that she is still not ready to commit any money to Tobi's potential clients and will only consider a smaller amount if he actually lands one.
- `SIM-005` confirmed · family_support: Kenji mentioned that Yuki's first paycheck at the nonprofit showed up, making her extra cheerful.
- `SIM-007` exploratory · asset_allocation: Rebecca requested to see the list of fixed income funds before signing the investment policy.
- `SIM-008` unconfirmed · asset_allocation: Rebecca is maintaining her 55% equity allocation.
- `SIM-025` confirmed · care_costs: Peg stated that Frank's care needs have not changed and they haven't decided on extra care or Maplewood.
- `SIM-018` confirmed · insurance_estate: Lucas cares about the monthly payout if he can't work and how long it pays for, and Camille thinks they should skip the fancy extras.
- `SIM-018` confirmed · education_funding: Lucas mentioned that the college fund is a lower priority until fall.
- `SIM-014` exploratory · insurance_estate: Grace mentioned she might ask Folake how she’d feel about a trust before reaching out to an attorney.
- `SIM-011` confirmed · care_costs: Sofia stated that childcare costs continue to be stable and she does not see any change there for now.
