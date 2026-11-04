"""Generate data/conversations.csv deterministically (template/authored dialogue, no LLM).

Usage:  python src/generate_dataset.py [--seed 20260101] [--out data/conversations.csv]
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cmfgen.build import build  # noqa: E402
from cmfgen.revisions import BEYOND_WINDOW, LEDGER_ANNOTATIONS  # noqa: E402

COLUMNS = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index",
           "speaker", "utterance", "meeting_context", "source_type", "consent_status"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--out", default=str(ROOT / "data" / "conversations.csv"))
    ap.add_argument("--manifest", default=str(ROOT / "reports" / "generation_manifest.json"))
    a = ap.parse_args()
    meetings, ledger = build(a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(COLUMNS)
        for m in meetings:
            for i, (spk, text) in enumerate(m["turns"], 1):
                w.writerow([m["interaction_id"], m["client"]["client_id"], m["client"]["advisor_id"],
                            m["dt"].strftime("%Y-%m-%dT%H:%M:%S"), m["channel"], i, spk, text,
                            m["context"][:1].upper() + m["context"][1:], "synthetic_meeting", "synthetic"])
    # Authoring-side ground truth for later evaluation. NOT part of the source evidence.
    man = Path(a.manifest)
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(json.dumps({
        "note": "Generator-side QC manifest. Never feed to the CMF ingestion pipeline.",
        "seed": a.seed,
        "interactions": [dict(interaction_id=m["interaction_id"], client_id=m["client"]["client_id"],
                              step=m["step"], scenario_tags=m["tags"],
                              authored_outcomes=[list(x) for x in m["log"]]) for m in meetings],
        "action_ledger": ledger,
        "commitments_beyond_observation_window": [
            dict({k: v for k, v in c.items() if k not in ("client_idx", "step")},
                 client_id=next(m["client"]["client_id"] for m in meetings if m["client"]["client_id"].endswith(str(c["client_idx"] + 1))),
                 made_in=next(m["interaction_id"] for m in meetings
                              if m["client"]["client_id"].endswith(str(c["client_idx"] + 1)) and m["step"] == c["step"]))
            for c in BEYOND_WINDOW],
        "ledger_annotations": LEDGER_ANNOTATIONS}, indent=1), encoding="utf-8")
    print(f"Wrote {out} ({len(meetings)} interactions, {sum(len(m['turns']) for m in meetings)} turns)")


if __name__ == "__main__":
    main()
