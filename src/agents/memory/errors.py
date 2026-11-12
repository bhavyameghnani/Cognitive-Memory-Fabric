"""Memory-layer exceptions (kept in their own module so the store, the integrity boundary and the harness validator can share them without import cycles)."""
from __future__ import annotations

# the memory package confines database DRIVER imports to store.py (a test enforces it); this only borrows the exception CLASS so callers that catch IntegrityError keep working
_IntegrityError = __import__("sqlite3").IntegrityError
DatabaseIntegrityError = _IntegrityError          # raw driver uniqueness / foreign-key / trigger failures: the boundary translates them to MemoryWriteRejected



class EvidenceError(ValueError):
    """Conversation evidence that does not exist in the CSV (turn missing, other client, other speaker, excerpt absent)."""


class MemoryWriteRejected(ValueError):
    """A durable memory mutation was refused by the integrity boundary.  `code` is a STABLE reason code; `reasons` lists every code that applied.  The message never
    contains another client's data and never says whether another client's memory exists."""

    def __init__(self, code: str, message: str = "", reasons=None, operation_id: str = "", replayed: bool = False) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.reasons = code, message, list(reasons or [code])
        self.operation_id, self.replayed = operation_id, replayed


class ScopeDenied(MemoryWriteRejected, PermissionError):
    """Missing identity, missing client scope, or a client outside the caller's authorized scope (the same error for 'not yours' and 'does not exist')."""


class InvalidRecord(MemoryWriteRejected, _IntegrityError):
    """The record violates a structural rule that the database also enforces (kept an IntegrityError for compatibility with callers that catch it)."""


class EvidenceRejected(MemoryWriteRejected, EvidenceError):
    """A cited source does not resolve inside the caller's client scope."""


class MemoryIntegrityUnavailable(RuntimeError):
    """The store cannot offer the integrity boundary (e.g. a non-SQLite backend): durable writes are refused rather than done without it."""


class MemoryResetRefused(RuntimeError):
    """init(reset=True) was asked to erase a store that holds memories or audit history.  Destroying that is a trusted OFFLINE operation (set CMF_MEMORY_MAINTENANCE=1 in the operator's shell), never an application call."""
