"""B4: semantic validation of model-written chronology prose.  Pure code: no model, no store, no retrieval.

THE PROBLEM THIS SOLVES.  The report's verifier checks FIGURES (and, for model-written findings, clause-level support).  A supported value does not make an INTERPRETATION about that value supported:
"Kenji's retirement age has been clarified to 63, superseding previous exploratory and corrected plans" has a correct value and an unproven relationship ("superseding", "clarified to").  So every
sentence the model writes about the chronology is checked, here, against the timeline claims it says it rests on (B2B's TimelineClaim, written [C3] or [C3][C4] at the end of the sentence).

WHAT IS CHECKED, outside quotation marks (quoted words are the record itself and must be VERBATIM from a cited claim):
  causes and reasons      never ("because", "as a result", "led to", "prompted" ...): the record gives no reasons beyond the speakers' own quoted words;
  inner states            "wanted", "intended", "worried", "preferred" ... only when a cited claim's own wording uses the word;
  decision language       "decided", "confirmed", "agreed", "final", "current" ... only when hedged or negated (the report verifier applies the same vocabulary to anything resting on a source turn);
  attribution             "the client said" needs a cited client statement and no cited advisor account unless the sentence also names the advisor; "the advisor ..." needs a cited advisor claim;
  corrections             "corrected", "clarified", "revised" ... need a cited claim that B2B recorded as correcting or corrected; "superseded", "replaced", "no longer" ... need a cited claim that
                          B2B marked superseded or corrected; a cited corrected claim must be accompanied by its correction, a cited disputed claim by its conflict;
  time                    "later", "before", "then" ... need two cited claims at different points; "the same day" needs the same date; "now"/"currently" need a cited CURRENT claim;
                          "finally", "ultimately", "originally", "still" ... are never allowed (they assert an outcome or a continuity the timeline does not record);
  figures and dates       every figure and date must occur in a cited claim; certainty adverbs ("definitely", "firmly") are not allowed, "explicitly" only for the client's own words.
A sentence that fails is REMOVED and the reason is recorded; nothing is repaired or reworded, and nothing that fails ever reaches the report.  What survives is converted by code into a finding
whose provenance (meeting, turn, recorded speaker, evidence handle) is written by code, and then it still faces the unchanged EvidenceVerifier.

WHAT IT CANNOT DO.  It checks wording against recorded structure; it cannot tell whether a paraphrase is faithful.  A sentence that passes is not thereby true: the verifier decides that.  Accuracy
on real conversations is unmeasured.
"""
from __future__ import annotations

import re
from typing import Optional

from agents.report.timeline import TAG, _norm_val, _where, significant
from agents.report.validate import _OVERREACH
from schemas.report import Finding, NarrativeSentence, TimelineClaim

MARK = re.compile(r"\[C(\d+)\]")
_QUOTE = re.compile(r'"([^"]*)"')
CAUSAL = re.compile(r"\b(?:because|therefore|hence|thus|consequently|as a result|due to|owing to|led to|leading to|so that|in order to|which is why|that is why|prompted|triggered|motivated|"
                    r"the reason|reasons? for|why|explains?|explained by|caused|causing|stemm\w+ from|resulting? in|resulted in|in response to|in light of|given that)\b", re.I)
INNER = re.compile(r"\b(?:wanted|wants|wanting|intended|intends|intending|hoped|hopes|hoping|feared|worried|worries|preferred|prefers|chose|chooses|opted|felt|feels|believed|believes|realis(?:ed|es)|"
                   r"realiz(?:ed|es)|reluctant|keen|anxious|concerned|eager|aimed|aiming|expects|expected|desired|desires?|wish(?:es|ed)?|longed|motivat\w+|dreamed|planned|plans|planning|thought)\b", re.I)
HEDGE = re.compile(r"\b(?:not|no|never|neither|nor|without|undecided|unconfirmed|unresolved|tentative(?:ly)?|exploratory|possibility|possible|may|might|could|yet|unless|if|whether|only)\b|n't\b", re.I)
CORRECTION = re.compile(r"\b(?:corrected|corrects|correcting|correction|corrections|amended|amends|revised|revises|retracted|retracts|clarified|clarifies|clarifying|reversed|reverses|rectified|walked back)\b", re.I)
SUPERSESSION = re.compile(r"\b(?:superseded|supersedes|superseding|replaced|replaces|replacing|overrode|overrides|overriding|supplanted|displaced|in place of|instead of|no longer|rather than|made obsolete)\b", re.I)
ORDER = re.compile(r"\b(?:later|earlier|before|after|afterwards|subsequently|previously|then|following|thereafter|prior to|next)\b", re.I)
OUTCOME = re.compile(r"\b(?:finally|ultimately|eventually|initially|originally|at first|ever since|still|always|once and for all|in the end)\b", re.I)
PRESENT = re.compile(r"\b(?:now|currently|today|at present|presently|at the moment)\b", re.I)
SAME_DAY = re.compile(r"\b(?:the same day|same day|the same meeting|same meeting|that day)\b", re.I)
ADVERB = re.compile(r"\b(?:definitely|clearly|firmly|certainly|unambiguously|categorically|undoubtedly|obviously|explicitly)\b", re.I)
DISPUTE_WORD = re.compile(r"\b(?:conflict\w*|disput\w*|contradict\w*|unresolved|inconsistent)\b", re.I)
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_FIG = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
_SAID_CLIENT = r"(?:said|stated|told|explained|clarified|confirmed|decided|agreed|indicated|replied|wrote|mentioned|expressed|announced|described|asserted|insisted|requested|asked|noted|recorded)"
_SAID_ADVISOR = r"(?:said|stated|recorded|noted|reported|wrote|told|summari[sz]ed|confirmed|understood|acknowledged|suggested|recommended|explained|described|advised)"


def client_names(request_text: str) -> list[str]:
    """First names the REQUEST uses for the client ("Kenji's retirement age" -> ["Kenji"]); the model may name the client the same way."""
    return sorted(set(re.findall(r"\b([A-Z][a-z]{2,})'s\b", request_text or "")))


def strip_quotes(s: str) -> str:
    return _QUOTE.sub('""', s)


def split_sentences(text: str) -> list[str]:
    """Sentences of a paragraph, quotation-aware (a full stop inside quotation marks does not end a sentence)."""
    out, cur, quoted, i = [], [], False, 0
    text = " ".join((text or "").split())
    while i < len(text):
        ch = text[i]
        cur.append(ch)
        if ch == '"':
            quoted = not quoted
        if not quoted and ch in ".!?":
            j = i + 1
            while j < len(text) and text[j] in "])\"":
                cur.append(text[j])
                j += 1
            if j >= len(text) or text[j] == " ":
                out.append("".join(cur).strip())
                cur = []
                i = j
                continue
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _digits(*texts) -> set[str]:
    return {m for t in texts for m in _FIG.findall(t or "")}


def _kind_set(cited: list[TimelineClaim]) -> set[str]:
    return {c.statement_kind for c in cited}


# ------------------------------------------------------------------------------------------------------------------------------------ one sentence
def check_sentence(sentence: str, cited: list[TimelineClaim], by_id: dict, names=(), summary_mode: bool = False, claims=()) -> list[str]:
    """The problems of one sentence against the claims it cites (summary_mode: a summary sentence cites nothing, so only claim-independent rules apply and interpretive words are not allowed at all).
    Returns problem codes with a short reason; an empty list means no rule is broken."""
    bare = strip_quotes(MARK.sub("", sentence))
    low = bare.lower()
    out: list[str] = []
    pool = list(claims) if summary_mode else cited
    wording = " ".join(c.wording.lower() for c in pool)
    if not summary_mode and not cited:
        return ["no_claim_marker: the sentence names no timeline claim it rests on"]
    # --- causes, reasons, inner states
    m = CAUSAL.search(bare)
    if m:
        out.append(f"causal_explanation: '{m.group(0)}' supplies a reason the record does not state")
    for m in INNER.finditer(bare):
        if summary_mode or m.group(0).lower() not in wording:
            out.append(f"inner_state: '{m.group(0)}' states an intention or feeling the cited wording does not")
            break
    # --- decision / confirmation language (the report verifier applies the same vocabulary to anything resting on a source turn)
    m = _OVERREACH.search(bare)
    if m and not HEDGE.search(bare):
        out.append(f"decision_language: '{m.group(0)}' presents a statement as decided, agreed, confirmed or current")
    # --- attribution
    nm = "|".join(re.escape(n) for n in names)
    client_subject = re.search(rf"\b(?:the client|client's{('|' + nm) if nm else ''})\b[^.;:]{{0,40}}?\b{_SAID_CLIENT}\b", bare, re.I)
    advisor_subject = re.search(rf"\b(?:the advisor|advisor's)\b[^.;:]{{0,40}}?\b{_SAID_ADVISOR}\b", bare, re.I)
    if not summary_mode:
        kinds = _kind_set(cited)
        if client_subject and not advisor_subject and "client_statement" not in kinds:
            out.append("attribution_mismatch: the sentence attributes the words to the client, but no cited claim is a client statement")
        if client_subject and not advisor_subject and "advisor" not in low and kinds & {"advisor_reported", "advisor_interpretation"}:
            out.append("attribution_mismatch: an advisor's account or interpretation is cited in a sentence that says the client said it")
        if advisor_subject and not client_subject and not re.search(rf"\b(?:client{('|' + nm) if nm else ''})\b", bare, re.I) and not kinds & {"advisor_reported", "advisor_interpretation"}:
            out.append("attribution_mismatch: the sentence attributes the words to the advisor, but no cited claim is the advisor's")
    # --- corrections and supersession, only where B2B recorded them
    related = any(r["type"] in ("corrects", "corrected_by") for c in cited for r in c.relations)
    if CORRECTION.search(bare) and (summary_mode or not (related or any("correction" in c.cues for c in cited))):
        out.append("relation_unsupported: the sentence speaks of a correction that no cited claim records")
    if SUPERSESSION.search(bare) and (summary_mode or not any(c.temporal_status in ("superseded", "corrected") for c in cited)):
        out.append("supersession_unsupported: the sentence says one position replaced another, but no cited claim is recorded as superseded or corrected")
    if not summary_mode:
        for c in cited:
            if c.temporal_status == "corrected":
                correctors = {r["claim_id"] for r in c.relations if r["type"] == "corrected_by"}
                if correctors and not correctors & {x.claim_id for x in cited}:
                    out.append(f"correction_omitted: {c.claim_id} was corrected, but the sentence does not cite its correction")
            if c.temporal_status == "disputed" and not DISPUTE_WORD.search(bare):
                out.append(f"dispute_omitted: {c.claim_id} is in an unresolved conflict, but the sentence does not say so")
    # --- time
    if OUTCOME.search(bare):
        out.append(f"temporal_unsupported: '{OUTCOME.search(bare).group(0)}' asserts an outcome or a continuity the timeline does not record")
    if PRESENT.search(bare) and (summary_mode or not any(c.temporal_status == "current" for c in cited)):
        out.append("present_state_unsupported: the sentence speaks of the present position, but no cited claim is current")
    if ORDER.search(bare) and (summary_mode or len({(c.event_date, c.interaction_id, c.turn_index) for c in cited}) < 2):
        out.append("temporal_unsupported: the sentence orders events in time, but fewer than two distinct cited claims support an order")
    if SAME_DAY.search(bare) and (summary_mode or len(cited) < 2 or len({c.event_date for c in cited}) != 1):
        out.append("temporal_unsupported: 'the same day' needs at least two cited claims with one date")
    # --- certainty adverbs
    for m in ADVERB.finditer(bare):
        if m.group(0).lower() == "explicitly" and not summary_mode and cited and all(c.statement_kind == "client_statement" for c in cited):
            continue
        out.append(f"certainty_adverb: '{m.group(0)}' strengthens a statement beyond the record")
        break
    # --- dates, figures, quotations: for a NARRATIVE sentence they must come from the claims it cites.  A summary sentence cites nothing, and figures and dates there are the job of the existing
    #     checks (NumberChecker, the harness EvidenceVerifier): this validator is about MEANING, and does not pre-empt them.
    if not summary_mode:
        allowed_dates = {c.event_date for c in pool if c.event_date}
        for d in _DATE.findall(bare):
            if d not in allowed_dates:
                out.append(f"unsupported_date: {d} is not the date of a cited claim")
        no_dates = _DATE.sub(" ", bare)
        allowed_figs = _digits(*[c.wording for c in pool], *[(c.value or "") if c.verification == "verified" else "" for c in pool], *[str(c.turn_index) for c in pool], *[c.interaction_id or "" for c in pool])
        for f in _FIG.findall(no_dates):
            if f not in allowed_figs and f not in ("1", "2"):
                out.append(f"unsupported_figure: {f} does not occur in a cited claim")
                break
        # --- quotations are the original words
        for q in _QUOTE.findall(MARK.sub("", sentence)):
            qq = " ".join(q.replace("...", " ").split()).lower().strip(" .,;:")
            if len(qq) >= 6 and qq not in " ".join(wording.split()):
                out.append("quote_not_verbatim: a quotation does not occur in the cited wording")
                break
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ closure of the model's own words
FUNCTION_WORDS = frozenset("""a an the and or but of to in on at by for with from as is are was were be been being that this these those it its he she they them his her their which who whom whose
said says stated states recorded records noted notes wrote writes replied asked told reported described mentioned added answered acknowledged understood suggested recommended advised
client advisor speaker turn turns int meeting conversation record account wording words note file notes
then later earlier before after same day also both while when where than so per according it's""".split())
_STATUS_WORDS = {"tentative": ("tentative", "tentatively", "exploratory", "exploring"), "correction": ("corrected", "correction", "corrects", "correcting", "corrected"),
                 "dispute": ("conflict", "conflicting", "conflicts", "disputed", "unresolved", "contradicts", "contradicted"), "unverified": ("unverified", "unconfirmed")}
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _stem(w: str) -> str:
    w = w.lower()
    if w.endswith("'s"):
        w = w[:-2]
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def closure_problems(sentence: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """A narrative sentence may contain, outside quotation marks, only: function words and reporting verbs, the client's name, words of the cited wording itself, and status words that a cited claim's
    recorded state licenses (tentative, corrected, conflicting, unverified).  Any other word is an unrecorded assertion ("agreed", "wanted", "reluctantly" ...) and the sentence is refused.  This is the
    mechanical guard against invented explanations, decisions and relationships: it does not judge truth, it removes the room to make something up."""
    bare = _DATE.sub(" ", strip_quotes(MARK.sub("", sentence)))
    allowed = {_stem(w) for w in FUNCTION_WORDS} | {_stem(n) for n in names}
    for c in cited:
        allowed |= {_stem(w) for w in _WORD.findall(c.wording)}
        if c.modality in ("tentative", "hypothetical") or "not_final" in c.cues:
            allowed |= {_stem(w) for w in _STATUS_WORDS["tentative"]}
        if c.relations or c.temporal_status in ("corrected", "superseded"):
            allowed |= {_stem(w) for w in _STATUS_WORDS["correction"]}
        if c.temporal_status in ("disputed", "unresolved") or any(r["type"] == "contradicts" for r in c.relations):
            allowed |= {_stem(w) for w in _STATUS_WORDS["dispute"]}
        if c.verification == "unverified":
            allowed |= {_stem(w) for w in _STATUS_WORDS["unverified"]}
    stray = []
    for w in _WORD.findall(bare):
        if _stem(w) not in allowed and w.lower() not in ("s", "t"):
            stray.append(w)
    return [f"unrecorded_wording: '{', '.join(dict.fromkeys(stray))[:80]}' is not a function word, a word of the cited wording or a status the record licenses"] if stray else []


def quote_problems(sentence: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """Quote-led sentences: at least one quotation, every quotation verbatim from a cited claim, and the sentence's attribution must fit the claim that OWNS the quotation."""
    out = []
    qs = [" ".join(q.replace("...", " ").split()).lower().strip(" .,;:") for q in _QUOTE.findall(MARK.sub("", sentence))]
    qs = [q for q in qs if len(q) >= 6]
    if not qs:
        return ["no_quotation: a narrative sentence must carry at least one verbatim quotation from a cited claim"]
    raw = MARK.sub("", sentence)
    nm = "|".join(re.escape(n) for n in names)
    speaker_rx = re.compile(rf"\b(the client|client's{('|' + nm) if nm else ''}|the advisor|advisor's)\b", re.I)
    for m in _QUOTE.finditer(raw):
        q = " ".join(m.group(1).replace("...", " ").split()).lower().strip(" .,;:")
        if len(q) < 6:
            continue
        owners = [c for c in cited if q in " ".join(c.wording.lower().split())]
        who = [x.group(1).lower() for x in speaker_rx.finditer(raw[: m.start()])]
        if not owners or not who:
            continue                                                            # a quotation that is not verbatim is reported by check_sentence; one with no named speaker by the closure rule
        said_by_client = not who[-1].startswith(("the advisor", "advisor"))      # the nearest speaker named before the quotation
        if said_by_client and not any(c.statement_kind == "client_statement" for c in owners):
            out.append("attribution_mismatch: the quotation is attributed to the client, but the cited claim that contains it is not a client statement")
        if not said_by_client and not any(c.statement_kind in ("advisor_reported", "advisor_interpretation") for c in owners):
            out.append("attribution_mismatch: the quotation is attributed to the advisor, but the cited claim that contains it is the client's")
    return out


def normalise_quotes(text: str, cited: list[TimelineClaim]) -> str:
    """Formatting repair only: a span the model wrapped in SINGLE quotes is re-delimited with double quotes when, and only when, it is a verbatim run of a cited claim's wording.  Nothing about
    the words changes, nothing is added, and a span that is not verbatim is left as it was (and so is not a quotation: the sentence then fails for want of one)."""
    wording = [" ".join(c.wording.lower().split()) for c in cited]
    if "'" not in text:
        return text
    out, i = [], 0
    while i < len(text):
        if text[i] == "'" and (i == 0 or text[i - 1] in " ([,;:"):
            best = None
            for j in range(len(text) - 1, i + 6, -1):
                if text[j] == "'" and (j == len(text) - 1 or text[j + 1] in " .,;:)]"):
                    span = " ".join(text[i + 1:j].lower().split()).strip(" .,;:")
                    if len(span) >= 6 and any(span in w for w in wording):
                        best = j
                        break
            if best is not None:
                out.append('"' + text[i + 1:best] + '"')
                i = best + 1
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def strip_claim_ids(text: str, ids) -> str:
    """Formatting repair only: the model sometimes copies a claim id into its sentence ("... said \"...\", C5." or "[C5]").  An id that is one of THIS sentence's own claim ids is metadata, not a word,
    and is removed; any other id stays in the text (and the sentence then fails for want of a recorded word)."""
    for i in ids:
        text = re.sub(rf"\s*[,;(\[]*\s*\b{re.escape(i)}\b\s*[)\]]*(?=[\s.,;]|$)", "", text)
    return re.sub(r"\s+([.,;])", r"\1", text).strip()


def judge_sentence(text: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """Every rule a narrative sentence must satisfy against the claims it cites (shared by the Report Agent and, on claims it reconstructs itself, the EvidenceVerifier)."""
    if not cited:
        return ["unknown_source: the sentence cites no claim of the authorised timeline"]
    if any(c.source != "source_turn" for c in cited):
        return ["unverified_source: a cited claim does not rest on a retrieved source turn"]
    cross = []
    if len({c.interaction_id for c in cited}) > 1:                       # "X said ..., and the advisor noted ..." joins two statements as one exchange: only turns of ONE meeting can be so joined
        cross = ["cross_meeting: the sentence joins claims from different meetings, which implies a connection the record does not state; each meeting needs its own sentence"]
    return cross + check_sentence(text, cited, {}, names, claims=cited) + closure_problems(text, cited, names) + quote_problems(text, cited, names)


# ------------------------------------------------------------------------------------------------------------------------------------ composing and re-checking
def source_key(c: TimelineClaim) -> str:
    return f"{c.interaction_id}#{c.turn_index}"


def compose(text: str, cited: list[TimelineClaim]) -> str:
    """The report text of one sentence: the model's words, then provenance WRITTEN BY CODE for each cited claim (meeting, turn, recorded speaker, evidence handle)."""
    body = " ".join(MARK.sub("", text).split()).rstrip()
    body = body[:-1].rstrip() if body[-1:] in ".!?" else body
    return body + "".join(f" ({_where(c)}, recorded speaker {c.speaker}) [{c.evidence_refs[0]}]" for c in cited) + "."


def _order_key(c: TimelineClaim):
    return (c.event_date or "", c.interaction_id or "", c.turn_index if c.turn_index is not None else -1)


def check_narrative_finding(f: Finding, claims: list[TimelineClaim], names=()) -> list[str]:
    """Problems with a narrative finding judged against `claims`: the timeline as RECONSTRUCTED by the caller from authorised, source-verified evidence.  The finding's own claim of what it rests on is
    only a pointer (a source turn "INT-049#4"); the claim at that turn, its wording, speaker, status and relations all come from `claims`.  An empty list means no rule is broken."""
    sents = f.narrative or []
    if not sents:
        return ["unknown_source: the finding records no sentences to check"]
    index: dict = {}
    for c in claims:
        if c.source == "source_turn":
            index.setdefault(source_key(c), c)
    out: list[str] = []
    texts: list[str] = []
    refs: list[str] = []
    last = None
    for s in sents:
        cited, missing = [], []
        for k in s.sources:
            (cited if k in index else missing).append(index[k] if k in index else k)
        if missing:
            out.append(f"unknown_source: {missing[0]} is not a source turn of the authorised timeline")
            continue
        probs = judge_sentence(s.text, cited, names)
        out += probs
        if cited:
            first = min(_order_key(c) for c in cited)
            if last is not None and first < last:
                out.append("chronology_reversed: the sentence rests on an earlier claim than the sentence before it")
            last = max(_order_key(c) for c in cited)
            texts.append(compose(s.text, cited))
            refs += [c.evidence_refs[0] for c in cited if c.evidence_refs[0] not in refs]
    if not out and (" ".join(texts) != f.text or refs != list(f.refs)):
        out.append("unsupported_clause: the finding's text or references differ from what its sentences and their source turns compose to")
    return out


def build_checked(paragraphs, claims: list[TimelineClaim], names=()) -> tuple[list[Finding], list[dict]]:
    """(findings, rejected).  `paragraphs`: the narrative call's paragraphs, each sentence {text, claims: ["C4", ...]}.  Each sentence is judged against the claims it cites; a failing sentence is
    removed (recorded with its problem codes, never with its text).  The surviving sentences of a paragraph become ONE finding, origin "narrative_checked", whose text and references code composes."""
    by_id = {c.claim_id: c for c in claims}
    out: list[Finding] = []
    rejected: list[dict] = []
    for pi, para in enumerate(paragraphs or []):
        kept, sents, last = [], [], None
        for si, s in enumerate(para.sentences):
            ids = list(dict.fromkeys(i.strip() for i in s.claims))
            cited = [by_id[i] for i in ids if i in by_id]
            if len(cited) != len(ids):
                rejected.append({"paragraph": pi, "sentence": si, "problems": ["unknown_claim"]})
                continue
            s_text = strip_claim_ids(normalise_quotes(s.text, cited), ids)
            problems = judge_sentence(s_text, cited, names)
            if not problems and last is not None and min(_order_key(c) for c in cited) < last:
                problems = ["chronology_reversed: the sentence rests on an earlier claim than the sentence before it; the narrative must run in date order"]
            if problems:
                rejected.append({"paragraph": pi, "sentence": si, "problems": [x.split(":")[0] for x in problems]})
                continue
            last = max(_order_key(c) for c in cited)
            kept.append(compose(s_text, cited))
            sents.append(NarrativeSentence(text=" ".join(s_text.split()), sources=[source_key(c) for c in cited]))
        if kept:
            refs = list(dict.fromkeys(c.evidence_refs[0] for sn in sents for c in claims if source_key(c) in sn.sources))
            f = Finding(text=" ".join(kept), refs=refs, origin="narrative_checked", narrative=sents)
            why = check_narrative_finding(f, claims, names)      # the same check the verifier runs, on the agent's own timeline: a paragraph that fails it is never offered
            if why:
                rejected.append({"paragraph": pi, "sentence": None, "problems": [x.split(":")[0] for x in why[:3]]})
            else:
                out.append(f)
    return out, rejected


# ------------------------------------------------------------------------------------------------------------------------------------ the readable story (model-written, NOT independently verified)
# The verified narrative is quote-led because only a verbatim quotation can be checked against its source.  The STORY is the model's plain-English account of how the position developed.  Code cannot
# check that a paraphrase means what the source says, so the story is never put through the EvidenceVerifier, never carries code provenance, and is a separate report field the UI labels as unverified.
# What code CAN do is remove what a story must not contain: causes, intentions, decisions, outcomes and relations the record does not state, the wrong speaker, and any figure, date, name or number-word
# that is not in the claims the sentence cites.  A sentence that fails is removed, never reworded.
_SMALL_NUMBERS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
_NUMBER_TOKENS = _SMALL_NUMBERS | {"eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
                                   "hundred", "thousand", "million", "billion", "percent"}
_NUMBER_WORDS = re.compile(r"\b(?:eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|percent|per cent)\b", re.I)
_CAP = re.compile(r"(?<=[a-z,;:)] )([A-Z][a-z]{2,})\b")
_MONTHS = {"january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06", "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12"}
_DOMAIN_PLANNING = re.compile(r"\b(?:tax|estate|financial|succession|exit|sale|retirement|formal|portfolio|goal|wealth)\s+planning\b|\bplanning\s+(?:date|horizon|process|session|meeting|purposes?|conversation)\b", re.I)
_SPEECH = re.compile(r"\b(?:said|says|told|stated|noted|mentioned|explained|wrote|replied|answered|asked|recorded|reported|added|described|raised|suggested)\b", re.I)


def story_problems(text: str, cited: list[TimelineClaim], names=()) -> list[str]:
    """Every rule a story sentence must satisfy against the claims it cites.  Quotations are optional here (the verified narrative carries them), but any quotation present must be verbatim."""
    if not cited:
        return ["unknown_source: the sentence cites no claim of the authorised timeline"]
    if any(c.source != "source_turn" for c in cited):
        return ["unverified_source: a cited claim does not rest on a retrieved source turn"]
    if len(text) > 420:
        return ["too_long: a story sentence is one plain sentence"]
    out = check_sentence(_DOMAIN_PLANNING.sub(" planning-topic ", text), cited, {}, names, claims=cited)     # "tax planning" is a topic, not an intention
    bare = strip_quotes(MARK.sub("", text))
    wording = " ".join(c.wording.lower() for c in cited)
    if any(p.startswith("decision_language") for p in out):
        words = {m.group(0).lower() for m in _OVERREACH.finditer(bare)}
        said = bool(_SPEECH.search(bare))                                         # "Rebecca said she had decided ..." attributes the decision to whoever said it
        own_decision = all(c.statement_kind == "client_statement" and c.modality == "decision" for c in cited)   # the client's own decision-worded line: telling it plainly is faithful to that line
        if (said or own_decision) and all(w in wording for w in words):
            out = [p for p in out if not p.startswith("decision_language")]
    wtoks = {re.sub(r"'s$", "", w.lower()) for w in re.findall(r"[A-Za-z']+", wording)}
    norm = " ".join(re.sub(r"[-\u2010-\u2015]", " ", wording).split())
    run: list[str] = []
    for w in re.sub(r"[-\u2010-\u2015]", " ", bare.lower()).split() + [""]:       # maximal runs of number words, e.g. "twenty four million", must occur as a phrase in the cited wording
        w = w.strip(".,;:!?()\"")
        if w in _NUMBER_TOKENS:
            run.append(w)
            continue
        if run and (len(run) > 1 or run[0] not in _SMALL_NUMBERS) and " ".join(run) not in norm:
            out.append(f"unsupported_figure: '{' '.join(run)}' is not in a cited claim")
            break
        run = []
    own = {n.lower() for n in names}
    months = {_MONTHS[k] for k in _MONTHS if k in bare.lower()}
    if months and not months <= {(c.event_date or "")[5:7] for c in cited}:
        out.append("unsupported_date: a month named in the sentence is not the month of a cited claim")
    for m in _CAP.finditer(bare):                                                # a capitalised name that is neither the client nor in the cited wording is an invented name
        w = re.sub(r"'s$", "", m.group(1).lower())
        if w not in own and w not in wtoks and w not in _MONTHS and w not in ("advisor", "the"):
            out.append("unsupported_name: a name in the sentence is not in the cited wording")
            break
    return out


def build_story(lines, claims: list[TimelineClaim], names=()) -> tuple[list[Finding], list[dict]]:
    """(paragraphs as findings, rejected).  `lines`: the model's story lines, each {claims: [ids], sentence}.  A line that fails story_problems is removed (recorded by code, never with its text);
    consecutive surviving lines of ONE meeting become a paragraph, each sentence followed by the evidence handle(s) of the claims it paraphrases (code-written).  The findings carry NO origin: they
    are model-written and unverified, and nothing downstream may treat them as anything else."""
    by_id = {c.claim_id: c for c in claims}
    out: list[Finding] = []
    rejected: list[dict] = []
    last = None
    group: list[tuple[str, list[str], str]] = []                     # (sentence with handles, handles, meeting)

    def flush():
        nonlocal group
        if group:
            out.append(Finding(text=" ".join(g[0] for g in group), refs=list(dict.fromkeys(h for g in group for h in g[1]))))
        group = []

    for li, ln in enumerate(lines or []):
        ids = list(dict.fromkeys(i.strip() for i in ln.claims))
        cited = [by_id[i] for i in ids if i in by_id]
        if len(cited) != len(ids):
            rejected.append({"line": li, "problems": ["unknown_claim"]})
            continue
        text = " ".join(strip_claim_ids(normalise_quotes(ln.sentence, cited), ids).split())
        if len(split_sentences(text)) > 1:                           # one plain sentence per line: a block of sentences cannot be traced to its claims
            rejected.append({"line": li, "problems": ["not_one_sentence"]})
            continue
        problems = story_problems(text, cited, names)
        if not problems and last is not None and min(_order_key(c) for c in cited) < last:
            problems = ["chronology_reversed: the story must run in date order"]
        if problems:
            rejected.append({"line": li, "problems": [x.split(":")[0] for x in problems]})
            continue
        last = max(_order_key(c) for c in cited)
        handles = [c.evidence_refs[0] for c in cited]
        meeting = cited[0].interaction_id or ""
        if group and (group[-1][2] != meeting or len(group) >= 5):
            flush()
        body = text if text[-1:] in ".!?" else text + "."
        group.append((body + "".join(f" [{h}]" for h in handles), handles, meeting))
    flush()
    return out, rejected


STORY_SYSTEM = """You turn a client's recorded chronology into plain English for the client's advisor. You are given TIMELINE lines. Each line [C#] is one dated statement from a real conversation turn: who said it (the client's own words, or the advisor's account or interpretation), how firm it was, its status and how it relates to other lines.

Return JSON: {"lines": [{"claims": ["C4"], "sentence": "..."}]}. Go through the timeline oldest first. For each line that matters to how the client's position developed (skip small talk and logistics), write ONE plain sentence that says what that line says. "claims" holds that line's id (two ids only if a line is a correction of the other).

HOW TO WRITE EACH SENTENCE: third person, past tense, one sentence, under 220 characters. Start with who spoke: the client's first name for the client's own words, "the advisor" for the advisor's. Say it in your own plain words, as a story would: for example "Rebecca said her accountant had sent three years of statements and a draft valuation." A short quotation is fine.

RULES (code checks every sentence and deletes any that breaks one):
- Say only what the line says. Every date, number, amount and name must come from that line. Write numbers as the line does.
- No reasons, causes, feelings, intentions, motives or consequences that the line does not state. Never write "because", "so that", "as a result", "wanted", "worried", "felt".
- Never write decided, agreed, confirmed, final or current unless the line has the client say it, and then attribute it: "Rebecca said she had decided ...".
- Keep tentative things tentative: "was considering", "raised the idea", "said she was weighing".
- The advisor's words are the advisor's: never present them as the client's. Never write that the advisor "confirmed" or "recommended" anything unless the line says so.
- Do not write finally, ultimately, initially, originally, still, now, always."""


def story_user(request_text: str, block: str) -> str:
    return f"REQUEST: {request_text}\n\nTIMELINE (oldest first):\n{block}\n\nWrite the story now."


# ------------------------------------------------------------------------------------------------------------------------------------ the model's summary
def filter_summary(summary: str, claims: list[TimelineClaim], names=(), held=frozenset()) -> tuple[str, list[str]]:
    """(kept summary, reasons for what was removed).  A summary sentence cites nothing, so it may not interpret: causes, intentions, decision language, relations, supersession, ordering, present-state
    words and uncited dates or figures are removed; so is a sentence that names a value nothing holds as current (agents.report.narrative.summary_conflict).  Descriptive sentences stay."""
    from agents.report.narrative import summary_conflict
    kept, reasons = [], []
    for s in split_sentences(summary):
        problems = check_sentence(s, [], {c.claim_id: c for c in claims}, names, summary_mode=True, claims=claims)
        why = summary_conflict(s, list(claims), held)
        if why:
            problems.append("value_conflict: " + why)
        if problems:
            reasons.append(problems[0].split(":")[0])
        else:
            kept.append(s)
    return " ".join(kept), reasons


# ------------------------------------------------------------------------------------------------------------------------------------ what the model is shown
def _priority(c: TimelineClaim) -> int:
    """How much a chronology about the plan's topic cannot do without this claim (lower = more).  0: related to another claim or not simply historical (memory structure).  1: a correction, a confirmed
    decision, or a topical decision.  2: a topical client statement.  3: any other topical claim.  4: not topical (a decision or hedge about something else)."""
    topical = bool(c.memory_refs or c.mentions_topic)
    if c.relations or c.temporal_status in ("current", "unresolved", "disputed", "corrected", "superseded"):
        return 0
    if "correction" in c.cues or c.confirmed_client_decision or (topical and c.modality in ("decision", "correction", "withdrawn")):
        return 1
    if topical and c.statement_kind == "client_statement":
        return 2
    return 3 if topical else 4


def timeline_prompt(claims: list[TimelineClaim], max_claims: int = 14, max_chars: int = 5200) -> tuple[str, list[TimelineClaim], int]:
    """(block, claims shown, claims left out): one compact line per significant claim, oldest first, carrying exactly what the model may rely on.  A long history has more claims than fit, so the
    best claim of EACH meeting (by _priority, the client's own words before the advisor's) is taken first - every meeting of the chronology is represented - and the remaining room goes to the
    highest-priority claims overall.  The code-written chronology still covers all of them."""
    pool = [c for c in claims if significant(c)]
    num = lambda c: int(c.claim_id[1:]) if c.claim_id and c.claim_id[1:].isdigit() else 0
    rank = lambda c: (_priority(c), 0 if c.statement_kind == "client_statement" else 1, num(c))
    order: list[TimelineClaim] = []
    by_meeting: dict = {}
    for c in sorted(pool, key=rank):
        by_meeting.setdefault(c.interaction_id, []).append(c)
    firsts = sorted((v[0] for v in by_meeting.values() if _priority(v[0]) <= 3), key=rank)      # a meeting with nothing topical is not represented for its own sake
    seen = {c.claim_id for c in firsts}
    order = firsts + [c for c in sorted(pool, key=rank) if c.claim_id not in seen]
    chosen: list[TimelineClaim] = []
    used = 0
    lines: dict[str, str] = {}
    for c in order:
        line = _prompt_line(c)
        if len(chosen) < max_claims and used + len(line) + 1 <= max_chars:
            chosen.append(c)
            used += len(line) + 1
            lines[c.claim_id] = line
    chosen.sort(key=lambda c: (c.event_date or "", c.interaction_id or "", c.turn_index if c.turn_index is not None else -1))
    return "\n".join(lines[c.claim_id] for c in chosen), chosen, len(pool) - len(chosen)


def _prompt_line(c: TimelineClaim) -> str:
    rel = []
    for r in c.relations:
        rel.append({"corrects": "corrects", "corrected_by": "corrected by", "contradicts": "conflicts with"}[r["type"]] + " " + r["claim_id"])
    mem = "memory: verified and confirmed" if c.confirmed_client_decision else "memory: unverified" if c.verification == "unverified" else "no memory record" if c.verification == "no_memory" else "memory: verified"
    w = " ".join(c.wording.replace('"', "'").split())
    w = w if len(w) <= 170 else w[:167] + "..."
    return (f"[{c.claim_id}] {c.event_date or 'undated'}, {_where(c)}, {TAG[c.statement_kind]}, {c.modality}, status {c.temporal_status}"
            + (f" ({'; '.join(rel)})" if rel else "") + f", {mem}: \"{w}\"")


NARRATIVE_SYSTEM = """You organise a recorded chronology into short, plain, professional paragraphs for an advisor. You are given TIMELINE lines. Each line [C#] is one dated statement from a real conversation turn: who said it (the client's own words, or the advisor's account or interpretation), how firm it was, its status and how it relates to other lines.

Return JSON: {"paragraphs": [{"sentences": [{"text": "...", "claims": ["C4"]}]}]}. Every sentence lists in "claims" the ids of the lines it rests on.

HOW TO WRITE A SENTENCE: exactly this shape and nothing more: On <date of the line> the <client or advisor> <said, noted or recorded> "<a quotation copied word for word from the line>". Nothing may come after the closing quotation mark except a full stop. Quote only the one or two sentences of the line that matter (under 30 words). Inside JSON write each double quotation mark as \\". Example: {"text": "On 2026-03-04 the client said \\"I was only exploring the possibility\\".", "claims": ["C4"]}. To show that one line corrects another, give two quotations in one sentence: On <date> the advisor recorded "<quotation>", and the client replied "<quotation>". with both claim ids.

RULES (code checks every sentence and deletes any that breaks one):
- Outside quotation marks the only words allowed are: the client, the advisor, said, noted, recorded, replied, asked, the date exactly as shown on the line, and, then, later. Do not describe, explain, summarise, interpret or comment: no "made a decision", no "correction", no "after discussing", no "because". The quotation does the telling.
- Say "the client" only for lines marked the client's own words, and "the advisor" for the advisor's lines. Never present an advisor's account as something the client said.
- Quote only words that are in the line. Do not add dates or numbers that are not in the lines.
- Keep a tentative line tentative. A line that is corrected by another line must be followed, in the same or the next sentence, by that correcting line. Say a conflict is unresolved when the line says so.
- Never give a reason or a cause. Never write decided, agreed, confirmed, final, current, replaced, superseded or clarified.
- Oldest line first. Use each line at most once. 2 to 4 paragraphs of 1 to 4 sentences."""


def narrative_user(request_text: str, block: str) -> str:
    return f"REQUEST: {request_text}\n\nTIMELINE (oldest first):\n{block}\n\nWrite the paragraphs now."
