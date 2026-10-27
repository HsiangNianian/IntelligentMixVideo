"""同屏渲染准备：只探测用到的媒体尺寸，生成并上传底色与边框，纯几何仍由模板配置解释。"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw
import httpx
from botocore.exceptions import BotoCoreError, ClientError

from ..template.split_screen import SplitScreen, screen_rect
from .errors import CompositionError
from .media import ffprobe_json
from .settings import ZosSettings
from .zos import upload_public, object_prefix


def border_image(rect: dict, color: str) -> Image.Image:
    """透明底内描边；两倍采样后缩小，避免圆角边缘出现锯齿。"""
    w, h = rect["width"], rect["height"]
    image = Image.new("RGBA", (w * 2, h * 2))
    draw = ImageDraw.Draw(image)
    r, b = rect["radius"] * 2, rect["border"] * 2
    draw.rounded_rectangle((0, 0, w * 2 - 1, h * 2 - 1), radius=r, fill=color)
    if b < min(w, h):
        draw.rounded_rectangle((b, b, w * 2 - 1 - b, h * 2 - 1 - b), radius=max(0, r - b), fill=(0, 0, 0, 0))
    return image.resize((w, h), Image.Resampling.LANCZOS)


async def prepare(task_id: str, data: dict, timeout: float) -> dict | None:
    """准备一次任务真正命中的媒体；结果随任务保存，恢复时不重传装饰图片。"""
    layout = SplitScreen.model_validate(data["template"].get("split_screen") or {})
    if not layout.enabled:
        return None
    urls = list(dict.fromkeys(item["matched_candidate_url"] for item in data.get("matches", []) if item.get("matched_candidate_url")))
    if not urls:
        return None
    avatar = data["request"]["videoUrl"]
    sizes = {}
    try:
        for url in dict.fromkeys([avatar, *urls]):
            info = await ffprobe_json(url, timeout, "-select_streams", "v:0", "-show_entries", "stream=width,height,sample_aspect_ratio:stream_side_data=rotation")
            stream = info["streams"][0]
            w, h = int(stream["width"]), int(stream["height"])
            if w <= 0 or h <= 0:
                raise ValueError("媒体尺寸无效")
            sar = stream.get("sample_aspect_ratio", "1:1")
            if sar not in ("N/A", "0:1"):
                a, b = map(int, sar.split(":"))
                w *= a / b
            rotation = next((item["rotation"] for item in stream.get("side_data_list", []) if "rotation" in item), 0)
            sizes[url] = [h, w] if abs(rotation) % 180 == 90 else [w, h]
        return await asyncio.to_thread(_images, task_id, data, layout, urls, avatar, sizes, timeout)
    except (OSError, ValueError, KeyError, IndexError, TypeError, TimeoutError, httpx.HTTPError, BotoCoreError, ClientError) as exc:
        raise CompositionError("split_screen_prepare_failed", "无法读取同屏素材尺寸或准备布局图片", "assembling") from exc


def _images(task_id: str, data: dict, layout: SplitScreen, urls: list[str], avatar: str, sizes: dict, timeout: float) -> dict:
    """CPU 绘图和对象存储都在工作线程；图片只包含几何、颜色，不复制用户视频。"""
    width, height = (data["output"][key] for key in ("width", "height"))
    result = {"media": sizes, "material_borders": {}}
    prefix = f"{object_prefix(data)}/{task_id}/split-screen"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        Image.new("RGB", (width, height), layout.background_color).save(root / "background.png")
        files = [(root / "background.png", f"{prefix}/background.png", "image/png")]
        borders = []
        for role, window, sources in (("material", layout.material, urls), ("avatar", layout.avatar, [avatar])):
            if not window.border_width:
                continue
            for url in sources:
                rect = screen_rect(window, sizes[url], width, height)
                path = root / f"border-{len(borders)}.png"
                border_image(rect, window.border_color).save(path)
                borders.append((role, url))
                files.append((path, f"{prefix}/{path.name}", "image/png"))
        uploaded = upload_public(files, ZosSettings(), timeout)
        result["background"] = uploaded[0]
        for (role, source), url in zip(borders, uploaded[1:]):
            if role == "avatar":
                result["avatar_border"] = url
            else:
                result["material_borders"][source] = url
    return result


def window_clip(clip: dict, window, source: list[float], width: int, height: int) -> dict:
    """移除全屏模糊底，保留静音等原效果，将圆角绑定到这一片段。"""
    rect = screen_rect(window, source, width, height)
    effects = [effect for effect in clip.get("Effects", []) if effect["Type"] != "Background"]
    if rect["radius"]:
        radius = rect["radius"] if rect["radius"] > 1 else rect["radius"] / max(rect["width"], rect["height"])
        effects.append({"Type": "VFX", "SubType": "mask_rec", "ExtParams":
                        f'x=0.5,y=0.5,width=1.0,height=1.0,radius={radius:.6f},antialias={0.5 / max(rect["width"], rect["height"]):.8f}'})
    return {**clip, "X": rect["x"], "Y": rect["y"], "Width": rect["width"], "Height": rect["height"],
            "AdaptMode": "Cover", "Effects": effects}


def border_clip(clip: dict, url: str) -> dict:
    """边框和所属窗口使用相同位置及显示区间，透明角露出真正的下层。"""
    return {key: value for key, value in clip.items() if key in ("X", "Y", "Width", "Height", "TimelineIn", "TimelineOut")} | {
        "Type": "Image", "MediaURL": url, "Duration": clip["TimelineOut"] - clip["TimelineIn"], "AdaptMode": "Fill",
    }
