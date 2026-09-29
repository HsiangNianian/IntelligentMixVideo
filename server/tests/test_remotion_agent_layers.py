"""Focused offline tests for the Outer → Plan → Executor handoff protocol."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from server.remotion_templates.agent import AgentRun
from server.remotion_templates.context import AssistantMessage
from server.remotion_templates.planning import Plan
from server.remotion_templates.provider import Budget


class FakeSession:
    """Small task-owned session exposing only the PR76 APIs used by AgentRun."""

    def __init__(self, _harness, _spec, _base, _budget, directory, images, _stage, intent):
        self.directory = Path(directory)
        self.images = images
        self.intent = intent or {"original_request": {"description": "demo"}}
        self.feedback = []
        self.step_scope = None
        self.latest_sprite_id = "sprite-1"
        self.saved_sprites = {"sprite-1": {"sprite_id": "sprite-1"}}

    def snapshot(self):
        """Return deterministic host facts for all three prompts."""
        return {"user_intent": self.intent, "sprites": sorted(self.saved_sprites)}

    def saved_sprite(self, identifier):
        """Resolve only the task's exact saved Sprite."""
        return self.saved_sprites[identifier]

    def validation_for(self, _record):
        """Expose a passing final validation for the finalizer test."""
        return SimpleNamespace(passed=True)

    async def execute(self, name, args, _catalog):
        """Record one deterministic business tool observation."""
        return {"tool": name, "args": args.model_dump(mode="json")}


class FakeHarness:
    """Script model turns while recording the role sequence and finalization."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.roles = []
        self.settings = SimpleNamespace(
            max_steps=4,
            max_tooluse=3,
            max_no_progress_turns=4,
        )

    async def _turn(self, _system, _context, _tools, _budget, _images, *, phase):
        """Return the next scripted response for one exact model role."""
        self.roles.append(phase)
        return next(self.responses)

    async def finalize_sprite(self, _session, identifier, _budget, _directory):
        """Return a marker proving only Outer performed publication."""
        return {"published": identifier}


def call(identifier, name, arguments):
    """Build a provider-compatible function call message."""
    return AssistantMessage(
        tool_calls=[
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    )


def test_outer_plan_executor_plan_outer_handoff(monkeypatch, tmp_path):
    """A planned task must traverse every layer and publish only after Outer completion."""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(
        goal="inspect",
        steps=[
            {
                "id": "inspect",
                "goal": "inspect a tool",
                "tool_modules": ["tools"],
                "done_when": "descriptor observed",
            }
        ],
    )
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            call("exec-1", "tools_inspect", {"tool_name": "sprite.create"}),
            AssistantMessage(content=json.dumps({"status": "step_done", "summary": "descriptor observed", "sprite_id": "sprite-1"})),
            call("plan-2", "tools_plan_execute", {"action": "complete"}),
            AssistantMessage(content=json.dumps({"action": "complete", "sprite_id": "sprite-1"})),
        ]
    )
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    result = asyncio.run(run.plan_execute())
    assert result == {"published": "sprite-1"}
    assert harness.roles == ["outer", "plan", "executor", "executor", "plan", "outer"]
    assert run.plan_context.messages() and run.executor_context.messages()


def test_executor_cannot_call_plan_control(monkeypatch, tmp_path):
    """An Executor orchestration call is rejected and does not advance the Plan."""
    monkeypatch.setattr("server.remotion_templates.agent.ToolSession", FakeSession)
    plan = Plan(
        goal="inspect",
        steps=[
            {
                "id": "inspect",
                "goal": "inspect",
                "tool_modules": ["tools"],
                "done_when": "done",
            }
        ],
    )
    harness = FakeHarness(
        [
            call("outer-1", "tools_plan_execute", {"action": "delegate", "plan": plan.model_dump()}),
            call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": plan.model_dump()}),
            call("exec-1", "tools_plan_execute", {"action": "advance"}),
            AssistantMessage(content=json.dumps({"status": "needs_input", "summary": "stop"})),
            AssistantMessage(content=json.dumps({"status": "needs_input", "summary": "stop", "questions": ["需要更多输入"]})),
            AssistantMessage(content=json.dumps({"questions": ["需要更多输入"]})),
        ]
    )
    run = AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None, intent={"original_request": {"description": "demo"}})
    result = asyncio.run(run.plan_execute())
    assert result.questions == ["需要更多输入"]
    assert run.state.index == 0
