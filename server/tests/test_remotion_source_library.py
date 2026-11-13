"""Shared complete-source reuse, separated from published assets and private works; offline transaction tests.

Run: uv run --locked pytest tests/test_remotion_source_library.py. Reuses deterministic sealed-publication fixtures.
"""

import asyncio
import json

import pytest
from sqlalchemy import func, inspect, select, update

from server.remotion_templates import deletion
from server.remotion_templates.source_library import archive, read, search
from server.remotion_templates.tables import effect_sources
from server.remotion_templates.tools.contracts import SourceReadInput, SourceSearchInput
from server.remotion_templates.tools.registry import get_tool
from server.remotion_templates.tools.workspace import file_page
from .test_remotion_publication import accepted_version, harness, sprite  # noqa: F401 -- shared pytest fixtures


def test_generated_source_is_shared_before_asset_publication_and_survives_work_deletion(harness, sprite):
    """生成成功即自动入库，重复采纳去重；删除私有作品不删除可复用源码，也不提前创建已发布资产。"""
    store, work, version = accepted_version(harness, sprite)
    with store.transaction() as db:
        first = archive(db, version)
        assert archive(db, version) == first
        assert db.execute(select(func.count()).select_from(effect_sources)).scalar_one() == 1
        assert not inspect(db).has_table("remotion_presets")
        assert not inspect(db).has_table("remotion_sprites")
    result = search(SourceSearchInput(query="标题"))
    assert [source.source_id for source in result.sources] == [first]
    code = read(SourceReadInput(source_id=first, path="Effect.tsx"))
    assert code.content == version.candidate.tsx_code
    manifest = json.loads(read(SourceReadInput(source_id=first, path="effect.json")).content)
    assert manifest["contract"]["default_parameters"] == version.candidate.default_config
    store.begin_deletion(work.id)
    deletion.finish(store, work.id)
    assert read(SourceReadInput(source_id=first, path="Effect.tsx")) == code
    assert search(SourceSearchInput(query="不存在")).sources == []
    with store.transaction() as db:
        db.execute(update(effect_sources).where(effect_sources.c.id == first).values(description="金色 Glow"))
    assert search(SourceSearchInput(query="GLOW")).sources[0].source_id == first


def test_library_tools_use_sources_and_bound_each_page(harness, sprite):
    """搜索只给摘要，分段读取完整代码时不截断丢字；旧 preset_id 输入被明确拒绝。"""
    store, _work, version = accepted_version(harness, sprite)
    result = asyncio.run(get_tool("library.search").invoke(None, {"query": "标题"}))
    identifier = result["data"]["sources"][0]["source_id"]
    assert "code" not in json.dumps(result) and "work_id" not in json.dumps(result)
    found = asyncio.run(get_tool("library.read").invoke(None, {"source_id": identifier, "path": "Effect.tsx"}))
    assert found["data"]["content"] == version.candidate.tsx_code
    assert not asyncio.run(get_tool("library.search").invoke(None, {"preset_id": "retired"}))["ok"]
    assert not asyncio.run(get_tool("library.read").invoke(None, {"source_id": identifier, "path": "../../secret"}))["ok"]
    text = '中文\\"\n' * 20_000
    offset, pieces = 0, []
    while offset < len(text):
        page = file_page("Effect.tsx", text, offset)
        assert len(json.dumps(page.content, ensure_ascii=False).encode()) <= 16_000
        pieces.append(page.content)
        offset += len(page.content)
    assert "".join(pieces) == text
