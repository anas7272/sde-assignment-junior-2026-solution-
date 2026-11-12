"""Derived facts as pure functions (no DB): the part of the spec most likely to break."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, Tuple

# (occurred_us, seq, code): seq is the ledger arrival order, used only to break exact ties.
DispEvent = Tuple[int, int, str]

BUCKET_YET = "yet_to_reach_out"
BUCKET_COMPLETED = "completed"
BUCKET_TELEPHONY_FAILED = "in_progress_telephony_failed"
BUCKET_DIALED = "in_progress_dialed"


def effective_disposition(sets: Iterable[DispEvent], overrides: Iterable[DispEvent]) -> Optional[str]:
    """Latest override by occurred_at; else latest set; else None. Never mutates events."""
    ov = list(overrides)
    if ov:
        return max(ov)[2]
    st = list(sets)
    if st:
        return max(st)[2]
    return None


@dataclass(frozen=True)
class CallFacts:
    call_id: str
    initiated_us: int
    connected: bool
    telephony_failed: bool
    effective: Optional[str]


def lead_bucket(calls: Sequence[CallFacts], is_terminal: Callable[[str], bool]) -> str:
    """First match wins: yet_to_reach_out, completed, in_progress_telephony_failed, in_progress_dialed."""
    if not calls:
        return BUCKET_YET
    if any(c.effective is not None and is_terminal(c.effective) for c in calls):
        return BUCKET_COMPLETED
    if all(c.telephony_failed for c in calls):
        return BUCKET_TELEPHONY_FAILED
    return BUCKET_DIALED


def current_disposition(calls: Sequence[CallFacts]) -> Optional[str]:
    """Effective disposition of the most recent call (by initiated time) that has one."""
    for c in sorted(calls, key=lambda c: (c.initiated_us, c.call_id), reverse=True):
        if c.effective is not None:
            return c.effective
    return None
