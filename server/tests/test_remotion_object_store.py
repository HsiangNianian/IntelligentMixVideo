"""Remotion 对象存储：本地目录实现的读写删、键越界拒绝、按 ZOS 配置选择实现，以及 ZOS 缺失对象的错误映射。

不连接真实 ZOS：S3 客户端由替身承担。在 server/ 目录执行
`uv run --locked pytest tests/test_remotion_object_store.py -v`。
"""

from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from server.remotion_templates.object_store import LocalObjects, ZosObjects, load_objects
from server.video_composition import zos

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
@pytest.mark.parametrize("code,expected", [("404", FileNotFoundError), ("NoSuchKey", FileNotFoundError), ("AccessDenied", ClientError)])
def test_zos_fetch_maps_missing_objects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, expected: type) -> None:
    """替身客户端按错误码抛 ClientError；404/NoSuchKey 变为 FileNotFoundError，其他错误原样上抛，客户端均被关闭。"""
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
