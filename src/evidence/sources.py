"""Reads dataset CSVs strictly through datasets/source_catalog.discover_source_files (evaluation ground truth is never reachable)."""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "datasets"
if str(DATASETS) not in sys.path:
    sys.path.insert(0, str(DATASETS))
import source_catalog  # noqa: E402


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Sources:
    """All source-native and derived rows, keyed by file name.  `files` is the exact list ingestion is allowed to read."""

    def __init__(self, datasets_root: Path | None = None) -> None:
        if datasets_root is not None:      # a copy of the dataset tree (tests); the catalog's rules still apply to its paths
            base = Path(datasets_root)
            files = [base / "conversations.csv"] + [base / f for ch in source_catalog.SOURCE_FILES.values() for f in ch] \
                + [base / source_catalog.MANIFEST] + [base / f for f in source_catalog.CRM_FILES]
            source_catalog.assert_no_evaluation_data(files)
            self.registry_path = base / "character_registry.csv"
        else:
            files = source_catalog.discover_source_files(include_crm=True)
            self.registry_path = DATASETS / "character_registry.csv"
        self.files = files
        self.by_name = {p.name: p for p in files}
        self._cache: dict[str, list] = {}

    def rows(self, name: str) -> list[dict]:
        if name not in self._cache:
            self._cache[name] = read_csv(self.by_name[name])
        return self._cache[name]

    def registry(self) -> dict[str, dict]:
        return {r["character_id"]: r for r in read_csv(self.registry_path)}

    def rel(self, name: str) -> str:
        p = self.by_name[name]
        try:
            return p.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return p.as_posix()

    def grouped(self, name: str, key: str) -> dict[str, list]:
        out = defaultdict(list)
        for r in self.rows(name):
            out[r[key]].append(r)
        return out
