"""Deterministic, token-aware prompt compression.

Design rule: compression only ever DELETES whole lines of explicitly low-priority categories (or exact duplicates).  It never rewrites, truncates or
paraphrases a line, so a negation, date, amount, unit, certainty tag, client id or source reference that survives is byte-identical to the original,
and an uncertain statement can never be turned into a certain one.  Anything that is not in a droppable category is PROTECTED.  If the protected
lines alone do not fit, compression reports failure and the caller fails safely (ContextOverflow) instead of cutting protected content.

Drop order (cheapest information first, per the spec "older history first"):
    1 duplicate        exact repeats after whitespace normalisation
    2 failed_attempt   Tool Selector history lines that were rejected / duplicate calls (the validator already blocks repeats)
    3 old_tool_result  Tool Selector history lines, oldest first
    4 episode          episodic evidence lines, least important (last) first
    5 superseded       superseded-history evidence lines (kept longest of the droppable ones: needed for "how did this change" questions)
Every removal is listed in the result (category, count, evidence handles) so the trace shows exactly what was omitted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness.token_counter import TokenCounter

_TOOL_RESULT = re.compile(r"^\[EX-[^\]]+\]\s+it\d+\s+\S+")
_FAILED = re.compile(r"\s->\s(rejected|duplicate):")
_EVIDENCE = re.compile(r"^\[(E\d+)\s")
_REF = re.compile(r"^\[(E\d+|EX-[^\]]+)")
TIERS = ("duplicate", "failed_attempt", "old_tool_result", "episode", "superseded", "episode_critical")
# P1.5: an episode line that carries a human correction, an exploratory / hypothetical / unconfirmed / recommended statement or a disputed status is what tells the reader HOW SURE the
# memory is.  It is dropped only after the plain episodes AND the superseded history, i.e. last of all the droppable lines.  False = the P1.4 order (position only), kept for the
# evaluation's before/after comparison.
CRITICAL_LAST = True
_NEGATED = re.compile(r"\b(?:not|never|no longer|without|isn't|wasn't|don't|doesn't|didn't|won't|hasn't|haven't|cannot)\b", re.I)    # a statement that says what is NOT the case
_CRITICAL_EPISODE = re.compile(r"\b(?:active|disputed|unconfirmed|candidate|superseded|expired)/(?:correction|exploratory|hypothetical|unconfirmed|recommendation)\b|\bdisputed/|\[(?:UNVERIFIED|CONTRADICTED|AMBIGUOUS|OPEN CONFLICT) - ")        # P1.4.3: a line the integrity boundary labelled not-verified / in conflict is a review requirement


def classify(line: str) -> str:
    """Category of a prompt line.  'protected' means: never touched."""
    s = line.strip()
    if _TOOL_RESULT.match(s):
        return "failed_attempt" if _FAILED.search(s) else "old_tool_result"
    m = _EVIDENCE.match(s)
    if m:
        if "Superseded earlier knowledge" in s:
            return "superseded"
        if re.search(r"\]\s*Episode;|\bEpisode;|conversation_evidence", s[:80]) or "Episode;" in s[:120]:
            return "episode_critical" if CRITICAL_LAST and (_CRITICAL_EPISODE.search(s[:240]) or _NEGATED.search(s)) else "episode"
    return "protected"


@dataclass
class CompressionResult:
    text: str
    original_tokens: int
    compressed_tokens: int
    fits: bool
    reason: str = ""
    removed: dict = field(default_factory=dict)          # category -> number of lines removed
    removed_refs: list = field(default_factory=list)     # evidence / execution handles that were omitted
    protected_lines: int = 0
    preserved_refs: list = field(default_factory=list)   # handles still present
    exact_tokens: bool = False

    @property
    def changed(self) -> bool:
        return self.compressed_tokens != self.original_tokens or bool(self.removed)

    @property
    def ratio(self) -> float:
        return round(self.compressed_tokens / self.original_tokens, 3) if self.original_tokens else 1.0

    def report(self) -> dict:
        return dict(original_tokens=self.original_tokens, compressed_tokens=self.compressed_tokens, ratio=self.ratio, fits=self.fits, reason=self.reason,
                    removed=dict(self.removed), removed_refs=list(self.removed_refs), protected_lines=self.protected_lines,
                    preserved_refs=list(self.preserved_refs), exact_tokens=self.exact_tokens)


MARKER_TOKEN_ALLOWANCE = 40


_KINDS = (("UNVERIFIED", "unverified"), ("CONTRADICTED", "contradicted"), ("OPEN CONFLICT", "open-conflict"), ("AMBIGUOUS", "ambiguous"), ("/correction", "correction"),
          ("/exploratory", "exploratory"), ("/hypothetical", "hypothetical"), ("disputed/", "disputed"))


def omitted_kinds(lines: list[str]) -> list[str]:
    """P1.5.1: which caveat categories the omitted lines carried, so a reader can tell 'omitted for budget' from 'does not exist' and knows what kind of evidence is missing."""
    return [name for key, name in _KINDS if any(key in ln for ln in lines)]


def _marker(category: str, n: int, refs: list[str], kinds: list[str] = ()) -> str:
    """Short on purpose (the full list of omitted handles goes to the trace): at most 4 handles, then a count."""
    shown = ", ".join(refs[:4]) + (f" +{len(refs) - 4} more" if len(refs) > 4 else "")
    tail = f"; handles: {shown}" if refs else ""
    what = f"; kinds: {', '.join(kinds)}" if kinds else ""
    return f"[context compressed: {n} {category.replace('_', ' ')} line(s) omitted{tail}{what}]"


def compress(text: str, budget_tokens: int, counter: TokenCounter, reason: str = "") -> CompressionResult:
    lines = text.split("\n")
    original = counter.count(text)
    res = CompressionResult(text=text, original_tokens=original.tokens, compressed_tokens=original.tokens, fits=original.tokens <= budget_tokens,
                            reason=reason, exact_tokens=original.exact)
    cats = [classify(ln) for ln in lines]

    # 1. exact duplicates (never the protected header lines: a repeated protected line is harmless, but dropping it is not our call)
    seen: set[str] = set()
    for i, ln in enumerate(lines):
        key = " ".join(ln.split())
        if cats[i] != "protected" and key:
            if key in seen:
                cats[i] = "duplicate"
            seen.add(key)

    keep = [True] * len(lines)
    removed: dict[str, list[int]] = {}

    def current_tokens() -> int:
        out = [ln for ln, k in zip(lines, keep) if k]
        return counter.n("\n".join(out)) + MARKER_TOKEN_ALLOWANCE * len(removed)   # room for the omission markers

    def drop(category: str, order: list[int]) -> None:
        for i in order:
            if not keep[i] or cats[i] != category:
                continue
            if current_tokens() <= budget_tokens and category != "duplicate":
                return
            keep[i] = False
            removed.setdefault(category, []).append(i)

    drop("duplicate", [i for i in range(len(lines))])                                     # duplicates always go (no information lost)
    if current_tokens() > budget_tokens:
        drop("failed_attempt", list(range(len(lines))))
    if current_tokens() > budget_tokens:
        drop("old_tool_result", list(range(len(lines))))                                  # oldest (earliest) first
    if current_tokens() > budget_tokens:
        drop("episode", list(reversed(range(len(lines)))))                                # least important (last) first
    if current_tokens() > budget_tokens:
        drop("superseded", list(reversed(range(len(lines)))))
    if current_tokens() > budget_tokens:
        drop("episode_critical", list(reversed(range(len(lines)))))                       # last resort among the droppable lines (P1.5)

    out: list[str] = []
    marked: set[str] = set()
    for i, ln in enumerate(lines):
        if keep[i]:
            out.append(ln)
            continue
        cat = cats[i]
        if cat not in marked:
            marked.add(cat)
            refs = []
            for j in removed[cat]:
                m = _REF.match(lines[j].strip())
                if m:
                    refs.append(m.group(1))
            out.append(_marker(cat, len(removed[cat]), refs, omitted_kinds([lines[j] for j in removed[cat]])))
    new_text = "\n".join(out)

    # safety net: every protected line of the original must still be present, unchanged
    protected = [ln for ln, c in zip(lines, cats) if c == "protected"]
    remaining = set(out)
    assert all(p in remaining for p in protected), "compression removed a protected line"   # internal invariant, never expected to fire

    new = counter.count(new_text)
    res.text, res.compressed_tokens, res.fits = new_text, new.tokens, new.tokens <= budget_tokens
    res.removed = {k: len(v) for k, v in removed.items()}
    res.removed_refs = [m.group(1) for k, v in removed.items() for j in v if (m := _REF.match(lines[j].strip()))]
    res.protected_lines = len(protected)
    res.preserved_refs = [m.group(1) for ln in out if (m := _REF.match(ln.strip()))]
    return res
