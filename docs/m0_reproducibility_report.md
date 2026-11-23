# M0 report: reproducible baseline

Worktree `C:\Users\Mohor\Documents\Cognitive Memory Fabric - Bhavya Baseline`, branch `feature/llm-first-ingestion`, HEAD `8081eaa` (unchanged; nothing staged, committed or pushed). AI-assisted engineering, not human validation. Evidence labels: **[RUN]** observed command output, **[CODE]** read from source.

## 1. Result
| | Before (audit run) | After M0 |
|---|---|---|
| Command | `python -m pytest tests -q -p no:cacheprovider -rfEsx` | same |
| Passed | 1363 | **1485** |
| Failed | 64 | **1** |
| Errors | 10 | **0** |
| Skipped | 33 | **0** |
| xfailed | 1 | 1 (documented known residual, unchanged) |
| Persistent files created in `build/` | 1 (`cmf_memory.db`) | **0** (verified by a session guard) |

[RUN] The 1485 includes 16 new tests (`tests/test_environment_isolation.py`) and the 33 previously skipped evidence-evaluation tests, which now run against a disposable database.

## 2. The causes found (reproduced, not assumed)
| # | Cause | Evidence | Fix |
|---|---|---|---|
| 1 | No `build/cmf_ops_smoke.db` in a fresh checkout (git-ignored; tests read it through `CMF_DATABASE_URL`'s default). 73 failures/errors across `test_tool_selector`, `test_lakeshore_remediation`, `test_mcp_tools`, `test_cmf_data`, `test_pipeline`, `test_harness_pipeline`. | [RUN] `FileNotFoundError ... create it with: python src/opdata/load_db.py --sqlite ...` | `tests/conftest.py` builds the operational database from the committed `data/operational/*.csv` with the repository's own loader into a disposable directory and points `CMF_DATABASE_URL` at it. Not copied from any demo/seed file (a test checks the hash differs from `cmf_ops_demo.db`). |
| 2 | No `build/cmf_evidence.db`: 33 `test_semantic_eval` tests were silently **skipped**. | [RUN] "build/cmf_evidence.db not present" | An on-demand session fixture `evidence_db` ingests into a disposable path with the repository's own ingestion; `test_semantic_eval.py` now reads that path (`CMF_EVIDENCE_DATABASE_URL`) and its helper no longer assumes protected files sit under the repository root. No assertion was changed. |
| 3 | The tests created an empty `build/cmf_memory.db` through the default memory database path. | [RUN] 241,664-byte schema-only file at suite start | `CMF_MEMORY_DATABASE_URL` is pointed at the disposable directory for every test. |
| 4 | **CRLF checkout.** Git for Windows has `core.autocrlf=true`; the committed files are LF (`git ls-files --eol`: `i/lf w/crlf`, 317 files). The dataset hash lock failed. **More seriously the checkout changed the data**: the text inside all 81 `datasets/emails/emails.csv` bodies gained `\r` (a CRLF checkout rewrites newlines inside quoted CSV fields), so prompts, excerpts and hashes differed from the committed data. | [RUN] 81/81 email bodies differed; `data/conversations.csv` 578,642 bytes vs committed 576,442 (2,200 extra CRs) | A narrow **`.gitattributes`**: `text eol=lf` for `*.csv`, `*.jsonl`, `*.sql`, JSON/SHA lock files under `datasets/`, `data/`, `evaluation/`, Markdown under `evaluation/`; `binary` for `*.db`/`*.sqlite*`. Source code, UI and documentation are deliberately not listed (no check depends on their line endings); there is no global rewrite. |

### 2.1 Restoring the already-checked-out files (verified, not blind)
The existing checkout still held CRLF copies. For the 62 files that the new attributes govern, a script wrote the committed bytes back **only after proving the difference was line endings alone** (`working.replace(CRLF, LF) == committed blob`: 62 of 62 passed, 0 skipped), then the index's stale stat entries were refreshed with `git checkout-index -u -f` over exactly those 62 paths (git otherwise reports "modified" for a file whose size changed, without comparing content). Afterwards `git ls-files --eol` shows `i/lf w/lf` for all 62, `data/conversations.csv` is `fd77a128...` (the committed hash), `git status` lists only the new/edited files, and nothing is staged. Seed databases remain `-text`. [RUN]

## 3. Database safety (M0.2)
* **Unconditional environment override** at conftest import, before any application module reads it: memory, operational, evidence and approval URLs all point into a `tempfile.mkdtemp` directory. A developer's shell or `.env` cannot redirect the tests to `build/`, `data/seed/` or a real database (a test asserts no URL points inside `build/`, `data/seed/` or `data/`).
* **Per-test reset.** One existing test (`test_memory_retrieval.py::test_default_url_and_env_switch`) deletes `CMF_MEMORY_DATABASE_URL` in a `finally`; every test therefore starts from the isolated values again (ordered tests prove the restoration).
* **Loader safety.** The operational loader deletes any existing file at its target; the conftest builder asserts the target is a fresh path inside the session directory (tested, including a refusal for `build/cmf_ops_smoke.db`).
* **Guard.** `pytest_sessionstart/finish` hash everything under `build/` and `data/seed/`; any created, changed or removed file prints `ISOLATION VIOLATION` and fails the session. The guard logic itself is unit-tested. In the full run it reported nothing: **`build/cmf_memory.db` and the three seed databases are byte-identical before and after** (the file left from the audit run was not touched).
* **Cleanup.** The session directory is removed at session end and at exit (best effort on Windows if a handle is still open).
* **Explicit initialisation.** Databases are created by explicit builders (`load_sqlite`, `ingest`), not as a side effect of opening a default path.
* **Limit.** A test that hands an explicit path to a database constructor can still open it; the guard detects the effect and cannot prevent the call.

## 4. Remaining failure (explained, not fixed)
`tests/test_semantic_eval.py::test_committed_package_is_not_stale` fails, deterministically. [RUN, diagnosed]
* Nine of the eleven committed evaluation files are **byte-identical** to what `scripts/semantic_eval.py` generates from a freshly ingested database. Two differ: `review_sheet_blind.md` (216,245 vs 216,713 bytes) and `known_defects.md` (19,229 vs 19,232). The generator emits 78 indentation-only blank lines (`"      \n"` where the committed copy has `"\n"`); the committed copies contain **0** lines with trailing whitespace.
* So it is a genuine, pre-existing mismatch between the committed artefact and its generator (probably trailing whitespace stripped when it was committed), **not** an environment problem: it reproduces in a correctly isolated environment and also reproduced on the previous branch. Fixing it needs either regenerating the committed package or changing the generator, and the task forbids weakening or regenerating to make tests pass, so it is reported and left.

## 5. Files
**Created:** `.gitattributes`; `tests/test_environment_isolation.py`; `docs/m0_reproducibility_report.md`.
**Modified:** `tests/conftest.py` (isolation); `tests/test_semantic_eval.py` (database path from the environment, an autouse module fixture that builds it, and the protected-file key for a path outside the repository; no assertion changed).
**Restored to committed bytes (content unchanged):** 62 tracked data/schema/evaluation files that a CRLF checkout had altered (list in the scratchpad `m0_restored.json`; categories: `data/`, `datasets/`, `evaluation/semantic_annotation/`, `sql/`).
No product source was modified in M0.

## 6. Acceptance
* Fresh checkout does not need local databases to run the tests: **met** by construction and shown by the run (the only `build/` file present was an empty one the tests no longer read). A truly fresh clone was **not** tested (it would not contain these uncommitted changes); the LF behaviour for a new clone follows from `.gitattributes`.
* Isolated temporary databases; no persistent seed/demo database modified: **met** [RUN guard].
* Dataset integrity checks work consistently: **met** (`test_multichannel_dataset_validation` passes; the email bodies are LF).
* Reproducible baseline with explained remaining failure: **met** (1 failed = section 4).

## 7. Limitations and risks
* The `.gitattributes` and test changes are uncommitted; teammates with `core.autocrlf=true` need them committed before they benefit. Existing clones with CRLF data files must renormalise them (`git add --renormalize .` is a staging action and was not run here).
* `git status` quirk: restoring bytes out of band leaves stale index stat data until refreshed; handled for these 62 paths with `git checkout-index -u`.
* Other tracked text files (Python, UI, docs) are still CRLF in this checkout; nothing depends on that.
