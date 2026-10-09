"""Registry consistency and tool-name regression tests for the Remotion agent.

Covers: every registered tool resolves by dotted and wire name, inspection accepts both,
visibility follows `implemented`, generation-starting tools are declared on the registry,
unknown names fail with a correctable message, the search/modify tools behave, and the
Plan contract errors seen in real task logs are actionable.
Run: `uv run --locked pytest tests/test_remotion_tool_registry.py -v`.
"""

import asyncio
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


def save_preset(session, description, properties=None):
    """Store one valid Preset record directly through the catalog."""
    from datetime import UTC, datetime
    from uuid import uuid4

    from server.remotion_templates.tools.contracts import PresetRecord

    record = PresetRecord(
        preset_id=uuid4().hex,
        created_at=datetime.now(UTC).isoformat(),
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
    assert "Registered tools:" in error.message and "preset.create" in error.message


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
