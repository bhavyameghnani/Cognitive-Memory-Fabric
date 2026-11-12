"""Process-wide accounting of provider workers and bounded best-effort cancellation (harness P1.3.1).

A model call runs in a daemon worker (and, on the Strands transport, in a second daemon thread that executes the provider call itself).  A Python thread cannot be killed, so an
ABANDONED call keeps running until the provider returns.  This module makes that visible and bounded across ALL requests and BOTH transports:

  Slot / WorkerRegistry   one Slot per model call.  The slot is held by the worker and by every thread that executes provider work for the call, and it is released only when the
                          LAST of them has actually exited - never merely because the requesting thread gave up.  Admission fails closed when the process already has
                          `max_provider_workers` live slots (running + abandoned) or `max_abandoned_workers` abandoned ones, so abandoned work cannot grow without bound.
  CancelPool              best-effort `cancel_call` runs in a bounded set of daemon threads.  The request never waits for it.  Every dispatch reports exactly one terminal status:
                          ok | failed | timeout (rejected when the pool is full).  `ok` only means the provider ACKNOWLEDGED the request; it does not prove the work stopped.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from harness.reliability import safe_error


class CapacityExhausted(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


class Slot:
    def __init__(self, registry: "WorkerRegistry") -> None:
        self.registry, self.refs, self.abandoned = registry, 1, False

    def hold(self) -> None:
        """Another thread now executes work for this call (it must call drop() when it exits)."""
        with self.registry.lock:
            self.refs += 1

    def drop(self) -> None:
        self.registry._drop(self)


class WorkerRegistry:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.live = 0                  # slots with refs > 0 (running + abandoned)
        self.abandoned_live = 0        # of those, abandoned ones whose work has not exited yet
        self.abandoned_total = 0
        self.exited_after_abandon = 0
        self.rejected_total = 0
        self.peak_live = 0
        self.peak_abandoned = 0

    def admit(self, max_total: int, max_abandoned: int) -> Slot:
        with self.lock:
            if self.live >= max_total:
                self.rejected_total += 1
                raise CapacityExhausted("provider_worker_capacity", f"{self.live}/{max_total} provider workers are live in this process")
            if self.abandoned_live >= max_abandoned:
                self.rejected_total += 1
                raise CapacityExhausted("abandoned_worker_capacity", f"{self.abandoned_live}/{max_abandoned} abandoned provider workers are still running in this process")
            self.live += 1
            self.peak_live = max(self.peak_live, self.live)
            return Slot(self)

    def mark_abandoned(self, slot: Slot) -> None:
        with self.lock:
            if slot.abandoned or slot.refs <= 0:
                return
            slot.abandoned = True
            self.abandoned_live += 1
            self.abandoned_total += 1
            self.peak_abandoned = max(self.peak_abandoned, self.abandoned_live)

    def _drop(self, slot: Slot) -> None:
        with self.lock:
            if slot.refs <= 0:
                return
            slot.refs -= 1
            if slot.refs == 0:
                self.live -= 1
                if slot.abandoned:
                    self.abandoned_live -= 1
                    self.exited_after_abandon += 1

    def snapshot(self) -> dict:
        with self.lock:
            return dict(live=self.live, abandoned_live=self.abandoned_live, abandoned_total=self.abandoned_total, exited_after_abandon=self.exited_after_abandon,
                        rejected_total=self.rejected_total, peak_live=self.peak_live, peak_abandoned=self.peak_abandoned)


class CancelPool:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.started = self.rejected = self.timeouts = 0

    def dispatch(self, fn: Callable[[], bool], limit: int, timeout: float, report: Callable[..., None], on_start: Optional[Callable[[], None]] = None) -> bool:
        """Run `fn` (the provider's cancel hook) in a bounded daemon thread.  `report(status, error)` is called EXACTLY once with ok | failed | timeout.
        Returns False (nothing started) when `limit` cancel threads are already active - a cancel hook that never returns keeps occupying its slot."""
        with self.lock:
            if self.active >= limit:
                self.rejected += 1
                return False
            self.active += 1
            self.started += 1
        gate, reported = threading.Lock(), [False]

        def once(status: str, error: Optional[str] = None) -> None:
            with gate:
                if reported[0]:
                    return
                reported[0] = True
            if status == "timeout":
                with self.lock:
                    self.timeouts += 1
            try:
                report(status, error)
            except Exception:                                   # noqa: BLE001 - reporting must never raise into a daemon thread
                pass

        timer = threading.Timer(max(0.01, timeout), lambda: once("timeout", f"cancel_call did not return within {timeout:g}s"))
        timer.daemon = True

        def run() -> None:
            try:
                ok = bool(fn())
                once("ok" if ok else "failed", None if ok else "provider declined")
            except Exception as e:                              # noqa: BLE001
                once("failed", safe_error(e))
            finally:
                timer.cancel()
                with self.lock:
                    self.active -= 1

        if on_start is not None:
            try:
                on_start()
            except Exception:                                   # noqa: BLE001
                pass
        timer.start()
        threading.Thread(target=run, name="cmf-cancel", daemon=True).start()
        return True

    def snapshot(self) -> dict:
        with self.lock:
            return dict(active=self.active, started=self.started, rejected=self.rejected, timeouts=self.timeouts)


REGISTRY = WorkerRegistry()
CANCELS = CancelPool()
