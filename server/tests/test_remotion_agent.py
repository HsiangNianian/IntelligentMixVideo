"""Offline state-transition tests for immutable Plan steps and shared execution limits."""

import pytest

from server.remotion_templates.planning import ExecutionState, Plan, PlanAction
from server.remotion_templates.provider import ExecutionFailure


def plan(*identifiers):
    """Build ordered goals with no implicit asset dependencies."""
    return Plan(goal="Create a Sprite", steps=[{
        "id": identifier,
        "goal": identifier,
        "done_when": "saved output available",
        "tool_modules": ["tools", "preset", "validate", "sprite"],
    } for identifier in identifiers])


def test_continuation_retains_step_and_hard_limit():
    """Continuing a logical step starts a new bounded batch without resetting task calls."""
    state = ExecutionState(2, 2)
    state.control(PlanAction(action="update_plan", plan=plan("title")))
    state.consume()
    state.consume()
    state.finish_batch("tool_limit_reached")
    state.control(PlanAction(action="continue"))
    assert state.step.id == "title" and state.revision == 1
    state.consume()
    state.consume()
    with pytest.raises(ExecutionFailure):
        state.consume()
    with pytest.raises(ExecutionFailure):
        state.control(PlanAction(action="continue"))
    assert state.calls == 4


def test_replan_preserves_completed_steps_and_outputs():
    """Only pending work can change and successful prior outputs remain available."""
    state = ExecutionState(6, 10)
    state.control(PlanAction(action="update_plan", plan=plan("title", "combine")))
    state.finish_batch("step_done", sprite_id="saved")
    state.control(PlanAction(action="advance"))
    revised = plan("title", "combine")
    revised.steps[1].goal = "combine remaining assets"
    state.control(PlanAction(action="update_plan", plan=revised))
    assert state.completed["title"]["sprite_id"] == "saved"
    revised.steps[0].goal = "replace completed work"
    with pytest.raises(ValueError, match="Completed"):
        state.control(PlanAction(action="update_plan", plan=revised))


def test_invalid_step_dependency_does_not_mutate_state():
    """Dispatch without a referenced saved Sprite fails before changing the Plan or budget."""
    state = ExecutionState(4, 10)
    proposal = plan("search", "make")
    proposal.steps[1].input_refs = ["steps.search.outputs.sprite"]
    state.control(PlanAction(action="update_plan", plan=proposal))
    state.finish_batch("step_done", summary="Only search results", sprite_id=None)
    before = state.snapshot()
    with pytest.raises(ValueError, match="without a saved"):
        state.control(PlanAction(action="advance"))
    assert state.snapshot() == before


def test_control_calls_count_without_consuming_executor_slots():
    """Plan decisions share the task total but do not consume an active batch slot."""
    state = ExecutionState(2, 2)
    state.consume_control()
    assert state.calls == 1 and state.batch_calls == state.batches == 0
    state.control(PlanAction(action="update_plan", plan=plan("title")))
    state.consume()
    assert state.calls == 2 and state.batch_calls == 1
