"""CLI text renderer: prints the AdvisorReport headings in spec F order. The structured object stays the source of truth."""
from __future__ import annotations

from schemas.report import AdvisorReport


def _findings(title: str, items) -> list[str]:
    out = [f"\n## {title}"]
    out += [f"- {f.text} [{', '.join(f.refs)}]" for f in items] or ["- (none)"]
    return out


def _strs(title: str, items: list[str]) -> list[str]:
    return [f"\n## {title}"] + ([f"- {x}" for x in items] or ["- (none)"])


def render_report(r: AdvisorReport) -> str:
    out = [f"# Advisor report {r.request_id} ({r.client_id})", f"Question: {r.question}", "\n## Summary", r.summary]
    out += _findings("Key findings", r.key_findings)
    out += _findings("Historical context", r.historical_context)
    out += _findings("Financial analysis", r.financial_analysis)
    out.append("\n## Evidence")
    for e in r.evidence:
        ids = ", ".join(x for x in [e.interaction_id and f"{e.interaction_id} turn {e.turn_index}", e.memory_id, e.tool_execution_id] if x)
        out.append(f"- [{e.ref} {e.source_class}{', synthetic' if e.synthetic else ''}] {e.description}" + (f" <{ids}>" if ids else "")
                   + (f' "{e.excerpt}"' if e.excerpt else ""))
    if not r.evidence:
        out.append("- (none)")
    out += _strs("Assumptions", r.assumptions)
    out += _strs("Uncertainties", r.uncertainties)
    out += _strs("Limitations", r.limitations)
    out += _strs("Suggested follow-ups", r.suggested_followups)
    out.append("\n## Validation")
    out += [f"- {k}: {v}" for k, v in r.validation.items()]
    return "\n".join(out)
