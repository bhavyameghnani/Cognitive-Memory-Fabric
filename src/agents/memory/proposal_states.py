"""The proposal state machine, shared by the store (which turns it into database triggers) and by agents/memory/staging.py (which drives it).  No imports: neither side
depends on the other.

A PROPOSAL is what extraction produces.  It is not a memory.  Validation, approval and promotion are different operations:

    extracted -> validated -> pending_approval -> approved -> promoted -> superseded | expired
         |           |              |                 |
         +-----------+--------------+-----------------+--> rejected          (terminal; kept for audit)
         +--> unresolved -> rejected | expired                                (an unresolved proposal can never be approved: new evidence means a NEW proposal)
         +--> expired                                                          (terminal)

`validated` means "passed the structural evidence checks" - it is NOT authorisation.  `approved` needs a recorded decision by an authenticated person.  `promoted` means a production
memory exists and the proposal points at it.
"""
STATES = ("extracted", "validated", "rejected", "unresolved", "pending_approval", "approved", "promoted", "superseded", "expired")
TRANSITIONS = {
    "extracted": ("validated", "rejected", "unresolved", "expired"),
    "validated": ("pending_approval", "rejected", "unresolved", "expired"),
    "unresolved": ("rejected", "expired"),
    "pending_approval": ("approved", "rejected", "expired"),
    "approved": ("promoted", "rejected", "expired"),
    "promoted": ("superseded", "expired"),
    "rejected": (), "superseded": (), "expired": (),
}
TERMINAL = tuple(s for s, nxt in TRANSITIONS.items() if not nxt)
# columns of memory_proposals that are fixed at creation (validation results, state and the promotion link are the only mutable ones)
IMMUTABLE_COLUMNS = ["proposal_id", "client_id", "memory_type", "topic", "subject", "statement", "value_text", "corrects_value", "certainty", "source_role", "attribution", "event_type",
                     "origin", "interaction_id", "turn_index", "excerpt", "excerpt_start", "excerpt_end", "match_method", "evidence_json", "record_json", "details_json",
                     "extraction_json", "content_hash", "target_memory_id", "created_by", "formed_at"]
