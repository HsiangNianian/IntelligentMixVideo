"""Remotion 对象存储：本地目录实现的读写删、键越界拒绝、按 ZOS 配置选择实现，ZOS 错误映射，以及存储不可达时路由的 503。

不连接真实 ZOS：S3 客户端由替身承担。在 server/ 目录执行
`uv run --locked pytest tests/test_remotion_object_store.py -v`。
"""

import shutil
from io import BytesIO
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from server.remotion_templates import object_store
from server.remotion_templates.media import save_image
from server.remotion_templates.models import GenerateTemplateRequest
from server.remotion_templates.object_store import LocalObjects, ObjectStoreError, ZosObjects, load_objects
from server.remotion_templates.routes import router
from server.remotion_templates.runtime import Runtime
from server.remotion_templates.settings import Settings
from server.remotion_templates.store import Store
from server.video_composition import zos

from .conftest import DEVICE, DEVICE_HEADERS

ZOS_ENV = {
    "ZOS_API_ENDPOINT": "https://hangzhou7.zos.ctyun.cn",
    "ZOS_BUCKET": "archives",
    "ZOS_ACCESS_KEY_ID": "test-zos-id",
    "ZOS_SECRET_ACCESS_KEY": "test-zos-secret",
    "ZOS_WEB_URL": "https://archives.hangzhou7.zos.ctyun.cn",
}


# 场景：本地实现按键保存、读回、按前缀删除，同一键再次写入替换旧内容且不留临时文件。
def test_local_objects_round_trip_and_prefix_delete(tmp_path: Path) -> None:
    """写入两个同前缀对象与一个其他对象；读回内容一致，删除前缀只去掉前两个，缺失对象读为 FileNotFoundError。"""
    objects = LocalObjects(tmp_path / "objects")
    source = tmp_path / "source.bin"
    for key, payload in (("imv/remotion/versions/a/x.js", b"one"), ("imv/remotion/versions/a/y.js", b"two"),
                         ("imv/remotion/assets/b.png", b"png")):
        source.write_bytes(payload)
        objects.put(key, source, "application/octet-stream")
    source.write_bytes(b"replaced")
    objects.put("imv/remotion/versions/a/x.js", source, "text/javascript")
    copy = tmp_path / "copy.bin"
    objects.fetch("imv/remotion/versions/a/x.js", copy)
    assert copy.read_bytes() == b"replaced"
    assert not [path for path in (tmp_path / "objects").rglob("*.tmp")]
    objects.delete_prefix("imv/remotion/versions/a/")
    with pytest.raises(FileNotFoundError):
        objects.fetch("imv/remotion/versions/a/y.js", copy)
    objects.fetch("imv/remotion/assets/b.png", copy)
    assert copy.read_bytes() == b"png"
    objects.delete_prefix("imv/remotion/assets/b.png")
    objects.delete_prefix("imv/remotion/missing/")
    with pytest.raises(FileNotFoundError):
        objects.fetch("imv/remotion/assets/b.png", copy)


# 场景：本地实现拒绝跳出存储目录的键，避免任何读写删除作用到数据目录之外。
@pytest.mark.parametrize("key", ["../outside.bin", "imv/../../outside.bin", "/etc/passwd"])
def test_local_objects_refuse_keys_outside_the_directory(tmp_path: Path, key: str) -> None:
    """put、fetch、delete_prefix 对越界键一律报 ValueError，目录外文件保持原样。"""
    objects = LocalObjects(tmp_path / "objects")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep")
    with pytest.raises(ValueError):
        objects.put(key, outside, "application/octet-stream")
    with pytest.raises(ValueError):
        objects.fetch(key, tmp_path / "copy.bin")
    with pytest.raises(ValueError):
        objects.delete_prefix(key)
    assert outside.read_bytes() == b"keep"


# 场景：只由 ZOS_BUCKET 决定是否使用 ZOS：桶为空或未设置时退回数据目录下的 objects/（.env.example 自带默认地址、空桶），桶非空且配置完整时使用 ZOS。
@pytest.mark.parametrize("env", [{}, {**ZOS_ENV, "ZOS_BUCKET": ""}, {"ZOS_API_ENDPOINT": ZOS_ENV["ZOS_API_ENDPOINT"]}])
def test_load_objects_without_a_bucket_uses_the_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict) -> None:
    """没有桶时无论其他 ZOS_* 是否填写，都返回位于数据目录的 LocalObjects。"""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    local = load_objects(tmp_path)
    assert isinstance(local, LocalObjects) and local.root == tmp_path / "objects"


# 场景：桶非空且其余必填项齐全时使用 ZOS。
def test_load_objects_with_a_bucket_uses_zos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """设置全部必填项后返回 ZosObjects，并带上配置的桶。"""
    for key, value in ZOS_ENV.items():
        monkeypatch.setenv(key, value)
    configured = load_objects(tmp_path)
    assert isinstance(configured, ZosObjects) and configured.settings.zos_bucket == "archives"


# 场景：已填写桶但其余配置缺失或无效时明确报错，不能悄悄把文件写到后端本地。
@pytest.mark.parametrize("env", [
    {"ZOS_BUCKET": "archives"},
    {**ZOS_ENV, "ZOS_WEB_URL": "https://archives.example/?signed=1"},
])
def test_load_objects_rejects_a_bucket_with_invalid_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict) -> None:
    """桶非空时缺少必填项或地址带查询参数都抛 ValueError。"""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="ZOS"):
        load_objects(tmp_path)


# 场景：ZOS 上不存在的对象读为 FileNotFoundError，供调用方区分「对象丢失」与其他存储故障。
@pytest.mark.parametrize("code,expected", [("404", FileNotFoundError), ("NoSuchKey", FileNotFoundError), ("AccessDenied", ObjectStoreError)])
def test_zos_fetch_maps_missing_objects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, expected: type) -> None:
    """替身客户端按错误码抛 ClientError；404/NoSuchKey 变为 FileNotFoundError，其他错误变为 ObjectStoreError，客户端均被关闭。"""
    for key, value in ZOS_ENV.items():
        monkeypatch.setenv(key, value)
    closed = []

    class Storage:
        """只实现下载与关闭的 S3 替身。"""

        def download_file(self, bucket, key, filename):
            """核对桶与键后按用例错误码失败。"""
            assert bucket == "archives" and key == "imv/remotion/assets/a.png"
            raise ClientError({"Error": {"Code": code}}, "GetObject")

        def close(self):
            """记录客户端被释放。"""
            closed.append(True)

    monkeypatch.setattr(zos.boto3, "client", lambda *args, **kwargs: Storage())
    objects = load_objects(tmp_path)
    with pytest.raises(expected):
        objects.fetch("imv/remotion/assets/a.png", tmp_path / "copy.png")
    assert closed == [True]


# 场景：ZOS 网络或权限故障统一成 ObjectStoreError（OSError），路由据此返回可重试的 503 而不是 500 或 422。
def test_zos_failures_become_object_store_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上传的匿名读取校验失败、删除与下载的连接错误都抛 ObjectStoreError；写入、列举与删除各用一次客户端。"""
    for key, value in ZOS_ENV.items():
        monkeypatch.setenv(key, value)
    source = tmp_path / "object.bin"
    source.write_bytes(b"x")

    class Storage:
        """连接失败的 S3 替身。"""

        def download_file(self, bucket, key, filename):
            """模拟网络不可达。"""
            raise EndpointConnectionError(endpoint_url="https://zos.example")

        def get_paginator(self, name):
            """列举时同样不可达。"""
            raise EndpointConnectionError(endpoint_url="https://zos.example")

        def close(self):
            """无需释放资源。"""

    monkeypatch.setattr(zos.boto3, "client", lambda *args, **kwargs: Storage())

    def unreadable(*args, **kwargs):
        """模拟上传后匿名读取校验失败。"""
        raise ValueError("ZOS 对象尚不能匿名读取")

    monkeypatch.setattr(object_store, "upload_public", unreadable)
    objects = load_objects(tmp_path)
    for call in (
        lambda: objects.put("imv/remotion/assets/a.png", source, "image/png"),
        lambda: objects.fetch("imv/remotion/assets/a.png", tmp_path / "copy.png"),
        lambda: objects.delete_prefix("imv/remotion/versions/v/"),
    ):
        with pytest.raises(ObjectStoreError):
            call()


class UnavailableObjects(LocalObjects):
    """本地对象存储替身：写入、读取与删除都像 ZOS 不可达那样失败。"""

    def put(self, key: str, path: Path, content_type: str) -> None:
        """模拟上传失败。"""
        raise ObjectStoreError("unreachable")

    def fetch(self, key: str, destination: Path) -> None:
        """模拟下载失败。"""
        raise ObjectStoreError("unreachable")

    def delete_prefix(self, prefix: str) -> None:
        """模拟删除失败。"""
        raise ObjectStoreError("unreachable")


# 场景：对象存储不可达时，上传参考图、读取参考图与删除会话都回答可重试的 503。
def test_routes_answer_503_while_the_object_store_is_unreachable(tmp_path: Path, template_db) -> None:
    """上传不再被误报为图片无效（422），读取与删除不再是 500；删除保持待重试。"""
    store = Store(tmp_path / "data")
    store.initialize()
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    asset = save_image(buffer.getvalue(), store, Settings(_env_file=None), DEVICE)
    work, _ = store.create(GenerateTemplateRequest(description="对象存储", images=[{"asset_id": asset.id}]), DEVICE)
    shutil.rmtree(store.root / "cache", ignore_errors=True)
    store.objects = UnavailableObjects(store.root / "objects")
    app = FastAPI()
    app.include_router(router)
    app.state.runtime = Runtime(store, None, Settings(_env_file=None))
    with TestClient(app) as client:
        uploaded = client.post("/assets", headers=DEVICE_HEADERS, files={"file": ("a.png", buffer.getvalue(), "image/png")})
        assert uploaded.status_code == 503
        assert client.get(f"/assets/{asset.id}").status_code == 503
        assert client.delete(f"/works/{work.id}", headers=DEVICE_HEADERS).status_code == 503
    assert store.pending_deletions() == [work.id]
