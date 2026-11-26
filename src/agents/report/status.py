"""Phase 4A (B): the client's DECISION, the SIGNING, the EXECUTION of an agreement and the COMPLETION of the sale are four independent facts.  Pure code: no model, no store, no retrieval.

WHY.  A client who says "I've decided, I'm going to sell" has made a decision; that is not a signed letter of intent, a signed exclusivity agreement or a closed sale.  The earlier report merged them: with
no verified stored memory it said "no current position is established" even though the advisor restated the decision and the client answered "Yes", and it listed the advisor's request for "the signed
exclusivity agreement" as a signing event.  Here each fact has its own evidence and its own status, and each status is written by code from the structure of the retrieved turns.

THE DECISION (`link_decisions`).  A decision is CONFIRMED by the client only when three retrieved turns of ONE meeting follow each other directly (consecutive turn numbers):
    the client states the decision  ->  the advisor's next turn RESTATES it accurately  ->  the client's next turn EXPLICITLY confirms it.
  "Accurately" = the restatement is declarative, not hedged, shares at least two content words with the decision and states no figure the decision does not.  "Explicitly" = the reply opens with an
  affirmation (yes, correct, that's right, exactly ...) and nothing in it hedges, qualifies or reopens the matter.  An isolated "yes" with no restatement before it confirms nothing; a restatement the
  client does not answer confirms nothing; a decision the advisor only reports ("you've decided ...") followed by the client's explicit "yes" is confirmed as the advisor's restatement confirmed by the
  client (basis `restated_confirmed`), not as the client's own words.  Every turn keeps its own speaker.

THE FACETS (`transaction_status`).  Each sentence of the retrieved turns that mentions signing, executing, closing or completing is classified by its own wording:
    event       it says, in the past tense and without hedge, that it happened
    negative    it says it had NOT happened ("I haven't signed the letter of intent")
    referenced  a document is called "signed" only inside a request, plan or question ("please get me the signed exclusivity agreement"): the speaker presupposes it, nobody states it
    pending     it is described as still to come, conditional, in draft or scheduled
    question    a question: it establishes nothing
  A facet is `established` only by an event; otherwise by the latest non-question mention of its own document; and `unavailable` (absence of evidence, which is not a finding that it did not happen) when
  no retrieved turn addresses it.  Signing = the letter of intent / the offer; agreement = the exclusivity agreement and the purchase agreement; completion = the closing of the sale itself ("cash at
  closing" is a term, not a closing)."""
from __future__ import annotations

import re
from typing import Iterable, Optional

from agents.memory import attribution
from schemas.report import OutcomeStatement, StatusEvidence, StatusFacet, TimelineClaim, TransactionStatus

TAG = {"client_statement": "the client's own words", "advisor_reported": "the advisor's account, not a client statement", "advisor_interpretation": "the advisor's interpretation or recommendation",
       "unattributed": "speaker not attributable"}
_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
EXCERPT_CHARS = 600


def key_of(c: TimelineClaim) -> str:
    return f"{c.interaction_id}#{c.turn_index}"


def where_of(c: TimelineClaim) -> str:
    return f"{c.interaction_id} turn {c.turn_index}"


def fmt_date(d: Optional[str]) -> str:
    return f"{int(d[8:10])} {_MONTHS[int(d[5:7])]}" if d and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else "an undated record"


def _order(c: TimelineClaim) -> tuple:
    return (c.event_date or "", c.interaction_id or "", c.turn_index if c.turn_index is not None else -1)


def sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", (text or "").replace("\r", " ")) if p and p.strip()]


def evidence_of(c: TimelineClaim, role: str, kind: Optional[str] = None, excerpt: Optional[str] = None) -> StatusEvidence:
    """The exact wording of one retrieved turn (or one of its sentences), where it is and who spoke it.  Never a paraphrase."""
    text = " ".join((excerpt if excerpt is not None else c.wording).split())
    if len(text) > EXCERPT_CHARS:
        text = text[:EXCERPT_CHARS - 1].rstrip() + "…"
    return StatusEvidence(claim_id=c.claim_id, key=key_of(c), date=c.event_date, where=where_of(c), speaker=TAG.get(c.statement_kind, c.statement_kind), role=role, kind=kind, excerpt=text,
                          ref=c.evidence_refs[0] if c.evidence_refs else None)


# ------------------------------------------------------------------------------------------------------------------------------------ words, subjects
_STOP = frozenset("""the and for with that this from have has had was were are been being will would could should about into over than then also very just not but you your yours his her they them their there here what when
where which while who whom whose how why can may might must shall its our ours your said say says some any all each every other another such more most much many only still yet even both either whether
because since until before after again once ever never always""".split())
_FAMILY = {"sell": "sale", "sold": "sale", "selling": "sale", "sale": "sale", "sales": "sale", "seller": "sale", "sells": "sale", "company": "firm", "business": "firm", "firm": "firm", "decided": "decid",
           "decide": "decid", "deciding": "decid", "decision": "decid", "decisions": "decid", "decides": "decid", "proposal": "offer", "offers": "offer", "offered": "offer", "accepted": "accept", "accepting": "accept",
           "acceptance": "accept", "accepts": "accept", "signed": "sign", "signing": "sign", "signs": "sign", "signature": "sign"}
_GENERIC_REQUEST = frozenset("""history chronology decision decisions report prepare give provide explain tell what when where which about actually versus said recorded record records source sources conversation conversations turns turn
including include corrections correction correct evidence meeting meetings timeline summary summarise summarize ever whether does did was were been being have has with without from into over than then also their there these those
such most more much many some any each every other another please show list describe how why who whose able across advisor client clients plan review portfolio next""".split())


def _stem(w: str) -> str:
    w = w.lower().replace("’", "'")
    if w.endswith("'s"):
        w = w[:-2]
    if w in _FAMILY:
        return _FAMILY[w]
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    return _FAMILY.get(w, w)


def content_roots(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[A-Za-z][A-Za-z']+", text or "") if len(w) >= 4 and w.lower() not in _STOP}


def grounded_roots(terms: Iterable[str]) -> set[str]:
    """The roots of the search phrases the Tool Selector found IN THE CLIENT'S OWN CONVERSATIONS (retrieval_report["terms"]): a subject the data confirms, not a word of the request's framing.  Each root also
    carries the form without a final "e" (propose / proposed, agree / agreed), because the roots of a claim's own words are made by stripping -ed and -ing."""
    out: set[str] = set()
    for t in terms or ():
        for w in re.findall(r"[A-Za-z][A-Za-z']+", t or ""):
            if len(w) >= 4 and w.lower() not in _STOP:
                r = _stem(w)
                out.add(r)
                if r.endswith("e") and len(r) > 4:
                    out.add(r[:-1])
    return out


def subject_roots(request_text: str, topics: Iterable[str] = (), names: Iterable[str] = (), terms: Iterable[str] = ()) -> set[str]:
    """The roots that say what a report is ABOUT: the request's own subject words and the plan's memory topics, without the client's name (it is in every line), the words that only describe the kind
    of report ("history", "chronology", "corrections") and the generic word "decision" (it would make every decision of the client relevant to every question).  When the plan names NO topic but
    source discovery found grounded search phrases (`terms`), those phrases are the subject: they are confirmed by the client's conversations, whereas the raw words of a long question ("cite",
    "original", "unless", "happen") are not."""
    if terms and not list(topics or ()):
        return {r for r in grounded_roots(terms) if r not in {"decid"}}
    skip = {n.lower() for n in names}
    roots: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z']+", request_text or ""):
        base = w.lower().replace("’", "'")
        base = base[:-2] if base.endswith("'s") else base
        if len(base) >= 4 and base not in _STOP and base not in _GENERIC_REQUEST and base not in skip:
            roots.add(_stem(base))
    for t in topics or ():
        for w in str(t).lower().split("_"):
            if len(w) >= 4 and w not in _STOP:
                roots.add(_stem(w))
    return {r for r in roots if r not in {"decid"}}


def about(c: TimelineClaim, roots: Optional[set]) -> bool:
    """Does the wording of this claim touch the subject of the report?  With no subject given, everything does."""
    return True if not roots else bool(content_roots(c.wording) & roots)


_NUMWORDS = frozenset("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion".split())


def numbers_in(text: str) -> set[str]:
    out = {m.replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}
    out |= {w.lower() for w in re.findall(r"[A-Za-z]+", text or "") if w.lower() in _NUMWORDS}
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ the decision: stated, restated, confirmed
_HEDGE = re.compile(r"\b(?:maybe|perhaps|might|not sure|unsure|undecided|considering|weighing|inclined|haven't decided|have not decided|not decided|not yet decided|thinking about|whether|if you|if i|if we|could be|possibly)\b|"
                    r"\bi (?:may|could) ", re.I)
_RESTATE = re.compile(r"\byou(?:'ve| have)\s+(?:both\s+)?(?:decided|chosen|settled)\b|\byou(?:'re| are)\s+(?:going to|planning to|ready to|moving ahead|proceeding)\b|"
                      r"\byou\s+(?:want|would like|'d like|wish)\s+to\s+(?:sell|proceed|go ahead|accept|move forward|go with)\b|\byou(?:'ve| have) agreed to\b|\bso you(?:'re| are| have|'ve)\s+(?:decided|going|selling)\b", re.I)
_OPEN_AFFIRM = re.compile(r"^\W*(?:yes|yeah|yep|yup|correct|that(?:'s| is) (?:right|correct|accurate)|exactly|absolutely|right|i do|i am|confirmed)\b", re.I)
_REOPEN = re.compile(r"\b(?:second thoughts|think (?:about it|it over|more)|hold off|not so fast|let me think|talk (?:to|with) my|wait (?:a|until|for|and|two|another)|on second thought|change my mind|not (?:entirely|fully|completely))\b", re.I)
_QUESTION_START = re.compile(r"^\W*(?:have|has|did|do|does|are|is|will|would|can|could|should|am)\b", re.I)


def _restating_sentence(text: str) -> Optional[str]:
    """The advisor's sentence that restates a decision: declarative, not hedged ("Have you decided?" is a question, "if you've decided" is a hypothesis), or None."""
    for s in sentences(text):
        if not _RESTATE.search(s):
            continue
        if s.rstrip().endswith("?") and _QUESTION_START.match(s):
            continue
        if _HEDGE.search(re.sub(r"\bsubject to (?:those|these|the|your|its|both)\b[^.,;]{0,40}\bconditions?\b", " ", s, flags=re.I)):
            continue
        return s
    return None


def explicit_confirmation(text: str) -> bool:
    """A client reply that CONFIRMS: it opens with an affirmation and nothing in it hedges, qualifies, asks or reopens ("Yes, but ..." / "Yes? Are you sure?" / "Yes. I need to think" are not confirmations)."""
    t = " ".join((text or "").split())
    if not t or not _OPEN_AFFIRM.match(t):
        return False
    first = sentences(t)[0] if sentences(t) else t
    if first.rstrip().endswith("?"):
        return False
    n = attribution._norm(t)
    return not (attribution._CONFIRM_BLOCK.search(n) or _HEDGE.search(t) or _REOPEN.search(t))


def _is_question(text: str) -> bool:
    parts = sentences(text)
    return bool(parts) and parts[-1].rstrip().endswith("?") and len(parts) <= 2


def _client_decision(c: TimelineClaim) -> bool:
    return c.source == "source_turn" and c.statement_kind == "client_statement" and c.modality == "decision" and not _is_question(c.wording) and not _HEDGE.search(c.wording)


def link_decisions(claims: list[TimelineClaim]) -> list[dict]:
    """Mark the claims of every decision the client CONFIRMED (see the module doc) with `decision_link` and return the links.  The advisor's restatement and the client's reply are the turns directly after
    each other in ONE meeting; the client's own statement of the decision is the turn before the restatement or, failing that, the nearest earlier client turn of the same meeting that states the same
    decision (shares two content words, no figure the restatement lacks).  Nothing is inferred across meetings and nothing is linked on an isolated "yes"."""
    turn = {(c.interaction_id, c.turn_index): c for c in claims if c.source == "source_turn" and c.interaction_id is not None and c.turn_index is not None}
    links: list[dict] = []
    for r in sorted(turn.values(), key=_order):
        if r.statement_kind == "client_statement" or r.decision_link:
            continue
        sent = _restating_sentence(r.wording)
        if sent is None:
            continue
        k = turn.get((r.interaction_id, r.turn_index + 1))
        if k is None or k.statement_kind != "client_statement" or not explicit_confirmation(k.wording) or k.decision_link:
            continue
        before = turn.get((r.interaction_id, r.turn_index - 1))
        if before is not None and before.statement_kind == "client_statement" and (before.modality in ("tentative", "hypothetical", "withdrawn") or _HEDGE.search(before.wording)):
            continue                                          # the client's own words just before are tentative: an advisor's firm restatement does not turn them into a decision
        said = content_roots(sent)
        d = None
        earlier = sorted((x for x in turn.values() if x.interaction_id == r.interaction_id and x.turn_index < r.turn_index and _client_decision(x)), key=lambda x: x.turn_index, reverse=True)
        for x in earlier:                                     # the nearest earlier client statement of THIS decision in the same meeting
            if len(content_roots(x.wording) & said) >= 2 and not (numbers_in(sent) - numbers_in(x.wording)):
                d = x
                break
        if d is None and earlier and before is earlier[0]:    # the turn before IS the client's decision but the restatement is about something else
            continue
        link = {"decision": key_of(d) if d else None, "restatement": key_of(r), "confirmation": key_of(k), "basis": "client_stated_restated_confirmed" if d else "restated_confirmed"}
        for role, c in (("decision", d), ("restatement", r), ("confirmation", k)):
            if c is not None and (c.decision_link is None or role != "decision"):
                c.decision_link = {**link, "role": role}
        links.append(link)
    return links


# ------------------------------------------------------------------------------------------------------------------------------------ signing, agreements, completion
_SIGN = re.compile(r"\bsign(?:ed|ing|s)?\b|\bsignatures?\b|\bexecut(?:e|ed|ion|ing)\b", re.I)
_SIGN_NOUN = re.compile(r"\b(?:good|bad|positive|clear|encouraging|promising|warning|first|early|any|no|a) sign\b|\bsign of\b", re.I)
_DOCS = (("exclusivity", re.compile(r"\bexclusivity\b", re.I)),
         ("purchase", re.compile(r"\b(?:purchase|sale|definitive|final) agreement\b|\bagreement draft\b|\bdraft agreement\b|\bthe agreement\b|\bpurchase and sale\b", re.I)),
         ("loi", re.compile(r"\bletter of intent\b|\bLOI\b|\bterm sheet\b|\b(?:the|that|this|first|lakeshore'?s?) offer\b|\boffer\b", re.I)),
         ("other", re.compile(r"\binvestment policy\b|\bpolicy statement\b|\bestate documents?\b|\btrust documents?\b|\bpower of attorney\b|\bwill and trust\b", re.I)))
_NEG = re.compile(r"\b(?:haven't|have not|hasn't|has not|hadn't|had not|didn't|did not|never|not yet|yet to|nothing)\b[^.?!]{0,30}\b(?:sign(?:ed)?|clos(?:e|ed)|complet(?:e|ed)|execut(?:e|ed)|finali[sz]ed)\b|"
                  r"\b(?:not|un)(?:\s+yet)?\s*(?:signed|closed|completed|executed)\b|\bunsigned\b|\bno (?:signed|signature)\b", re.I)
_MODIFIER = re.compile(r"\b(?:the|your|her|his|our|a|an|that|this|their|any)\s+(?:final\s+|draft\s+)?signed\s+(?:\w+\s+){0,2}(?:agreement|letter|offer|document|copy|contract|form|paperwork|exclusivity)\b", re.I)
_HARD_FUTURE = re.compile(r"\b(?:if|once|when|when's|whenever|will|would|'ll|should|shall|need(?:s)? to|needs|please|going to|gonna|to be|unless|until|whether|about to|ready to|want to|like to|"
                          r"for (?:your )?signature|for signing|were you able|able to|can you|could you|expected|schedul\w+|upcoming|slips?|timeline|waiting|await\w*)\b|'d like", re.I)
_PAST_EVENT = re.compile(r"\b(?:sale|deal|transaction|acquisition|closing|agreement|loi|offer|letter of intent)\s+(?:has\s+|had\s+|was\s+|were\s+)*(?:signed|executed|closed|completed|finali[sz]ed)\b|"
                         r"\b(?:i|we|she|he|they|you|rebecca|client|lakeshore|both(?: parties)?)\s+(?:have\s+|has\s+|had\s+|'ve\s+|just\s+|finally\s+|already\s+)*(?:signed|executed|closed|completed|finali[sz]ed)\b|"
                         r"\b(?:was|were|been|got|is now|are now|has been|have been)\s+(?:now\s+)?(?:signed|executed|closed|completed|finali[sz]ed)\b|\b(?:signed|executed)\s+(?:on|yesterday|last|this morning|today)\b", re.I)
_COMPLETION = re.compile(r"(?<!at )(?<!after )(?<!before )(?<!until )\bclosing\s+(?:is|in|on|date|will|set|slips?|expected|scheduled|timeline)\b|\bwill close\b|\bsale (?:closes|closed|closing|completes|completion)\b|"
                         r"\b(?:deal|transaction|acquisition|sale)\s+(?:has\s+|had\s+|was\s+)?(?:closed|completed|finali[sz]ed)\b|\bif the sale closes\b|\bclosing date\b|\bwhen (?:it|the sale|the deal) closes\b|"
                         r"\bclosing,? after\b|\bcomplet(?:e|ion) (?:of|date)\b|\b(?:we|they|it|she|he) (?:closed|completed)\b|\bclosing happened\b", re.I)


def is_event_sentence(s: str) -> bool:
    """A past-tense, unhedged statement that something was signed, executed, closed or completed: not a request ("please get me the signed ..."), a plan, a condition or a question."""
    return bool(_PAST_EVENT.search(s)) and not _HARD_FUTURE.search(s) and not _NEG.search(s) and not _MODIFIER.search(s) and not s.rstrip().endswith("?")


def _doc_of(s: str) -> str:
    for name, rx in _DOCS:
        if rx.search(s):
            return name
    return "generic"


def _kind_of(s: str) -> str:
    if s.rstrip().endswith("?"):
        return "question"
    if _NEG.search(s):
        return "negative"
    if _MODIFIER.search(s):
        return "referenced"
    if is_event_sentence(s):
        return "event"
    return "pending"


def mentions(claims: list[TimelineClaim]) -> list[dict]:
    """Every sentence of a retrieved turn that bears on signing, executing or completing, classified (facet, document, kind), in date order."""
    out: list[dict] = []
    for c in sorted((x for x in claims if x.source == "source_turn"), key=_order):
        for s in sentences(c.wording):
            sign = bool(_SIGN.search(s)) and not _SIGN_NOUN.search(s)                  # "significant" has no word boundary after "sign", so it never matches
            doc = _doc_of(s)
            if _COMPLETION.search(s):
                out.append(dict(claim=c, sentence=s, facet="completion", doc="sale", kind=_kind_of(s)))
            elif sign:
                if doc in ("exclusivity", "purchase"):
                    out.append(dict(claim=c, sentence=s, facet="agreement", doc=doc, kind=_kind_of(s)))
                elif doc in ("loi", "generic"):
                    out.append(dict(claim=c, sentence=s, facet="signing", doc=doc, kind=_kind_of(s)))
            elif doc in ("exclusivity", "purchase") and re.search(r"\bdraft\b", s, re.I):
                out.append(dict(claim=c, sentence=s, facet="agreement", doc=doc, kind="pending" if not s.rstrip().endswith("?") else "question"))
    return out


_DOC_NAME = {"exclusivity": "the exclusivity agreement", "purchase": "the purchase agreement", "loi": "the letter of intent or offer", "generic": "an unnamed document", "sale": "the sale"}
_WHO = {"client_statement": "the client", "advisor_reported": "the advisor", "advisor_interpretation": "the advisor"}


def _locs(ms: list[dict], limit: int = 4) -> str:
    seen: list[str] = []
    for m in ms:
        w = f"{m['claim'].interaction_id} turn {m['claim'].turn_index}"
        if w not in seen:
            seen.append(w)
    more = f" and {len(seen) - limit} more" if len(seen) > limit else ""
    return ", ".join(seen[:limit]) + more


def _status_of(ms: list[dict]) -> str:
    real = [m for m in ms if m["kind"] != "question"]
    if any(m["kind"] == "event" for m in real):
        later_neg = [m for m in real if m["kind"] == "negative" and _order(m["claim"]) > max(_order(e["claim"]) for e in real if e["kind"] == "event")]
        return "partial" if later_neg else "established"
    if not real:
        return "unavailable"
    return {"negative": "negative", "referenced": "referenced", "pending": "pending"}[sorted(real, key=lambda m: _order(m["claim"]))[-1]["kind"]]


_RANK = {"event": 0, "negative": 1, "referenced": 2, "pending": 3, "question": 4}


def _ev(ms: list[dict], limit: int = 6) -> list[StatusEvidence]:
    """The evidence shown for a facet: the mentions that decide it first (events, then negatives, references, pending), a question last; within a kind the earliest and the latest."""
    pick = sorted(ms, key=lambda m: (_RANK[m["kind"]], _order(m["claim"])))
    keep: list[dict] = []
    for kind in _RANK:
        of = [m for m in pick if m["kind"] == kind]
        keep += (of[:1] + of[-1:]) if len(of) > 3 else of                  # every turn a statement names must be among the evidence shown: only a long run is thinned to its ends
    pick = sorted({id(m): m for m in keep}.values(), key=lambda m: (_RANK[m["kind"]], _order(m["claim"])))[:limit]
    return [evidence_of(m["claim"], "mention", m["kind"], m["sentence"]) for m in pick]


_VERB = {"loi": "signed", "exclusivity": "signed or executed", "purchase": "signed or executed", "generic": "signed", "sale": "closed or completed"}


def _bound(last: dict, through: Optional[str], verb: str) -> str:
    """A finding is TIME-BOUNDED: what the last mention says holds as of its date.  When the retrieved record runs later, say that nothing in it settles what happened after that date."""
    d = last["claim"].event_date
    if not d or not through or d >= through:
        return ""
    return f" This is as of {fmt_date(d)}: nothing in the retrieved turns settles whether it was {verb} after that date, in either direction."


def _doc_statement(doc: str, ms: list[dict], through: Optional[str]) -> tuple[str, str]:
    """(status, code-written statement) for one document or the sale, from its classified mentions.  The statement describes WHERE and WHEN the retrieved turns say something and of what kind; it never
    restates their content in other words (the exact wording is in the evidence list)."""
    name = _DOC_NAME.get(doc, doc)
    verb = _VERB.get(doc, "signed")
    cap = name[0].upper() + name[1:]
    status = _status_of(ms)
    real = sorted((m for m in ms if m["kind"] != "question"), key=lambda m: _order(m["claim"]))
    asks = [m for m in ms if m["kind"] == "question"]
    tail = f" A question about it was asked ({_locs(asks)}); a question establishes nothing." if asks else ""
    upto = f" The retrieved record runs to {fmt_date(through)}." if through else ""
    if status == "established":
        ev = [m for m in real if m["kind"] == "event"]
        return status, f"{len(ev)} retrieved turn(s) state in the past tense, without hedge, that {name} was {verb} ({_locs(ev)}); read them before relying on it.{tail}"
    if status == "partial":
        return status, f"A retrieved turn states that {name} was {verb} and a later turn says it had not been; the record conflicts ({_locs(real)})."
    if status == "negative":
        neg = [m for m in real if m["kind"] == "negative"]
        mine = [m for m in neg if m["claim"].statement_kind == "client_statement"]
        theirs = [m for m in neg if m["claim"].statement_kind != "client_statement"]
        who = (f"the client's own statement ({_locs(mine)})" + (f" and the advisor's restatement of it ({_locs(theirs)})" if theirs else "")) if mine else f"the advisor's account ({_locs(theirs)}), not a client statement,"
        return status, (f"As of {fmt_date(neg[-1]['claim'].event_date)}, {who} {'say' if (mine and theirs) else 'says'} {name} had not been {verb}. No retrieved turn records it as {verb}."
                        f"{_bound(neg[-1], through, verb)}{tail}{upto}")
    if status == "referenced":
        ref = [m for m in real if m["kind"] == "referenced"]
        return status, (f"{cap} is called 'signed' only in requests, plans or questions ({_locs(ref)}; last {fmt_date(ref[-1]['claim'].event_date)}): the speaker presupposes it, but no retrieved "
                        f"turn states that it was signed, sent or received.{_bound(ref[-1], through, verb)}{tail}{upto}")
    if status == "pending":
        pend = [m for m in real if m["kind"] == "pending"]
        return status, (f"{cap} is described as still to come, conditional or in draft ({_locs(pend)}; last {fmt_date(pend[-1]['claim'].event_date)}). No retrieved turn records it as {verb}.{_bound(pend[-1], through, verb)}{tail}{upto}")
    return "unavailable", f"No retrieved turn addresses whether {name} was {verb}: an absence of evidence, not a finding that it did not happen.{tail}{upto}"


def _later_note(others: list[dict], after: Optional[str]) -> str:
    later = [m for m in others if after and (m["claim"].event_date or "") > after and m["kind"] != "question"]
    if not later:
        return ""
    return f" Later turns ({_locs(later)}) refer to signing without naming a document or in the future."


# What a LATER client statement about the subject is.  A decision is withdrawn by a statement that withdraws it, not by a feeling: "I feel sick about it. What if they leave?" is a concern about a consequence,
# "I'm having second thoughts about selling" reopens the question without answering it, "I've changed my mind, I'm not selling" is a reversal.  Anything else is no signal at all.
_REVERSAL = re.compile(r"\b(?:i(?:'ve| have)? changed my mind|i(?:'m| am) (?:not selling|no longer selling|not going to sell|calling it off|pulling out|backing out|not going ahead)|"
                       r"i (?:won't|will not|can't|cannot) (?:sell|go ahead|go through)|(?:call|calling|called) (?:it|the sale|the deal) off|(?:back|backing|pull|pulling) out of (?:the|this) (?:sale|deal)|"
                       r"(?:cancel|cancelling|canceling|scrap|scrapping) (?:the|this) (?:sale|deal)|(?:withdraw|withdrawing) (?:from|my) (?:the )?(?:sale|deal|decision)|"
                       r"i(?:'ve| have) decided (?:not to sell|against (?:selling|the sale))|(?:no longer|don't) want to sell)\b", re.I)
_RECONSIDER = re.compile(r"\b(?:second thoughts|reconsider\w*|i(?:'m| am) not (?:so )?sure (?:about|whether|if) (?:selling|the sale|going ahead|to sell)|should i (?:wait|hold off|still sell|really sell)|"
                         r"maybe i should (?:wait|hold off|not sell)|i (?:need|want) to think (?:about|it over) (?:selling|the sale)|have (?:some )?doubts? about (?:selling|the sale))\b", re.I)
_CONCERN = re.compile(r"\b(?:feel sick|sick about|worr(?:y|ied|ies|ying)|nervous|anxious|afraid|scared|lying awake|what if|stress(?:ed|ful)?|uneasy|concern(?:ed|s)?|dread)\b", re.I)


def later_signal(c: TimelineClaim) -> Optional[str]:
    """withdrawal | reconsideration | concern | None, from the client's own wording (see the comment above).  Order matters: an explicit withdrawal is a withdrawal even if it also voices a worry."""
    if c.modality == "withdrawn" or _REVERSAL.search(c.wording):
        return "withdrawal"
    if _RECONSIDER.search(c.wording):
        return "reconsideration"
    if _CONCERN.search(c.wording):
        return "concern"
    return None


def decision_facet(claims: list[TimelineClaim], roots: Optional[set] = None) -> StatusFacet:
    """The client's decision on the subject: confirmed (stated or reported, restated and explicitly confirmed), merely stated, tentative, or not found."""
    src = [c for c in claims if c.source == "source_turn"]
    by_key = {key_of(c): c for c in src}
    chains = []
    for c in src:
        dl = c.decision_link
        if not dl or dl.get("role") != "restatement":
            continue
        d, r, k = by_key.get(dl.get("decision") or ""), by_key.get(dl["restatement"]), by_key.get(dl["confirmation"])
        if r is None or k is None or not (about(r, roots) or (d is not None and about(d, roots))):
            continue
        chains.append((d, r, k))
    if chains:
        last_meeting = sorted(chains, key=lambda t: _order(t[2]))[-1][1].interaction_id
        mine = sorted((t for t in chains if t[1].interaction_id == last_meeting), key=lambda t: t[2].turn_index)
        d = next((t[0] for t in mine if t[0] is not None), None)
        r, k = mine[0][1], mine[0][2]
        ev = ([evidence_of(d, "decision")] if d else []) + [e for t in mine for e in (evidence_of(t[1], "restatement"), evidence_of(t[2], "confirmation"))]
        after = mine[-1][2]
        later = sorted(((c, later_signal(c)) for c in src if _order(c) > _order(after) and c.statement_kind == "client_statement" and about(c, roots)), key=lambda t: _order(t[0]))
        later = [(c, sg) for c, sg in later if sg]
        reversed_ = [(c, sg) for c, sg in later if sg == "withdrawal"]
        if d is not None:
            how = (f"On {fmt_date(d.event_date)} the client stated the decision ({where_of(d)}), the advisor restated it in the next turn ({where_of(r)}) and the client explicitly confirmed it in the turn after "
                   f"({where_of(k)}).")
        else:
            how = (f"On {fmt_date(r.event_date)} the advisor restated the client's decision ({where_of(r)}) and the client explicitly confirmed it in the next turn ({where_of(k)}); the client's own first "
                   f"statement of it was not retrieved.")
        for t in mine[1:]:
            how += f" The advisor restated it again ({where_of(t[1])}) and the client confirmed it again ({where_of(t[2])})."
        note = " This is the client's decision only: it does not establish that any agreement was signed or that the sale completed."
        if reversed_:
            c = reversed_[-1][0]
            ev.append(evidence_of(c, "later", "withdrawal"))
            return StatusFacet(key="decision", label="The client's decision", status="partial", as_of=after.event_date, evidence=ev,
                               statement=how + f" On {fmt_date(c.event_date)} ({where_of(c)}) a later client statement is worded as a withdrawal or reversal of it, so it is not established as the current decision.")
        for c, sg in later[-2:]:
            ev.append(evidence_of(c, "later", sg))
        recon = [c for c, sg in later if sg == "reconsideration"]
        concern = [c for c, sg in later if sg == "concern"]
        if recon:
            note += (f" On {fmt_date(recon[-1].event_date)} ({where_of(recon[-1])}) the client's wording reopens the question; that is neither a withdrawal nor a reaffirmation, so the retrieved turns do not show "
                     f"whether the decision changed.")
        if concern:
            note += (f" On {fmt_date(concern[-1].event_date)} ({where_of(concern[-1])}) the client's wording is a worry or a 'what if' question; a concern is not a withdrawal of the decision.")
        note += (f" No retrieved client statement after {fmt_date(after.event_date)} is worded as a withdrawal or reversal of it (the retrieved record runs to {fmt_date(max((c.event_date for c in src if c.event_date), default=None))}); "
                 f"that is the absence of such a statement, not proof that nothing changed.")
        return StatusFacet(key="decision", label="The client's decision", status="established", as_of=after.event_date, evidence=ev, statement=how + note)
    stated = sorted((c for c in src if c.statement_kind == "client_statement" and not _is_question(c.wording) and about(c, roots)
                     and (c.modality in ("decision", "tentative", "hypothetical") or _HEDGE.search(c.wording))), key=_order)
    if stated:
        c = stated[-1]
        if c.modality == "decision" and not _HEDGE.search(c.wording):
            txt = (f"The latest decision-worded statement is the client's own on {fmt_date(c.event_date)} ({where_of(c)}). No restatement by the advisor followed by the client's explicit confirmation was "
                   f"retrieved, so it is not established as a confirmed decision.")
        else:
            txt = (f"The latest client statement on the subject is tentative ({fmt_date(c.event_date)}, {where_of(c)}); no decision of the client on it is stated or confirmed in the retrieved turns.")
        return StatusFacet(key="decision", label="The client's decision", status="partial", as_of=c.event_date, statement=txt, evidence=[evidence_of(c, "decision")])
    return StatusFacet(key="decision", label="The client's decision", status="unavailable", statement="No retrieved turn records a client decision on this subject: an absence of evidence, not a finding that none exists.")


def transaction_status(claims: list[TimelineClaim], roots: Optional[set] = None) -> TransactionStatus:
    """The four facets (see the module doc), written by code from the retrieved turns."""
    src = [c for c in claims if c.source == "source_turn"]
    dates = sorted({c.event_date for c in src if c.event_date})
    through = dates[-1] if dates else None
    ms = mentions(claims)

    # signing: the letter of intent / the offer
    loi = [m for m in ms if m["facet"] == "signing" and m["doc"] == "loi"]
    gen = [m for m in ms if m["facet"] == "signing" and m["doc"] == "generic"]
    if loi:
        status, txt = _doc_statement("loi", loi, through)
        last_loi = max((m["claim"].event_date or "" for m in loi if m["kind"] != "question"), default=None)
        txt += _later_note(gen, last_loi)
    else:
        status, txt = ("unavailable", "No retrieved turn says whether the letter of intent or the offer was signed: an absence of evidence, not a finding that it was not signed." + (f" The retrieved record runs to {fmt_date(through)}." if through else ""))
        if any(m["kind"] != "question" for m in gen):
            txt += f" Turns that mention signing without naming a document: {_locs([m for m in gen if m['kind'] != 'question'])}."
    signing = StatusFacet(key="signing", label="Signing of the letter of intent or offer", status=status, statement=txt, as_of=max((m["claim"].event_date for m in loi if m["kind"] != "question" and m["claim"].event_date), default=None),
                          evidence=_ev(loi + gen))

    # agreement execution: the exclusivity agreement and the purchase agreement are separate documents
    docs = []
    for doc in ("exclusivity", "purchase"):
        dm = [m for m in ms if m["facet"] == "agreement" and m["doc"] == doc]
        if dm:
            st, tx = _doc_statement(doc, dm, through)
            docs.append(dict(document=_DOC_NAME[doc], status=st, statement=tx, keys=[key_of(m["claim"]) for m in dm]))
    order = ["established", "partial", "referenced", "pending", "negative", "unavailable"]
    a_status = min((d["status"] for d in docs), key=order.index) if docs else "unavailable"
    a_txt = " ".join(f"{d['document'][0].upper() + d['document'][1:]}: {d['statement']}" for d in docs) if docs else (
        "No retrieved turn addresses whether the exclusivity agreement or the purchase agreement was signed or executed: an absence of evidence, not a finding that neither was." + (f" The retrieved record runs to {fmt_date(through)}." if through else ""))
    agreement = StatusFacet(key="agreement", label="Execution of the exclusivity and purchase agreements", status=a_status, statement=a_txt, documents=docs,
                            as_of=max((m["claim"].event_date for m in ms if m["facet"] == "agreement" and m["kind"] != "question" and m["claim"].event_date), default=None),
                            evidence=_ev([m for m in ms if m["facet"] == "agreement"]))

    # completion: the closing of the sale itself
    cm = [m for m in ms if m["facet"] == "completion"]
    if cm:
        c_status, c_txt = _doc_statement("sale", cm, through)
    else:
        c_status, c_txt = "unavailable", ("No retrieved turn addresses whether the sale closed or completed: an absence of evidence, not a finding that it did not." + (f" The retrieved record runs to {fmt_date(through)}." if through else ""))
    completion = StatusFacet(key="completion", label="Completion of the sale", status=c_status, statement=c_txt, evidence=_ev(cm),
                             as_of=max((m["claim"].event_date for m in cm if m["kind"] != "question" and m["claim"].event_date), default=None))
    return TransactionStatus(decision=decision_facet(claims, roots), signing=signing, agreement=agreement, completion=completion, record_through=through)


def outcome_from(t: TransactionStatus) -> OutcomeStatement:
    """The older, flat outcome record, derived from the facets (kept for consumers of `AdvisorReport.outcome`)."""
    est = [f.statement for f in (t.signing, t.agreement, t.completion) if f.status == "established"]
    notest = [f"{f.label}: {f.statement}" for f in (t.signing, t.agreement, t.completion) if f.status != "established"]
    kind = {"event": "event", "negative": "hedged", "question": "hedged", "referenced": "pending", "pending": "pending"}
    ms = [dict(claim_id=e.claim_id, date=e.date, where=e.where, speaker=e.speaker, excerpt=e.excerpt, kind=kind.get(e.kind or "", "pending"), ref=e.ref) for f in (t.signing, t.agreement, t.completion) for e in f.evidence]
    return OutcomeStatement(established=est, not_established=notest, mentions=ms)
