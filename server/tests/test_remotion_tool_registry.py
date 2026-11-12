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
    PresetSearchInput,
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


def save_preset(session, description, properties=None, *, preset_id=None, created_at=None):
    """Store one valid Preset record directly through the catalog."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from server.remotion_templates.tools.contracts import PresetRecord

    record = PresetRecord(
        preset_id=preset_id or uuid4().hex,
        created_at=created_at or datetime.now(UTC).isoformat(),
        description=description,
        code="export default function C(){return null}",
        parameter_schema={"type": "object", "properties": properties or {}, "additionalProperties": False},
        default_parameters={},
    )
    session.catalog.append_preset(record)
    return record


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


def test_search_lists_summaries_with_optional_keyword(tmp_path, monkeypatch):
    """Search is a plain listing: filter by substring, limit, empty result is not an error."""
    session = make_session(tmp_path, monkeypatch)
    save_preset(session, "渐显标题", {"text": {"type": "string"}})
    save_preset(session, "下划线")
    everything = session.search_presets(PresetSearchInput())
    assert len(everything.presets) == 2
    only = session.search_presets(PresetSearchInput(query="标题"))
    assert [item.description for item in only.presets] == ["渐显标题"]
    assert only.presets[0].parameter_names == ["text"]
    assert session.search_presets(PresetSearchInput(query="不存在")).presets == []
    assert len(session.search_presets(PresetSearchInput(limit=1)).presets) == 1


def test_search_orders_presets_by_creation_time(tmp_path, monkeypatch):
    """数据库按 ID 读出的顺序不能替代创建时间：先按创建时间倒序排序，再截取。"""
    session = make_session(tmp_path, monkeypatch)
    local = save_preset(session, "Title local", preset_id="b" * 32, created_at="2026-10-08T12:00:00+00:00")
    save_preset(session, "Title oldest", preset_id="e" * 32, created_at="2026-10-07T00:00:00+00:00")
    save_preset(session, "Title older", preset_id="f" * 32, created_at="2026-10-08T00:00:00+00:00")
    newest = save_preset(session, "Title newest", preset_id="0" * 32, created_at="2026-10-09T00:00:00+00:00")
    result = session.search_presets(PresetSearchInput(query="title", limit=2))
    assert [item.preset_id for item in result.presets] == [newest.preset_id, local.preset_id]
    assert session.search_presets(PresetSearchInput(limit=1)).presets[0].preset_id == newest.preset_id
    with pytest.raises(ToolFault) as missing:
        session.search_presets(PresetSearchInput(preset_id="missing"))
    message = missing.value.error.message
    assert message.index(newest.preset_id) < message.index(local.preset_id)


def test_unknown_preset_error_lists_known_ids(tmp_path, monkeypatch):
    """A made-up ID (seen in logs) fails with the real IDs so the next call can be correct."""
    session = make_session(tmp_path, monkeypatch)
    record = save_preset(session, "x")
    with pytest.raises(ToolFault) as caught:
        session._find_preset("imv:preset/0")
    assert caught.value.error.code == "PRESET_NOT_FOUND" and record.preset_id in caught.value.error.message


def test_advance_with_sprite_id_explains_the_fix():
    """The log's `advance` + preset id mistake now says exactly what to change."""
    with pytest.raises(ValueError) as caught:
        PlanAction(action="advance", sprite_id="a0fde34e")
    assert "only valid with action=complete" in str(caught.value) and "omit it" in str(caught.value)


def test_search_with_preset_id_returns_the_full_record(tmp_path, monkeypatch):
    """A preset_id read returns code and schema; an unknown id fails with the known ids."""
    session = make_session(tmp_path, monkeypatch)
    record = save_preset(session, "标题", {"text": {"type": "string"}})
    found = session.search_presets(PresetSearchInput(preset_id=record.preset_id))
    assert found.preset.code == record.code and found.presets[0].preset_id == record.preset_id
    with pytest.raises(ToolFault) as missing:
        session.search_presets(PresetSearchInput(preset_id="nope"))
    assert missing.value.error.code == "PRESET_NOT_FOUND"


def test_search_tolerates_invalid_and_naive_catalog_timestamps(tmp_path, monkeypatch):
    """历史无时区值按 UTC 排序，非法时间置后且仍可按 ID 读取或提供纠错提示。"""
    session = make_session(tmp_path, monkeypatch)
    invalid = save_preset(session, "invalid", created_at="not-a-date")
    aware = save_preset(session, "aware", created_at="2026-10-09T08:00:00+08:00")
    naive = save_preset(session, "naive", created_at="2026-10-09T01:00:00")
    result = session.search_presets(PresetSearchInput())
    assert [item.preset_id for item in result.presets] == [naive.preset_id, aware.preset_id, invalid.preset_id]
    assert session.search_presets(PresetSearchInput(preset_id=invalid.preset_id)).preset == invalid
    with pytest.raises(ToolFault) as missing:
        session._find_preset("missing")
    assert missing.value.error.code == "PRESET_NOT_FOUND"
    assert invalid.preset_id in missing.value.error.message


@pytest.mark.parametrize("tool_name,method,arguments", [
    ("library.search", "search_presets", {}),
])
def test_preset_storage_errors_are_in_the_published_descriptor(tool_name, method, arguments):
    """读取目录的实际存储错误必须出现在 inspect 返回的错误清单中。"""
    def broken(_request):
        """模拟磁盘读失败，不让测试访问真实存储。"""
        raise OSError("disk read failed")

    tool = get_tool(tool_name)
    result = asyncio.run(tool.invoke(SimpleNamespace(**{method: broken}), arguments))
    assert result["ok"] is False
    assert result["error"]["code"] == "PRESET_STORE_FAILED"
    assert result["error"]["code"] in tool.describe()["error_codes"]


def test_search_rejects_an_unbounded_requested_limit():
    """过大的 limit 在工具边界返回可纠正错误，且不读取目录。"""
    result = asyncio.run(get_tool("library.search").invoke(None, {"limit": 101}))
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["error"]["field"] == "/limit"


def test_large_search_replies_fit_a_complete_multi_tool_exchange(tmp_path, monkeypatch):
    """四次合法大列表调用仍可保存成完整会话；包含中文和转义字符的字节计数有效。"""
    from server.remotion_templates.context import AssistantMessage, Conversation

    session = make_session(tmp_path, monkeypatch)
    for index in range(25):
        save_preset(session, f"标题 {index} " + '中\\"\n' * 800,
                    properties={'参数\\"\n' * 20: {"type": "string"}})
    message = AssistantMessage(tool_calls=[{
        "id": f"search-{index}", "function": {"name": "library_search", "arguments": '{"limit":100}'},
    } for index in range(4)])
    exchange = [message.wire()]
    for call in message.tool_calls:
        result = asyncio.run(get_tool("library.search").invoke(session, call.function.arguments))
        assert result["ok"] is True
        assert result["data"]["has_more"] is True
        assert 0 < len(result["data"]["presets"]) < 25
        content = json.dumps(result, ensure_ascii=False)
        assert len(json.dumps(content, ensure_ascii=False).encode()) <= 40_000
        exchange.append({"role": "tool", "tool_call_id": call.id, "content": content})
    context = Conversation()
    context.append(exchange)
    assert len(context.messages()) == 5
    assert len(context.serialize().encode()) < 240_000


@pytest.mark.parametrize("large_field", ["summary", "code"])
def test_single_oversized_preset_returns_a_bounded_error(tmp_path, monkeypatch, large_field):
    """单条摘要或完整源码超过响应预算时明确报错，不返回空列表或截断代码。"""
    session = make_session(tmp_path, monkeypatch)
    stored = save_preset(session, "normal")
    oversized = stored.model_copy(update={"description" if large_field == "summary" else "code": "中" * 40_000})
    monkeypatch.setattr(session.catalog, "read_presets", lambda: [oversized])
    monkeypatch.setattr(session.catalog, "find_preset", lambda preset_id: oversized if preset_id == stored.preset_id else None)
    request = {} if large_field == "summary" else {"preset_id": stored.preset_id}
    result = asyncio.run(get_tool("library.search").invoke(session, request))
    assert result["ok"] is False
    assert result["error"]["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert len(json.dumps(result).encode()) < 1000


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
