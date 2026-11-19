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
        self.distances = {}
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
        kind = (kwargs.get("where") or {}).get("kind")
        ids = [key for key, row in self.rows.items() if kind is None or row[1].get("kind") == kind][:self.query_limit]
        return {"ids": [ids], "metadatas": [[self.rows[key][1] for key in ids]],
                "documents": [[self.rows[key][0] for key in ids]],
                "distances": [[self.distances.get(key, self.distance) for key in ids]]}


def test_index_uses_code_description_name_kind_and_updates_and_deletes(monkeypatch, tmp_path):
    """新增、覆盖、改名、删除只留下 MySQL 当前资产的向量和文字。"""
    code = "return <b>标题</b>"
    data = {"sprite-1": {"name": "霓虹标题", "kind": "SPRITE_KIND_TEXT", "tsx_code": code,
                         "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "parameters": []}}
    collection = FakeCollection()
    (tmp_path / "modules.json").write_text("[]", encoding="utf-8")
    (tmp_path / "pytorch_model.bin").touch()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_model_path=str(tmp_path),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
    search = sprite_search.SpriteSearch(settings)
    assert search.index_enabled and search.enabled
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


def test_partial_index_failure_is_reported_and_retried(monkeypatch, tmp_path):
    """单条描述失败不阻断整轮，下一次对账重试缺失资产。"""
    code = "return <b>标题</b>"
    data = {"sprite-1": {"name": "霓虹标题", "kind": "SPRITE_KIND_TEXT", "tsx_code": code,
                         "code_sha256": hashlib.sha256(code.encode()).hexdigest()}}
    (tmp_path / "modules.json").write_text("[]", encoding="utf-8")
    (tmp_path / "pytorch_model.bin").touch()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_model_path=str(tmp_path),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
    search = sprite_search.SpriteSearch(settings)
    collection = FakeCollection()
    monkeypatch.setattr(search, "_collection", lambda: collection)
    monkeypatch.setattr(search, "_embed", lambda _: [1.0, 0.0])
    monkeypatch.setattr(sprite_search.sprite_store, "listing", lambda: list(data.items()))
    attempts = 0

    def describe(*_):
        """Fail the first model call and succeed on retry."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("model temporarily unavailable")
        return "蓝紫色发光标题，文字从下方上移并淡入，关键词持续呼吸发光。"

    monkeypatch.setattr(search, "_describe", describe)
    assert search.reconcile() is False
    assert collection.rows == {}
    assert search.reconcile() is True
    assert "sprite-1" in collection.rows


def test_search_returns_current_candidates_above_threshold_and_near_best(monkeypatch, tmp_path):
    """只保留前十条中分数至少 0.7、距有效第一名不超过 0.1 的当前资产。"""
    data = {f"sprite-{i}": {"name": f"标题 {i}", "kind": "SPRITE_KIND_TEXT", "tsx_code": "code",
            "code_sha256": hashlib.sha256(b"code").hexdigest()} for i in range(11)}
    collection = FakeCollection()
    (tmp_path / "modules.json").write_text("[]", encoding="utf-8")
    (tmp_path / "pytorch_model.bin").touch()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_model_path=str(tmp_path),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
    search = sprite_search.SpriteSearch(settings)
    monkeypatch.setattr(search, "_collection", lambda: collection)
    monkeypatch.setattr(search, "_embed", lambda _: [1.0, 0.0])
    monkeypatch.setattr(sprite_search.sprite_store, "get", lambda key: data.get(key))
    for key, item in data.items():
        collection.upsert(ids=[key], documents=[item["name"]], embeddings=[[1.0, 0.0]],
                          metadatas=[{"revision": search._revision(item), "kind": item["kind"],
                                      "embedding_model": settings.embedding_model,
                                      "description": f"{item['name']}发光上移"}])
    data["sprite-9"]["name"] = "已改名"
    collection.distances = {"sprite-0": 0.1, "sprite-1": 0.19, "sprite-2": 0.2,
                            "sprite-3": 0.21, "sprite-4": 0.3, "sprite-9": 0.05}
    collection.distance = 0.8
    monkeypatch.setattr(search, "_api", lambda *_: (_ for _ in ()).throw(AssertionError("no rerank API call")))
    matches = search.search("发光上移标题")
    assert collection.query_limit == 10
    assert [item.sprite_id for item in matches] == ["sprite-0", "sprite-1", "sprite-2"]
    assert [round(item.score, 2) for item in matches] == [0.9, 0.81, 0.8]
    assert [item.distance for item in matches] == [0.1, 0.19, 0.2]
    collection.distances = {"sprite-0": 0.3, "sprite-1": 0.31}
    assert [item.sprite_id for item in search.search("发光上移标题")] == ["sprite-0"]


def test_search_rejects_invalid_vector_distance_and_empty_catalog(monkeypatch, tmp_path):
    """空索引无候选，非法余弦距离不能流入 Outer。"""
    import pytest

    collection = FakeCollection()
    (tmp_path / "modules.json").write_text("[]", encoding="utf-8")
    (tmp_path / "pytorch_model.bin").touch()
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_model_path=str(tmp_path),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
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
    collection.distances["sprite-1"] = float("nan")
    with pytest.raises(ValueError, match="invalid cosine distance"):
        search.search("标题")


def test_local_embedding_uses_the_model_path_and_1024_normalized_values(monkeypatch):
    """索引和查询使用同一个本地模型，拒绝错误维度且不调用向量 API。"""
    import pytest

    calls = []

    class FakeModel:
        """Return a known vector without loading model weights or contacting a service."""

        def encode(self, text):
            """Record the input and return a non-unit 1024-dimensional vector."""
            calls.append(text)
            return [2.0] + [0.0] * 1023

    paths = []
    monkeypatch.setattr(sprite_search, "_embedding_model", lambda path: paths.append(path) or FakeModel())
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_model_path="/models/bge-m3")
    search = sprite_search.SpriteSearch(settings)
    monkeypatch.setattr(search, "_api", lambda *_: (_ for _ in ()).throw(AssertionError("embedding API called")))
    assert search._embed("资产描述") == [1.0] + [0.0] * 1023
    assert search._embed("用户问题") == [1.0] + [0.0] * 1023
    assert paths == ["/models/bge-m3"] * 2 and calls == ["资产描述", "用户问题"]
    monkeypatch.setattr(FakeModel, "encode", lambda *_: [1.0])
    with pytest.raises(ValueError, match="1024 dimensions"):
        search._embed("错误维度")


def test_search_stays_disabled_until_model_files_exist(tmp_path):
    """模型尚未下载完成时不向 Outer 提供检索工具。"""
    settings = sprite_search.SpriteSearchSettings(_env_file=None, embedding_model_path=str(tmp_path),
        description_api_key=SecretStr("key"), description_model="model", description_base_url="http://model/v1")
    search = sprite_search.SpriteSearch(settings)
    assert not search.enabled
    (tmp_path / "modules.json").write_text("[]", encoding="utf-8")
    assert not search.enabled
    (tmp_path / "pytorch_model.bin").touch()
    assert search.enabled
