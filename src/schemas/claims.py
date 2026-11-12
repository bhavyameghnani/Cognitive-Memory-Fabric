"""Structured claims and verification outcomes (harness P1.1).

A free-text finding is turned into one StructuredClaim per quantitative assertion it makes.  Every field that cannot be read from the text is left as None / UNSPECIFIED
instead of being guessed: "not stated" and "stated as X" must never look the same to a verifier.

Outcomes (what the verifier concluded about ONE claim)
    VERIFIED      every checked dimension matches a verified quotation (or typed tool value) of the cited evidence
    UNVERIFIED    the evidence does not support the claim (missing, wrong client, different unit, wrong period, weaker modality, ...)
    CONTRADICTED  the evidence affirmatively says something incompatible (opposite polarity, or a different value for the same thing)
    AMBIGUOUS     the evidence might support it but the text cannot be compared safely (unclear subject, qualifiers, a weaker-than-evidence claim, qualitative claim)
Only VERIFIED may be presented as verified.  AMBIGUOUS is shown qualified; UNVERIFIED and CONTRADICTED are withheld.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Outcome(str, Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONTRADICTED = "CONTRADICTED"
    AMBIGUOUS = "AMBIGUOUS"


class Reason(str, Enum):
    CLIENT_MISMATCH = "CLIENT_MISMATCH"                  # claim and evidence are about different clients (or the claim's client is outside the plan)
    AMBIGUOUS_SUBJECT = "AMBIGUOUS_SUBJECT"              # whose claim/evidence is it cannot be established
    POLARITY_MISMATCH = "POLARITY_MISMATCH"              # evidence says "not X", claim says "X" (or the reverse)
    VALUE_MISMATCH = "VALUE_MISMATCH"                    # a different number for the same thing (exact decimal comparison, never substring)
    UNIT_MISMATCH = "UNIT_MISMATCH"                      # same figure, different unit (months vs years, % vs currency) or a unit that cannot be established
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"              # historical vs current, or a different date / year
    MODALITY_MISMATCH = "MODALITY_MISMATCH"              # exploratory / hypothetical / reported evidence versus a firmer claim (or the reverse)
    EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"            # the cited handle does not exist
    EVIDENCE_INACCESSIBLE = "EVIDENCE_INACCESSIBLE"      # the handle exists but the actor may not use it
    QUOTE_NOT_IN_SOURCE = "QUOTE_NOT_IN_SOURCE"          # the quoted excerpt is not text of the original source
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"              # there is no source text to check the claim against
    NO_COMPARABLE_FACT = "NO_COMPARABLE_FACT"            # the evidence contains no fact about this topic
    NO_QUANTIFIABLE_CLAIM = "NO_QUANTIFIABLE_CLAIM"      # the claim states no checkable value (a qualitative statement can never be VERIFIED deterministically)
    QUALIFIER_PRESENT = "QUALIFIER_PRESENT"              # "unless / except / only if ...": scope cannot be compared safely
    AMBIGUOUS_EVIDENCE = "AMBIGUOUS_EVIDENCE"            # the evidence itself is unclear (several values, double negation, unclear negation scope)
    SOURCE_NOT_PERMITTED = "SOURCE_NOT_PERMITTED"        # this kind of evidence cannot support this kind of statement
    EVIDENCE_NOT_FINAL = "EVIDENCE_NOT_FINAL"            # supported, but only by exploratory / unconfirmed / superseded evidence: shown qualified, never as a verified fact
    UNSUPPORTED_CLAUSE = "UNSUPPORTED_CLAUSE"            # P1.5.1: a clause of the finding states something the cited source does not
    ACTOR_MISMATCH = "ACTOR_MISMATCH"                    # P1.5.1: the clause is about a different person (spouse, parent, advisor ...) than the source clause it rests on
    EVIDENCE_NOT_VERIFIED = "EVIDENCE_NOT_VERIFIED"      # P1.5.1: the cited memory is unverified / contradicted / in an open conflict: it cannot support a finding


class Polarity(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class Modality(str, Enum):
    CONFIRMED = "CONFIRMED"          # decided / confirmed / agreed
    REPORTED = "REPORTED"            # someone says / wants / prefers (a reported preference stays reported)
    ESTIMATED = "ESTIMATED"          # about / roughly
    RECOMMENDED = "RECOMMENDED"      # an advisor recommendation, not a client decision
    HYPOTHETICAL = "HYPOTHETICAL"    # if / suppose / would
    EXPLORATORY = "EXPLORATORY"      # considering / could / thinking about
    UNCERTAIN = "UNCERTAIN"          # may / might / not settled


class PeriodKind(str, Enum):
    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    PLANNED = "PLANNED"
    UNSPECIFIED = "UNSPECIFIED"


class Period(BaseModel):
    kind: PeriodKind = PeriodKind.UNSPECIFIED
    dates: list[str] = Field(default_factory=list, description="explicit ISO dates (YYYY-MM-DD) or years (YYYY) named in the text")


class StructuredClaim(BaseModel):
    """One quantitative assertion.  None means 'not stated in the text'."""
    claim_id: str
    text: str
    subject_client: Optional[str] = None            # resolved client id (e.g. CLIENT-001)
    subject_inferred: bool = False                  # True when no one was named and the single client of the report was assumed
    topic: Optional[str] = None                     # canonical topic (retirement_age, cash_runway, ...) when the wording identifies one
    topic_terms: list[str] = Field(default_factory=list)
    value: Optional[Decimal] = None
    unit: Optional[str] = None                      # years | months | weeks | days | percent | USD | INR | EUR | GBP ; None = unitless / unstated
    unit_implied: bool = False                      # True when `unit` comes from the topic's default (retirement age -> years), not from the text
    polarity: Optional[Polarity] = None
    modality: Optional[Modality] = None             # None = a plain assertion with no hedge or confirmation wording
    period: Optional[Period] = None
    evidence_handle: Optional[str] = None
    source_id: Optional[str] = None                 # memory id / interaction turn / tool execution that produced the supporting evidence
    flags: list[str] = Field(default_factory=list)  # qualifier, negation_scope, multi_negation, multi_subject, temporal_conflict, unparsed_number


class ClaimVerification(BaseModel):
    claim: StructuredClaim
    outcome: Outcome
    reasons: list[Reason] = Field(default_factory=list)
    detail: str = ""
    evidence_handle: Optional[str] = None
    source_id: Optional[str] = None
    # the verified source quotation the decision was based on (a substring of the original source text).  Kept in memory for audit, never serialised: reports and traces must not
    # gain client text through the verification record.
    quote: Optional[str] = Field(None, exclude=True)
    quote_verified: bool = False
