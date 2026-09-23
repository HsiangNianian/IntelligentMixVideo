"""异步合成 API；受理先持久化，查询读取本地状态和新任务的 ZOS 成片地址。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from ..client_config import parse_config
from .settings import ClientSettings
from . import store
from .schema import AcceptedResponse, CompositionRequest, MatchCallback, TaskResponse

router = APIRouter(prefix="/api/v1/video-compositions", tags=["video-composition"])


def client_config(value: Annotated[str | None, Header(alias="X-IMS-Config")] = None) -> ClientSettings | None:
    """从请求头读取 IMS 凭据，避免进入持久化业务请求和执行日志。"""
    return parse_config(value, ClientSettings)


Config = Annotated[ClientSettings | None, Depends(client_config)]


@router.post("", status_code=200, response_model=AcceptedResponse)
async def create_composition(payload: CompositionRequest, request: Request, response: Response, config: Config) -> AcceptedResponse:
    """受理后后台依次执行 ASR、切片、素材匹配和 IMS；其他兼容控制字段暂不生效。"""
    record = await request.app.state.video_composition.accept(payload, str(request.base_url).rstrip("/"), await request.json(), config=config)
    response.headers["Location"] = f"/api/v1/video-compositions/{record['task_id']}"
    return AcceptedResponse(data=record["task_id"])


@router.post("/{task_id}/segment-match-callback")
async def segment_match_callback(
    task_id: UUID, payload: MatchCallback, request: Request,
    token: str = Query(default="", max_length=200),
) -> dict:
    """验证任务令牌并持久化匹配结果；重复或迟到回调确认收件，不重复渲染。"""
    await request.app.state.video_composition.receive_match_callback(str(task_id), token, payload, await request.json())
    return {"status": "ok"}


@router.get("/{task_id}", response_model=TaskResponse)
async def get_composition(task_id: UUID, request: Request, config: Config) -> TaskResponse:
    """失败任务返回 200；新任务返回 ZOS 地址，旧任务取 IMS 地址失败返回 503。"""
    record = await request.app.state.video_composition.sync(store.get, str(task_id))
    if record is None:
        raise HTTPException(404, "合成任务不存在")
    return await request.app.state.video_composition.response(record, config=config)
