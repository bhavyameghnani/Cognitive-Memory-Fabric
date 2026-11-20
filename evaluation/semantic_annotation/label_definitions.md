# Label definitions and permitted values

Fill `annotation_template.csv`. Multi-valued fields use `|`. Blank = not annotated; `none` = nothing applies; `unresolved` = cannot tell from the evidence.
Judge each turn from its own text and the context shown. Distinguish what the speaker SAYS from what the speaker ATTRIBUTES to someone else. Do not infer a correction target from shared vocabulary:
a `correction_target_turn` requires `correction_evidence` other than `none`. An advisor's recap is not a confirmed client decision.

## dialogue_acts

- **question**: The speaker asks for information. A question mark is neither necessary nor sufficient; an embedded or rhetorical '?' is not a question.
- **request**: The speaker asks the other party to DO something (send, check, confirm, call) rather than to say something.
- **answer**: The turn supplies the information a preceding question asked for. Say which question in answers_turn.
- **suggestion**: The speaker recommends or suggests a course of action without committing anyone to it ('I'd suggest', 'you could').
- **proposal**: The speaker puts a concrete plan or next step forward for acceptance ('let's schedule', 'shall we').
- **agreement**: The speaker agrees with something said earlier. A discourse opener ('Good morning', 'Right now', 'Okay so') is NOT agreement.
- **rejection**: The speaker declines or disagrees with a proposal, suggestion or claim. Courtesy phrases ('No problem', 'No apology needed') are not rejection.
- **preference**: The speaker states what they want or would rather do, without deciding ('I'd rather keep the cash', 'I don't want to run out of money').
- **correction**: The speaker states that an earlier statement, record or note (their own or another's) was wrong and gives the right version. Apology alone is not correction.
- **decision**: The speaker, FIRST-HAND, states a settled choice made by themselves or jointly with the other party. A choice reported on behalf of someone else is a decision_report: tag decision_status=reported_by_other.
- **commitment**: The speaker undertakes to do something specific. 'I'll have to think about that' is not a commitment.
- **deferral**: The speaker explicitly leaves something open, undecided or for later ('nothing is decided', 'let's revisit').
- **exploration**: The speaker is considering an option without settling it ('I'm thinking about', 'what if', 'leaning toward').
- **fact**: The speaker states information, a figure, an event or a status with no other act above.
- **social**: Greeting, thanks, courtesy or boilerplate carrying no planning content.
- **other**: A real act not covered above (explain in notes).
- **unresolved**: Reviewer cannot tell from the evidence. Use alone; the item is excluded from precision/recall and counted separately.

## decision_maker

- **client**: The client made the choice.
- **advisor**: The advisor made the choice (rare; advisors recommend).
- **joint**: Both parties explicitly agreed.
- **third_party**: Someone else (accountant, lawyer, family member).
- **none**: No decision is stated in this turn.
- **unresolved**: Cannot tell.

## decision_status

- **confirmed**: Settled and stated first-hand by the decision-maker or explicitly confirmed by them.
- **conditional**: Settled only if a stated condition holds.
- **exploratory**: Being considered, leaning, or hypothetical.
- **recommendation_only**: An advisor's recommendation the client has not accepted.
- **reported_by_other**: The speaker REPORTS or recaps a decision attributed to someone else. An advisor's recap is evidence of what the advisor says the client decided, not a confirmed client decision.
- **none**: No decision content.
- **unresolved**: Cannot tell.

## attributed_to

- **self**: The speaker is stating their own position or act.
- **client**: The speaker attributes the content to the client.
- **advisor**: The speaker attributes the content to the advisor.
- **third_party**: The speaker attributes it to someone else.
- **none**: No attribution.
- **unresolved**: Cannot tell.

## topics

- **retirement_planning**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **portfolio_allocation**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **liquidity_and_cash**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **estate_and_trusts**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **tax**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **insurance_and_care**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **housing_and_debt**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **business_and_equity_events**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **family_and_education**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **goals_and_planning**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **follow_up_commitments**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **scheduling_and_admin**: Keyword-lexicon topic (existing taxonomy; may be judged too broad in notes).
- **general_discussion**: No identifiable planning topic.
- **other**: A real topic outside this list (name it in notes).
- **none**: No topic content (pure social turn).
- **unresolved**: Cannot tell.

## correction_evidence

- **explicit_reference**: The text names the earlier statement (by date, medium or quoted content): 'my February 18 email', 'I wrote that ...'.
- **restated_value**: The turn restates the earlier item with a changed value and signals the change ('actually it is 60%').
- **self_repair**: The speaker repairs their own immediately preceding words.
- **none**: No supporting evidence in the text: do NOT give a correction target.

## confidence

- **high**: Clear from the text.
- **medium**: Reasonable but another reading exists.
- **low**: Weak evidence; consider 'unresolved'.

## Other fields

- **correction_target_turn**: a turn id from the same container, or `none` / `unresolved`.
- **answers_turn**: the turn id of the question this turn answers, or `none` / `unresolved`.
- **temporal_refs**: verbatim date/time expressions in this turn separated by `|`, or `none`. A modal verb ('may', 'will') is not a date.
- **Segmentation sheet**: `boundary_turns` = turn ids that begin a new topical segment (first turn excluded) or `none`; `segment_topics` = topics in order (`|`); `related_groups` = turn groups that must stay together, `;` between groups and `,` between ids.
