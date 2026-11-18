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
        self.query_limit = None

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

    def query(self, **kwargs):
        """Return stored candidates and keep the requested recall size visible to tests."""
        self.query_limit = kwargs["n_results"]
        ids = list(self.rows)[:self.query_limit]
        return {"ids": [ids], "metadatas": [[self.rows[key][1] for key in ids]],
                "documents": [[self.rows[key][0] for key in ids]],
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
    assert search.index_enabled and not search.enabled
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


def test_search_reranks_ten_and_returns_only_close_high_scoring_current_candidates(monkeypatch):
    """召回十条、查 MySQL 后重排，仅将高分且接近最高分的候选交给 Outer。"""
    data = {f"sprite-{i}": {"name": f"标题 {i}", "kind": "SPRITE_KIND_TEXT", "tsx_code": "code",
            "code_sha256": hashlib.sha256(b"code").hexdigest()} for i in range(11)}
    collection = FakeCollection()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_api_key=SecretStr("key"),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1",
        rerank_url="https://rerank.example/api")
    search = sprite_search.SpriteSearch(settings)
    monkeypatch.setattr(search, "_collection", lambda: collection)
    monkeypatch.setattr(search, "_embed", lambda _: [1.0, 0.0])
    monkeypatch.setattr(sprite_search.sprite_store, "get", lambda key: data.get(key))
    for key, item in data.items():
        collection.upsert(ids=[key], documents=[item["name"]], embeddings=[[1.0, 0.0]],
                          metadatas=[{"revision": search._revision(item), "embedding_model": settings.embedding_model,
                                      "description": f"{item['name']}发光上移"}])
    data["sprite-9"]["name"] = "已改名"
    calls = []

    def rerank(base, key, path, payload):
        """Return out-of-order scores using the provider's source indices."""
        calls.append((base, key.get_secret_value(), path, payload))
        assert len(payload["input"]["documents"]) == 9
        return {"output": {"results": [
            {"index": 3, "relevance_score": 0.75}, {"index": 0, "relevance_score": 0.91},
            {"index": 4, "relevance_score": 0.71}, {"index": 2, "relevance_score": 0.6},
        ]}}

    monkeypatch.setattr(search, "_api", rerank)
    matches = search.search("发光上移标题")
    assert collection.query_limit == 10
    assert [item.sprite_id for item in matches] == ["sprite-0", "sprite-3"]
    assert [item.score for item in matches] == [0.91, 0.75]
    assert calls[0][1] == "key"
    assert calls[0][3]["model"] == settings.rerank_model


def test_search_rejects_invalid_rerank_response_and_empty_catalog(monkeypatch):
    """空索引无需远程排序，越界或重复索引及无效分数不能流入 Outer。"""
    import pytest

    collection = FakeCollection()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_api_key=SecretStr("key"),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1",
        rerank_url="https://rerank.example/api")
    search = sprite_search.SpriteSearch(settings)
    monkeypatch.setattr(search, "_collection", lambda: collection)
    monkeypatch.setattr(search, "_embed", lambda _: [1.0, 0.0])
    assert search.search("标题") == []
    data = {"name": "标题", "kind": "SPRITE_KIND_TEXT", "tsx_code": "code",
            "code_sha256": hashlib.sha256(b"code").hexdigest()}
    monkeypatch.setattr(sprite_search.sprite_store, "get", lambda _: data)
    collection.upsert(ids=["sprite-1"], documents=["标题"], embeddings=[[1.0, 0.0]],
                      metadatas=[{"revision": search._revision(data), "embedding_model": settings.embedding_model,
                                  "description": "白色标题"}])
    monkeypatch.setattr(search, "_api", lambda *_: {"output": {"results": [{"index": 5, "relevance_score": 0.99}]}})
    with pytest.raises(ValueError, match="invalid scores or indices"):
        search.search("标题")
