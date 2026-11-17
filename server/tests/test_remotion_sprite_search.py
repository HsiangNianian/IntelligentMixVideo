"""Published Sprite search lifecycle and fail-closed matching; run with pytest tests/test_remotion_sprite_search.py."""

import hashlib

from pydantic import SecretStr

from server.remotion_templates import sprite_search


class FakeCollection:
    """Keep Chroma records in memory while exercising the real indexing and matching rules."""

    def __init__(self):
        """Start with an empty published catalog."""
        self.rows = {}
        self.distance = 0.04

    def get(self, **_kwargs):
        """Return IDs and metadata in Chroma's collection shape."""
        return {"ids": list(self.rows), "metadatas": [row[1] for row in self.rows.values()]}

    def upsert(self, *, ids, documents, embeddings, metadatas):
        """Replace one indexed publication by its stable ID."""
        self.rows[ids[0]] = (documents[0], metadatas[0], embeddings[0])

    def delete(self, *, ids):
        """Remove publications that MySQL no longer contains."""
        for identifier in ids:
            self.rows.pop(identifier, None)

    def query(self, **_kwargs):
        """Return the stored top candidate at the configured distance."""
        ids = list(self.rows)[:1]
        return {"ids": [ids], "metadatas": [[self.rows[key][1] for key in ids]],
                "distances": [[self.distance for _ in ids]]}


def test_index_uses_code_description_name_kind_and_updates_and_deletes(monkeypatch):
    """新增、覆盖、改名、删除只留下 MySQL 当前资产的向量和文字。"""
    code = "return <b>标题</b>"
    data = {"sprite-1": {"name": "霓虹标题", "kind": "SPRITE_KIND_TEXT", "tsx_code": code,
                         "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "parameters": []}}
    collection = FakeCollection()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_api_key=SecretStr("key"),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
    search = sprite_search.SpriteSearch(settings)
    monkeypatch.setattr(search, "_collection", lambda: collection)
    monkeypatch.setattr(search, "_chat", lambda *_: "蓝紫色发光标题，文字从下方上移并淡入，关键词持续呼吸发光。")
    monkeypatch.setattr(search, "_embed", lambda _: [1.0, 0.0])
    monkeypatch.setattr(sprite_search.sprite_store, "listing", lambda: list(data.items()))
    search.reconcile()
    assert "霓虹标题" in collection.rows["sprite-1"][0]
    assert "SPRITE_KIND_TEXT" in collection.rows["sprite-1"][0]
    assert "上移并淡入" in collection.rows["sprite-1"][0]
    old = collection.rows["sprite-1"][1]["revision"]
    data["sprite-1"]["name"] = "蓝紫光标题"
    search.reconcile()
    assert collection.rows["sprite-1"][1]["revision"] != old
    data.clear()
    search.reconcile()
    assert collection.rows == {}


def test_search_requires_threshold_current_revision_and_verifier(monkeypatch):
    """距离过大、旧索引或模型否决均不能让 Outer 复用，准确命中才返回 ID。"""
    data = {"name": "霓虹标题", "kind": "SPRITE_KIND_TEXT", "tsx_code": "code",
            "code_sha256": hashlib.sha256(b"code").hexdigest()}
    collection = FakeCollection()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_api_key=SecretStr("key"),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
    search = sprite_search.SpriteSearch(settings)
    monkeypatch.setattr(search, "_collection", lambda: collection)
    monkeypatch.setattr(search, "_embed", lambda _: [1.0, 0.0])
    monkeypatch.setattr(sprite_search.sprite_store, "get", lambda _: data)
    monkeypatch.setattr(search, "_chat", lambda *_: "MATCH")
    collection.upsert(ids=["sprite-1"], documents=["霓虹标题"], embeddings=[[1.0, 0.0]],
        metadatas=[{"revision": search._revision(data), "embedding_model": settings.embedding_model,
                    "description": "蓝紫发光上移标题"}])
    assert search.search("蓝紫发光上移标题").sprite_id == "sprite-1"
    collection.distance = 0.3
    assert search.search("蓝紫发光上移标题") is None
    collection.distance = 0.04
    data["name"] = "改名后"
    assert search.search("蓝紫发光上移标题") is None
    data["name"] = "霓虹标题"
    monkeypatch.setattr(search, "_chat", lambda *_: "NO")
    assert search.search("蓝紫发光上移标题") is None
