"""两类预览蒙版的 HTTP 范围读取回归；uv run --locked pytest tests/test_preview_mask_ranges.py。

使用真实路由与 CORS，替换编码器为固定字节，不访问生产服务或生成媒体文件。
"""

import pytest

from server.template import router


@pytest.fixture(params=[
    ("preview-mask", "mask_video", dict(width=100, height=200, box_width=100, box_height=200, radius=20, seconds=11)),
    ("preview-circle-mask", "circle_mask_video", dict(width=100, height=200, x=0.5, y=0.5, radius=0.2, feather=0, seconds=11)),
], ids=["rounded", "circle"])
def mask_resource(request, monkeypatch):
    """两个实际接口共用十字节资源，便于精确核对范围边界及响应正文。"""
    path, encoder, params = request.param

    def encode(**params):
        """隔离 FFmpeg；HTTP 层必须只返回请求所需的原始字节。"""
        return b"0123456789"

    monkeypatch.setattr(router, encoder, encode)
    return f"/template/{path}", params


@pytest.mark.parametrize("byte_range,status,content,content_range", [
    (None, 200, b"0123456789", None),
    ("bytes=1-2", 206, b"12", "bytes 1-2/10"),
    ("bytes=0-0", 206, b"0", "bytes 0-0/10"),
    ("bytes=3-", 206, b"3456789", "bytes 3-9/10"),
    ("bytes=-3", 206, b"789", "bytes 7-9/10"),
    ("bytes=8-99", 206, b"89", "bytes 8-9/10"),
    ("bytes=-99", 206, b"0123456789", "bytes 0-9/10"),
    ("bytes=10-", 416, b"", "bytes */10"),
    ("bytes=20-30", 416, b"", "bytes */10"),
    ("bytes=-0", 416, b"", "bytes */10"),
    ("bytes=5-2", 200, b"0123456789", None),
    ("bytes=a-b", 200, b"0123456789", None),
    ("bytes=-", 200, b"0123456789", None),
    ("bytes=0-1,4-5", 200, b"0123456789", None),
    ("items=0-1", 200, b"0123456789", None),
])
def test_mask_byte_ranges(client, mask_resource, byte_range, status, content, content_range):
    """WebKit 请求范围时返回精确的 206；越界 416，其余完整读取，所有响应均可跨域。"""
    path, params = mask_resource
    headers = {"Origin": "tauri://localhost"}
    if byte_range is not None:
        headers["Range"] = byte_range
    response = client.get(path, params=params, headers=headers)
    assert response.status_code == status
    assert response.content == content
    assert response.headers.get("content-range") == content_range
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    if status != 416:
        assert response.headers["content-type"] == "video/mp4"
        assert response.headers["cache-control"] == "public, max-age=86400"


def test_mask_if_range_returns_full_content(client, mask_resource):
    """资源没有 ETag/Last-Modified 可供匹配时，If-Range 必须回完整内容，避免拼接旧缓存。"""
    path, params = mask_resource
    response = client.get(path, params=params, headers={"Range": "bytes=1-2", "If-Range": '"old-mask"'})
    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert "content-range" not in response.headers
