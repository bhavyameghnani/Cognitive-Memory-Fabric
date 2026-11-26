"""Phase 3: evidence-grounded narrative synthesis.  Pure code (data in, data out): no model, no store, no retrieval.

THE PROBLEM THIS SOLVES.  The Phase 2 narrative could only be verified if every sentence was a verbatim quotation, so the report read as a quotation dump.  Here the model PARAPHRASES, and code
validates each PROPOSITION (one sentence and the one to three source claims it rests on) for factual support instead of demanding verbatim text.  Quotations are still allowed, but any
quotation must match its source exactly, and a paraphrase may never be placed inside quotation marks.

WHAT A PROPOSITION MUST SATISFY (every rule is a test in tests/test_phase3.py; none weakens an existing check - `story_problems`, the closed rules for causes, inner states, decisions, attribution,
relations, dates, figures and names, all still run):
  * ATTRIBUTION: the sentence opens with who said or recorded it (the client by name or "the client", or "the advisor"); a client subject needs a client statement among the cited claims, an advisor
    subject needs an advisor's account or interpretation, and a sentence that mixes both says so.
  * CERTAINTY: a tentative or hypothetical client line stays hedged in the sentence ("was inclined", "was weighing", "had not signed"); "decided" appears only as "<client> said she had decided"
    over a client line that says so.
  * SUPPORT: every content word of the sentence that is not a function word, a reporting or stance word, a name or a date is a word (by stem) of the cited wording.  This is what stops a fluent
    sentence from asserting something no cited turn says: the room to invent a fact, a motive or an outcome is closed, while ordinary paraphrase of the cited words stays open.
  * OUTCOMES: signing, completion, closing, acceptance and agreement words must occur in the cited wording and may not be firmer than the clause they come from.
  * DATES: a day-and-month in the sentence must be the date of a cited claim (or a date inside the cited wording); a year must be a cited year.
  * TIME: a sentence that joins claims of different dates carries a time marker.
  * INFORMATIVENESS (Phase 4A): the sentence has a proposition of its own: not an interjection or agenda reply ("said that fine, net proceeds ..."), not an elliptical reply ("said that it is, ..."), not
    a bare "it / that" with nothing to refer to, not a verbless fragment after "said".  Structural rules (informativeness_problems), not a list of topic words.
  * ROLES (Phase 4A): who is "I" and who is "you" is kept.  A cited sentence that joins the speaker with someone ("your accountant and I", "we agreed") is paraphrased only if both the advisor and the client
    are named in plain words, and a role may appear only where the cited wording licenses it (role_problems).
The Evidence Verifier re-runs the same function on claims it rebuilds itself (harness/evidence_verifier.py), so the Report Agent's own judgement is never the only one."""
from __future__ import annotations

import re
from typing import Optional

from agents.report import semantic as S
from agents.report import status as ST
from agents.report.timeline import _where, significant
from agents.report.validate import _ATTRIBUTING, _OVERREACH, source_record_problem
from schemas.report import Finding, NarrativeSentence, TimelineClaim

MAX_CITED = 3                    # a proposition rests on at most this many claims
MAX_CITED_SUMMARY = 10           # an executive-summary sentence may rest on the claims of several propositions
MIN_WORDS, MAX_CHARS = 6, 420
MIN_LINE_WORDS = 8               # a source line shorter than this is not paraphrased
MAX_STRAY = 0                    # content words not found in the cited wording that one sentence may contain: NONE (a reporting verb, a connective or a neutral equivalent is allowed by name; any other word is a new fact)

SYN_WINDOW, SYN_HARD = 12, 15
GREETING = re.compile(r"^(?:hi|hey|hello|morning|good (?:morning|timing)|it's \w+|\w+, thanks for making time|thanks for making time)\b", re.I)


# ------------------------------------------------------------------------------------------------------------------------------------ what is narrated
NEAR_TURNS = 3       # a relation word (agree, speak, propose) counts only within this many turns of a line about the MAIN subject, in the same meeting


def _focus_keep(c: TimelineClaim, roots: set, main: Optional[set], mains: dict) -> bool:
    """Is this claim part of the focused subject?  It mentions the main subject itself (the best grounded search phrase: "accountant"), or it mentions a RELATION word of the question (agree, speak, propose) within
    NEAR_TURNS turns of such a line in the same meeting.  "I'd only propose changes if the mix drifts" in another meeting says nothing about who proposed a call."""
    if ST.about(c, main or roots):
        return True
    return ST.about(c, roots) and any(abs(int(c.turn_index or 0) - t) <= NEAR_TURNS for t in mains.get(c.interaction_id, ()))


def synthesis_pool(claims: list[TimelineClaim], roots: Optional[set] = None, main: Optional[set] = None) -> list[TimelineClaim]:
    """The claims a synthesised report covers: the significant ones (timeline.significant) and the advisor's substantive accounts that mention the subject (a recap, a plan, an open-items list), which are
    where outcomes and next steps are stated.  Greetings and agenda lines are not.

    `roots` (status.subject_roots / grounded_roots) is given ONLY when the plan names no topic.  Without a topic nothing is "the subject" for the old test (`mentions_topic` is None), so every advisor
    line was dropped and every client line kept: a question about ONE call with an accountant came back as the client's whole history, without the advisor's own words about the call.  With `roots`, the
    pool is the claims that are ABOUT those roots - the advisor's and the client's alike - and nothing else: a relevant advisor statement is no longer excluded, an unrelated one still is."""
    out = []
    mains: dict = {}
    if roots:
        for c in claims:
            if c.source == "source_turn" and ST.about(c, main or roots):
                mains.setdefault(c.interaction_id, set()).add(c.turn_index)
    for c in claims:
        if c.source != "source_turn" or not c.relevant:
            continue
        if len(c.wording.split()) < MIN_LINE_WORDS and not any(r["type"] == "corrects" for r in c.relations):
            continue                                    # "Go ahead." / "Correct. Move on it.": a paraphrase would have to supply the meaning; the line stays available as its recorded wording
        if acknowledgement_only(c.wording):
            continue                                    # "Fine. Net proceeds and the people side of a sale first.": an agenda reply with nothing to report (Phase 4A); it stays in the code-written chronology
        if roots:
            if _focus_keep(c, roots, main, mains) and (significant(c) or (c.statement_kind in ("advisor_reported", "advisor_interpretation") and len(c.wording.split()) >= 10 and not GREETING.match(c.wording.strip())
                                                          and "anything pressing" not in c.wording.lower())):
                out.append(c)
            continue
        if significant(c):
            out.append(c)
        elif c.mentions_topic and c.statement_kind in ("advisor_reported", "advisor_interpretation") and len(c.wording.split()) >= 10 and not GREETING.match(c.wording.strip()) \
                and "anything pressing" not in c.wording.lower():
            out.append(c)
    return sorted(out, key=S._order_key)


def synthesis_windows(claims: list[TimelineClaim], size: int = SYN_WINDOW, hard: int = SYN_HARD, roots: Optional[set] = None, main: Optional[set] = None) -> list[list[TimelineClaim]]:
    """The pool cut into consecutive chronological windows (a window ends at a meeting boundary once it holds `size` claims; never more than `hard`; a tiny last window joins the one before)."""
    pool = synthesis_pool(claims, roots, main)
    windows: list[list[TimelineClaim]] = []
    cur: list[TimelineClaim] = []
    for i, c in enumerate(pool):
        cur.append(c)
        nxt = pool[i + 1] if i + 1 < len(pool) else None
        if nxt is not None and (len(cur) >= hard or (len(cur) >= size and nxt.interaction_id != c.interaction_id)):
            windows.append(cur)
            cur = []
    if cur:
        if windows and len(cur) < 4 and len(windows[-1]) + len(cur) <= hard + 3:
            windows[-1].extend(cur)
        else:
            windows.append(cur)
    return windows


# ------------------------------------------------------------------------------------------------------------------------------------ vocabulary
REPORT_WORDS = frozenset("""said say says told tell asked ask wondered mentioned noted note notes recorded record reported report described explained suggested recommended advised raised replied answered
added summarised summarized clarified acknowledged understood stated expressed discussed reviewed covered outlined proposed offered raised wrote spoke talked meeting meetings conversation conversations
client advisor advisor's account interpretation recommendation statement position earlier later then before after following subsequently previously same day week month year time next first second
considering weighing inclined unsure uncertain undecided curious hesitant thinking exploring tentative possibility idea option yet not never no only still also both either whether about again
corrected correction corrects correcting original originally record records recorded wrong overstated mistaken noted conflicting unresolved unverified
would could should might must will shall can may reminded listed repeated indicated replied
given further regarding including despite although since while meanwhile otherwise rather overall such other another several various some each every already during until""".split())
# the verbs that report what someone said: a sentence of the report names its speaker and uses one of these (it reports a statement; it does not assert the fact)
ATTRIB_VERBS = _ATTRIBUTING                       # ONE list, shared with the verifier's source-record rule (agents/report/validate.py): what the agent accepts the verifier accepts


def _subject_rx(names) -> re.Pattern:
    """The sentence (its own date phrase removed) opens with the speaker: the client's name, 'the client' or 'the advisor' - and not as a possessive ("Rebecca's accountant ..." is not Rebecca speaking)."""
    nm = "|".join(re.escape(n) for n in names)
    alt = rf"(?:the client|the advisor{('|' + nm) if nm else ''})"
    return re.compile(rf"^\W*(?P<who>{alt})\b(?!['’]s)", re.I)


NEG_COND = re.compile(r"\b(?:not|never|no|yet|until|if|before|unless|whether|pending|draft|proposed|expected|about to|would|could|might|may|should|still|inclined|considering|weighing|asked|waiting|await\w*|open items?)\b|n['’]t\b", re.I)
HEDGE_OK = re.compile(r"\b(?:inclined|considering|weighing|unsure|not sure|undecided|curious|thinking|wondering|wondered|asked|asking|might|maybe|possibly|tentative\w*|exploring|had not|has not|not yet|yet to|no decision|"
                      r"not decided|open to|hesitant|nervous|question\w*|possibility|idea|whether|if)\b|n['’]t\b", re.I)
OUTCOME_W = re.compile(r"\b(?:sign\w*|complet\w+|clos\w+|finali[sz]\w+|sold|exchang\w+|accept\w*|agree\w*|approv\w+|committed)\b", re.I)
_MON_RX = "|".join(sorted(S._MONTHS, key=len, reverse=True))
_DM = re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m>{_MON_RX})\b(?:,?\s+(?P<y>\d{{4}}))?", re.I)
_MD = re.compile(rf"\b(?P<m>{_MON_RX})\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<y>\d{{4}}))?", re.I)


def _clause_with(stem: str, wording: str) -> str:
    for part in re.split(r"[.;!?]", wording):
        if any(_root(w) == stem for w in S._WORD.findall(part)):
            return part
    return ""


# ------------------------------------------------------------------------------------------------------------------------------------ rules
def date_problems(text: str, cited: list[TimelineClaim]) -> tuple[str, list[str]]:
    """(text with each validated day-and-month reduced to the month name, problems).  A day and month must be the date of a cited claim, or a date that the cited wording itself contains."""
    out: list[str] = []
    wording = " ".join(c.wording for c in cited)
    dates = {(c.event_date or "")[5:7] + "-" + (c.event_date or "")[8:10] for c in cited if c.event_date}
    years = {(c.event_date or "")[:4] for c in cited if c.event_date}
    for rx in (_DM, _MD):
        def repl(m):
            mon = S._MONTHS[m.group("m").lower()]
            d = int(m.group("d"))
            ok = f"{mon}-{d:02d}" in dates or re.search(rf"\b{d}\b", wording) is not None
            if not ok:
                out.append(f"unsupported_date: {m.group(0)} is not the date of a cited claim")
            if m.group("y") and m.group("y") not in years:
                out.append(f"unsupported_date: the year {m.group('y')} is not a year of a cited claim")
            return m.group("m")
        text = rx.sub(repl, text)
    return text, out


_PERSON = re.compile(r"\b(?:you|your|yours|yourself|you['\u2019](?:re|ve|d|ll)|i|i['\u2019](?:m|ve|d|ll)|my|me|mine|myself|we|our|ours|us|we['\u2019](?:re|ve|ll|d))\b", re.I)
_CONTRACTIONS = frozenset("didn't don't doesn't hadn't hasn't haven't wasn't weren't wouldn't couldn't shouldn't isn't aren't won't can't cannot didnt dont".split())
# neutral words that mean the same thing in this domain: a paraphrase may use either of a pair when the cited wording uses the other.  Only nouns and stance words; never an outcome, a decision or a number.
_EQUIVALENT = [("company", "business", "firm", "enterprise"), ("approach", "contact"), ("offer", "proposal", "letter"), ("uncertain", "unsure", "undecided", "hesitant", "doubt"), ("curious", "interested", "interest"),
               ("accountant", "adviser"), ("worth", "value", "valuation"), ("statement", "statements", "financials")]


def _root(w: str) -> str:
    """A crude root for comparing a paraphrase with its source: plural / tense / -ing / -ly / -ness / -ity endings and doubled final consonants are dropped (running -> run, planning -> plan)."""
    w = w.lower().replace("\u2019", "'")
    if w.endswith("'s"):
        w = w[:-2]
    w = _IRREGULAR.get(w, w)
    for suf in ("ingly", "ness", "ment", "ity", "ing", "ied", "ies", "ed", "es", "ly", "s", "y"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]                                        # give / giving, close / closed
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
        w = w[:-1]
    return w


_IRREGULAR = {"felt": "feel", "thought": "think", "told": "tell", "said": "say", "made": "make", "knew": "know", "went": "go", "got": "get", "left": "leave", "sent": "send", "paid": "pay", "held": "hold",
              "kept": "keep", "took": "take", "gave": "give", "began": "begin", "wrote": "write", "met": "meet", "spoke": "speak"}


def unsupported_words(text: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """Content words of the sentence that are neither function / reporting / stance words, nor names, nor dates, nor words of the cited wording (compared by root and by a five-letter prefix, so
    "uncertainty" matches "uncertain" and "proceeding" matches "proceed"), nor a neutral equivalent of one."""
    bare = S._DATE.sub(" ", S.strip_quotes(S.MARK.sub("", text)))
    source = {w.lower() for c in cited for w in S._WORD.findall(c.wording)}
    allowed = source | {w.lower() for w in S.FUNCTION_WORDS} | {w.lower() for w in REPORT_WORDS} | {n.lower() for n in names} | set(S._MONTHS) | _CONTRACTIONS
    for c in cited:
        if c.relations or c.temporal_status in ("corrected", "superseded"):
            allowed |= {w.lower() for w in S._STATUS_WORDS["correction"]}
    for group in _EQUIVALENT:
        if any(g in source or _root(g) in {_root(s) for s in source} for g in group):
            allowed |= set(group)
    roots = {_root(w) for w in allowed}
    prefixes = {w[:5] for w in allowed if len(w) >= 6}
    stray: list[str] = []
    for w in S._WORD.findall(bare):
        lw = w.lower()
        if len(lw) < 4 or lw in allowed or _root(lw) in roots or (len(lw) >= 6 and lw[:5] in prefixes):
            continue
        stray.append(w)
    return list(dict.fromkeys(stray))


# ------------------------------------------------------------------------------------------------------------------------------------ Phase 4A (C): informativeness
# A sentence can pass every support rule and still say nothing: "Rebecca said that fine, net proceeds and the people side of a sale first." (the reply "Fine. Net proceeds and the people side of a sale first."
# answers an agenda question the report never shows) or "The advisor said that it is, the more important question is ..." (an elliptical reply with no antecedent).  These rules reject a sentence that has
# no understandable proposition of its own.  They look at the STRUCTURE of the reported clause and of the cited line (an interjection or an elliptical reply at the start, a bare demonstrative with nothing it
# could refer to, a fragment with no predicate after "said that"), never at a list of topic words, so an ordinary tentative statement, question, correction or preference is untouched.
_INTERJECTION = re.compile(r"^\W*(?:fine|ok(?:ay)?|yes|yeah|yep|yup|no|nope|sure|alright|all right|right|well|so|good|great|thanks|thank you|sounds good|got it|i see|indeed|absolutely|exactly|correct|of course|certainly)\s*[,.;:!—-]"
                           r"|^\W*(?:fine|ok(?:ay)?|yeah|yep|yup|alright)\s+(?=[a-z])|^\W*that(?:'s| is) (?:fine|good|great|right|correct|ok(?:ay)?)\b\s*[,.;:!]?", re.I)
_ELLIPTICAL = re.compile(r"^\W*(?:it|that|this|there|he|she|they|i|we|you)\s+(?:is|are|was|were|do|does|did|will|would|can|could|has|have|had)\s*[,;:.!—-]", re.I)
_DEMONSTRATIVE = re.compile(r"^\W*(?:it|that|this|these|those|there)\b", re.I)
_SAYING = re.compile(r"\b(?:said|says|replied|answered|added|responded)\b", re.I)         # verbs that take a CLAUSE ("said that ..."); "explained the risk of ...", "noted the offer" take a noun phrase and are not tested for a verb
_AUXILIARY = frozenset("""is are was were be been being am do does did have has had will would can could should may might must shall isn't aren't wasn't weren't don't doesn't didn't haven't hasn't hadn't won't wouldn't can't
couldn't shouldn't cannot""".split())
_VERBISH = frozenset("""want wants wanted need needs needed sell sells sold send sends sent give gives gave ask asks asked feel feels felt think thinks thought know knows knew like likes liked plan plans planned decide
decides decided consider considers considered move moves moved keep keeps kept take takes took make makes made get gets got go goes went come comes came see sees saw hold holds held wait waits waited accept accepts
accepted agree agrees agreed sign signs signed close closes closed expect expects expected review reviews reviewed cover covers covered discuss discusses discussed model prepare prepares prepared draft drafted hear
heard talk talks talked speak speaks spoke call calls called set sets raise raises raised weigh weighs weighed hope hopes hoped worry worries worried change changes changed correct corrects corrected continue
continues continued remain remains remained run runs ran carry carries carried care cares cared prefer prefers preferred remember remembers remembered understand understands understood appear appears appeared
seem seems seemed look looks looked mean means meant matter matters mattered depend depends depended help helps helped lose loses lost leave leaves left stay stays stayed stop stops stopped start starts started
tell tells told show shows showed shown pay pays paid fund funds funded hire hires hired buy buys bought invest invests invested""".split())
_NOT_VERBS = frozenset("morning evening meeting building during following financing housing nothing something anything everything wedding savings".split())


def has_predicate(clause: str) -> bool:
    """Does this stretch of words contain a verb?  (auxiliary, a common verb, or an -ed / -ing word that is not a known noun).  Deliberately generous: a doubtful clause counts as having one, so that a
    real statement is never refused for lack of one; only a clear fragment ("net proceeds and the people side of a sale first") has none."""
    for w in re.findall(r"[A-Za-z][A-Za-z']*", clause.lower().replace("’", "'")):
        if w in _AUXILIARY or w in _VERBISH or re.search(r"'(?:ll|ve|re|m|d)$", w) or w.endswith("n't"):
            return True
        if len(w) >= 5 and w.endswith(("ed", "ing")) and w not in _NOT_VERBS:
            return True
    return False


def acknowledgement_only(wording: str) -> bool:
    """A source line that opens with an interjection and whose remainder has no predicate of its own ("Fine. Net proceeds and the people side of a sale first."): an answer to a question the report does not
    contain, with nothing to report.  The line stays in the code-written chronology; it is not narrated."""
    parts = S.split_sentences(wording) or [wording]
    if len(parts) < 2 or len(parts[0].split()) > 3 or not _INTERJECTION.match(parts[0] + " x"):
        return False
    return not has_predicate(" ".join(parts[1:]))


def reported_clause(text: str, names=()) -> tuple[str, str]:
    """(clause, verb): what a sentence reports, after its speaker and speech verb and an optional "that / whether / if"; ("", "") when the sentence does not open with a speaker and a speech verb."""
    opening = _LEAD_DATE.sub("", " ".join(S.MARK.sub("", text).split())).strip()
    found = _subject_rx(names).match(opening)
    if not found:
        return "", ""
    rest = opening[found.end():]
    m = ATTRIB_VERBS.search(rest[:80])
    if not m:
        return "", ""
    clause = re.sub(r"^\s*(?:(?:to|of)\s+\w+\s+)?(?:that|whether|if)\b\s*", "", rest[m.end():], flags=re.I).strip()
    return clause, m.group(0).lower()


def _concrete(clause: str, names=()) -> bool:
    """A figure, a number word, a month or a proper name: something the clause is about even if its subject is a bare pronoun."""
    if re.search(r"\d", clause) or {w.lower() for w in re.findall(r"[A-Za-z]+", clause)} & _NUM_OR_MONTH:
        return True
    return any(w for w in re.findall(r"(?<=[a-z,;] )[A-Z][a-z]{2,}", clause) if w.lower() not in {n.lower() for n in names})


_NUM_OR_MONTH = frozenset(S._MONTHS) | frozenset("one two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million".split())


def informativeness_problems(text: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """Why a sentence says nothing of its own (empty list = it has a proposition).  Structural, never keyword-only:
      empty_acknowledgement  every cited line is an interjection plus a fragment ("Fine. Net proceeds and the people side of a sale first.")
      filler_clause          the reported clause begins with an interjection ("said that fine, ...", "said that yes, ...")
      elliptical_reply       it begins with a pronoun and an auxiliary and then breaks off ("said that it is, the more important ...")
      no_antecedent          the clause is about a bare "it / that / this / these / those" and names nothing concrete the pronoun could stand for
      fragment               after "said / told / noted ...", the clause has no verb at all"""
    out: list[str] = []
    if cited and all(acknowledgement_only(c.wording) for c in cited):
        out.append("empty_acknowledgement: the cited line is an acknowledgement or an agenda reply with nothing to report")
    clause, verb = reported_clause(text, names)
    if not clause:
        return out
    if _INTERJECTION.match(clause):
        out.append("filler_clause: the reported clause begins with an interjection (fine, yes, no, well ...); it is an answer to a question the sentence does not give")
    elif _ELLIPTICAL.match(clause):
        out.append("elliptical_reply: the reported clause is an elliptical reply (\"it is,\" ...) whose meaning depends on a question the sentence does not give")
    elif _DEMONSTRATIVE.match(clause) and not _concrete(clause, names):
        out.append("no_antecedent: the reported clause is about a bare 'it / that / this' that nothing in the sentence names")
    if _SAYING.fullmatch(verb) and not has_predicate(clause) and not any(o.startswith("filler_clause") for o in out):
        out.append("fragment: after the speech verb the clause has no verb, so it is not a proposition")
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ Phase 4A (E): who is "I" and who is "you"
# The support rules compare WORDS, so a paraphrase that swaps the two speakers keeps every word: "Your accountant and I should speak" (the advisor speaks, with Rebecca's accountant) became
# "Rebecca and her accountant should speak".  The rule is conservative and structural, not a ban on pronouns:
#   * a cited sentence that JOINS the speaker with someone ("your accountant and I", "we agreed", "both of us", "let's") may be paraphrased only if the reported clause keeps BOTH roles in plain words;
#   * a role may appear in the clause only if the cited wording licenses it: the advisor as an actor only where the line speaks in the first person (advisor speaker) or addresses "you" / names the
#     advisor (client speaker), and the client only where the line says "you / your" (advisor speaker) or speaks in the first person (client speaker).
_FIRST = re.compile(r"\b(?:i|me|my|mine|myself|we|us|our|ours|let's|i['’](?:m|ve|d|ll)|we['’](?:re|ve|d|ll))\b", re.I)
_SECOND = re.compile(r"\b(?:you|your|yours|yourself|you['’](?:re|ve|d|ll))\b", re.I)
_JOINT = re.compile(r"^\W*(?:(?:your|my|the|our|her|his)\s+)?[\w'’-]+(?:\s+[\w'’-]+)?\s+and\s+(?:i|me)\b|\byou\s+and\s+(?:i|me)\b|\b(?:i|me)\s+and\s+(?:you|your)\b|\b(?:both|each|all|two) of (?:us|you)\b|"
                    r"\bwe\b|\bus\b|\bour\b|\blet['’]?s\b|\btogether\b", re.I)
_ADVISOR_REF = re.compile(r"\bthe advisor\b|\badvisor['’]s\b", re.I)


def _client_named(clause: str, names=()) -> bool:
    """The client by name or as 'the client' (a pronoun does not name a role: 'he' may be the advisor)."""
    nm = "|".join(re.escape(n) for n in names)
    return bool(re.search(rf"\b(?:the client{('|' + nm) if nm else ''})\b", clause, re.I)) if clause else False


def role_problems(text: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """The roles of 'I', 'we', 'you' and 'your' in the cited line, kept in the paraphrase (see the section comment).  Empty = the roles are preserved or the line has no first / second person.  Only the
    cited sentences the paraphrase actually covers (two or more content words in common) are examined for a joint role: another sentence of the same turn ("Let me say it back to you so we don't
    misremember it.") does not bind a paraphrase of the sentence beside it."""
    clause, _ = reported_clause(text, names)
    if not clause or not cited:
        return []
    out: list[str] = []
    wording = " ".join(c.wording for c in cited)
    advisor_speaks = all(c.speaker == "Advisor" for c in cited)
    client_speaks = all(c.speaker == "Client" for c in cited)
    from agents.report.status import content_roots
    mine = content_roots(clause)
    covered = [x for x in (S.split_sentences(wording) or [wording]) if len(content_roots(x) & mine) >= 2]
    if not (advisor_speaks or client_speaks):
        # the speaker is not established as the advisor or the client (several speakers, or none): "I" and "you" cannot be mapped to a role, so a paraphrase of them cannot preserve one
        if any(_FIRST.search(x) or _SECOND.search(x) for x in covered):
            return ["role_unknown: the cited wording uses 'I', 'we', 'you' or 'your', but its speaker is not established as the advisor or the client, so who is meant cannot be preserved in a paraphrase"]
        return []
    has_first, has_second = bool(_FIRST.search(wording)), bool(_SECOND.search(wording))
    adv_in, cli_in = bool(_ADVISOR_REF.search(clause)), _client_named(clause, names)
    if any(_JOINT.search(x) and _FIRST.search(x) for x in covered):
        cli_pron = bool(re.search(r"\b(?:she|he|her|his|they|their)\b", clause, re.I))
        speaker_kept, other_kept = (adv_in, cli_in) if advisor_speaks else (cli_in or cli_pron, adv_in)
        if not (speaker_kept and other_kept):
            return ["role_swap: the cited line joins the speaker with someone ('your accountant and I', 'we', 'both of us'); the sentence must keep both the advisor and the client in plain words, or the roles cannot be preserved"]
    if advisor_speaks:
        if adv_in and not (has_first or re.search(r"\badvisor\b", wording, re.I)):
            out.append("role_swap: the sentence gives the advisor a part, but the cited line never speaks in the first person or names the advisor")
        if cli_in and not (has_second or any(n.lower() in wording.lower() for n in names) or re.search(r"\bclient\b", wording, re.I)):
            out.append("role_swap: the sentence gives the client a part, but the cited line never says 'you / your' or names the client")
    else:
        if cli_in and not (has_first or any(n.lower() in wording.lower() for n in names)):
            out.append("role_swap: the sentence gives the client a part, but the cited line never speaks in the first person")
        if adv_in and not (has_second or re.search(r"\badvisor\b", wording, re.I)):
            out.append("role_swap: the sentence gives the advisor a part, but the cited line never says 'you / your' or names the advisor")
    return out


# A promise is not a deed.  "From my side, I'll arrange a call with your accountant" (INT-070, turn 11) was reported as "the advisor arranged a call with the client's accountant": every word is supported, but a
# commitment became something done.  When the cited wording PROMISES an action ("I'll ...", "you'll ...", "I'm going to ...") and does not also state it as done, the paraphrase may not put that action in the
# past tense unless it keeps the promise ("said he would arrange", "agreed to send", "planned to").
_PROMISE = re.compile(r"\b(?:i['’]ll|i will|we['’]ll|we will|you['’]ll|you will|i['’]m going to|i am going to|we['’]re going to|you['’]re going to)\s+(?:also\s+|then\s+|just\s+|first\s+)?([a-z]{3,})\b", re.I)
# verbs whose SAYING is the act ("I'll record that as your plan" records it as it is said; "I'll outline ..." outlines): reporting them in the past tense is not a promise turned into a deed
_PERFORMATIVE = frozenset("record note say tell mention outline summarise summarize recap repeat explain state log put".split())
_KEEPS_PROMISE = re.compile(r"\b(?:would|will|going to|agreed to|planned to|promised|offered to|intended|intends|undertook|committed to|expected to|was to|were to|is to|are to)\b", re.I)


def tense_problems(text: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """A promised action reported as a completed one (see the comment above).  Empty = no promised action is turned into a past one."""
    opening = _LEAD_DATE.sub("", " ".join(S.MARK.sub("", text).split())).strip()
    found = _subject_rx(names).match(opening)
    if not found or not cited:
        return []
    clause = opening[found.end():]                                        # everything the sentence says after its speaker ("arranged a call ... and reminded the client to send ...")
    wording = " ".join(c.wording for c in cited)
    promised = {_root(m.group(1)): m.group(1).lower() for m in _PROMISE.finditer(wording) if m.group(1).lower() not in _PERFORMATIVE}
    if not promised or _KEEPS_PROMISE.search(clause):
        return []
    said_done = {_root(w) for w in S._WORD.findall(wording) if w.lower().endswith("ed") or w.lower() in _IRREGULAR}
    for w in S._WORD.findall(clause):
        lw = w.lower()
        base = _IRREGULAR.get(lw)
        past = lw.endswith("ed") or base is not None
        if past and (_root(lw) in promised or (base is not None and _root(base) in promised)) and _root(lw) not in said_done:
            return [f"tense_shift: the cited wording only promises '{next((v for r, v in promised.items() if r == _root(lw) or (base and r == _root(base))), lw)}' (\"I'll ...\", \"you'll ...\"); the sentence reports it as done"]
    return []


def proposition_problems(text: str, cited: list[TimelineClaim], names=(), *, summary: bool = False) -> list[str]:
    """The problems of one paraphrased proposition against the claims it rests on.  Empty = every rule holds.  `summary` relaxes only what an executive-summary sentence legitimately does (rest on the
    claims of several propositions, open with a time phrase, name the client anywhere in the sentence)."""
    if not cited:
        return ["unknown_source: the sentence cites no claim of the authorised timeline"]
    if any(c.source != "source_turn" for c in cited):
        return ["unverified_source: a cited claim does not rest on a retrieved source turn"]
    if len(cited) > (MAX_CITED_SUMMARY if summary else MAX_CITED):
        return [f"too_many_sources: a proposition rests on at most {MAX_CITED_SUMMARY if summary else MAX_CITED} claims"]
    body = " ".join(S.MARK.sub("", text).split())
    if len(body.split()) < MIN_WORDS:
        return ["too_short: a proposition is a full sentence"]
    if len(body) > MAX_CHARS or len(S.split_sentences(body)) > 1:
        return ["not_one_sentence: a proposition is one sentence"]
    plain, out = date_problems(body, cited)
    rest = S.strip_quotes(plain)
    # --- attribution: who said it, in the sentence's own subject
    kinds = {c.statement_kind for c in cited}
    if summary:
        found = re.search(r"\b(?:the advisor|the client)\b", plain, re.I) or (re.search("|".join(re.escape(n) for n in names), plain) if names else None)
        advisor_subject = False
    else:
        opening = _LEAD_DATE.sub("", body).strip()                    # the sentence without its own date phrase
        found = _subject_rx(names).match(opening)
        advisor_subject = bool(found) and found.group("who").lower().startswith("the advisor")
        if found and not ATTRIB_VERBS.search(opening[found.end(): found.end() + 80]):
            out.append("attribution_verb_missing: the speaker is named but no speech verb (said, told, asked, noted, recorded, recommended ...) follows: the sentence reports a statement, it does not assert the fact")
    if not found:
        out.append("attribution_missing: the sentence does not name who said or recorded it (the client, by name, or the advisor) at its start")
    elif not summary:
        client_words = bool(re.search(r"\b(?:the client|" + "|".join(re.escape(n) for n in names) + r")\b", rest, re.I)) if names else "the client" in rest.lower()
        if advisor_subject and not kinds & {"advisor_reported", "advisor_interpretation"}:
            out.append("attribution_mismatch: the advisor is the subject, but no cited claim is the advisor's")
        if advisor_subject and "client_statement" in kinds and not client_words:
            out.append("attribution_mismatch: the sentence rests on a client statement but never says the client said it")
        if not advisor_subject and "client_statement" not in kinds:
            out.append("attribution_mismatch: the client is the subject, but no cited claim is a client statement")
        if not advisor_subject and kinds & {"advisor_reported", "advisor_interpretation"} and "advisor" not in rest.lower():
            out.append("attribution_mismatch: the sentence rests on an advisor's account or interpretation but never says the advisor said it")
    # --- everything the existing story rules already forbid: causes, inner states, decision language, wrong speaker, relations, supersession, figures, names, months, verbatim quotations
    wording_low = " ".join(c.wording.lower() for c in cited)
    for prob in S.story_problems(plain, cited, names):
        if prob.startswith("attribution_mismatch"):
            continue                                                       # replaced by the explicit subject rules above: the speaker opens the sentence and must fit the cited lines
        if prob.startswith("decision_language") and all(m.group(0).lower() in wording_low and ATTRIB_VERBS.search(rest[:m.start()]) for m in _OVERREACH.finditer(rest)):
            continue                                                       # "Rebecca said she had decided ...": reported speech, and the word is the cited line's own
        if prob.startswith("present_state_unsupported") and all(m.group(0).lower() in wording_low for m in S.PRESENT.finditer(rest)):
            continue                                                       # "she was not sure about selling now": the cited line itself says "now"
        if prob.startswith("temporal_unsupported: the sentence orders events") and all(m.group(0).lower() in wording_low for m in S.ORDER.finditer(rest)):
            continue                                                       # "before we begin": the cited line itself uses the word, it is not an invented sequence
        if prob.startswith("temporal_unsupported: '") and "asserts an outcome or a continuity" in prob and all(m.group(0).lower() in wording_low for m in S.OUTCOME.finditer(rest)):
            continue                                                       # "she was still weighing": the cited line itself says "still"
        if prob.startswith("inner_state") and all(_root(m.group(0)) in {_root(w) for w in S._WORD.findall(wording_low)} for m in S.INNER.finditer(S.strip_quotes(rest))):
            continue                                                       # "wanted" for a line that says "I want": the same word in another tense
        out.append(prob)
    # --- a question is reported as a question: "Do I need to time the sale?" is never "she said she needed to time the sale"
    if not summary and any(is_question(c) for c in cited) and not re.search(r"\b(?:asked|asks|asking|wondered|wondering|whether|if)\b", rest, re.I):
        out.append("question_as_statement: the cited line is a question; the sentence must report it as one (asked, wondered whether ...)")
    # --- the report speaks about the people in the lines: a copied "you" / "I" / "we" is a line pasted into the report, not a paraphrase
    pron = _PERSON.search(rest)
    if pron:
        out.append(f"person_shift: '{pron.group(0)}' is the line's own first or second person; a paraphrase is in the third person")
    # --- Phase 4A: the sentence must have a proposition of its own (C), and must keep who is "I" and who is "you" (E)
    out += informativeness_problems(body, cited, names)
    if not summary:
        out += role_problems(body, cited, names)
        out += tense_problems(body, cited, names)
    # --- certainty: a tentative client line stays tentative
    client_lines = [c for c in cited if c.statement_kind == "client_statement"]
    if client_lines and all(c.modality in ("tentative", "hypothetical") for c in client_lines) and not HEDGE_OK.search(rest) and not any(c.statement_kind != "client_statement" for c in cited):
        # the turn as a whole is tentative, but a firm fact inside it (an approach, a date) may be told firmly: the sentence is inflated only if it restates a HEDGED sentence of the line without its hedge
        content = lambda s: {_root(w) for w in S._WORD.findall(s) if len(w) >= 4 and w.lower() not in S.FUNCTION_WORDS and w.lower() not in REPORT_WORDS and w.lower() not in {n.lower() for n in names}}
        mine = content(S.strip_quotes(rest))
        for c in client_lines:
            for s in S.split_sentences(c.wording) or [c.wording]:
                if (S.HEDGE.search(s) or HEDGE_OK.search(s) or s.strip().endswith("?")) and mine & content(s):
                    out.append("certainty_inflated: the cited client line hedges what the sentence states without hedging")
                    break
            else:
                continue
            break
    # --- outcomes may not be firmer than the clause they come from, and must be in the cited wording at all
    wording = " ".join(c.wording for c in cited)
    for m in OUTCOME_W.finditer(rest):
        st = _root(m.group(0))
        clause = _clause_with(st, wording)
        if not clause:
            out.append(f"outcome_unsupported: '{m.group(0)}' is not in the cited wording")
            break
        if NEG_COND.search(clause) and not NEG_COND.search(rest) and not HEDGE_OK.search(rest):
            out.append(f"outcome_inflated: '{m.group(0)}' is hedged, negated or conditional in the source but not in the sentence")
            break
    # --- no unsupported content words (paraphrase of the cited words is open; new facts are not)
    stray = unsupported_words(plain, cited, names)
    if len(stray) > MAX_STRAY:
        out.append(f"unsupported_assertion: '{', '.join(stray)[:80]}' is not in the cited wording")
    # --- sentences that join claims of different dates carry a time marker
    if len({c.event_date for c in cited}) > 1 and not (S.ORDER.search(rest) or re.search(rf"\b(?:{_MON_RX})\b", plain, re.I) or re.search(r"\b(?:by|since|over|during|from)\b", rest, re.I)):
        out.append("cross_date_unmarked: the sentence joins claims of different dates without saying when each happened")
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ one sentence per source line
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_LEAD_DATE = re.compile(rf"^\W*(?:on|in|by)\s+(?:(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MON_RX}))|(?:(?:{_MON_RX})\s+\d{{1,2}}(?:st|nd|rd|th)?)|\d{{4}}-\d{{2}}-\d{{2}}|(?:{_MON_RX}))(?:,?\s+\d{{4}})?\s*,?\s*", re.I)


def is_question(c: TimelineClaim) -> bool:
    """A line that is a question (its last sentence ends with '?' and it has at most two sentences): marked QUESTION for the model, so it is reported as what was asked."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", c.wording.strip()) if p]
    return bool(parts) and parts[-1].endswith("?") and len(parts) <= 2


def date_phrase(c: TimelineClaim) -> str:
    d = c.event_date or ""
    return f"On {int(d[8:10])} {_MONTH_NAMES[int(d[5:7])]}, " if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else ""


def prepare_sentence(c: TimelineClaim, sentence: str, names=(), dated: bool = True) -> str:
    """The model's sentence for one line, made ready to be judged: claim ids and a leading date of its own are removed (the software writes the date), a leading she / he / they becomes the client's name
    when the line IS a client statement (a pronoun cannot say who spoke), and the date of the claim is put in front of the first sentence of each meeting."""
    s = " ".join(S.MARK.sub("", sentence).split()).strip()
    s = _LEAD_DATE.sub("", s).strip()
    if names and c.statement_kind == "client_statement":
        s = re.sub(r"^(?:she|he|they)\b", names[0], s, flags=re.I)
    s = s[:1].upper() + s[1:] if s else s
    lead = date_phrase(c) if dated else ""
    if lead:
        s = lead + (s[0].lower() + s[1:] if s.lower().startswith("the ") else s)
    return s if s.endswith((".", "?", "!")) else s + "."


def build_lines(lines, claims: list[TimelineClaim], names=(), *, window=None, context=(), narrated: Optional[set] = None, seen_texts: Optional[set] = None, after=None,
                accepted: Optional[list] = None, risky: Optional[set] = None, kept: Optional[list] = None) -> tuple[list[Finding], list[dict]]:
    """(findings, rejected) for one window.  `lines`: the model's {claim, sentence}.  Code orders them by the record, prepares each sentence (the date, the client's name for a pronoun), judges it
    against its ONE claim with `proposition_problems` (speaker, certainty, support, causes, decisions, outcomes, figures, names, dates, verbatim quotations), and groups the survivors into one finding
    per meeting (at most five sentences), origin "synthesis_checked".  Rejection records hold codes and claim ids, never text.  `kept` collects what was accepted ((claim, sentences, risky) tuples) so
    that a later repair pass for the same window can be merged in claim order (findings_from_kept)."""
    by_id = {c.claim_id: c for c in claims}
    win, ctx = (set(window) if window is not None else None), set(context or ())
    rejected: list[dict] = []
    ordered = []
    for li, ln in enumerate(lines or []):
        cid = ln.claim.strip()
        c = by_id.get(cid)
        if c is None:
            rejected.append({"line": li, "problems": ["unknown_claim"], "claims": [cid]})
        elif win is not None and cid not in win:
            rejected.append({"line": li, "problems": ["outside_window" if cid not in ctx else "context_only"], "claims": [cid]})
        else:
            ordered.append((S._order_key(c), li, c, ln.sentence))
    ordered.sort(key=lambda x: (x[0], x[1]))
    mine: list[tuple] = []
    last = after
    seen_claims: set[str] = set()
    for key, li, c, raw in ordered:
        cid = c.claim_id
        problems: list[str] = []
        if cid in seen_claims or (narrated is not None and cid in narrated):
            problems = ["duplicate_claim: this claim is already narrated"]
        elif last is not None and key < last:
            problems = ["chronology_reversed: the sentence rests on an earlier claim than the one before it"]
        dated = S.split_sentences(prepare_sentence(c, raw, names, dated=True)) or []
        plain = S.split_sentences(prepare_sentence(c, raw, names, dated=False)) or []
        if len(plain) != len(dated):
            plain = dated
        if not problems and not dated:
            problems = ["too_short: empty sentence"]
        if not problems and len(dated) > 2:
            problems = ["not_one_sentence: one sentence per line"]
        good: list[tuple[str, str]] = []
        if not problems:
            for d, p in zip(dated, plain):
                got = proposition_problems(d, [c], names)               # judged WITH its date, the form in which it may be shown
                if not got and seen_texts is not None and p.lower() in seen_texts:
                    got = ["duplicate_sentence: this sentence was already written"]
                if got:
                    problems += got
                else:
                    good.append((d, p))
        if problems:
            rejected.append({"line": li, "problems": list(dict.fromkeys(x.split(":")[0] for x in problems)), "claims": [cid]})
        if not good:
            continue
        last = key
        seen_claims.add(cid)
        if narrated is not None:
            narrated.add(cid)
        if seen_texts is not None:
            seen_texts.update(p.lower() for _, p in good)
        if accepted is not None:
            accepted.append({"claims": [cid], "primary": [cid]})
        mine.append((c, good, bool(risky) and bool(c.evidence_refs) and c.evidence_refs[0] in risky))
    if kept is not None:
        kept.extend(mine)
    out, bad = findings_from_kept(mine, claims, names)
    for b in bad:
        rejected.append(b)
        for cid in b["claims"]:                                          # what was counted as accepted is taken back
            if narrated is not None:
                narrated.discard(cid)
            if accepted is not None and {"claims": [cid], "primary": [cid]} in accepted:
                accepted.remove({"claims": [cid], "primary": [cid]})
            if seen_texts is not None:
                for c, good, _ in mine:
                    if c.claim_id == cid:
                        seen_texts.difference_update(p.lower() for _, p in good)
    return out, rejected


def findings_from_kept(kept: list[tuple], claims: list[TimelineClaim], names=()) -> tuple[list[Finding], list[dict]]:
    """Group accepted lines (in the record's order) into findings: one per run of one meeting and one authority class, at most five lines.  The first sentence of a run carries its date; the others do
    not.  Every finding is re-checked with `check_synthesis_finding`, the same check the Evidence Verifier runs; a finding that fails is dropped and its claims are returned in the second list."""
    kept = sorted(kept, key=lambda k: S._order_key(k[0]))
    groups: list[list] = []
    for k in kept:
        if groups and groups[-1][0][0].interaction_id == k[0].interaction_id and groups[-1][0][2] == k[2] and len(groups[-1]) < 5:
            groups[-1].append(k)
        else:
            groups.append([k])
    out: list[Finding] = []
    bad: list[dict] = []
    for g in groups:
        parts: list[tuple[str, TimelineClaim]] = []
        for i, (c, good, _) in enumerate(g):
            for j, (dated, plain) in enumerate(good):
                parts.append((dated if (i == 0 and j == 0) else plain, c))
        sents = [NarrativeSentence(text=t, sources=[S.source_key(c)]) for t, c in parts]
        f = Finding(text=" ".join(compose_synthesis(t, [c]) for t, c in parts), refs=list(dict.fromkeys(c.evidence_refs[0] for c, _, _ in g if c.evidence_refs)), origin="synthesis_checked", narrative=sents)
        why = check_synthesis_finding(f, claims, names)
        if why:
            bad.append({"line": None, "problems": [x.split(":")[0] for x in why[:3]], "claims": [c.claim_id for c, _, _ in g]})
        else:
            out.append(f)
    return out, bad


# ------------------------------------------------------------------------------------------------------------------------------------ composing and re-checking
def compose_synthesis(text: str, cited: list[TimelineClaim]) -> str:
    """The report text of one proposition: the model's words, then provenance WRITTEN BY CODE for each cited claim (meeting, turn, recorded speaker, evidence handle), exactly as for a quotation.  The
    interface shows the plain sentence and the evidence chips (`Finding.narrative`); this composed text is what the verifier's source-record rule reads."""
    return S.compose(text, cited)


def check_synthesis_finding(f: Finding, claims: list[TimelineClaim], names=()) -> list[str]:
    """Problems with a synthesised finding judged against `claims`, the timeline as RECONSTRUCTED by the caller from authorised, source-verified evidence (the Report Agent's own is not trusted).  A
    finding marked window -1 is an executive summary and may rest on the claims of several propositions."""
    sents = f.narrative or []
    if not sents:
        return ["unknown_source: the finding records no sentences to check"]
    index: dict = {}
    for c in claims:
        if c.source == "source_turn":
            index.setdefault(S.source_key(c), c)
    out: list[str] = []
    texts: list[str] = []
    refs: list[str] = []
    for s in sents:
        cited, missing = [], []
        for k in s.sources:
            (cited if k in index else missing).append(index[k] if k in index else k)
        if missing:
            out.append(f"unknown_source: {missing[0]} is not a source turn of the authorised timeline")
            continue
        out += proposition_problems(s.text, cited, names, summary=f.window == -1)
        texts.append(compose_synthesis(s.text, cited))
        refs += [c.evidence_refs[0] for c in cited if c.evidence_refs and c.evidence_refs[0] not in refs]
    if not out and (" ".join(texts) != f.text or refs != list(f.refs)):
        out.append("unsupported_clause: the finding's text or references differ from what its sentences and their source turns compose to")
    return out


def build_executive(sentences, props: dict, claims: list[TimelineClaim], names=()) -> tuple[Optional[Finding], list[dict]]:
    """The executive summary: each sentence names the accepted propositions (P1, P2 ...) it summarises; its sources are the claims those propositions rest on.  A sentence is judged exactly like a
    proposition (summary mode).  Returns (one finding, marked window -1) or None, and the rejection records."""
    by_id = {c.claim_id: c for c in claims}
    sents: list[tuple[str, NarrativeSentence, list[TimelineClaim]]] = []
    rejected: list[dict] = []
    last = None
    for si, s in enumerate(sentences or []):
        pids = list(dict.fromkeys(p.strip() for p in s.props))
        if any(p not in props for p in pids):
            rejected.append({"sentence": si, "problems": ["unknown_proposition"], "claims": []})
            continue
        ids = list(dict.fromkeys(i for p in pids for i in props[p]["claims"]))
        cited = sorted((by_id[i] for i in ids if i in by_id), key=S._order_key)
        text = " ".join(re.sub(r"\[?\bP\d+\b\]?", "", s.text).split())
        problems = proposition_problems(text, cited, names, summary=True)
        if not problems and last is not None and min(S._order_key(c) for c in cited) < last:
            problems = ["chronology_reversed: the summary must run in date order"]
        if problems:
            rejected.append({"sentence": si, "problems": [x.split(":")[0] for x in problems], "claims": ids})
            continue
        last = max(S._order_key(c) for c in cited)
        sents.append((compose_synthesis(text, cited), NarrativeSentence(text=text, sources=[S.source_key(c) for c in cited]), cited))
    if not sents:
        return None, rejected
    refs = list(dict.fromkeys(c.evidence_refs[0] for _, _, cs in sents for c in cs if c.evidence_refs))
    f = Finding(text=" ".join(t for t, _, _ in sents), refs=refs, origin="synthesis_checked", narrative=[n for _, n, _ in sents], window=-1)
    why = check_synthesis_finding(f, claims, names)
    if why:
        return None, rejected + [{"sentence": None, "problems": [x.split(":")[0] for x in why[:3]], "claims": []}]
    return f, rejected


# ------------------------------------------------------------------------------------------------------------------------------------ prompts
SYNTHESIS_SYSTEM = """You help write a client history report for a financial advisor. You are given SOURCE LINES: dated statements from real conversations. Each line [C#] says who spoke (the client's own words, or the advisor's account or interpretation) and how firm it was.

For EACH line that matters to the history (skip small talk and logistics), write what that line says as ONE plain sentence in your own words. Return JSON: {"lines": [{"claim": "C4", "sentence": "..."}]}.

HOW TO WRITE A SENTENCE
- Open with who spoke: the client's name for the client's own words ("Rebecca ..."), "The advisor ..." for the advisor's lines. Never open with "She" or "He". Do not write a date: the report software adds it.
- Past tense, plain and professional, for example: "Rebecca said that a private equity group called Lakeshore Partners had contacted her about acquiring Holloway Precision Packaging." / "The advisor asked for three years of financial statements and any valuation work." / "Rebecca asked whether she should accept the offer."
- Say only what the line says, with its own facts, names and numbers. Put nothing in quotation marks.
- Keep it exactly as firm as the line. If the line is tentative ("curious", "inclined", "weighing", "not sure", "not yet", "haven't decided"), the sentence says so in those terms. Never write "decided", "agreed", "accepted", "confirmed" or "signed" unless the line itself says it, and then in the line's own terms ("Rebecca said she had decided to sell ...").
- Never add a reason, cause, motive or feeling the line does not state: no "because", "so that", "worried", "wanted", "felt", "desire", "primary reason". Never add an outcome or a next step the line does not state.
- Third person only: write "she", "her", "the advisor", never "you", "your", "I" or "we" (the lines are spoken to and by people; you are reporting them).
- Skip a line that cannot be understood without the lines around it (it says "it", "that" or "this" without saying what): do not guess what it refers to. Skip a very short reply ("Go ahead.", "Fine.") rather than turn it into a decision. Skip an agenda reply such as "Fine. Net proceeds and the people side of a sale first." Start every sentence with the speaker, never with "fine", "yes", "no" or "it is".
- Keep who is who. "I", "my" and "we" are the person speaking; "you" and "your" are the person spoken to. When a line joins them ("Your accountant and I should speak", "we agreed"), the sentence names both ("The advisor said that the advisor and Rebecca's accountant should speak").
- A line marked as correcting an earlier record gets one sentence that says what the earlier record had said and what this line says instead, e.g. "The advisor corrected his earlier note, which had said that Rebecca had accepted the offer, to say that she was inclined to accept but had not signed."
- One sentence per line, one line per id, under 45 words."""


SYNTHESIS_RETRY = ("\n\nMOST OF YOUR PREVIOUS SENTENCES WERE REJECTED by the checks. Write again, shorter and plainer: each sentence opens with the speaker (\"Rebecca ...\" or \"The advisor ...\") and restates only "
                   "the words and facts of its own line, with no reasons, feelings, decisions or outcomes the line does not state, no quotation marks and no date.")

SUMMARY_SYSTEM = """You write the executive summary of a client history report for a financial advisor, from a list of already-checked PROPOSITIONS (P1, P2, ...), oldest first.

Return JSON: {"sentences": [{"text": "...", "props": ["P1", "P3"]}]}. Write 3 to 6 sentences that tell the client's overall history in order: what first happened, how the client's position developed, what was corrected, and where the record leaves things. Every sentence lists in "props" the proposition ids it summarises.

RULES
- Use only what the propositions say, in the same strength: tentative stays tentative, an advisor's account stays the advisor's, and nothing is described as decided, signed, agreed or completed unless a proposition says so in those terms.
- Name who said or recorded each thing: the client by name, or the advisor. No quotation marks. No reasons, motives or feelings. No dates other than those in the propositions.
- Oldest first. One sentence each."""


def synthesis_user_window(request_text: str, block: str, context_block: str, index: int, total: int, first: Optional[str], last: Optional[str], names=(), retry: bool = False) -> str:
    who = f" The client is {names[0]}." if names else ""
    ctx = f"\n\nRELATED LINES OF OTHER PARTS (for understanding only; do not write sentences for them):\n{context_block}" if context_block else ""
    return (f"REQUEST: {request_text}{who}\n\nThis is part {index + 1} of {total} of the history ({first or 'undated'} to {last or 'undated'}). Write sentences only for the SOURCE LINES below."
            f"\n\nSOURCE LINES (oldest first):\n{block}{ctx}\n\nWrite the sentences now." + (SYNTHESIS_RETRY if retry else ""))


_HINTS = (("decision_language", "do not write decided, agreed, accepted, confirmed, final or signed unless this line itself says it, and then say it as the line does"),
          ("outcome", "do not write signed, accepted, agreed, closed or completed unless this line says it, and keep it as hedged as the line"),
          ("unsupported", "use only words, names and numbers that appear in this line; add nothing else"),
          ("inner_state", "give no feeling, wish or intention that the line does not state"), ("causal", "give no reason or cause that the line does not state"),
          ("attribution", "open with the speaker and a speech verb: \"{client} said that ...\" for the client's line, \"The advisor said that ...\" for the advisor's line"),
          ("certainty", "keep it tentative, exactly as the line is"), ("relation", "do not say anything was corrected or replaced"), ("supersession", "do not say anything was corrected or replaced"),
          ("temporal", "do not use then, later, still, finally, now or other time words that the line does not use"), ("duplicate", "skip it"),
          ("role_swap", "keep who is speaking and who is spoken to: when the line says \"I\" or \"we\" together with someone, name both the advisor and the client"),
          ("filler", "start with the speaker and say what was said, never with fine, yes, no or it is"), ("elliptical", "start with the speaker and say what was said, never with fine, yes, no or it is"),
          ("no_antecedent", "say what \"it\" or \"that\" refers to, or skip the line"), ("fragment", "write a full statement with a verb, or skip the line"),
          ("empty_acknowledgement", "skip it: it is only an acknowledgement"),
          ("tense_shift", "a promise is not a deed: write \"said he would ...\" or \"agreed to ...\" for what the line only promises"), ("role_unknown", "skip the line: it is not clear who \"I\" and \"you\" are"))


def repair_hints(codes, names=()) -> str:
    """Plain-words notes for the lines whose sentences were rejected, from the rule codes (never from the rejected text)."""
    who = names[0] if names else "the client"
    notes = []
    for key, note in _HINTS:
        if any(c.startswith(key) for c in codes):
            notes.append(note.replace("{client}", who))
    return "; ".join(dict.fromkeys(notes)) or "restate only what this line says, in your own plain words"


def synthesis_user_repair(request_text: str, block: str, index: int, total: int, names=()) -> str:
    who = f" The client is {names[0]}." if names else ""
    return (f"REQUEST: {request_text}{who}\n\nThis is part {index + 1} of {total} of the history. Your sentences for the lines below were REJECTED by the checks. Write them again, one plain sentence per line, "
            f"following the NOTE under each line. Skip a line if you cannot say it using only what the line says.\n\nSOURCE LINES (oldest first):\n{block}\n\nWrite the sentences now.")


def summary_user(request_text: str, props: dict) -> str:
    lines = "\n".join(f"{pid} [{p['date']}] {p['text']}" for pid, p in props.items())
    return f"REQUEST: {request_text}\n\nPROPOSITIONS (oldest first):\n{lines}\n\nWrite the executive summary now."


def synthesis_line(c: TimelineClaim) -> str:
    """One source line for the model: id, date, where, who spoke, how firm, status, relations, and the wording (longer than the quotation prompt shows: a paraphrase needs the whole thought)."""
    rel = [{"corrects": "corrects", "corrected_by": "corrected by", "contradicts": "conflicts with"}[r["type"]] + " " + r["claim_id"] for r in c.relations]
    w = " ".join(c.wording.replace('"', "'").split())
    w = w if len(w) <= 340 else w[:337] + "..."
    q = " QUESTION," if is_question(c) else ""
    fix = " [THIS LINE CORRECTS AN EARLIER RECORD: in one sentence say what the earlier record had said and what this line says instead]" if any(r["type"] == "corrects" for r in c.relations) or c.modality == "correction" else ""
    return (f"[{c.claim_id}] {c.event_date or 'undated'}, {_where(c)}, {S.TAG[c.statement_kind]}, {c.modality},{q} status {c.temporal_status}" + (f" ({'; '.join(rel)})" if rel else "") + f"{fix}: \"{w}\"")


# ------------------------------------------------------------------------------------------------------------------------------------ the outcome, written by code
_SIGN = re.compile(r"\b(?:sign(?:ed|ing|s|ature|atures|atory)?\b|complet\w+|clos(?:e|ed|es|ing)\b|finali[sz]\w+|exchang\w+|purchase agreement)", re.I)


_EVENT = re.compile(r"\b(?:signed|completed|closed|exchanged|finali[sz]ed|executed)\b", re.I)


def outcome_lines(claims: list[TimelineClaim]) -> dict:
    """What the retrieved record does and does not establish about signing and completion, by scanning the source turns.  Every sentence that mentions signing, closing, completion or the purchase
    agreement is classified: `event` (a past-tense signing / completion / closing that nothing negates or conditions: it would establish the outcome), `hedged` (negated or conditional: "I haven't
    signed", "if the sale closes") or `pending` (a neutral or forward-looking mention: "for your signature", "the final purchase agreement draft").  Written by code from the cited wording; no model, no
    inference.  Returns {established, not_established, mentions}."""
    mentions = []
    for c in sorted((x for x in claims if x.source == "source_turn" and x.relevant), key=S._order_key):
        for part in re.split(r"(?<=[.!?])\s+", c.wording):
            if _SIGN.search(part):
                hedged = bool(NEG_COND.search(part)) or part.strip().endswith("?")                 # a question ("Have you signed anything?") establishes nothing
                kind = "hedged" if hedged else "event" if (_EVENT.search(part) and ST.is_event_sentence(part)) else "pending"        # "please get me the signed exclusivity agreement" is a request, not a signing
                mentions.append({"claim_id": c.claim_id, "date": c.event_date, "where": _where(c), "speaker": S.TAG[c.statement_kind], "excerpt": " ".join(part.split())[:220],
                                 "kind": kind, "ref": c.evidence_refs[0] if c.evidence_refs else None})
    events = [m for m in mentions if m["kind"] == "event"]
    dates = sorted({c.event_date for c in claims if c.source == "source_turn" and c.relevant and c.event_date})
    last = dates[-1] if dates else None
    upto = f"the record runs to {last or 'an unknown date'}"
    if not mentions:
        return {"established": [], "not_established": [f"No retrieved source turn mentions the letter of intent being signed or the sale completing ({upto})."], "mentions": []}
    if not events:
        return {"established": [], "not_established": [f"No retrieved source turn establishes that the letter of intent was signed or that the sale completed ({upto}). {len(mentions)} turn(s) mention signing, closing or the "
                                                         f"purchase agreement, every one of them negated, conditional, pending or still to come."], "mentions": mentions}
    return {"established": [], "not_established": [f"{len(events)} retrieved turn(s) record a signing, closing or completion in the past tense without hedging; read them before relying on any outcome ({upto})."], "mentions": mentions}


# ------------------------------------------------------------------------------------------------------------------------------------ readability: a quotation dump is not a report
def quote_share(text: str) -> float:
    """The share of the characters of `text` (handles removed) that sit inside quotation marks."""
    t = re.sub(r"\[E\d+\]", "", text)
    inside = sum(len(m) for m in S._QUOTE.findall(t))
    return inside / max(len(t), 1)


def readability(findings: list[Finding]) -> dict:
    """Measures that tell a coherent narrative from a quotation dump: how much of the text is quoted, how long the sentences are, and how many sentences are paraphrase that opens with its speaker."""
    sents = [s.text for f in findings for s in (f.narrative or [])] or [f.text for f in findings]
    n = len(sents)
    if not n:
        return {"sentences": 0, "quote_share": 0.0, "avg_words": 0.0, "paraphrase_share": 0.0, "attributed_share": 0.0}
    subject = re.compile(r"^\W*(?:(?:on|in|by|after|later|then|earlier|before|during|at|from|since|over)\b[^,]{0,40},\s*)*(?:the client|the advisor|[A-Z][a-z]+)\b")
    return {"sentences": n, "quote_share": round(sum(quote_share(s) * len(s) for s in sents) / max(sum(len(s) for s in sents), 1), 3), "avg_words": round(sum(len(s.split()) for s in sents) / n, 1),
            "paraphrase_share": round(sum(1 for s in sents if not S._QUOTE.search(s)) / n, 3), "attributed_share": round(sum(1 for s in sents if subject.search(s)) / n, 3)}


def is_quotation_dump(findings: list[Finding], max_quote_share: float = 0.4, min_paraphrase_share: float = 0.6) -> bool:
    r = readability(findings)
    return r["sentences"] == 0 or r["quote_share"] > max_quote_share or r["paraphrase_share"] < min_paraphrase_share
