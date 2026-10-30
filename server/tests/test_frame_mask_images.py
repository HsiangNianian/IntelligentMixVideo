"""图片蒙版的上传、持久引用与真实 PNG 回归；运行 uv run --locked pytest tests/test_frame_mask_images.py。"""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

from fastapi import HTTPException
import httpx
from PIL import Image
import pytest

from server.template import images
from .template_wire import detail_template, post_template, saved_template


def picture(size=(90, 160), color=(200, 80, 40, 128), format="PNG", **kwargs):
    """在内存生成真实图片，默认含半透明像素。"""
    output = BytesIO()
    image = Image.new("RGB" if format == "JPEG" else "RGBA", size, color[:3] if format == "JPEG" else color)
    image.save(output, format, **kwargs)
    return output.getvalue()


@pytest.fixture
def image_store(monkeypatch):
    """只替换外部对象存储，上传仍执行真实解码与 PNG 编码；已上传内容不随模板更新删除。"""
    objects = {}
    monkeypatch.setattr(images, "ZosSettings", lambda: None)

    def upload(files, settings, timeout):
        """按实际上传路径保存字节，供预览与合成读取。"""
        path, key, mime = files[0]
        assert mime == "image/png"
        objects[key.rsplit("/", 1)[1][:-4]] = path.read_bytes()
        return [f"https://storage.test/{key}"]

    def read(image_id):
        """丢失图片明确返回 404，不代替成颜色。"""
        if image_id not in objects:
            raise HTTPException(404, "蒙版图片不存在，请重新上传")
        return objects[image_id]

    monkeypatch.setattr(images, "upload_public", upload)
    monkeypatch.setattr(images, "read_image", read)
    return objects


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_upload_still_image_and_read(client, image_store, format):
    """三个静态格式统一返回哈希引用与可读取 PNG，保留尺寸和可用透明度。"""
    response = client.post("/template/frame-mask-images", files={"file": ("image", picture(format=format))})
    assert response.status_code == 201, response.text
    image_id = response.json()["imageId"]
    assert sha256(image_store[image_id]).hexdigest() == image_id
    preview = client.get(f"/template/frame-mask-images/{image_id}")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    assert "immutable" in preview.headers["cache-control"]
    with Image.open(BytesIO(preview.content)) as image:
        assert image.size == (90, 160) and image.mode == "RGBA"
        assert image.getpixel((20, 20))[3] == (255 if format == "JPEG" else 128)


def test_upload_limits_animation_invalid_and_storage_failure(client, image_store, monkeypatch):
    """超限、损坏和动图不写存储；存储故障返回 503 而不是成功引用。"""
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 512)
    too_large = client.post("/template/frame-mask-images", files={"file": ("large.png", b"x" * 513)})
    assert too_large.status_code == 413
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 100)
    too_many = client.post("/template/frame-mask-images", files={"file": ("large.png", picture((11, 10)))})
    assert too_many.status_code == 422
    for payload in (b"broken", picture((3, 3), format="GIF"), picture((3, 3), save_all=True,
                    append_images=[Image.new("RGBA", (3, 3), "blue")], duration=100)):
        assert client.post("/template/frame-mask-images", files={"file": ("image.png", payload)}).status_code == 422
    assert not image_store

    def fail(*args):
        """模拟外部上传故障。"""
        raise OSError("storage unavailable")

    monkeypatch.setattr(images, "upload_public", fail)
    assert client.post("/template/frame-mask-images", files={"file": ("image.png", picture((3, 3)))}).status_code == 503


def test_upload_preserves_resolution_and_exif_orientation(image_store):
    """不继承参考图 2048 缩图；EXIF 横向照片只纠正方向，不二次缩放。"""
    large = images.save_image(picture((1242, 2208)))
    with Image.open(BytesIO(image_store[large])) as image:
        assert image.size == (1242, 2208)
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private metadata"
    rotated = images.save_image(picture((30, 50), format="JPEG", exif=exif))
    with Image.open(BytesIO(image_store[rotated])) as image:
        assert image.size == (17, 30) and not image.getexif()


def test_image_reference_roundtrip_clear_and_frozen_copy(client, template_payload, image_store):
    """旧客户端省略保留图片，显式空值清除；停用保留，替换不改冻结快照及旧图片。"""
    first = client.post("/template/frame-mask-images", files={"file": ("one.png", picture())}).json()["imageId"]
    second = client.post("/template/frame-mask-images", files={"file": ("two.png", picture(color=(40, 80, 200, 255)))}).json()["imageId"]
    mask = {"top": {"enabled": True, "height": 20, "color": "#112233", "imageId": first},
            "bottom": {"enabled": False, "height": 0, "color": "#112233", "imageId": second}}
    response = post_template(client, {**template_payload, "frame_mask": mask})
    assert response.status_code == 201, response.text
    record = saved_template(response)
    template_id = record["template_id"]
    assert detail_template(client.get(f"/template/{template_id}"))["frame_mask"]["top"]["imageId"] == first
    frozen = deepcopy(response.json()["template"]["frameMask"])
    payload = {**template_payload, "template_id": template_id}
    legacy = deepcopy(mask)
    for bar in legacy.values():
        del bar["imageId"]
    preserved = post_template(client, {**payload, "frame_mask": legacy})
    assert preserved.status_code == 200
    assert preserved.json()["template"]["frameMask"]["top"]["imageId"] == first
    mask["top"]["imageId"] = second
    assert post_template(client, {**payload, "frame_mask": mask}).status_code == 200
    assert frozen["top"]["imageId"] == first and first in image_store
    mask["top"]["imageId"] = ""
    cleared = post_template(client, {**payload, "frame_mask": mask})
    assert cleared.status_code == 200 and cleared.json()["template"]["frameMask"]["top"]["imageId"] == ""
    assert cleared.json()["template"]["frameMask"]["bottom"]["imageId"] == second
    original = detail_template(client.get(f"/template/{template_id}"))
    mask["top"]["imageId"] = "https://untrusted.test/image.png"
    assert post_template(client, {**payload, "frame_mask": mask}).status_code == 422
    mask["top"]["imageId"] = "f" * 64
    assert post_template(client, {**payload, "frame_mask": mask}).status_code == 404
    assert detail_template(client.get(f"/template/{template_id}")) == original




def test_read_checks_fixed_storage_and_content_hash(monkeypatch):
    """只读固定存储目录；损坏或缺失对象不返回成功图片。"""
    payload = picture()
    image_id = sha256(payload).hexdigest()
    monkeypatch.setattr(images, "ZosSettings", lambda: SimpleNamespace(zos_web_url="https://storage.test"))
    original = httpx.Client

    def response(request):
        """模拟对象存储的正常、缺失和损坏三种结果。"""
        assert str(request.url).startswith("https://storage.test/imv/template-mask/")
        if request.url.path.endswith(f"{image_id}.png"):
            return httpx.Response(200, content=payload)
        return httpx.Response(404 if request.url.path.endswith("0" * 64 + ".png") else 200, content=b"corrupt")

    monkeypatch.setattr(images.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    assert images.read_image(image_id) == payload
    for bad, status in [("0" * 64, 404), ("1" * 64, 503)]:
        with pytest.raises(HTTPException) as error:
            images.read_image(bad)
        assert error.value.status_code == status
