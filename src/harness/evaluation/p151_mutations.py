"""P1.5.1 - reproducible mutation evaluation for the P1.5 / P1.5.1 components.

Each mutation is applied to a THROWAWAY COPY of the repository (a temp directory); the real working tree is only read.  The named test selection must FAIL (the mutation is "killed"); if
it still passes the mutation "survived" - a gap in the tests.  A pattern that no longer matches the source is reported as NOT-APPLIED, never as killed.

    PYTHONPATH=src python -m harness.evaluation.p151_mutations --out <dir>      # writes <dir>/mutations.json and <dir>/mutations.md

Only components listed here are claimed as mutation-tested.  Runtime: a few minutes (one pytest process per mutation).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
T1 = "tests/test_p151_report_verification.py"
T2 = "tests/test_p143_report_authority.py"
T3 = "tests/test_p15_evaluation.py"
CL = "src/harness/clause_support.py"

# (id, intended defect, component, file, old, new, tests that must detect it)
MUTATIONS = [
    ("M01", "clause-level check disabled: an appended unsupported clause passes", "report verifier clause matching", CL, "    if not ENABLED or not sources:", "    if True:", [T1, "-k", "probe or categories or pipeline"]),
    ("M02", "actor comparison disabled: a spouse's value verifies the client's", "report verifier subject consistency", CL, 'elif _outsiders(c["actors"]) != _outsiders(best["actors"]) or', "elif False and", [T1, "-k", "probe or categories"]),
    ("M03", "a person that the source never mentions may be added to a finding", "report verifier subject consistency", CL, 'stranger = sorted(_outsiders(c["actors"]) - src_outsiders)', "stranger = []", [T1, "-k", "clause_unit"]),
    ("M04", "any unit word is forgiven: 'a month' accepted for 'a year'", "report verifier numeric / unit matching", CL, 'if uncovered == ["year"] and not (best["stems"] & _TIME_UNITS):', "if uncovered:", [T1, "-k", "probe or categories"]),
    ("M05", "negation parity disabled in the report check", "report verifier negation / modality", CL, 'elif c["neg"] != best["neg"]:', "elif False:", [T1, "-k", "probe or categories"]),
    ("M06", "hearsay / attribution parity disabled", "report verifier negation / modality", CL, 'elif c["attrib"] != best["attrib"]:', "elif False:", [T1, "-k", "clause_unit"]),
    ("M07", "completed-versus-planned action check disabled", "report verifier temporal meaning", CL, "            if done:", "            if False:", [T1, "-k", "probe or categories"]),
    ("M08", "retraction anywhere in the excerpt ignored", "report verifier retraction", CL, "if any(support.retracted(s) for s, _ in sources):", "if False:", [T1, "-k", "probe or categories"]),
    ("M09", "the actor is dropped from the cache key", "cache key isolation", "src/harness/cache.py", 'actor or "-"', '"-"', [T3, "-k", "cache"]),
    ("M10", "the client scope is dropped from the cache key", "cache key isolation", "src/harness/cache.py", '(json.dumps(sorted({str(c) for c in clients})) if clients else "-")', '"-"', [T3, "-k", "cache", ]),
    ("M11", "conflict creation / resolution no longer changes the data version", "cache invalidation", "src/harness/runtime.py", ":c{extra['c']}:x{extra['x']}", "", [T3, "-k", "cache"]),
    ("M12", "the releasing actor is no longer checked", "approval withholding", "src/harness/approval.py", 'if actor is not None and (not (actor.actor_id or "").strip() or not actor.authenticated):', "if False:", [T1, "-k", "unauthenticated"]),
    ("M13", "compression drops caveat-bearing lines first again", "compression retention", "src/harness/compression.py", "CRITICAL_LAST = True", "CRITICAL_LAST = False", [T1, "-k", "default_compression"]),
    ("M14", "the omission marker no longer names what was omitted", "compression disclosure", "src/harness/compression.py", 'return [name for key, name in _KINDS if any(key in ln for ln in lines)]', "return []", [T1, "-k", "markers"]),
    ("M15", "every memory is classed verified in the report evidence", "evidence provenance / authority", "src/agents/report/evidence.py", "evidence_class=ecls,", 'evidence_class="verified",', [T2]),
    ("M16", "findings on non-verified memory are no longer withdrawn", "report authority split", "src/agents/report/validate.py", "        if bad:\n            moved.append(", "        if False:\n            moved.append(", [T2]),
    ("M17", "the report verifier ignores the memory layer's verdict", "report authority (verifier guard)", "src/harness/evidence_verifier.py", 'if e.evidence_class in ("unverified", "contradicted", "open_conflict"):        # P1.5.1', "if False:        # P1.5.1", [T1, "-k", "refuses_any_finding"]),
    # ---- P1.5.3: direct mutants of the condition logic (clause_support.conditions / _is_tax_qualifier / the source-vs-claim comparison).  M01 disables the whole check and is NOT counted as coverage of these.
    ("N01", "the tax-qualifier exception is removed: 'a month after tax' becomes a condition", "condition detection (tax exemption)", CL, 'if t == "after" and not _is_tax_qualifier(tk, i):', 'if t == "after":', [T1, "-k", "test_p153_amount_qualifier_after_tax_is_not_a_condition"]),
    ("N02", "every 'after' is exempt: 'after the sale' / 'after retirement' are no longer conditions", "condition detection (exemption scope)", CL, 'if t == "after" and not _is_tax_qualifier(tk, i):', "if False:", [T1, "-k", "test_p153_after_retirement_and_after_the_sale_remain_conditions"]),
    ("N03", "the old comparison is restored (raw 'after' token on both sides, no tax exception)", "condition comparison", CL, 'elif conditions(best["toks"]) - conditions(c["toks"]):', 'elif (best["toks_set"] & CONDITION) - c["toks_set"]:', [T1, "-k", "test_p153_amount_qualifier_after_tax_is_not_a_condition"]),
    ("N04", "claim-side masking: the claim's raw tokens hide a source condition", "condition comparison (claim side)", CL, 'elif conditions(best["toks"]) - conditions(c["toks"]):', 'elif {k for k in conditions(best["toks"]) if k.split(":")[0] not in c["toks_set"]}:', [T1, "-k", "test_p153_a_claim_side_tax_qualifier_cannot_mask_a_source_condition"]),
    ("N05", "the source-versus-claim condition comparison is removed", "condition comparison", CL, 'elif conditions(best["toks"]) - conditions(c["toks"]):', "elif False:", [T1, "-k", "test_p153_after_retirement_and_after_the_sale_remain_conditions"]),
    ("N06", "the comparison is reversed (claim minus source)", "condition comparison", CL, 'elif conditions(best["toks"]) - conditions(c["toks"]):', 'elif conditions(c["toks"]) - conditions(best["toks"]):', [T1, "-k", "test_p153_after_retirement_and_after_the_sale_remain_conditions"]),
    ("N07", "shape (b) broadened: 'after tax season' / 'after tax filing' are exempt", "tax exemption scope", CL, "return rest[0] in AMOUNT_NOUNS and all(", "return True or all(", [T1, "-k", "test_p153_after_tax_season_remains_a_condition"]),
    ("N08", "shape (a) broadened: 'I retire after tax' is exempt without an amount cue", "tax exemption scope", CL, 'return any(t in AMOUNT_CUES or t.startswith("$") for t in tk[:i])', "return True", [T1, "-k", "test_p153_after_tax_season_remains_a_condition"]),
    ("N09", "exemption is per clause, not per occurrence: one tax qualifier hides a real 'after'", "tax exemption (per occurrence)", CL, 'if t == "after" and not _is_tax_qualifier(tk, i):', 'if t == "after" and not any(_is_tax_qualifier(tk, j) for j in range(len(tk))):', [T1, "-k", "test_p153_a_source_with_both_a_tax_qualifier_and_a_real_condition_stays_conditional"]),
    ("N10", "the 'after' key ignores what follows it: any claim 'after' covers any source 'after'", "condition comparison (key)", CL, 'found.add(f"after:{nxt}")', 'found.add("after:")', [T1, "-k", "test_p153_a_different_after_in_the_claim_does_not_cover_the_sources_after"]),
    ("N11", "determiners are not skipped: 'after the sale' no longer equals 'after sale'", "condition comparison (key)", CL, "if w not in _DETERMINERS), \"\")", "if True), \"\")", [T1, "-k", "test_p153_a_different_after_in_the_claim_does_not_cover_the_sources_after"]),
]


def _copy_repo(dst: Path) -> None:
    for d in ("src", "tests", "sql", "api"):
        shutil.copytree(ROOT / d, dst / d, ignore=shutil.ignore_patterns("__pycache__"))
    (dst / "data").mkdir()
    shutil.copytree(ROOT / "data" / "seed", dst / "data" / "seed")
    for p in (ROOT / "data").iterdir():
        if p.is_file():
            shutil.copy(p, dst / "data" / p.name)
    for f in ("requirements.txt", "pytest.ini", "pyproject.toml", "conftest.py"):
        if (ROOT / f).exists():
            shutil.copy(ROOT / f, dst)


def run(out: Path, only=None) -> list:
    work = Path(tempfile.mkdtemp(prefix="cmf_p151_mut_"))
    _copy_repo(work)
    results = []
    for mid, defect, comp, f, old, new, tests in MUTATIONS:
        if only and mid not in only:
            continue
        path = work / f
        raw = io.open(path, encoding="utf-8", newline="").read()
        crlf = "\r\n" in raw
        s = raw.replace("\r\n", "\n")
        if old not in s:
            results.append(dict(id=mid, defect=defect, component=comp, file=f, tests=" ".join(tests), result="NOT-APPLIED", evidence="pattern not found in the source"))
            continue
        mutated = s.replace(old, new, 1)
        io.open(path, "w", encoding="utf-8", newline="").write(mutated.replace("\n", "\r\n") if crlf else mutated)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-x", "-rf", "-p", "no:cacheprovider", *tests], cwd=work, env=env, capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or [""])[-1][:120]
        failed = [ln[7:110] for ln in r.stdout.splitlines() if ln.startswith("FAILED ")]
        if failed:
            last += " | killed by " + failed[0]                                          # P1.5.3: name the test that actually failed
        results.append(dict(id=mid, defect=defect, component=comp, file=f, tests=" ".join(tests), result="KILLED" if r.returncode != 0 else "SURVIVED", evidence=last))
        io.open(path, "w", encoding="utf-8", newline="").write(raw)                       # restore the copy
    shutil.rmtree(work, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mutations.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    lines = ["| ID | Component | Intended defect | Detecting tests | Result | Evidence |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['component']} | {r['defect']} | `{r['tests']}` | **{r['result']}** | {r['evidence']} |" for r in results]
    (out / "mutations.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args(argv)
    res = run(Path(a.out), a.only)
    for r in res:
        print(f"{r['id']} {r['result']:11s} {r['component']}: {r['defect']}")
    k = sum(r["result"] == "KILLED" for r in res)
    for grp, label in (("M", "P1.5.1 original"), ("N", "P1.5.3 condition logic")):
        g = [r for r in res if r["id"].startswith(grp)]
        if g:
            print(f"  {label}: attempted {len(g)}  killed {sum(r['result'] == 'KILLED' for r in g)}  survived {sum(r['result'] == 'SURVIVED' for r in g)}  not-applied {sum(r['result'] == 'NOT-APPLIED' for r in g)}")
    print(f"attempted {len(res)}  killed {k}  survived {sum(r['result'] == 'SURVIVED' for r in res)}  not-applied {sum(r['result'] == 'NOT-APPLIED' for r in res)}")
    return 0 if k == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
