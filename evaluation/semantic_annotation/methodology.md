# Semantic annotation evaluation: methodology

Status: preparation only. No human annotation exists yet, so **no accuracy figure for the current rules is claimed here**. This document defines how accuracy will be measured and which numbers are already computable.

## 1. Principles
1. Existing rule-generated labels are *system output under test*, never ground truth. They appear only in `items.jsonl` (analyst view). Annotators use `review_sheet_blind.md`, which shows source text and context but no system label, stratum or defect hint, so they are not anchored.
2. Expected annotations are written only into `annotation_template.csv` / `segmentation_template.csv` (blank, human-filled). The filled files are separate artefacts; the scorer joins them to the system view by `item_id`.
3. Uncertainty is first-class: every field accepts `unresolved`; `none` means "nothing applies" and is distinct from blank ("not annotated"). `unresolved` rows are excluded from precision/recall and reported as a count.
4. What a speaker says is separate from what they attribute to someone else: `decision_maker`, `decision_status` (`reported_by_other`) and `attributed_to`. An advisor recap is never promoted to a confirmed client decision.
5. A correction target needs explicit evidence (`correction_evidence` other than `none`). Shared vocabulary is not evidence; the scorer rejects a target with no evidence (`invalid_gold_target_without_evidence`).
6. No LLM-generated label is used as ground truth. Two independent human annotators are recommended for a subset, and inter-annotator agreement (Cohen's kappa for single-label fields, per-class Jaccard for multi-label) should be reported before the labels are trusted.
7. The source evidence is immutable. The builder opens the evidence database with `mode=ro` and writes only inside `evaluation/semantic_annotation/`.

## 2. Sample
Deterministic and stratified (`scripts/semantic_eval.py build`): candidates for each of 25 strata are ordered by `sha1(stratum|turn_id)`, taken round-robin over Meetings, Calls, Email, Chat (turns not yet chosen are preferred), then the known-defect turns are added unconditionally. Strata include both system-labelled and *unlabelled-but-cue-bearing* turns (possible false negatives) plus a control stratum of turns where no rule fired. The sample is **not** a random sample of the corpus, so it measures per-label error behaviour; it must not be used to estimate corpus-wide prevalence. A separate set of 20 containers (5 per channel, chosen for boundary-relevant flags) supports segmentation scoring.

## 3. Metrics
"Computable now" needs no human input. "Needs annotation" is computed by `score` once the template is filled.

| Dimension | Metric | Definition | Status |
|---|---|---|---|
| Topic | per-label and micro P/R/F1; macro-F1 | System topic of a turn = the label of its segment. Gold = `topics` (multi-label). tp: label in both; fp: only system; fn: only gold. | Needs annotation. Computable now: label distribution, per-cue breadth and matched surface forms (`diagnostics.json`). |
| Dialogue acts | per-class P/R/F1, micro, macro-F1 | System acts mapped to the gold vocabulary (`information`->`fact`, `deferral_unresolved`->`deferral`). Classes the rules cannot emit (`request`, `answer`, `preference`, `exploration`, `other`) are reported as unsupported: recall 0 by construction. | Needs annotation. Computable now: label counts, turns with 3+ acts, default-only turns, regex-detectable false-positive families. |
| Decision attribution | attribution precision; recall; error breakdown | For turns the system labels `decision`: correct iff gold is first-hand (`decision_status=confirmed`) and `decision_maker` equals the system-implied maker (the speaker's party). Errors are split into `recap_read_as_decision` (gold `reported_by_other`), `not_a_confirmed_decision` (exploratory, recommendation, conditional), `no_decision_at_all`, `wrong_decision_maker`. Recall: first-hand gold decisions found with the right maker. | Needs annotation. Computable now: 75 of 97 decision labels sit on advisor turns. |
| Correction relations | relation precision; target accuracy; recall | Precision: share of system `corrects` rows where gold has a correction link. Target accuracy: among those, share with the same target turn. Recall: gold links found. | Needs annotation. Computable now: date check (a correction text naming "my February 18 email" vs the system target; `diagnostics.json: corrects_date_check`). This is candidate evidence for the annotator, not ground truth. |
| Question-answer | relation precision; target accuracy; recall | Same definitions with `answers_turn`. | Needs annotation. Computable now: 143 of 692 `answers` links cross a segment boundary; 13 answer a turn holding two or more questions. |
| Temporal | exact and lenient P/R/F1 | Gold `temporal_refs` (verbatim) vs system `temporal_ref` values, lowercased. Lenient counts a match when one string contains the other. Modal 'may'/'will' are not dates. | Needs annotation. Computable now: 19 of 27 capitalisation-insensitive "May" hits are the modal verb. |
| Segmentation | boundary P/R/F1 (exact and within one turn); related-group preservation; over-split ratio | Gold boundaries = turn ids that start a new segment. Preservation = share of annotator-declared related groups (Q+A, claim+correction) that lie wholly inside one system segment. Over-split = system segments / gold segments. | Needs annotation. Computable now: 156 of 810 relations cross a boundary, 33 single-turn segments, 33 mixed-topic segments, 8 of 22 email threads and 4 of 28 chats split. |

## 4. Reporting rules
Report counts with every ratio (the sample is small; per-class supports will often be below 20), report the unresolved fraction, and report unsupported classes separately rather than averaging them in silently. Before drawing conclusions, compute annotator agreement; a class with kappa below 0.6 needs a sharper definition, not a better classifier.

## 5. Running
```
python scripts/semantic_eval.py build                       # regenerate artefacts (deterministic)
python scripts/semantic_eval.py diagnose                    # print the computable diagnostics
python scripts/semantic_eval.py score --items evaluation/semantic_annotation/items.jsonl --gold filled.csv \
       [--seg-items evaluation/semantic_annotation/segmentation_items.jsonl --seg-gold filled_seg.csv]
```
