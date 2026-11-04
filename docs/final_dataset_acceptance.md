# Final dataset acceptance (Phase 1 source conversations)

**Verdict: FIT TO FREEZE.** Fit to freeze as the final Phase 1 source dataset. All automated checks pass, 4 genuine inconsistencies found in this pass were corrected with a deterministic regeneration that changed only 4 interactions, and no confirmed inconsistency remains open. The items listed under human_review_required are recommended confirmations, not blockers.

CSV md5 `03d14ce01d5f053bd843a1d9ec559f8f` (v1 backup `ee199516894c8111e6903ce40ea68d9e`, pre-final v2 snapshot `9a30a8ab0c4b7dccdb49c00c62522a98`). Machine-readable version: `reports/final_acceptance_audit.json`.

## 1. What this pass did

A targeted QA pass, not a rewrite. Every new fact from revision 2 was traced, the six half-year reviews and INT-148 were read, and the audit found four genuine inconsistencies. They were corrected in the generator source and the CSV regenerated deterministically. **Only 4 of 150 interactions changed text** (INT-009, INT-115, INT-119, INT-148); every other row is byte-identical to the pre-final snapshot, and IDs, timestamps, channels, advisors and `meeting_context` labels are unchanged.

| Interaction | Problem found | Fix |
|---|---|---|
| INT-009 | portfolio withdrawal stated as 'about three thousand' while the 5 January figures (8,900 spending - 6,300 income) give 2,600 | 'about twenty-six hundred' |
| INT-119 | portfolio-funded gap stated as 4,200; 10,100 - 6,300 = 3,800. The INT-009 promise to revisit Frank's pension survivor amount 'when we look at the care costs' had not been kept | 3,800 (and 2,600 as the prior gap); the unconfirmed survivor amount is carried forward without inventing an answer |
| INT-115 | Vantora holding text said 'you've chosen to hold the rest until after the third-quarter earnings' on 29 May, but that decision is made on 9 June (INT-127) | step-aware wording: starting-size description until the third sale |
| INT-148 | disability-insurance follow-up had no status: the closing said 'nothing we saw requires action' and logged no outcome; the dialogue also implied the broker was overdue although 'three weeks' was said on 18 June | explicit pending status ('quotes not back, nothing bought until seen'), logged as one client action and one unconfirmed intention; timing made consistent. No decision was manufactured |

## 2. Automated checks

| Check | Result | Detail |
|---|---|---|
| 150_interactions | PASS | 150 |
| six_clients_25_each | PASS | {'CLIENT-001': 25, 'CLIENT-002': 25, 'CLIENT-006': 25, 'CLIENT-004': 25, 'CLIENT-005': 25, 'CLIENT-003': 25} |
| three_advisors | PASS |  |
| eleven_columns_unchanged | PASS | ['interaction_id', 'client_id', 'advisor_id', 'interaction_datetime', 'channel', 'turn_index', 'speaker', 'utterance', ' |
| ids_clients_advisors_timestamps_channels_identical_to_v1 | PASS |  |
| meeting_context_labels_identical_to_pre_final_v2 | PASS |  |
| turn_indices_sequential | PASS |  |
| no_manifest_ids_or_annotation_terms_in_csv | PASS | [] |
| source_type_and_consent_constant | PASS |  |
| only_the_four_audited_interactions_changed_text | PASS | ['INT-009', 'INT-115', 'INT-119', 'INT-148'] |
| unchanged_interactions_byte_identical_rows | PASS |  |
| ledger_48_opened_40_completed_8_outstanding | PASS | 48/40/8 |
| ledger_identical_to_v1 | PASS |  |
| july_1_commitment_recorded_outside_ledger_counts | PASS | [{"id": "g_jul1_call", "owner_role": "advisor", "owner_id": "ADVISOR-001", "description": "call Grace before the July 1  |
| relative_date_and_cross_reference_audit_clean | PASS | [] |
| INT-148_has_explicit_pending_status | PASS |  |
| INT-148_closing_no_longer_says_nothing_requires_action | PASS |  |
| INT-148_logs_follow_up_and_unconfirmed_intention | PASS |  |
| INT-148_broker_timing_consistent_with_18_June | PASS |  |
| gap_arithmetic_consistent_CLIENT-006 | PASS |  |
| vantora_text_not_ahead_of_decision | PASS |  |
| generator_deterministic_two_runs_identical | PASS | [(0, '03d14ce01d5f053bd843a1d9ec559f8f', '955c616b2515c92bb361289b90b403b2'), (0, '03d14ce01d5f053bd843a1d9ec559f8f', '9 |
| generated_output_equals_delivered_csv_and_manifest | PASS |  |
| validator_all_33_checks_pass | PASS | exit 0, 33 checks, failed=[] |
| v1_backup_csv_md5_matches_value_recorded_at_backup_time | PASS | ee199516894c8111e6903ce40ea68d9e |
| v1_backup_files_share_one_copy_timestamp_no_later_edits | PASS | spread 0.26s over 18 files |
| v1_backup_ledger_48_40_8 | PASS |  |
| quality_comparison_runs_and_preservation_flags_true | PASS |  |
| every_new_fact_located_in_the_csv | PASS | [] |
| all_half_year_recap_claims_traceable_to_earlier_dialogue | PASS | {} |
| half_year_reviews_reference_ledger_items_that_are_open | PASS |  |

## 3. New facts introduced by revision 2

Evidence quotes are extracted from the CSV. The consistency result and action are manual judgments.

| Client | Fact | First interaction | Supporting dialogue | Consistency result | Required action |
|---|---|---|---|---|---|
| CLIENT-002 | Ethan has an existing college account of about $85k | INT-038 (2026-02-19, Client) | "...David started a college account when Ethan was born. There's about eighty-five in it. The rest comes out of income. I don't want Ethan choosing a school on price...." | consistent | none |
| CLIENT-002 | Walk-away price of $22M against the $24M letter of intent | INT-077 (2026-04-17, Client) | "...Twenty-two. Below twenty-two I walk...." | consistent | none |
| CLIENT-002 | Retention pool of up to ~$1.2M, ~$700k after tax | INT-121 (2026-06-03, Advisor) | "...Out of your proceeds, you mean. After tax that's roughly seven hundred thousand less in your pocket. Can you live with that?..." | consistent, tax figure unverified | human tax plausibility check (optional) |
| CLIENT-002 | $55k pre-signing costs ($40k retainer + $15k advance) paid from cash | INT-126 (2026-06-08, Advisor) | "...Fifty-five thousand out of your own pocket before the deal pays for itself. Where's it coming from?..." | consistent | none |
| CLIENT-002 | 12-month paid consulting role after closing (corrects 'salary stops at closing') | INT-142 (2026-06-23, Client) | "...No, and I should have said so. The draft keeps me on as a paid consultant for twelve months after closing, at my current salary. Lakeshore wants me for the handover. It's in section six...." | consistent | none |
| CLIENT-003 | Lucia born Tuesday 3 March; the 4 March meeting is a call from hospital | INT-048 (2026-03-04, Client) | "...Lucia was born on Tuesday, March 3. Mother and baby are doing well, and yes, I'm still in the hospital. I didn't want to cancel..." | consistent | none |
| CLIENT-003 | Manager hinted at a four-day week (about -20% salary) | INT-045 (2026-03-02, Advisor) | "...Good. You also mentioned your manager might let you move to four days after leave?..." | consistent | none |
| CLIENT-003 | Sofia's student loans are federal | INT-065 (2026-04-02, Client) | "...Federal. And what's an elimination period? The broker used that on the disability quote and I nodded like I knew...." | consistent | none |
| CLIENT-004 | Survivor pension is 60% of Samuel's pension (corrects 'about half') | INT-050 (2026-03-09, Advisor) | "...More. The form says sixty percent for life, or a lump-sum alternative that we haven't evaluated yet. I had read the company's summary too loosely...." | consistent | none |
| CLIENT-004 | First investing step on 1 June of ~$24k (one of six) | INT-110 (2026-05-19, Advisor) | "...It isn't silly. Let me be concrete. The first step is one sixth of the amount heading into stocks, about twenty-four thousand dollars. If that fell twenty percent, you'd be down about forty-eight hundred...." | consistent | none |
| CLIENT-004 | Annual house cost of 'fifteen thousand or more' | INT-135 (2026-06-17, Advisor) | "...So fifteen thousand or more. But I'd rather you not decide this in the first year. In January you said no large irreversible decisions fo..." | partly supported | none; accepted |
| CLIENT-005 | April tax shortfall of $20-25k, covered ~22k from invoices and ~3k from cash | INT-042 (2026-02-25, Advisor) | "...twenty-seven thousand, plus federal income tax, plus state. I'd expect fifty-five to sixty all in. So you're short by twenty to twenty-five, due in April...." | consistent | none |
| CLIENT-005 | Estimated taxes use the safe-harbor method (110% of last year's tax) | INT-016 (2026-01-22, Advisor) | "...Then we can't be precise today. There's a safe harbor: if you pay at least a hundred and ten percent of last year's total tax, in four equal installments, there's no underpayment penalty however big this year turns out...." | consistent | none |
| CLIENT-005 | Remaining Vantora position about a sixth of liquid assets after three sales | INT-148 (2026-06-29, Advisor) | "...Good question. After the three sales it's down to roughly a sixth of the liquid portfolio, and you've chosen to hold the rest until after the third-quarter earnings...." | inconsistency found and fixed | fixed in this pass |
| CLIENT-006 | Required distribution about $31k; $6k of it via the church gift | INT-085 (2026-04-24, Advisor) | "...Dennis Hall has confirmed the numbers for the church gift. Your required distribution this year is about thirty-one thousand. A qualified charitable distribution of six thousand counts toward it, and it isn't added to your taxable inc..." | consistent, IRA balance never stated | human check (optional) |
| CLIENT-006 | Aide five days a week at ~$6,400 a month; spending $10,100; portfolio gap $3,800 | INT-119 (2026-06-02, Client) | "...No, it's five days now. Since Frank's fall in March the agency moved us up, and the invoice for May was sixty-four hundred...." | inconsistency found and fixed | fixed in this pass |
| CLIENT-006 | Remaining required amount taken monthly Jul-Dec with 12% withheld | INT-129 (2026-06-11, Advisor) | "...Dennis said twelve percent. Is that what you want?..." | weakly supported | human check (optional) |
| CLIENT-006 | Maturing bond rung of about $50k | INT-137 (2026-06-18, Advisor) | "...No, the opposite. The bond pays back its face value in cash, about fifty thousand dollars. The question is what to do with the cash...." | consistent | none |
| CLIENT-006 | Frank's pension survivor amount unconfirmed (carried forward) | INT-009 (2026-01-12, Advisor) | "...That's worth knowing, and it affects how much you can safely spend. For now I'll record that the survivor amount is unconfirmed, and we'll come back to it when we look at the care costs...." | loose end found and fixed | fixed in this pass |
| CLIENT-001 | Working assumption of six months' base salary as severance | INT-022 (2026-02-03, Advisor) | "...That's a reasonable instinct. I'll use a conservative working assumption of six months of base salary and replace it if you ever learn the real terms. On that basis, cash plus severance would cover well over a y..." | consistent, salary never stated | human check (optional) |
| CLIENT-001 | Ren's 14k is the program fee, flights and housing, not extra tuition | INT-091 (2026-05-04, Client) | "...Ren wants to spend a semester in Seoul next spring. The program would cost about fourteen thousand beyond normal tuition...." | consistent | none |

Notes per fact (arithmetic and cross-checks):

- **CLIENT-002 - Ethan has an existing college account of about $85k:** Single mention; Ethan is 17 (profile) and applying this autumn; nothing contradicts it.
- **CLIENT-002 - Walk-away price of $22M against the $24M letter of intent:** Arithmetic checked: 2M price drop x ~0.6 after tax = 1.2M; 13.5M cash at closing (INT-058) -> 12.3M as stated in INT-077. Recorded as a floor 'where I am today', not a decision, and not restated after the INT-083 decision to sell; no contradiction. Related interactions: INT-077.
- **CLIENT-002 - Retention pool of up to ~$1.2M, ~$700k after tax:** Tentative idea, never decided; stays tentative in the manifest. The 700k after-tax cost implies roughly a 40% tax effect, stated by the advisor as 'roughly'.
- **CLIENT-002 - $55k pre-signing costs ($40k retainer + $15k advance) paid from cash:** 40k + 15k = 55k. Cash 220k -> 165k afterwards; no later meeting quotes a Rebecca cash balance, so nothing contradicts the new level.
- **CLIENT-002 - 12-month paid consulting role after closing (corrects 'salary stops at closing'):** The earlier assumption is preserved in INT-130 (advisor says income stops at closing) and explicitly superseded in INT-142 (later). Correction wording attributes the error to the advisor's assumption. Related interactions: INT-142.
- **CLIENT-003 - Lucia born Tuesday 3 March; the 4 March meeting is a call from hospital:** INT-045 (2 March) has her at 39 weeks, 'any day now'; January said baby due in March; 5 weeks later (10 April) Lucia smiles for the first time. Token date resolves to Tuesday, March 3 (a Tuesday). Related interactions: INT-045, INT-071.
- **CLIENT-003 - Manager hinted at a four-day week (about -20% salary):** Tentative in INT-045; closed as 'not now' in INT-147; no intermediate contradiction. Related interactions: INT-147.
- **CLIENT-003 - Sofia's student loans are federal:** INT-019 gives $48k at 5.2% without a type; nothing contradicts federal.
- **CLIENT-004 - Survivor pension is 60% of Samuel's pension (corrects 'about half'):** Original 'about half' statement is in INT-029 (9 Feb); INT-050 (9 Mar) explicitly corrects it and keeps the original. No later mention uses 'half' for Grace. Related interactions: INT-029, INT-050.
- **CLIENT-004 - First investing step on 1 June of ~$24k (one of six):** Arithmetic: 6 x 24k = 144k = 10 points of $1.45M, i.e. 15% -> 25% as agreed in INT-097; a 20% fall of 24k = 4.8k as stated. INT-097 says only 'starting next month'; the date 1 June (a Monday) first appears in INT-110 and is confirmed by the advisor. v1 had the investing-step meetings dated before this; fixed in revision 2. Related interactions: INT-122, INT-128.
- **CLIENT-004 - Annual house cost of 'fifteen thousand or more':** Client states property tax ~9k and insurance 2k (=11k) plus unquantified upkeep; the 15k is the advisor's estimate. Not contradicted anywhere.
- **CLIENT-005 - April tax shortfall of $20-25k, covered ~22k from invoices and ~3k from cash:** Feb 25 -> Apr 15 is seven weeks as stated. INT-076 closes the loop (22k of 25k from invoices, rest from cash). Estimates paid for 2025 (35k) were his own guess (INT-016), consistent with the shortfall being on the 2025 return. Related interactions: INT-076.
- **CLIENT-005 - Estimated taxes use the safe-harbor method (110% of last year's tax):** Decided INT-016, applied INT-118; 110% applies because net income (~190k) exceeds the 150k threshold. Related interactions: INT-016, INT-118.
- **CLIENT-005 - Remaining Vantora position about a sixth of liquid assets after three sales:** v2 text said 'a fifth' and cited the hold-until-Q3 decision on 29 May, eleven days before it was made (9 June). Arithmetic: ~33% start, -30% fall, 40% trimmed => ~14-17%. Text is now step-aware: starting-size wording until the third sale, 'about a sixth' only after it. Related interactions: INT-034, INT-115.
- **CLIENT-006 - Required distribution about $31k; $6k of it via the church gift:** 31k - 6k = 25k as stated in INT-129. No IRA balance appears in any dialogue, so 31k cannot be tied to a balance; plausible for age 74 with a ~0.8M IRA. Related interactions: INT-129.
- **CLIENT-006 - Aide five days a week at ~$6,400 a month; spending $10,100; portfolio gap $3,800:** 5,200 -> 6,400 is +1,200 and 8,900 -> 10,100 as stated. But the gap was given as 4,200 (and 3,000 in INT-009) while January's own figures give 8,900 - 6,300 = 2,600, so the new gap is 10,100 - 6,300 = 3,800. INT-009 and INT-119 corrected; the January statements are untouched. Related interactions: INT-119.
- **CLIENT-006 - Remaining required amount taken monthly Jul-Dec with 12% withheld:** The 12% is attributed to the accountant Dennis, who is mentioned earlier (INT-028, INT-085), but 12% appears first in the advisor's own turn in INT-129. Not contradicted. Left as is: it is the advisor reporting an accountant's figure.
- **CLIENT-006 - Maturing bond rung of about $50k:** At the corrected 3,800 a month, one year of withdrawals is ~46k, so a ~50k rung fits. No earlier statement gives a rung size.
- **CLIENT-006 - Frank's pension survivor amount unconfirmed (carried forward):** INT-009 promised to return to it 'when we look at the care costs'; INT-041 and INT-119 did not. INT-119 now carries it forward as still unconfirmed with Peg's own estimate of about half used for the stress case. No answer is invented. Related interactions: INT-009, INT-119.
- **CLIENT-001 - Working assumption of six months' base salary as severance:** 'Well over a year' of cover depends on a base salary that is not stated in any dialogue; with 150k cash alone ~13 months, the claim holds for any plausible salary. Not contradicted.
- **CLIENT-001 - Ren's 14k is the program fee, flights and housing, not extra tuition:** INT-091 (client: 'beyond normal tuition') -> INT-114 (advisor note had it as extra tuition; client corrects). Original statements preserved. Related interactions: INT-114.

## 4. INT-148

- Status: **unresolved / follow-up pending**. Pending: broker quotes for disability cover; Lucas wants the cover but has bought nothing and decided nothing. Decision made: **False**. Advisor commitment in the dialogue: **False** (The advisor recommends checking in with the broker and records the item as pending; no advisor task is promised.)
- Client action logged: check in with your broker so he has what he needs for the disability insurance quotes.
- Ledger: Represented as the open client item l_ins (2 deferrals). Its description is the original request (send policy summaries); the dialogue evolved to broker quotes. The drift is documented in the manifest's ledger_annotations, and counts are unchanged.

## 5. The 1 July commitment

- call Grace before the July 1 purchase if the stock holding is down more than ten percent since the first purchase (made in INT-128, due 2026-07-01, only if the holding is down more than 10 percent since the 1 June purchase).
- Held in the manifest's commitments_beyond_observation_window, outside the 48/40/8 in-window ledger, with status 'outstanding_beyond_observation_window'. No meeting was added or extended.

## 6. Half-year reviews (INT-145 to INT-150)

| Review | Client | Recap claims traced to earlier dialogue | Open ledger items referenced | Unresolved threads not carried into the review |
|---|---|---|---|---|
| INT-145 | CLIENT-006 | 6/6 | p_poa | Frank's pension survivor amount (unconfirmed since INT-009; carried forward in INT-119 only) |
| INT-146 | CLIENT-004 | 7/7 | none (client has no outstanding ledger item or none named) | whether Folake's share should be held in trust (INT-124); downsizing the house, deferred to at least November (INT-135); second investing step on 1 July with a conditional advisor call (INT-128; beyond the window) |
| INT-147 | CLIENT-003 | 7/7 | s_will | childcare gap covered by trimming brokerage contributions (decided, not open); Mateo's job-offer details (ledger item s_offer, moot) |
| INT-148 | CLIENT-005 | 8/8 | l_ins | annualized-installment tax method, reconsidered for Q3 (INT-118); any further Vantora reduction deferred to after Q3 earnings (carried in the snapshot line, not in the recap) |
| INT-149 | CLIENT-001 | 8/8 | k_estate, k_spouse_ss | Roth conversion deferred to the second-half review (INT-141 neighbourhood) |
| INT-150 | CLIENT-002 | 7/7 | r_spa, r_estate | Ethan's college funding gap (INT-038); retention pool for key staff (INT-121); 22M walk-away floor (INT-077) |

No review was rewritten to force a decision. Each recap sentence is evidenced by earlier dialogue; no historical preference is stated as current without evidence, apart from the template-derived snapshot caveat under accepted limitations.

## 7. Manual checks performed

- Read INT-148 and INT-145 in full before and after the correction; read INT-009, INT-115, INT-119, INT-148 in their revised form
- Read the earlier and later mentions of every new fact (greps over the CSV by client) and compared them with the client profile and storyline
- Recomputed the arithmetic behind each stated figure (see notes in the fact table)
- Compared each half-year recap sentence with the earlier dialogue it summarises (automated phrase search plus reading)
- Checked each outstanding ledger item against the last dialogue about it
- Confirmed that the corrected text changed no other interaction by diffing the CSV against the pre-final snapshot

## 8. Checks that still require a human reviewer

- Tax plausibility of the retention-pool after-tax cost (INT-121) and of the 'safe harbor' explanation (INT-016); advisor figures are stated as approximate
- Whether a 31k required distribution (INT-085) and a ~50k bond rung (INT-137) are acceptable without a stated IRA balance or ladder schedule
- The 12% withholding attributed to the accountant (INT-129) first appears in the advisor's own turn
- Whether 'a sixth' (INT-148) is acceptable as an approximation of the remaining Vantora position
- Spot-check that the rewritten meetings still read as natural speech (this audit does not assess realism)
- Independent confirmation of this audit: it was produced by the same assistant that wrote the revision

## 9. Confirmed inconsistencies

- Fixed: INT-009/INT-119 gap arithmetic
- Fixed: INT-115/INT-148 premature Vantora decision and share figure
- Fixed: INT-148 missing pending status and closing contradiction
- Fixed: INT-119 unkept advisor promise to revisit Frank's pension survivor amount
- Still open: none
- Ledger note: CLIENT-003:s_offer is counted as outstanding but the dialogue made it moot; kept to preserve 48/40/8 and annotated in the manifest.

## 10. Accepted limitations

- Template-derived portfolio snapshot lines state allocations from generator state; for CLIENT-004 (15% then 17%) and CLIENT-005 (about 90%, 'matches your current target') the target was never stated in dialogue
- The six half-year reviews do not list every unresolved thread (see unresolved_threads_not_carried_into_review per review); the manifest holds them
- Priya's conditional 1 July call falls after 30 June and is outside the ledger by design
- Advisor 'I'll call/arrange' statements logged as a_action in closings are not all in the ledger (the ledger tracks only structured items; v1 behaviour)
- Meetings are shorter than v1 (mean 14.7 turns); routine segments remain templated (28.6% exact-repeat turns); voices are least differentiated in the preserved v1 scripts
- 'In January you said no large irreversible decisions for twelve months' (INT-135) slightly overstates the advisor's 'six to twelve months' that Grace agreed to in INT-005
- The automated 'no substantive new information' proxy is not discriminating (see comparison report)

## 11. Reproduce

```bash
python src/generate_dataset.py
python src/validate_dataset.py
python src/compare_quality.py
python src/final_audit.py
```
