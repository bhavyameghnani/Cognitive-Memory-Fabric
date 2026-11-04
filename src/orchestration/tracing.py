"""Structured execution tracing: one JSONL file per request under logs/traces/. No secrets are written."""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
TRACE_DIR = ROOT / "logs" / "traces"
_SECRET = re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)|((?:api[_-]?key|password|token|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", re.I)


def redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return _SECRET.sub(lambda m: (m.group(1) + "***" + m.group(2)) if m.group(1) else (m.group(3) + "***"), obj)
    if isinstance(obj, dict):
        return {k: ("***" if re.search(r"password|api_?key|secret|token", str(k), re.I) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


class Tracer:
    """Collects events in memory and (optionally) appends them to logs/traces/<request_id>.jsonl."""

    def __init__(self, request_id: str, persist: bool = True, trace_dir: Optional[Path] = None) -> None:
        self.request_id, self.events, self._lock = request_id, [], threading.Lock()
        self.t0 = time.perf_counter()
        self.path: Optional[Path] = None
        if persist:
            d = trace_dir or TRACE_DIR
            d.mkdir(parents=True, exist_ok=True)
            self.path = d / f"{request_id}.jsonl"
            self.path.write_text("", encoding="utf-8")

    def event(self, component: str, name: str, **payload: Any) -> None:
        rec = dict(ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"), t_ms=int((time.perf_counter() - self.t0) * 1000),
                   request_id=self.request_id, component=component, event=name, payload=redact(payload))
        with self._lock:
            self.events.append(rec)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")

    def duration_ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


class NullTracer(Tracer):
    def __init__(self) -> None:
        super().__init__("null", persist=False)


def render_trace(path_or_id: str | Path, max_payload: int = 220) -> str:
    p = Path(path_or_id)
    if not p.exists():
        p = TRACE_DIR / f"{path_or_id}.jsonl"
    lines = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        e = json.loads(ln)
        pl = json.dumps(e["payload"], ensure_ascii=False, default=str)
        lines.append(f"[{e['t_ms']:>7} ms] {e['component']:<18} {e['event']:<28} {pl[:max_payload]}{'...' if len(pl) > max_payload else ''}")
    return "\n".join(lines)
