"""Loads <repo>/.env into os.environ on first import (stdlib only). Variables already set in the real environment win."""
import os
from pathlib import Path

_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ENV.is_file():
    for _ln in _ENV.read_text(encoding="utf-8").splitlines():
        _ln = _ln.strip()
        if _ln and not _ln.startswith("#") and "=" in _ln:
            _k, _v = _ln.split("=", 1)
            if _v.strip():  # blank values stay unset so code defaults apply
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
