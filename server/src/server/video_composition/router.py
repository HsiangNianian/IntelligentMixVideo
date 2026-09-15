"""异步合成 API；受理先持久化，查询读取本地状态并为成功成片刷新播放地址。"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from . import store
from .schema import Accepted, CompositionRequest, MatchCallback, TaskResponse

router = APIRouter(prefix="/api/v1/video-compositions", tags=["video-composition"])


@router.post("", status_code=202, response_model=Accepted)
async def create_composition(payload: CompositionRequest, request: Request, response: Response) -> Accepted:
    """受理后后台依次执行 ASR、切片、素材匹配和 IMS；其他兼容控制字段暂不生效。"""
    record = await request.app.state.video_composition.accept(payload, str(request.base_url).rstrip("/"), await request.json())
    response.headers["Location"] = f"/api/v1/video-compositions/{record['task_id']}"
    return Accepted(task_id=record["task_id"])


@router.post("/{task_id}/segment-match-callback")
async def segment_match_callback(
    task_id: UUID, payload: MatchCallback, request: Request,
    token: str = Query(default="", max_length=200),
) -> dict:
    """验证任务令牌并持久化匹配结果；重复或迟到回调确认收件，不重复渲染。"""
    await request.app.state.video_composition.receive_match_callback(str(task_id), token, payload, await request.json())
    return {"status": "ok"}


@router.get("/{task_id}", response_model=TaskResponse)
async def get_composition(task_id: UUID, request: Request) -> TaskResponse:
    """失败任务返回 200；成功时刷新地址，临时失败返回可重试 503，不改变终态。"""
    record = await request.app.state.video_composition.sync(store.get, str(task_id))
    if record is None:
        raise HTTPException(404, "合成任务不存在")
    return await request.app.state.video_composition.response(record)
