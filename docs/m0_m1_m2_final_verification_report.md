# M0 / M1 / M2 final verification report

Worktree `...\Cognitive Memory Fabric - Bhavya Baseline`, branch `feature/llm-first-ingestion`, HEAD `8081eaa`. Nothing staged or committed. AI-assisted engineering; no human semantic validation.

| # | Item | Status | Evidence / detail |
|---|---|---|---|
| 1 | Files created/modified | Verified | See the three milestone reports (`m0_reproducibility_report.md`, `m1_attribution_safety_report.md`, `m2_staged_memory_report.md`, section "Files") and `git status`. The final verification task changed only two test assertions (123 to 130) and the docs. |
| 2 | Architecture before/after | Verified (description) | M2 report section 1. Direct pipeline remains; staged mode is an opt-in alternative. |
| 3 | Memory lifecycle | Partially verified | 9 states, DB-enforced transitions, audit events; 22 mechanism tests on temporary stores. Not exercised with a real LLM or real reviewers. |
| 4 | Advisor-reported decisions | Partially verified | Capped at `unconfirmed`, source `Advisor`, kept through promotion (tests 7, 8). Wording rules and recall are unmeasured on real data. |
| 5 | Client negation/uncertainty | Partially verified | M1 policy plus 45 tests; synthetic and a limited real-data audit. Accuracy not measured. |
| 6 | Provenance retention | Verified (mechanism) | Excerpt, offsets against original text, speaker, attribution preserved (tests 11, 12); matcher never guesses offsets. |
| 7 | Approval gate | Partially verified | Human-only authenticated Principal, content-hash-bound decisions, DB CHECKs. Tested only with a **simulated** Principal; no review API/UI. |
| 8 | Production write protection | Partially verified | Holds only for stores latched to staged mode (opt-in). Direct mode is the default; Bhavya's direct pipeline remains. **Not a universally enforced production approval boundary.** |
| 9 | Test results | Partially verified | See below. |
| 10 | Remaining failures/limitations | Verified | One pre-existing failure `test_semantic_eval.py::test_committed_package_is_not_stale` (untouched); limitations in M2 report sections 9 and 11. |
| 11 | DB preservation | Verified | SHA-256 of `data/seed/*.db` (3) and `build/cmf_memory.db` identical before and after this verification task. Tests use temporary databases. Not independently re-hashed across the earlier M0-M2 work beyond the checks recorded in the milestone reports. |
| 12 | Git status/branch | Verified | `feature/llm-first-ingestion`, `8081eaa`, 0 staged, 0 stashes, no new untracked files during this task. No commit, push, merge, rebase, reset, clean or branch switch. |
| 13 | Recommended next milestone | Recommendation | Wire a real review surface (API with real authentication and the PermissionGate `approve` role) only if separately approved; then legacy-row re-verification on a copy and a real-LLM recall measurement. |

## Test results (item 9)
All 48 test files were executed after M2 across six sequential batches (`python -m pytest <8 files> -q -p no:cacheprovider -rfEsx`). **No single uninterrupted full-suite run was completed after M2** (the first attempt was killed for low memory).

* Initial: 1541 passed, 12 failed, 1 xfailed, 0 skipped, 0 errors. Failures: 11 stale `user_version == 123` assertions plus the 1 pre-existing failure.
* Fix: the two stale assertions changed to 130 (no other edit).
* Reruns, reported separately: the two affected files 87 passed (exit 0); batch 4 379 passed (exit 0); batch 5 366 passed (exit 0). Batch 6 and the other 37 files were not rerun; the pre-existing failure remains.
* Unverified: a single-process full-suite pass after M2.
