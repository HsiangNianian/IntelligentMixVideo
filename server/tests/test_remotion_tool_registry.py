"""Registry consistency and tool-name regression tests for the Remotion agent.

Covers: every registered tool resolves by dotted and wire name, inspection accepts both,
visibility follows `implemented`, generation-starting tools are declared on the registry,
unknown names fail with a correctable message, the read-only library behaves, the
Plan contract errors seen in real task logs are actionable, and call arguments tolerate only
surplus closing brackets while every other JSON or schema error names where it is.
Run: `uv run --locked pytest tests/test_remotion_tool_registry.py -v`.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.remotion_templates.planning import PlanAction
from server.remotion_templates.tools.catalog import available, resolve
from server.remotion_templates.tools.contracts import (
    ImageInspectInput,
    WorkspaceReadInput,
)
from server.remotion_templates.tools.registry import (
    ToolFault,
    get_tool,
    parse_arguments,
    registered_tools,
    wire_name,
)
from server.remotion_templates.tools.session import ToolSession


@pytest.fixture(autouse=True)
def catalog_database(template_db):
    """The Preset catalog lives in MySQL only; every test here uses the temporary SQLite stand-in."""
    return template_db


def make_session(tmp_path, monkeypatch):
    """Build a ToolSession whose Preset catalog is the temporary database."""
    harness = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path), renderer=None)
    return ToolSession(harness, None, None, None, tmp_path, [], lambda *_: None, {})




@pytest.mark.parametrize("item", registered_tools(), ids=lambda item: item.name)
def test_every_tool_resolves_by_dotted_and_wire_name(item):
    """Both spellings reach the same tool, and the provider wire name is what is exposed."""
    assert get_tool(item.name) is item
    assert get_tool(wire_name(item.name)) is item
    assert item.wire()["function"]["name"] == wire_name(item.name)
    assert item.descriptor.tool_name == item.name


def test_visibility_follows_implementation_and_dispatch_accepts_wire_names():
    """Only implemented tools are offered, and the layer resolver accepts the wire name."""
    offered = {item.name for item in available(None)}
    assert offered == {item.name for item in registered_tools() if item.implemented} | {"tools.plan_execute"}
    for item in available(None, executor=True):
        assert resolve(wire_name(item.name), None, executor=True) is item
    with pytest.raises(ValueError):
        resolve("image_info", None, executor=True)


def test_generation_tools_are_declared_on_the_registry():
    """The host's 'generation started' signal comes from registration, not a hardcoded set."""
    assert {item.name for item in registered_tools() if item.starts_generation} == {
        "workspace.write", "workspace.edit", "effect.save",
    }


@pytest.mark.parametrize("bad", ["preset", "sprite", "Preset_Create", ""])
def test_unknown_tool_names_fail_with_list_and_suggestions(bad):
    """Names seen in real logs ('preset', 'sprite') are rejected but explained, never guessed."""
    with pytest.raises(ToolFault) as caught:
        get_tool(bad)
    error = caught.value.error
    assert error.code == "TOOL_NOT_FOUND"
    assert "Available tools:" in error.message and "effect.save" in error.message


def test_inspect_description_does_not_invite_guessing_names():
    """模型可见的 tools_inspect 描述曾写着「未知名称会列出全部 ID，猜名字即可学到真名」，与系统提示「不能搜索或列出工具」相矛盾。"""
    description = get_tool("tools.inspect").wire()["function"]["description"]
    assert "does not search" in description and "guess" not in description


def test_empty_tool_window_never_falls_back_to_the_registry():
    """空权限窗口必须拒绝已注册工具，且不能建议窗口外的工具。"""
    with pytest.raises(ToolFault) as caught:
        get_tool("effect_save", [])
    assert caught.value.error.message.endswith("Available tools: none")
    assert "Closest:" not in caught.value.error.message


def test_inspect_accepts_wire_name():
    """tools.inspect no longer rejects the underscore spelling the model sees on the wire."""
    session = SimpleNamespace()
    result = asyncio.run(get_tool("tools_inspect").invoke(session, {"tool_name": "effect_save"}))
    assert result["ok"] and result["data"]["tool_name"] == "effect.save"








def test_advance_with_sprite_id_explains_the_fix():
    """The log's `advance` + preset id mistake now says exactly what to change."""
    with pytest.raises(ValueError) as caught:
        PlanAction(action="advance", sprite_id="a0fde34e")
    assert "only valid with action=complete" in str(caught.value) and "omit it" in str(caught.value)








def test_search_rejects_an_unbounded_requested_limit():
    """过大的 limit 在工具边界返回可纠正错误，且不读取目录。"""
    result = asyncio.run(get_tool("library.search").invoke(None, {"limit": 101}))
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["error"]["field"] == "/limit"




@pytest.mark.parametrize("surplus", ["}", " }}", "]\n"])
def test_surplus_closing_brackets_after_the_arguments_are_dropped(surplus):
    """回放 bb0decec：完整参数后多写的结尾括号被丢弃并告知调用方，不让一个括号作废整次调用。"""
    request, dropped = parse_arguments(WorkspaceReadInput, '{"path": "Effect.tsx"}' + surplus)
    assert request.path == "Effect.tsx"
    assert dropped == surplus.strip()
    assert parse_arguments(WorkspaceReadInput, '{"path": "Effect.tsx"}') == (WorkspaceReadInput(path="Effect.tsx"), "")


@pytest.mark.parametrize("raw, expected", [
    ('{"path": "Effect.tsx"} trailing words', "Unexpected text after the JSON arguments at character 22"),
    ('{"path": "Effect.tsx"', "Arguments are not valid JSON"),
    ('{"path": "Effect.tsx"}{"path": "effect.json"}', "Unexpected text after the JSON arguments"),
])
def test_other_invalid_json_names_the_position_and_nearby_text(raw, expected):
    """除结尾多余括号外的非法 JSON 一律拒绝，错误给出位置与附近文本，而不是笼统的解析异常。"""
    with pytest.raises(ToolFault) as refused:
        parse_arguments(WorkspaceReadInput, raw)
    assert refused.value.error.code == "INVALID_ARGUMENT"
    assert expected in refused.value.error.message and "near" in refused.value.error.message


def test_schema_errors_list_up_to_three_located_fields():
    """结构错误带 JSON Pointer：field 为第一处，消息逐条写出前三处位置，其余给出数量。"""
    with pytest.raises(ToolFault) as refused:
        parse_arguments(ImageInspectInput, json.dumps({"image": 0, "x": -1, "y": 2, "width": 0}))
    error = refused.value.error
    assert error.field == "/image"
    assert all(field in error.message for field in ("/image", "/x", "/y"))
    assert error.message.endswith("and 1 more")
