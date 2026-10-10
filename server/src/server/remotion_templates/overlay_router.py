"""JSON routes for the bus to request a template's Remotion overlay video under /api/v1/overlay-renders.

Mirrors the video-composition contract (camelCase, async task, callback notification). The runtime and
its task service are borrowed from the Remotion app, like `sprite_router.py`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from starlette.concurrency import run_in_threadpool

from ..video_composition.schema import MediaURL, PositiveSeconds
from .api import app as remotion_templates_app
from .overlay_service import OverlayRenders
from .routes import runtime_for

router = APIRouter(prefix="/api/v1/overlay-renders", tags=["Remotion 叠加视频"])


class OverlayRequest(BaseModel):
    """Template to render plus the optional TTS audio or explicit length that decides its duration."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    style_id: UUID
    audio_url: MediaURL | None = None
    duration: PositiveSeconds | None = None
    callback_url: MediaURL | None = None


async def overlay_renders() -> OverlayRenders:
    """Share the Remotion runtime's task service and start recovery on first use."""
    service = (await runtime_for(remotion_templates_app.state)).overlays
    service.resume()
    return service


Service = Annotated[OverlayRenders, Depends(overlay_renders)]


@router.post("", summary="受理整个模板的 Remotion 叠加视频渲染")
async def create_overlay_render(payload: OverlayRequest, service: Service) -> dict:
    """有 audioUrl 以音频完整时长为准，否则用 duration，再否则取摆放的最晚结束；渲染后转存 ZOS 并回调。"""
    task_id = await service.accept(payload.style_id, payload.audio_url, payload.duration, payload.callback_url)
    return {"taskId": task_id}


@router.get("/{task_id}", summary="查询叠加视频渲染任务")
async def get_overlay_render(task_id: UUID, service: Service) -> dict:
    """成功返回 ZOS 公开地址；失败仍为 200 并携带脱敏错误。"""
    return await run_in_threadpool(service.get, str(task_id))
