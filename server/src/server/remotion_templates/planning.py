"""Sequential Plan/Execute state and hard task-wide tool limits, independent of model quotas."""

from typing import Literal

from pydantic import Field, model_validator

from .models import Contract, Text
from .provider import ExecutionFailure

# 工具模块：前五个用于 Preset 组合；workspace、preview、effect 与 library 用于自由组件（validate 与 image 两者共用）。
Module = Literal["tools", "image", "preset", "validate", "sprite", "workspace", "preview", "effect", "library"]
# 自由组件的工具模块：整个任务都开放同一份工具（工具 Schema 在请求前缀里，换一份就让缓存失效）；步骤的 tool_modules 只决定完成
# 条件：带 effect 的步骤要保存候选并看过联系表，只带工作区的步骤要当前文件通过检查。
FREE_TOOL_MODULES = ("workspace", "preview", "validate", "library", "image", "effect")


class Step(Contract):
    """One logical goal; inputs may refer only to supplied inputs or completed earlier steps."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    goal: str = Field(min_length=1, max_length=2000)
    input_refs: list[str] = Field(
        default_factory=lambda: ["user_intent"],
        max_length=32,
        description=(
            "Accepted references only: user_intent, accepted_base, "
            "steps.<earlier_step_id>.outputs.sprite for the Sprite an earlier step with the sprite module saves, or "
            "steps.<earlier_step_id>.outputs.presets for the Presets an earlier step with the preset module readies, or "
            "steps.<earlier_step_id>.outputs.effect for the free-form effect an earlier step with the workspace module built."
        ),
    )
    tool_modules: list[Module] = Field(min_length=1, max_length=8)
    done_when: str = Field(min_length=1, max_length=2000)


# Each step output is produced by exactly one tool module; the host records it from tool receipts.
# A free-form step with the workspace module hands on the effect it built as its summary; the files stay in the workspace.
OUTPUT_MODULES = {"sprite": "sprite", "presets": "preset", "effect": "workspace"}
REFERENCE_FORMS = (
    "Inputs must reference user_intent, accepted_base, steps.<earlier_id>.outputs.sprite, "
    "steps.<earlier_id>.outputs.presets or steps.<earlier_id>.outputs.effect"
)


class PlanDraft(Contract):
    """Model-proposed goal and steps; update_plan sends only the steps that are not finished yet."""

    goal: str = Field(min_length=1, max_length=2000)
    steps: list[Step] = Field(min_length=1, max_length=32)


class Plan(PlanDraft):
    """The complete ordered Plan; revision and limits belong to the host."""

    @model_validator(mode="after")
    def ordered_inputs(self):
        """Reject forward references, duplicate IDs, outputs an earlier step cannot produce and other inputs."""
        seen: dict[str, Step] = {}
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
                    or parts[2] != "outputs"
                    or parts[3] not in OUTPUT_MODULES
                ):
                    raise ValueError(REFERENCE_FORMS)
                if OUTPUT_MODULES[parts[3]] not in seen[parts[1]].tool_modules:
                    raise ValueError(
                        f"Step input {ref}: step {parts[1]} has no {OUTPUT_MODULES[parts[3]]} module, so it "
                        f"produces no {parts[3]}. Compose and save a Sprite in the same step that has the sprite module."
                    )
            seen[step.id] = step
        return self


def free_plan(edit: bool) -> Plan:
    """The host's standard plan for a free-form effect: one step, so the whole task extends one append-only Executor window and
    the provider's prefix cache keeps hitting. The Plan model is only needed for complex work or recovery.

    A new work turns the starter into the effect; an edit changes only what the request asks of the accepted version's files.
    Either saves with `effect.save` and checks the saved candidate's measured motion and middle frame against the request.
    """
    finish = ("Save it with effect_save, compare the motion in its reply with the request and look at its middle frame (attached to the next turn) "
              "for what cannot be measured. Fix what is clearly missing or wrong and save again; small differences from a reference are fine.")
    done = ("The latest effect_save succeeded, its motion and middle frame were checked and the files are unchanged since; report with summary, "
            "choices (how you read anything ambiguous) and suggestions (other directions the user may want).")
    if edit:
        return Plan(goal="Change the accepted effect as the user asks and save it.", steps=[Step(
            id="edit",
            goal="Change only what the request asks of the accepted version: edit the lines of Effect.tsx and effect.json involved with workspace_edit and keep everything else as it is. " + finish,
            input_refs=["user_intent", "accepted_base"], tool_modules=[*FREE_TOOL_MODULES], done_when=done,
        )])
    return Plan(goal="Build and save the effect the user asked for.", steps=[Step(
        id="build",
        goal=("Turn the starter in the workspace into the requested effect: describe each subject's look, starting state, motion and final state "
              "from the request and reference images, then write Effect.tsx and effect.json. " + finish),
        input_refs=["user_intent"], tool_modules=[*FREE_TOOL_MODULES], done_when=done,
    )])


def default_plan(edit: bool, base_drafts: bool | None = None) -> Plan:
    """The host's standard two-step pipeline, so the Plan model is only needed when something goes wrong.

    ``base_drafts`` says whether the accepted base registered ``base_*`` Preset drafts (default: ``edit``);
    a pre-Sprite base has one legacy component instead, which the edit rewrites as a Preset. An edit of a
    composed base changes only what the request asks: the base drafts stay as they are and a draft that must
    draw differently is copied with exact code edits, never rewritten from a stored Preset.
    """
    base_drafts = edit if base_drafts is None else base_drafts
    if base_drafts:
        preset_goal = (
            "Get the Presets the edited Sprite needs ready, changing only what the request asks of the accepted version: the base_* "
            "drafts listed in base are ready as they are. Copy a draft that must draw differently with preset_modify {preset_id: "
            "base_*, changes: {edits: [{old_str, new_str}]}}, replacing only the lines involved, and write a new Preset with "
            "preset_create only for an element the base does not have. A value the base exposes as a parameter (colour, size, font, timing) "
            "needs no Preset change; a value written into a draft's code is changed by editing that line."
        )
    else:
        preset_goal = (
            "Get the Preset(s) the Sprite needs ready. Reuse a fitting stored Preset (read it with preset_search "
            "{preset_id}) instead of writing a near copy; otherwise write complete code with preset_create."
        )
        if edit:
            preset_goal += (
                " For this edit, the accepted base is one legacy component (base.legacy_code), not reusable drafts: "
                "write the Preset it needs with preset_create."
            )
    return Plan(
        goal="Build and save the Sprite the user asked for.",
        steps=[
            Step(
                id="preset",
                goal=preset_goal,
                input_refs=["user_intent", *(["accepted_base"] if edit else [])],
                tool_modules=["preset"],
                done_when="The Presets the Sprite needs are saved, read or available as drafts; then call tools_report_step.",
            ),
            Step(
                id="sprite",
                goal=(
                    f"Compose the Presets from step_inputs{' (or base_* drafts), starting from base.text or base.instances and changing only what the request asks,' if base_drafts else ''} with sprite_compose, one instance per "
                    "visual element (a filter is one full-canvas instance), choosing its kind and binding the kind's required "
                    "parameters, then save it with sprite_create {draft_id}."
                ),
                input_refs=["user_intent", "steps.preset.outputs.presets"],
                tool_modules=["preset", "sprite"],
                done_when=("sprite_create succeeded and its source and available review frames were compared with the original request. "
                           "Fix a missing or contradictory motion before finishing; report the before/during/after observations with frame numbers."),
            ),
        ],
    )


class PlanAction(Contract):
    """Host-owned control shared by Outer (answer/ask/delegate/plan/stop) and Plan ReAct (step changes)."""

    action: Literal[
        "answer", "ask", "delegate", "plan", "update_plan", "continue", "advance", "complete", "stop"
    ]
    plan: PlanDraft | None = Field(
        default=None,
        description="update_plan only: the goal and the steps that are not finished yet; completed steps are kept by the host.",
    )
    sprite_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="", max_length=2000)
    answer: Text | None = Field(default=None, description="answer only: the reply to a factual question.")
    questions: list[Text] = Field(default_factory=list, max_length=5, description="ask only: necessary clarification questions.")

    @model_validator(mode="after")
    def action_arguments(self):
        """Require exactly the payload consumed by the selected control transition."""
        if self.action == "update_plan" and self.plan is None:
            raise ValueError(f"{self.action} requires a plan")
        if self.action != "update_plan" and self.plan is not None:
            # delegate runs the host's standard plan and plan hands the design to the Plan ReAct; a carried plan is ignored.
            raise ValueError("Only update_plan accepts a plan; delegate (standard plan) and plan (Plan ReAct designs it) start it themselves")
        if self.sprite_id is not None and self.action != "complete":
            raise ValueError(
                f"sprite_id is only valid with action=complete; omit it for {self.action} "
                "(Preset IDs are not Sprite IDs)"
            )
        if (self.action == "answer") != (self.answer is not None):
            raise ValueError("answer requires the answer text, and only answer accepts it")
        if (self.action == "ask") != bool(self.questions):
            raise ValueError("ask requires nonempty questions, and only ask accepts them")
        return self


class StepResult(Contract):
    """Executor report through tools_report_step; the host fills in the step's outputs from tool receipts."""

    status: Literal["step_done", "blocked", "needs_input"]
    summary: str = Field(min_length=1, max_length=2000)
    questions: list[str] = Field(
        default_factory=list, max_length=5,
        description="needs_input only: the questions Outer relays to the user.",
    )
    choices: list[str] = Field(
        default_factory=list, max_length=5,
        description="How you read anything the request left open or ambiguous (for example 「把双栏理解为上下两行」); shown to the user with the result.",
    )
    suggestions: list[str] = Field(
        default_factory=list, max_length=5,
        description="Short directions the user may want next (for example 「下栏改为逐字出现」); shown to the user as options.",
    )

    @model_validator(mode="after")
    def questions_for_input(self):
        """needs_input goes straight to Outer, which can only ask the user what the report asks."""
        if self.status == "needs_input" and not self.questions:
            raise ValueError("needs_input requires the questions for the user")
        return self


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

    def control(self, action: PlanAction, *, start_batch=True) -> list[str]:
        """Update pending steps or dispatch exactly one batch while preserving completed work.

        Returns the IDs of completed steps an update_plan resent: the host kept its own copies, and the
        caller tells the Plan so, because a step it meant to redo under the same ID did not run again.
        """
        proposed = self.plan.model_copy(deep=True) if self.plan else None
        index, revision, completed = self.index, self.revision, dict(self.completed)
        ignored: list[str] = []
        if action.action == "delegate":
            raise ValueError("Delegation belongs to Outer; Plan owns step definitions")
        if action.action == "update_plan":
            done = bool(self.result and self.result["status"] == "step_done")
            count = index + int(done)
            kept = proposed.steps[:count] if proposed else []
            # Completed steps are the host's; a resent copy is recognised by ID and ignored, never compared as text.
            finished = {step.id for step in kept}
            pending = [step for step in action.plan.steps if step.id not in finished]
            ignored = [step.id for step in action.plan.steps if step.id in finished]
            if not pending:
                raise ValueError(
                    f"Plan has no pending step: {', '.join(ignored)} already finished and the host keeps them. "
                    "Give work to redo a new step id, or call complete."
                )
            if done:
                completed[self.step.id] = self.result
                index += 1
            # Building the whole Plan checks every reference now, before any step of it runs.
            proposed = Plan(goal=action.plan.goal, steps=[*kept, *pending])
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
            raise ValueError("Only update_plan, continue and advance change the Plan")
        for ref in proposed.steps[index].input_refs:
            if ref.startswith("steps.") and not self._output(completed, ref):
                step_id, output = ref.split(".")[1], ref.split(".")[3]
                raise ValueError(f"Step input {ref} is missing: step {step_id} finished without {output}.")
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
        return ignored

    def finish_batch(self, status: str, **details):
        """Return a batch result to the outer loop without marking a limit as step completion."""
        self.result = {
            "plan_revision": self.revision,
            "step_id": self.step.id if self.step else None,
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
                ref: self._output(self.completed, ref)
                for ref in self.step.input_refs
                if ref.startswith("steps.")
            }
            if self.step
            else {},
            "last_batch": dict(self.result) if self.result else None,
            # Remaining counts only: raw used/limit pairs were misread as an exhausted budget.
            "tool_calls_left_in_batch": max(self.max_tooluse - self.batch_calls, 0),
            "tool_calls_left_in_task": max(self.total_limit - self.calls, 0),
        }

    @staticmethod
    def _output(completed: dict, ref: str):
        """Resolve steps.<id>.outputs.<sprite|presets|effect> to what that step recorded, or None."""
        step_id, output = ref.split(".")[1], ref.split(".")[3]
        return completed.get(step_id, {}).get({"sprite": "sprite_id", "presets": "presets", "effect": "summary"}[output]) or None
