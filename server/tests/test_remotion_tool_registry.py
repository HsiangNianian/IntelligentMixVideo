"""Registry consistency and tool-name regression tests for the Remotion agent.

Covers: every registered tool resolves by dotted and wire name, inspection accepts both,
visibility follows `implemented`, generation-starting tools are declared on the registry,
unknown names fail with a correctable message, the search/modify tools behave, and the
Plan contract errors seen in real task logs are actionable.
Run: `uv run --locked pytest tests/test_remotion_tool_registry.py -v`.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from server.remotion_templates.planning import PlanAction
from server.remotion_templates.tools.catalog import available, resolve
from server.remotion_templates.tools.contracts import (
    PresetModifyInput,
    PresetSearchInput,
)
from server.remotion_templates.tools.registry import (
    ToolFault,
    get_tool,
    registered_tools,
    wire_name,
)
from server.remotion_templates.tools.session import ToolSession


def make_session(tmp_path, monkeypatch):
    """Build a ToolSession on a temporary catalog with the MySQL backend disabled."""
    from sqlalchemy.exc import SQLAlchemyError

    from server.remotion_templates.tools import catalog_store

    def unavailable(self):
        raise SQLAlchemyError("no database in tests")

    monkeypatch.setattr(catalog_store.CatalogStore, "_engine", unavailable)
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
        "preset.create", "sprite.compose", "sprite.create",
    }


@pytest.mark.parametrize("bad", ["preset", "sprite", "Preset_Create", ""])
def test_unknown_tool_names_fail_with_list_and_suggestions(bad):
    """Names seen in real logs ('preset', 'sprite') are rejected but explained, never guessed."""
    with pytest.raises(ToolFault) as caught:
        get_tool(bad)
    error = caught.value.error
    assert error.code == "TOOL_NOT_FOUND"
    assert "Available tools:" in error.message and "preset.create" in error.message


def test_contract_only_tools_say_they_are_not_callable():
    """只有契约的工具仍可被 inspect，但描述第一句就声明当前不可调用，实现了的工具不受影响。"""
    contract_only = [item for item in registered_tools() if not item.implemented]
    assert contract_only, "the registry must still hold deferred contracts"
    for item in contract_only:
        assert item.describe()["description"].startswith("[Not callable in this build"), item.name
    for item in registered_tools():
        if item.implemented:
            assert not item.describe()["description"].startswith("[Not callable"), item.name


def test_inspect_unknown_name_lists_callable_tools_apart_from_contract_only_ones():
    """模型在真实日志里曾被「Available tools」带去调用 validate.code；报错必须把可调用与仅有契约的分开列。"""
    result = asyncio.run(get_tool("tools.inspect").invoke(None, {"tool_name": "preset"}))
    assert result["ok"] is False and result["error"]["code"] == "TOOL_NOT_FOUND"
    available, _, contract_only = result["error"]["message"].partition(". Contract-only, not callable: ")
    callable_names = set(available.split("Available tools: ")[1].split(", "))
    reference_names = set(contract_only.split(", "))
    implemented = {item.name for item in registered_tools() if item.implemented}
    assert implemented <= callable_names
    assert reference_names == {item.name for item in registered_tools() if not item.implemented}
    assert callable_names.isdisjoint(reference_names)


def test_empty_tool_window_never_falls_back_to_the_registry():
    """空权限窗口必须拒绝已注册工具，且不能建议窗口外的工具。"""
    with pytest.raises(ToolFault) as caught:
        get_tool("preset_create", [])
    assert caught.value.error.message.endswith("Available tools: none")
    assert "Closest:" not in caught.value.error.message


def test_inspect_accepts_wire_name():
    """tools.inspect no longer rejects the underscore spelling the model sees on the wire."""
    session = SimpleNamespace()
    result = asyncio.run(get_tool("tools_inspect").invoke(session, {"tool_name": "preset_create"}))
    assert result["ok"] and result["data"]["tool_name"] == "preset.create"


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


def test_search_orders_merged_database_and_local_presets_by_creation_time(tmp_path, monkeypatch, template_db):
    """数据库 UUID 顺序与本地追加顺序均不能替代创建时间，混合后排序再截取。"""
    from server.remotion_templates.tools import catalog_store

    session = make_session(tmp_path, monkeypatch)
    local = save_preset(session, "Title local", preset_id="b" * 32, created_at="2026-10-08T12:00:00+00:00")
    save_preset(session, "Title oldest", preset_id="e" * 32, created_at="2026-10-07T00:00:00+00:00")
    catalog_store.metadata.create_all(template_db)
    monkeypatch.setattr(catalog_store.CatalogStore, "_engine", lambda self: template_db)
    save_preset(session, "Title older", preset_id="f" * 32, created_at="2026-10-08T00:00:00+00:00")
    newest = save_preset(session, "Title newest", preset_id="0" * 32, created_at="2026-10-09T00:00:00+00:00")
    result = session.search_presets(PresetSearchInput(query="title", limit=2))
    assert [item.preset_id for item in result.presets] == [newest.preset_id, local.preset_id]
    assert session.search_presets(PresetSearchInput(limit=1)).presets[0].preset_id == newest.preset_id
    with pytest.raises(ToolFault) as missing:
        session.modify_preset(PresetModifyInput(preset_id="missing", changes={"description": "copy"}))
    message = missing.value.error.message
    assert message.index(newest.preset_id) < message.index(local.preset_id)


def test_modify_returns_a_copy_without_changing_the_original(tmp_path, monkeypatch):
    """Modify replaces whole fields in a draft, links the source, and rejects empty/unknown input."""
    session = make_session(tmp_path, monkeypatch)
    record = save_preset(session, "原描述")
    draft = session.modify_preset(
        PresetModifyInput(preset_id=record.preset_id, changes={"description": "新描述"})
    ).preset
    assert draft.description == "新描述" and draft.code == record.code
    assert draft.source_preset_id == record.preset_id
    assert session.catalog.find_preset(record.preset_id).description == "原描述"
    with pytest.raises(ToolFault) as empty:
        session.modify_preset(PresetModifyInput(preset_id=record.preset_id, changes={}))
    assert empty.value.error.code == "INVALID_ARGUMENT"
    with pytest.raises(ToolFault) as missing:
        session.modify_preset(PresetModifyInput(preset_id="imv:preset/0", changes={"description": "x"}))
    assert missing.value.error.code == "PRESET_NOT_FOUND" and record.preset_id in missing.value.error.message


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


def test_search_contract_version_is_discoverable_before_calling():
    """调用方可先检查版本和 Schema，区分旧 matches 契约与已实现的 v2 列表。"""
    result = asyncio.run(get_tool("tools.inspect").invoke(None, {"tool_name": "preset_search"}))
    descriptor = result["data"]
    assert descriptor["contract_version"] == 2
    assert "presets" in json.dumps(descriptor["output_schema"])
    assert "matches" not in json.dumps(descriptor["output_schema"])
    assert get_tool("preset.create").describe()["contract_version"] == 1


@pytest.mark.parametrize("tool_name,method,arguments", [
    ("preset.search", "search_presets", {}),
    ("preset.modify", "modify_preset", {"preset_id": "preset", "changes": {"description": "new"}}),
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
    result = asyncio.run(get_tool("preset.search").invoke(None, {"limit": 101}))
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
        "id": f"search-{index}", "function": {"name": "preset_search", "arguments": '{"limit":100}'},
    } for index in range(4)])
    exchange = [message.wire()]
    for call in message.tool_calls:
        result = asyncio.run(get_tool("preset.search").invoke(session, call.function.arguments))
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
    request = {} if large_field == "summary" else {"preset_id": stored.preset_id}
    result = asyncio.run(get_tool("preset.search").invoke(session, request))
    assert result["ok"] is False
    assert result["error"]["code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert len(json.dumps(result).encode()) < 1000


@pytest.mark.parametrize("changes", [
    {"default_parameters": {"text": 123}},
    {"default_parameters": {"undeclared": "x"}},
    {"parameter_schema": {"type": "not-a-type"}},
    {"parameter_schema": {"type": "object", "$ref": "https://example.invalid/schema"}},
])
def test_modify_rejects_invalid_merged_contracts_without_writing(tmp_path, monkeypatch, changes):
    """修改草稿必须保证 Schema 与默认值一致；拒绝外部引用且原记录不变。"""
    session = make_session(tmp_path, monkeypatch)
    stored = save_preset(session, "title", {"text": {"type": "string"}})
    result = asyncio.run(get_tool("preset.modify").invoke(session, {
        "preset_id": stored.preset_id, "changes": changes,
    }))
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["error"]["field"] == "/changes"
    assert result["error"]["details"]["diagnostics"]
    assert session.catalog.read_presets() == [stored]


def test_modify_can_replace_schema_and_defaults_together(tmp_path, monkeypatch):
    """一致的整体替换仍可返回草稿，且不隐式写库或执行渲染。"""
    session = make_session(tmp_path, monkeypatch)
    stored = save_preset(session, "title", {"text": {"type": "string"}})
    schema = {"type": "object", "properties": {"size": {"type": "integer"}}, "required": ["size"], "additionalProperties": False}
    result = session.modify_preset(PresetModifyInput(preset_id=stored.preset_id, changes={
        "parameter_schema": schema, "default_parameters": {"size": 42},
    }))
    assert result.preset.parameter_schema == schema
    assert result.preset.default_parameters == {"size": 42}
    assert session.catalog.read_presets() == [stored]
