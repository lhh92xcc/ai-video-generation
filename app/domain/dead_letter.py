"""Durable dead-letter markers for tasks that need operator intervention.

The task table already persists ``input_data`` as JSON in both repository
implementations.  Keeping the small operational marker there avoids a schema
migration while still making the state survive API/Worker restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEAD_LETTER_KEY = "dead_letter"
DEAD_LETTER_OPEN = "open"
DEAD_LETTER_REQUEUED = "requeued"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_dead_letter(
    input_data: dict[str, Any],
    *,
    error_code: str,
    error_message: str,
    auto_retry_count: int,
    max_auto_retries: int,
    now: str | None = None,
) -> dict[str, Any]:
    """Open or re-open an operator-visible dead-letter marker."""

    timestamp = now or _now_iso()
    previous = input_data.get(DEAD_LETTER_KEY)
    previous_record = previous if isinstance(previous, dict) else {}
    previous_status = str(previous_record.get("status", ""))
    reopen_count = int(previous_record.get("reopen_count", 0) or 0)
    if previous_status == DEAD_LETTER_REQUEUED:
        reopen_count += 1

    record = {
        "status": DEAD_LETTER_OPEN,
        "opened_at": str(previous_record.get("opened_at") or timestamp),
        "last_failed_at": timestamp,
        "error_code": error_code[:120],
        "error_message": error_message[:300],
        "auto_retry_count": max(0, int(auto_retry_count)),
        "max_auto_retries": max(0, int(max_auto_retries)),
        "reopen_count": reopen_count,
        "requeue_count": int(previous_record.get("requeue_count", 0) or 0),
    }
    input_data[DEAD_LETTER_KEY] = record
    return record


def mark_dead_letter_requeued(
    input_data: dict[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any] | None:
    """Record that an operator took ownership of a dead-letter task."""

    record = input_data.get(DEAD_LETTER_KEY)
    if not isinstance(record, dict) or record.get("status") != DEAD_LETTER_OPEN:
        return None
    updated = dict(record)
    updated["status"] = DEAD_LETTER_REQUEUED
    updated["requeued_at"] = now or _now_iso()
    updated["requeue_count"] = int(updated.get("requeue_count", 0) or 0) + 1
    input_data[DEAD_LETTER_KEY] = updated
    return updated


def is_open(input_data: dict[str, Any]) -> bool:
    record = input_data.get(DEAD_LETTER_KEY)
    return isinstance(record, dict) and record.get("status") == DEAD_LETTER_OPEN


def record_from_input(input_data: dict[str, Any]) -> dict[str, Any] | None:
    record = input_data.get(DEAD_LETTER_KEY)
    if not isinstance(record, dict) or record.get("status") != DEAD_LETTER_OPEN:
        return None
    return record
