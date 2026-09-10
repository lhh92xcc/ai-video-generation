"""Durable marker names for topic short-video Production Runs."""

from __future__ import annotations

TOPIC_PIPELINE = "topic_pipeline"
TOPIC_RUN_ID = "topic_run_id"
TOPIC_RUN_PLAN = "topic_run_plan"
TOPIC_SCRIPT_TASK_ID = "topic_script_task_id"
TOPIC_SCENE_INDEX = "topic_scene_index"
TOPIC_RUN_STATUS = "topic_run_status"
TOPIC_RUN_CONTROL_REVISION = "topic_run_control_revision"
TOPIC_RUN_ERROR_CODE = "topic_run_error_code"
TOPIC_RUN_ERROR_MESSAGE = "topic_run_error_message"

TOPIC_RUN_STATUSES = ("active", "blocked", "completed", "failed")


def topic_run_status_from_tasks(tasks: list[object]) -> str:
    """Resolve the durable public status for a topic media Run.

    The status marker is deliberately separate from the task status. A Run
    can be blocked after all currently persisted tasks succeed, for example
    when the next video Provider profile is not configured yet.
    """

    statuses = {
        str(getattr(task, "input_data", {}).get(TOPIC_RUN_STATUS))
        for task in tasks
        if getattr(task, "input_data", {}).get(TOPIC_RUN_STATUS)
    }
    for candidate in ("failed", "blocked", "active", "completed"):
        if candidate in statuses:
            return candidate
    return "active"


def topic_control_revision(input_data: dict[str, object]) -> int:
    """Read a tolerant topic Run marker revision."""

    value = input_data.get(TOPIC_RUN_CONTROL_REVISION, 0)
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)
