"""Phase 4A (A): the question-answering executive block.  Pure code (data in, data out): no model, no store, no retrieval, NO MODEL CALL.

THE PROBLEM.  The executive summary used to be chosen by POSITION (the first record, each correction, one client statement per meeting, the latest record), so for "what happened to Rebecca's Lakeshore sale?"
it opened with the first record, ended with an unrelated reminder about a will and said nothing about the decision or the outcome.

THE ANSWER IS A SET OF FIVE SLOTS, filled by code from what the retrieved turns establish, selected by RELEVANCE TO THE SUBJECT of the request (status.subject_roots), never by position:
    beginning     how the situation began: the earliest retrieved record that is about the subject
    acceptance    what the client accepted, rejected or had not yet accepted (the client's own statements about the offer)
    corrections   important corrections of a previously recorded position: what was recorded, what the client said, how the advisor corrected it (corrections.py)
    decision      the latest decision the client CONFIRMED (status.link_decisions), or the latest decision-worded or tentative statement, which is NOT promoted
    transaction   signing / agreement execution / completion, each on its own (status.transaction_status)
A slot is `filled` only from evidence that supports it; `negative` when a retrieved turn says it had NOT happened; `partial` when it is stated but not established (tentative, referenced, pending);
`unavailable` when no retrieved turn addresses it, which is an absence of evidence and never a finding that it did not happen.  Slots that do not apply to the subject (a retirement age has no
signing) are left out and listed in `not_applicable`.

PROVENANCE.  Every populated item carries the exact wording of the turns it rests on (speaker, turn, date).  Its prose, when there is some, is a sentence of the validated narrative (the same
proposition the section below it shows, judged by `synthesis.proposition_problems` and again by the Evidence Verifier); when no validated sentence exists for the claim, the item shows the exact wording
instead, never a new paraphrase.  The verifier re-derives the whole block from the claims it rebuilds itself and replaces the agent's copy."""
from __future__ import annotations

import re
from typing import Optional

from agents.report import corrections as CO
from agents.report import semantic as S
from agents.report import status as ST
from agents.report import synthesis as SY
from schemas.report import AnswerBlock, ExecutiveSlot, Finding, NarrativeSentence, SlotItem, StatusEvidence, TimelineClaim

_NOT_ACCEPTED = re.compile(r"\b(?:haven't|have not|hasn't|has not|not yet|never)\s+(?:yet\s+)?(?:signed|accepted|agreed|committed|decided)\b|\bnot (?:signed|accepted)\b|\binclined to (?:accept|sell|say yes)\b", re.I)
_ACCEPTED = re.compile(r"\b(?:i(?:'ll| will| do)? accept|i(?:'ve| have) accepted|i(?:'m| am) accepting|i accepted|i(?:'ll| will) take the offer|i say yes)\b", re.I)
_REJECTED = re.compile(r"\b(?:i(?:'ll| will)? (?:reject|decline|pass on)|i(?:'ve| have) (?:rejected|declined)|turn(?:ed|ing)? (?:it|the offer|them) down|no thanks|i(?:'m| am) not interested)\b", re.I)
_TXN_ROOTS = frozenset({"sale", "offer", "accept", "agreement", "purchase", "closing", "complet", "proposal", "firm", "lakeshore"})      # "propos" (from "who PROPOSED speaking") is not a sale word; "proposal" is (and is the "offer" family)
# a question that asks WHAT THE RETRIEVED TURNS ESTABLISH about one exchange ("who proposed speaking, who was supposed to take part, what the source actually establishes")
_FOCUS_Q = re.compile(r"\bwho (?:proposed|suggested|asked|said|agreed|raised|was supposed|were supposed|should|would|participat\w+|attend\w+)\b|"
                      r"\bwhat the (?:source|record|turns?|conversations?|evidence)\b[^?.]{0,25}\b(?:establish\w*|say|says|show\w*|record\w*)\b|\bwhat (?:did|do|does|was|were)\b[^?.]{0,60}\b(?:agree\w*|propose\w*|suggest\w*)\b", re.I)
MAX_FOCUS_ITEMS, MAX_LATER_ITEMS = 8, 3


def _order(c: TimelineClaim) -> tuple:
    return (c.event_date or "", c.interaction_id or "", c.turn_index if c.turn_index is not None else -1)


def _question(c: TimelineClaim) -> bool:
    parts = ST.sentences(c.wording)
    return bool(parts) and parts[-1].rstrip().endswith("?") and len(parts) <= 2


class _Builder:
    def __init__(self, claims: list[TimelineClaim], sentence_for: dict[str, str]) -> None:
        self.claims = [c for c in claims if c.source == "source_turn"]
        self.by_key = {ST.key_of(c): c for c in self.claims}
        self.sentence_for = sentence_for
        self.used: list[tuple[TimelineClaim, str]] = []

    def sentences(self, c: TimelineClaim) -> list[NarrativeSentence]:
        k = ST.key_of(c)
        if k in self.sentence_for:
            self.used.append((c, self.sentence_for[k]))
            return [NarrativeSentence(text=self.sentence_for[k], sources=[k])]
        return []

    def item(self, label: str, status: str, c: Optional[TimelineClaim], role: str, note: Optional[str] = None, extra: Optional[list[StatusEvidence]] = None, with_sentence: bool = True) -> SlotItem:
        ev = ([ST.evidence_of(c, role)] if c is not None else []) + list(extra or [])
        return SlotItem(label=label, status=status, sentences=self.sentences(c) if (c is not None and with_sentence) else [], evidence=ev, note=note)


def _slot(key: str, label: str, items: list[SlotItem], reason: Optional[str] = None) -> ExecutiveSlot:
    live = [i for i in items if i.status != "unavailable"]
    if not live:
        return ExecutiveSlot(key=key, label=label, status="unavailable", items=items, reason=reason)
    st = "filled" if any(i.status == "filled" for i in live) else "negative" if any(i.status == "negative" for i in live) else "partial"
    return ExecutiveSlot(key=key, label=label, status=st, items=items, reason=reason)


def assemble_answer(request_text: str, claims: list[TimelineClaim], sentence_for: dict[str, str], names=(), topics=(), terms=()) -> tuple[AnswerBlock, Optional[Finding]]:
    """The answer block and the executive finding (window -1) made of the validated sentences it uses.  `sentence_for` maps "INT-083#2" to the validated, dated sentence the narrative holds for that
    turn.  Deterministic: the same claims and sentences always give the same block, which is how the Evidence Verifier can re-derive it."""
    roots = ST.subject_roots(request_text, topics, names, terms=terms)
    b = _Builder(claims, sentence_for)
    src = b.claims
    txn = ST.transaction_status(src, roots)
    recs = CO.correction_records(src)
    slots: list[ExecutiveSlot] = []
    na: list[str] = []
    if _FOCUS_Q.search(request_text or "") and not (roots & _TXN_ROOTS):
        # "what do the retrieved turns establish about THIS exchange?": no sale slot applies, so none is filled; the answer is the turns themselves, in order, with who said each
        slots.append(_subject_slot(b, src, roots, ST.grounded_roots(list(terms)[:1]) if terms else roots, terms))
        return _finalize(AnswerBlock(subject_terms=sorted(roots), slots=slots, not_applicable=["beginning", "acceptance", "corrections", "decision", "transaction"]), b, names)

    # 1. how the situation began: the earliest retrieved record that is ABOUT the subject (not the earliest record)
    about = [c for c in SY.synthesis_pool(src) if ST.about(c, roots) and not _question(c)] or [c for c in sorted(src, key=_order) if c.statement_kind == "client_statement" and ST.about(c, roots) and len(c.wording.split()) >= 8]
    if about:
        c = sorted(about, key=_order)[0]
        slots.append(_slot("beginning", "How the situation began", [b.item(f"Earliest retrieved record about the subject ({ST.fmt_date(c.event_date)})", "filled", c, "first_record")]))
    else:
        slots.append(_slot("beginning", "How the situation began", [SlotItem(label="Earliest retrieved record about the subject", status="unavailable")],
                           "No retrieved turn is about the subject of the request: an absence of evidence in the retrieved turns."))

    # 2. what the client accepted, rejected or had not yet accepted
    if roots & _TXN_ROOTS:
        dec_date = txn.decision.as_of if txn.decision.status == "established" else None
        client = [c for c in src if c.statement_kind == "client_statement" and not _question(c)]
        before = [c for c in client if not dec_date or (c.event_date or "") <= dec_date]
        explicit = sorted((c for c in before if _NOT_ACCEPTED.search(c.wording) and ST.about(c, roots | {"sign", "accept", "offer"})), key=_order)
        hesitant = sorted((c for c in before if c.modality in ("tentative", "hypothetical") and ST.about(c, roots) and not _NOT_ACCEPTED.search(c.wording)
                           and (not explicit or _order(c) > _order(explicit[-1]))), key=_order)
        said_yes = sorted((c for c in client if _ACCEPTED.search(c.wording) and c.modality not in ("tentative", "hypothetical") and ST.about(c, roots)), key=_order)
        said_no = sorted((c for c in client if _REJECTED.search(c.wording) and ST.about(c, roots)), key=_order)
        items = []
        if explicit:
            c = explicit[-1]
            items.append(b.item(f"Had not accepted or signed (the client's own words, {ST.fmt_date(c.event_date)})", "negative", c, "mention"))
        else:
            items.append(SlotItem(label="Had not yet accepted", status="unavailable", note="No retrieved client statement says the offer had not yet been accepted or signed: an absence of evidence in the retrieved turns."))
        if hesitant:
            c = hesitant[-1]
            items.append(b.item(f"Hesitation before the decision ({ST.fmt_date(c.event_date)})", "partial", c, "mention"))
        if said_yes:
            c = said_yes[-1]
            items.append(b.item(f"Accepted (the client's own words, {ST.fmt_date(c.event_date)})", "filled", c, "mention"))
        else:
            items.append(SlotItem(label="Accepted", status="unavailable", note="No retrieved turn records the client accepting the offer in those words: an absence of evidence in the retrieved turns. A decision to sell "
                                                                                  "(below) is not the same as accepting or signing an offer."))
        if said_no:
            c = said_no[-1]
            items.append(b.item(f"Rejected (the client's own words, {ST.fmt_date(c.event_date)})", "filled", c, "mention"))
        else:
            items.append(SlotItem(label="Rejected", status="unavailable", note="No retrieved turn records the client rejecting the offer: an absence of evidence in the retrieved turns."))
        slots.append(_slot("acceptance", "What the client accepted, rejected or had not yet accepted", items))
    else:
        na.append("acceptance")

    # 3. corrections of a previously recorded position
    if recs:
        items = []
        for r in recs[:3]:
            c = b.by_key.get(r.correction.evidence.key) if r.correction.evidence else None
            extra = [p.evidence for p in (r.original, r.client_said) if p is not None and p.evidence is not None]
            ev_note = r.missing
            it = b.item(f"Correction of {ST.fmt_date(r.date)}", "filled", c, "correction", note=ev_note, extra=extra)
            items.append(it)
        slots.append(_slot("corrections", "Corrections of previously recorded positions", items))
    else:
        slots.append(_slot("corrections", "Corrections of previously recorded positions", [SlotItem(label="Corrections", status="unavailable")],
                           "No retrieved turn corrects an earlier record: an absence of evidence in the retrieved turns, not a finding that none exists."))

    # 4. the latest decision the client CONFIRMED (never promoted from a tentative statement)
    d = txn.decision
    if d.status == "established":
        dc = next((b.by_key[e.key] for e in d.evidence if e.role == "decision" and e.key in b.by_key), None) or next((b.by_key[e.key] for e in d.evidence if e.role == "restatement" and e.key in b.by_key), None)
        it = b.item(f"The client's latest confirmed decision ({ST.fmt_date(d.as_of)})", "filled", dc, "decision", note=d.statement, extra=[e for e in d.evidence if dc is None or e.key != ST.key_of(dc)])
        slots.append(_slot("decision", "The client's latest confirmed decision", [it]))
    elif d.status == "partial":
        dc = next((b.by_key[e.key] for e in d.evidence if e.key in b.by_key), None)
        slots.append(_slot("decision", "The client's latest confirmed decision", [b.item("Latest decision-worded statement: NOT confirmed", "partial", dc, "decision", note=d.statement, with_sentence=False)]))
    else:
        slots.append(_slot("decision", "The client's latest confirmed decision", [SlotItem(label="The client's decision", status="unavailable", note=d.statement)], d.statement))

    # 5. the transaction: signing, agreement execution and completion, each on its own
    if roots & _TXN_ROOTS:
        items = []
        for f in (txn.signing, txn.agreement, txn.completion):
            st = {"established": "filled", "negative": "negative", "referenced": "partial", "pending": "partial", "partial": "partial", "unavailable": "unavailable"}[f.status]
            items.append(SlotItem(label=f"{f.label}: {f.status.replace('_', ' ')}", status=st, note=f.statement, evidence=f.evidence[:4]))
        slots.append(_slot("transaction", "Signing, agreement execution and completion", items))
    else:
        na.append("transaction")

    block = AnswerBlock(subject_terms=sorted(roots), slots=slots, not_applicable=na)
    return _finalize(block, b, names)


_EXCHANGE = re.compile(r"\b(?:call|calls|conversation|spoke|spoken|speak|speaking|talk|talked|meeting|met)\b", re.I)      # a sentence about an exchange between people


def _subject_slot(b: _Builder, src: list[TimelineClaim], roots: set, main: set, terms=()) -> ExecutiveSlot:
    """What the retrieved turns say about the subject, as the turns themselves.  The MEETING(S) in which the most of the question's own terms occur together come first (turn 3 proposes, turn 6
    answers, turn 11 recaps: one meeting); then up to MAX_LATER_ITEMS later records that mention the subject.  Each item is the exact sentence(s) of the turn that mention the subject, with the speaker's
    role and the turn; a sentence that PROMISES an action ("I'll arrange a call") says so: a promise is not a call that took place."""
    about = [c for c in sorted(src, key=_order) if ST.about(c, roots)]
    if not about:
        return ExecutiveSlot(key="subject", label="What the retrieved turns establish about the question", status="unavailable",
                             reason="No retrieved turn mentions the subject of the question: an absence of evidence in the retrieved turns, not a finding that nothing was said.")
    cover: dict[str, set] = {}
    for c in about:
        cover.setdefault(c.interaction_id or "", set()).update(ST.content_roots(c.wording) & roots)
    best = max(len(v) for v in cover.values())
    meetings = sorted((i for i, v in cover.items() if len(v) == best), key=lambda i: min(_order(c) for c in about if (c.interaction_id or "") == i))[:2]
    items: list[SlotItem] = []
    shown: set = set()
    for i in meetings:
        mine = [x for x in about if (x.interaction_id or "") == i]
        anchors = [x.turn_index for x in mine if ST.about(x, main)]
        for c in [x for x in mine if ST.about(x, main) or any(abs((x.turn_index or 0) - t) <= SY.NEAR_TURNS for t in anchors)][:MAX_FOCUS_ITEMS]:
            items.append(_subject_item(b, c, roots))
            shown.add(ST.key_of(c))
    last = max((c.event_date or "") for c in about if (c.interaction_id or "") in meetings)
    later = [c for c in about if ST.key_of(c) not in shown and (c.event_date or "") > last and ST.about(c, main) and _EXCHANGE.search(c.wording)][:MAX_LATER_ITEMS]
    for c in later:
        it = _subject_item(b, c, main | roots, exchange=True)
        it.label = "Later record that mentions the subject (not shown to concern the same exchange) · " + it.label
        items.append(it)
    names = ", ".join(dict.fromkeys(t for t in terms)) if terms else ""
    scope = SlotItem(label="How these turns were chosen", status="filled", note=(f"The meeting{'s' if len(meetings) > 1 else ''} {', '.join(meetings)} match{'es' if len(meetings) == 1 else ''} the most of the question's own terms"
                                                                              f"{' (' + names + ')' if names else ''}; the turns below are the exact sentences of the retrieved turns that mention them, in order. Nothing here is inferred "
                                                                              f"beyond what a turn says: who spoke is the recorded speaker of the turn."))
    return ExecutiveSlot(key="subject", label="What the retrieved turns establish about the question", status="filled", items=[scope] + items)


def _subject_item(b: _Builder, c: TimelineClaim, roots: set, exchange: bool = False) -> SlotItem:
    sents = ST.sentences(c.wording)
    mine = [x for x in sents if ST.content_roots(x) & roots or (exchange and _EXCHANGE.search(x))] or sents[:1]       # a later record also shows its own sentences about a call or a conversation ("I spoke with Mei Lin this morning")
    ev = [ST.evidence_of(c, "mention", None, x) for x in mine[:3]]
    promised = any(SY._PROMISE.search(x) for x in mine)
    note = ("This is what the speaker says they WILL do (a promise or a plan); no retrieved turn is cited here as showing that it was done." if promised else None)
    who = ST.TAG.get(c.statement_kind, c.statement_kind)
    return SlotItem(label=f"{ST.fmt_date(c.event_date)} · {ST.where_of(c)} · {c.speaker or 'speaker'} ({who})", status="filled", sentences=b.sentences(c), evidence=ev, note=note)


def _finalize(block: AnswerBlock, b: _Builder, names) -> tuple[AnswerBlock, Optional[Finding]]:
    """The executive finding is made of exactly the validated sentences the slots use; each is judged once more (summary mode) and the finding as a whole is re-checked, the check the Evidence Verifier
    runs.  A sentence that fails is removed from its slot (the slot then shows the exact wording)."""
    seen: dict[str, tuple[TimelineClaim, str]] = {}
    for c, t in b.used:
        seen.setdefault(ST.key_of(c), (c, t))
    good = {k: (c, t) for k, (c, t) in seen.items() if not SY.proposition_problems(t, [c], names, summary=True)}
    parts = [good[k] for k in seen if k in good]
    f = None
    if parts:
        f = Finding(text=" ".join(SY.compose_synthesis(t, [c]) for c, t in parts), refs=list(dict.fromkeys(c.evidence_refs[0] for c, _ in parts if c.evidence_refs)), origin="synthesis_checked",
                    narrative=[NarrativeSentence(text=t, sources=[ST.key_of(c)]) for c, t in parts], window=-1)
        if SY.check_synthesis_finding(f, [c for c, _ in parts], names):
            f, good = None, {}
    for slot in block.slots:
        for it in slot.items:
            it.sentences = [s for s in it.sentences if s.sources and s.sources[0] in good]
    return block, f


def sentence_map(findings: list[Finding]) -> dict[str, str]:
    """{turn key: validated dated sentence} from the narrative findings of a report (the first sentence that cites a turn alone)."""
    out: dict[str, str] = {}
    for f in findings:
        for sn in f.narrative or []:
            if len(sn.sources) == 1:
                out.setdefault(sn.sources[0], sn.text)
    return out
