"""Demo databases that ship with the repo (data/seed/), so a fresh clone shows a populated brain map without running the LLM ingestion.

    python utils/seed_demo_db.py install [--force] [--dest build]   # copy the seed DBs to where .env.example expects them
    python utils/seed_demo_db.py export  [--memory PATH] [--ops PATH]  # maintainers: refresh the seeds from your local DBs

The seed memory DB holds CLIENT-001 (Kenji) ingested from the synthetic meetings. `export` drops working memories (per-question scratch notes that
expire after 4 h) together with every row that points at them. The ops DB is deterministic synthetic data (`python src/opdata/load_db.py --sqlite ...`).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "seed"
MEMORY_SEED, OPS_SEED = SEED / "cmf_memory_demo.db", SEED / "cmf_ops_demo.db"
# names that .env.example points CMF_MEMORY_DATABASE_URL / CMF_DATABASE_URL at
MEMORY_NAME, OPS_NAME = "cmf_memory.db", "cmf_ops_smoke.db"


def install(dest: Path, force: bool) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    clash = [d for d in (dest / MEMORY_NAME, dest / OPS_NAME) if d.exists()]
    if clash and not force:
        print("refusing to overwrite (use --force):", *clash, sep="\n  ")
        return 1
    shutil.copyfile(MEMORY_SEED, dest / MEMORY_NAME)
    shutil.copyfile(OPS_SEED, dest / OPS_NAME)
    print(f"installed {dest / MEMORY_NAME} and {dest / OPS_NAME}")
    return 0


def export(memory: Path, ops: Path) -> int:
    SEED.mkdir(parents=True, exist_ok=True)
    MEMORY_SEED.unlink(missing_ok=True)
    src, dst = sqlite3.connect(f"file:{memory}?mode=ro", uri=True), sqlite3.connect(MEMORY_SEED)
    src.backup(dst)
    src.close()
    working = "(SELECT memory_id FROM memory_records WHERE memory_type = 'working')"
    # the schema guards audit tables with append-only triggers; lift them for this offline copy only, then restore them verbatim
    triggers = dst.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall()
    with dst:
        for name, _ in triggers:
            dst.execute(f'DROP TRIGGER "{name}"')
        dst.execute(f"UPDATE memory_candidates SET memory_id = NULL WHERE memory_id IN {working}")
        for sql in (f"DELETE FROM memory_relationships WHERE source_id IN {working} OR target_id IN {working}",
                    f"DELETE FROM memory_evidence WHERE memory_id IN {working}",
                    f"DELETE FROM memory_lifecycle_events WHERE memory_id IN {working} OR related_memory_id IN {working}",
                    f"DELETE FROM working_context WHERE memory_id IN {working}",
                    "DELETE FROM memory_records WHERE memory_type = 'working'"):
            dst.execute(sql)
        for _, sql in triggers:
            dst.execute(sql)
    dst.execute("VACUUM")
    assert len(dst.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()) == len(triggers), "triggers not restored"
    counts = dict(dst.execute("SELECT memory_type, COUNT(*) FROM memory_records GROUP BY 1"))
    dst.close()
    shutil.copyfile(ops, OPS_SEED)
    print(f"wrote {MEMORY_SEED} {counts} and {OPS_SEED}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install")
    p.add_argument("--dest", type=Path, default=ROOT / "build")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("export")
    p.add_argument("--memory", type=Path, default=ROOT / "build" / "cmf_memory_openai.db")
    p.add_argument("--ops", type=Path, default=ROOT / "build" / OPS_NAME)
    a = ap.parse_args(argv)
    return install(a.dest, a.force) if a.cmd == "install" else export(a.memory, a.ops)


if __name__ == "__main__":
    sys.exit(main())
