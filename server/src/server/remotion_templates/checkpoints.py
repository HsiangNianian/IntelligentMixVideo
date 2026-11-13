"""Persist editable progress independently of disposable renders; recover older failures from host audit receipts.

A retry keeps source, the current plan step and bounded tool history. Acceptance hashes and local image paths
are deliberately not restored: the new execution must save and review fresh evidence before publication.
"""

from copy import deepcopy
import json
from pathlib import Path

from .context import Conversation, StepWindow
from .planning import Plan, default_plan
from .tools.contracts import SpriteRecord, WorkspaceEditInput, WorkspaceWriteInput
from .tools.registry import parse_arguments
from .tools.session import _apply_edits
from .tools.workspace import CODE, MANIFEST, parse_manifest

RESUME_NOTE = (
    "Continue the interrupted task from these recovered files and its current step; do not restart the effect, "
    "repeat library research or replace completed code unless the latest user instruction requires it. "
    "Earlier tool receipts describe the previous execution. Its temporary images and acceptance evidence expired. "
    "If the files were already checked, proceed to effect_save, which runs the checks again, review its new "
    "motion and middle frame, then report completion. Fix only remaining problems."
)


def recent_exchanges(groups):
    """Keep complete text tool exchanges within 96 KB; source lives separately and images must be recaptured."""
    kept, size = [], 0
    for group in reversed(groups):
        if not group or group[0].get("role") != "assistant":
            continue
        if any(not isinstance(message.get("content"), (str, type(None))) for message in group):
            continue
        encoded = json.dumps(group, ensure_ascii=False).encode()
        if len(encoded) > 24_000:
            continue
        if len(kept) == 8 or size + len(encoded) > 96_000:
            break
        # Validate pairing before storing a detached copy.
        Conversation([group])
        kept.append(deepcopy(group))
        size += len(encoded)
    return list(reversed(kept))


def capture(run, exchange=None):
    """Take one serializable snapshot after a tool or a complete turn, excluding credentials and render files."""
    if run.state.plan is None and not run.generation_started:
        # An interrupted greeting or routing request has no effect progress to resume in the Executor.
        return None
    groups = list(run.executor_context.groups)
    if exchange and run.layer.value == "executor":
        groups.append(exchange)
    outputs = {str(key): value[-1:] for key, value in run.step_sprites.items()}
    referenced = {identifier for values in outputs.values() for identifier in values}
    referenced.update(value.get("sprite_id") for value in run.state.completed.values())
    referenced.add(run.session.latest_sprite_id)
    return {
        "version": 1,
        "files": dict(run.session.files),
        "plan": run.state.plan.model_dump(mode="json") if run.state.plan else None,
        "index": run.state.index,
        "revision": run.state.revision,
        "completed": deepcopy(run.state.completed),
        "saved_sprites": {key: value.model_dump(mode="json", exclude_unset=True)
                          for key, value in run.session.saved_sprites.items() if key in referenced},
        "latest_sprite_id": run.session.latest_sprite_id,
        "step_sprites": outputs,
        "history": recent_exchanges(groups),
        "feedback": list(run.session.feedback),
        "delegation": deepcopy(run.delegation),
    }


def restore(run, checkpoint):
    """Resume the unfinished step with new execution budgets and fresh acceptance, preserving completed outputs."""
    if checkpoint.get("version") != 1:
        raise ValueError("Unsupported task recovery checkpoint; progress was not discarded.")
    files = checkpoint["files"]
    if set(files) != {CODE, MANIFEST} or not all(isinstance(value, str) for value in files.values()):
        raise ValueError("Invalid task recovery workspace; progress was not discarded.")
    parse_manifest(files[MANIFEST])
    plan = Plan.model_validate(checkpoint["plan"]) if checkpoint.get("plan") else default_plan(run.session.base is not None)
    index = checkpoint.get("index", 0)
    if not isinstance(index, int) or not 0 <= index < len(plan.steps):
        raise ValueError("Invalid task recovery step; progress was not discarded.")
    run.session.files = dict(files)
    run.session.saved_sprites = {
        key: SpriteRecord.model_validate_json(json.dumps(value)) for key, value in checkpoint.get("saved_sprites", {}).items()
    }
    run.session.latest_sprite_id = checkpoint.get("latest_sprite_id")
    if run.session.latest_sprite_id is not None:
        run.session.saved_sprite(run.session.latest_sprite_id)
    run.session.feedback = list(checkpoint.get("feedback", []))
    run.state.plan, run.state.index = plan, index
    run.state.revision = checkpoint.get("revision", 0)
    run.state.completed = deepcopy(checkpoint.get("completed", {}))
    run.state.executing = True
    run.state.begin_batch()
    run.step_sprites = {int(key): list(value) for key, value in checkpoint.get("step_sprites", {}).items()}
    run.delegation = deepcopy(checkpoint.get("delegation"))
    run.layer = type(run.layer).EXECUTOR
    run.generation_started = True
    run.window_step = (run.state.revision, run.state.index)
    run.executor_context = StepWindow()
    run._append_handoff(run.executor_context, "task_resumed", {**run._handoff_detail(), "resume": RESUME_NOTE})
    run._append_references()
    run.executor_context.pinned = len(run.executor_context.groups)
    for group in recent_exchanges(checkpoint.get("history", [])):
        run.executor_context.append(group)
    run.window_floor = len(run.executor_context.serialize().encode())
    run.seen_calls.update(call["id"] for message in run.executor_context.messages() for call in message.get("tool_calls", []))


def from_audit(path: Path, initial_files: dict[str, str]):
    """Recover pre-checkpoint failures by applying only successful host-recorded file changes, never executing tools.

    The file is the private audit for the selected work/job, not a client-supplied path. An incomplete last log
    line is ignored after a crash; a recorded change that cannot be reproduced refuses recovery rather than
    silently replacing the user's progress with a starter.
    """
    if not path.is_file():
        return None
    files, groups, changed = dict(initial_files), [], False
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("event") != "tool_result" or "arguments" not in item:
                continue
            name, result = item.get("tool"), item.get("result", {})
            if result.get("ok") and name in {"workspace_write", "workspace.write", "workspace_edit", "workspace.edit"}:
                model = WorkspaceWriteInput if name in {"workspace_write", "workspace.write"} else WorkspaceEditInput
                request, _ = parse_arguments(model, item["arguments"])
                content = request.content if isinstance(request, WorkspaceWriteInput) else _apply_edits(
                    files[request.path], [edit.model_dump() for edit in request.edits],
                )
                if request.path == MANIFEST:
                    parse_manifest(content)
                files[request.path] = content
                changed = True
            if item.get("role") == "executor" and name and item.get("call_id"):
                groups.append([
                    {"role": "assistant", "content": None, "tool_calls": [{"id": item["call_id"], "type": "function",
                     "function": {"name": name, "arguments": item["arguments"]}}]},
                    {"role": "tool", "tool_call_id": item["call_id"], "content": json.dumps(result, ensure_ascii=False)},
                ])
                groups = recent_exchanges(groups)
    return {"version": 1, "files": files, "history": groups} if changed else None
