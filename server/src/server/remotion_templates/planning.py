"""Sequential Plan/Execute state and hard task-wide tool limits, independent of model quotas."""

from typing import Literal

from pydantic import Field, model_validator

from .models import Contract
from .provider import ExecutionFailure

Module = Literal["tools", "image", "preset", "validate", "sprite"]


class Step(Contract):
    """One logical goal; inputs may refer only to supplied inputs or completed earlier steps."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    goal: str = Field(min_length=1, max_length=2000)
    input_refs: list[str] = Field(
        default_factory=lambda: ["user_intent"],
        max_length=32,
        description=(
            "Accepted references only: user_intent, accepted_base, or "
            "steps.<earlier_step_id>.outputs.sprite for a Sprite saved by an earlier step."
        ),
    )
    tool_modules: list[Module] = Field(min_length=1, max_length=5)
    done_when: str = Field(min_length=1, max_length=2000)


class Plan(Contract):
    """Model proposes ordered goals; revision and limits belong to the host."""

    goal: str = Field(min_length=1, max_length=2000)
    steps: list[Step] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def ordered_inputs(self):
        """Reject forward references, duplicate IDs and arbitrary filesystem-like inputs."""
        seen = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError("Plan step IDs must be unique")
            for ref in step.input_refs:
                if ref in {"user_intent", "accepted_base"}:
                    continue
                parts = ref.split(".")
                if (
                    len(parts) != 4
                    or parts[0] != "steps"
                    or parts[1] not in seen
                    or parts[2:] != ["outputs", "sprite"]
                ):
                    raise ValueError(
                        "Inputs must reference user_intent, accepted_base or steps.<earlier_id>.outputs.sprite"
                    )
            seen.add(step.id)
        return self


class PlanAction(Contract):
    """Host-owned handoff control shared by Outer and Plan ReAct layers."""

    action: Literal[
        "delegate", "update_plan", "continue", "advance", "complete", "stop"
    ]
    plan: Plan | None = None
    sprite_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def action_arguments(self):
        """Require exactly the payload consumed by the selected control transition."""
        if self.action == "update_plan" and self.plan is None:
            raise ValueError(f"{self.action} requires a plan")
        if self.action not in {"delegate", "update_plan"} and self.plan is not None:
            raise ValueError("Only delegate/update_plan accept a plan")
        if self.sprite_id is not None and self.action != "complete":
            raise ValueError(
                f"sprite_id is only valid with action=complete; omit it for {self.action} "
                "(Preset IDs are not Sprite IDs)"
            )
        return self


class StepResult(Contract):
    """Executor observation, never proof that a requested artifact passed acceptance."""

    status: Literal["step_done", "blocked", "needs_input"]
    summary: str = Field(min_length=1, max_length=2000)
    sprite_id: str | None = Field(default=None, max_length=64)


class PlanResult(Contract):
    """Return plan conclusions to Outer without granting publication authority."""

    status: Literal["plan_done", "blocked", "needs_input"]
    summary: str = Field(min_length=1, max_length=2000)
    sprite_id: str | None = Field(default=None, max_length=64)
    questions: list[str] = Field(default_factory=list, max_length=5)


class ExecutionState:
    """Retain completed steps, receipts and counters across continue and plan revisions."""

    def __init__(self, max_steps: int, max_tooluse: int):
        """Limits are supplied once by server settings, never by the model."""
        self.max_steps, self.max_tooluse = max_steps, max_tooluse
        self.batches = self.calls = self.batch_calls = 0
        self.plan: Plan | None = None
        self.revision = self.index = 0
        self.completed: dict[str, dict] = {}
        self.result: dict | None = None
        self.executing = False

    @property
    def total_limit(self) -> int:
        """Return the hard task-wide tool/control budget."""
        return self.max_steps * self.max_tooluse

    @property
    def step(self) -> Step | None:
        """Resolve the active logical step without implicitly advancing it."""
        return (
            self.plan.steps[self.index]
            if self.plan and self.index < len(self.plan.steps)
            else None
        )

    def dependents(self) -> list[str]:
        """IDs of later steps that take the current step's saved Sprite as their input.

        Such a step is not finished until a Sprite was actually saved: the Plan cannot re-open a step
        that was reported done, so the missing output has to be caught while the Executor can still
        produce it.
        """
        if self.plan is None or self.step is None:
            return []
        reference = f"steps.{self.step.id}.outputs.sprite"
        return [later.id for later in self.plan.steps[self.index + 1 :] if reference in later.input_refs]

    def begin_batch(self):
        """A continuation consumes a fresh batch; unused slots never transfer."""
        if (
            self.batches >= self.max_steps
            or self.calls >= self.max_steps * self.max_tooluse
        ):
            raise ExecutionFailure(
                "tool_budget_exhausted", "Execution batch/tool limit exhausted."
            )
        self.batches += 1
        self.batch_calls = 0

    def consume(self):
        """Charge before validation, so failed calls and explicit retries also count."""
        if (
            self.batch_calls >= self.max_tooluse
            or self.calls >= self.max_steps * self.max_tooluse
        ):
            raise ExecutionFailure(
                "tool_limit_reached",
                "Tool limit reached; return control to the outer loop before executing another tool.",
            )
        self.batch_calls += 1
        self.calls += 1

    def consume_control(self):
        """Charge a Plan control call without consuming an Executor tool slot."""
        if self.calls >= self.total_limit:
            raise ExecutionFailure(
                "tool_limit_reached",
                "Task tool/control limit reached; return the current outcome to the outer loop.",
            )
        self.calls += 1

    def control(self, action: PlanAction, *, start_batch=True):
        """Update pending steps or dispatch exactly one batch while preserving completed work."""
        proposed = self.plan.model_copy(deep=True) if self.plan else None
        index, revision, completed = self.index, self.revision, dict(self.completed)
        if action.action == "delegate":
            raise ValueError("Delegation belongs to Outer; Plan owns step definitions")
        if action.action == "update_plan":
            done = bool(self.result and self.result["status"] == "step_done")
            count = index + int(done)
            old = proposed.steps[:count] if proposed else []
            if action.plan.steps[: len(old)] != old:
                raise ValueError(
                    "Completed plan steps and their outputs must be preserved"
                )
            if count >= len(action.plan.steps):
                raise ValueError("Plan has no pending step; request completion instead")
            if done:
                completed[self.step.id] = self.result
                index += 1
            proposed = action.plan.model_copy(deep=True)
            revision += 1
        elif action.action == "advance":
            if (
                self.step is None
                or not self.result
                or self.result["status"] != "step_done"
            ):
                raise ValueError("Advance requires a completed current step")
            if index + 1 >= len(proposed.steps):
                raise ValueError("No next step; complete the task or update the plan")
            completed[self.step.id] = self.result
            index += 1
        elif action.action == "continue":
            if (
                self.step is None
                or self.result
                and self.result["status"] == "step_done"
            ):
                raise ValueError("Continue requires an unfinished current step")
        else:
            raise ValueError("Completion and stop are host finalization decisions")
        for ref in proposed.steps[index].input_refs:
            if ref.startswith("steps.") and not completed.get(
                ref.split(".")[1], {}
            ).get("sprite_id"):
                usable = [key for key, value in completed.items() if value.get("sprite_id")]
                raise ValueError(
                    f"Step input {ref} refers to an earlier step without a saved Sprite output; "
                    f"steps with a Sprite output: {usable or 'none'}. Drop input_refs for steps that only "
                    "created a Preset, or reference user_intent instead"
                )
        if start_batch:
            self.begin_batch()
        else:
            # The outer orchestration call is already charged to the task total, not to inner tool slots.
            self.batch_calls = 0
        self.plan, self.index, self.revision, self.completed = (
            proposed,
            index,
            revision,
            completed,
        )
        self.executing = True
        self.result = None

    def finish_batch(self, status: str, **details):
        """Return a batch result to the outer loop without marking a limit as step completion."""
        self.result = {
            "plan_revision": self.revision,
            "step_id": self.step.id if self.step else None,
            "batch": self.batches,
            "tool_calls": self.batch_calls,
            "status": status,
            **details,
        }
        self.executing = False

    def snapshot(self):
        """Expose host-owned counters and step outputs without mutable internal references."""
        return {
            "plan": self.plan.model_dump() if self.plan else None,
            "plan_revision": self.revision,
            "step_id": self.step.id if self.step else None,
            "completed": {key: dict(value) for key, value in self.completed.items()},
            "step_inputs": {
                ref: self.completed.get(ref.split(".")[1], {}).get("sprite_id")
                for ref in self.step.input_refs
                if ref.startswith("steps.")
            }
            if self.step
            else {},
            "last_batch": dict(self.result) if self.result else None,
            "limits": {"max_steps": self.max_steps, "max_tooluse": self.max_tooluse},
            "batches": self.batches,
            "actual_tooluse": self.calls,
            "total_tooluse": self.total_limit,
        }
