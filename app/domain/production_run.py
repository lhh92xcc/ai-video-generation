"""Durable control markers shared by the Run service and task repositories."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


AUTO_RUN_ID = "auto_run_id"
AUTO_RUN_PLAN = "auto_run_plan"
AUTO_RUN_ENABLED = "auto_advance"
AUTO_RUN_STATUS = "auto_run_status"
AUTO_RUN_CONTROL_REVISION = "auto_run_control_revision"

CONTROL_MARKER_KEYS = (
    AUTO_RUN_ID,
    AUTO_RUN_PLAN,
    AUTO_RUN_ENABLED,
    AUTO_RUN_STATUS,
    AUTO_RUN_CONTROL_REVISION,
)

RUN_STATUS_ORDER = ("canceled", "paused", "failed", "blocked", "completed", "active")


def run_status_from_tasks(tasks: list[Any]) -> str:
    """Resolve one public Run status from persisted task markers."""

    statuses = {
        str(task.input_data.get(AUTO_RUN_STATUS))
        for task in tasks
        if task.input_data.get(AUTO_RUN_STATUS)
    }
    return next(
        (candidate for candidate in RUN_STATUS_ORDER if candidate in statuses),
        "active",
    )


def control_revision(input_data: dict[str, Any]) -> int:
    """Read a tolerant integer revision from persisted task input data."""

    value = input_data.get(AUTO_RUN_CONTROL_REVISION, 0)
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def merge_run_control_markers(
    existing_input: dict[str, Any] | None,
    incoming_input: dict[str, Any],
) -> dict[str, Any]:
    """Prevent a stale Worker snapshot from undoing Run control actions.

    Provider services hold a task snapshot while a model is running.  If an
    operator pauses or cancels the Run during that call, the later Provider
    update must retain the newer control marker while still accepting all
    normal task progress, error and Artifact changes.
    """

    if not existing_input or not incoming_input:
        return incoming_input
    existing_run_id = existing_input.get(AUTO_RUN_ID)
    incoming_run_id = incoming_input.get(AUTO_RUN_ID)
    if not existing_run_id or existing_run_id != incoming_run_id:
        return incoming_input

    existing_revision = control_revision(existing_input)
    incoming_revision = control_revision(incoming_input)
    newer = existing_revision > incoming_revision
    if not newer:
        return incoming_input

    merged = dict(incoming_input)
    for key in CONTROL_MARKER_KEYS:
        if key in existing_input:
            merged[key] = deepcopy(existing_input[key])
    if existing_input.get(AUTO_RUN_STATUS) == "canceled":
        # Cancellation also clears a pending automatic retry.  A stale Worker
        # snapshot must not resurrect that retry after the control revision
        # has already moved forward.
        merged["auto_retry_pending"] = False
        merged.pop("next_retry_at", None)
    return merged
