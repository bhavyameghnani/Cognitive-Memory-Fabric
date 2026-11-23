"""Illustrations for the project documents, made with the OpenAI Images API.

    PYTHONPATH=src .venv/bin/python scripts/generate_images.py                 # every image in IMAGES that does not exist yet
    PYTHONPATH=src .venv/bin/python scripts/generate_images.py --only cover --force

Images are ILLUSTRATIONS only (no text, no data).  Diagrams that carry information (pipeline stages, labels, arrows) are drawn as SVG by hand
(docs/images/*.svg): image models do not render exact labels or arrows reliably.  The key is read only from OPENAI_API_KEY; nothing is logged.
Output: docs/images/<name>.jpg, plus docs/images/images.json (prompt, model, size, quality, date) for provenance.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import llm  # noqa: E402,F401  (loads .env)
from llm.client import _ssl_context  # noqa: E402  (certifi bundle: the macOS python.org build has no system CA store)

OUT = ROOT / "docs" / "images"
STYLE = ("Clean editorial illustration, dark navy background, soft blue, teal and amber accents, flat vector shapes with subtle depth, "
         "generous negative space, no text, no letters, no numbers, no logos, no human faces.")
IMAGES = {
    "cover": "Many thin, coloured threads arrive from the left, each one starting from a small icon of a different communication channel "
             "(a video call screen, a telephone handset, an envelope, a chat bubble). They are woven together in the centre into a tidy, glowing "
             "network of connected nodes, like a fabric or a constellation. A few nodes glow brighter than others. " + STYLE,
    "evidence": "A single glowing memory node in the foreground, connected by a fine bright line back to one highlighted sentence-shaped bar inside "
                "a stack of conversation bubbles. Other bubbles are dimmer. The image conveys that every memory points back to its exact source. " + STYLE,
    "timeline": "A horizontal timeline of small nodes; earlier nodes are hollow and fading, linked by curved arrows to newer solid nodes, the last one "
                "glowing. The picture conveys that old beliefs are superseded, never erased. " + STYLE,
}
# ESTIMATED USD per image at 1536x1024 (gpt-image-1 list prices; newer models may differ: check https://openai.com/api/pricing).
COST = {"low": 0.016, "medium": 0.063, "high": 0.25}


def generate(prompt: str, size: str, quality: str, model: str) -> bytes:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CMF_LLM_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set")
    body = {"model": model, "prompt": prompt, "size": size, "quality": quality, "n": 1, "output_format": "jpeg", "output_compression": 82}   # JPEG keeps the repo light
    req = urllib.request.Request("https://api.openai.com/v1/images/generations", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=300, context=_ssl_context()) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"OpenAI Images HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    return base64.b64decode(out["data"][0]["b64_json"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", choices=sorted(IMAGES))
    ap.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    ap.add_argument("--quality", default="medium", choices=sorted(COST))
    ap.add_argument("--size", default="1536x1024")
    ap.add_argument("--model", default="gpt-image-2")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "images.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    spent = 0.0
    for name in a.only or sorted(IMAGES):
        path = OUT / f"{name}.jpg"
        if path.exists() and not a.force:
            print(f"{name}: exists, skipped")
            continue
        path.write_bytes(generate(IMAGES[name], a.size, a.quality, a.model))
        spent += COST[a.quality]
        meta[name] = dict(file=path.name, model=a.model, size=a.size, quality=a.quality, prompt=IMAGES[name], created=date.today().isoformat())
        meta_path.write_text(json.dumps(meta, indent=1) + "\n")
        print(f"{name}: {path.relative_to(ROOT)}  (~${COST[a.quality]:.3f}, total ~${spent:.2f})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
