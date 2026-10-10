"""Executor/Plan protocol-reply parsing for the Remotion agent.

Covers fenced or prose-wrapped StepResult JSON being accepted, ambiguous replies being
rejected with an actionable steer, and the no-progress guard now defaulting to on.
Run: `uv run --locked pytest tests/test_remotion_reply_parsing.py -v`.
"""

import pytest

from server.remotion_templates.agent import STEP_RESULT_FORMAT, extract_json_object
from server.remotion_templates.planning import StepResult
from server.remotion_templates.settings import Settings

OBJECT = '{"status":"step_done","summary":"ok","sprite_id":null}'


@pytest.mark.parametrize("reply", [
    OBJECT,
    f"```json\n{OBJECT}\n```",
    f"已完成本步骤。\n```json\n{OBJECT}\n```\n以上。",
    f"Step is complete: {OBJECT} done.",
    f'Step is "complete": {OBJECT}',
])
def test_step_result_is_found_in_fences_and_prose(reply):
    """Logged replies were fenced or wrapped in prose; the strict schema still validates the object."""
    assert StepResult.model_validate_json(extract_json_object(reply)).status == "step_done"


@pytest.mark.parametrize("reply", ["no json here", f"{OBJECT} and {OBJECT}", "[1, 2]", "{broken"])
def test_ambiguous_or_missing_json_is_not_guessed(reply):
    """Zero or several objects (or a non-object) are rejected so the steer can ask again."""
    assert extract_json_object(reply) is None


@pytest.mark.parametrize("reply", [
    f"[{OBJECT}]",
    f"Result: [{OBJECT}]",
    '{"unfinished":' + OBJECT,
    'Prefix {broken ' + OBJECT,
    '"{\\"status\\":\\"step_done\\",\\"summary\\":\\"ok\\"}"',
    OBJECT + " and {broken",
])
def test_nested_or_broken_json_is_not_salvaged_as_a_protocol_reply(reply):
    """数组、字符串或损坏容器中的子对象不能被提升为合法协议回复。"""
    assert extract_json_object(reply) is None


def test_extracted_object_still_fails_strict_validation():
    """Locating the JSON never weakens the schema: a bad status is still an error."""
    with pytest.raises(ValueError):
        StepResult.model_validate_json(extract_json_object('说明 {"status":"done","summary":"x"}'))


def test_steer_text_states_the_exact_format():
    """The correction names the single-object, no-fence rule and shows the shape."""
    assert "no markdown fence" in STEP_RESULT_FORMAT and "step_done" in STEP_RESULT_FORMAT


def test_no_progress_guard_is_on_by_default():
    """默认开启保护并允许六次连续空转，示例配置与实际默认值保持一致。"""
    from pathlib import Path

    assert Settings.model_fields["enforce_no_progress"].default is True
    assert Settings(_env_file=None).max_no_progress_turns == 6
    example = Path(__file__).parents[1] / ".env.example"
    assert "IMV_MAX_NO_PROGRESS_TURNS=6\n" in example.read_text()
