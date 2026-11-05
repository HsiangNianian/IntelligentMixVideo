"""异步合成 API；受理先持久化，查询状态、成片地址及本机文件执行日志。"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from ..client_config import parse_config
from .settings import ClientSettings
from . import execution_log, store
from .schema import AcceptedResponse, CompositionRequest, MatchCallback, PlaygroundRequest, TaskResponse

router = APIRouter(prefix="/api/v1/video-compositions", tags=["video-composition"])


def client_config(value: Annotated[str | None, Header(alias="X-IMS-Config")] = None) -> ClientSettings | None:
    """从请求头读取 IMS 凭据，避免进入持久化业务请求和执行日志。"""
    return parse_config(value, ClientSettings)


Config = Annotated[ClientSettings | None, Depends(client_config)]


@router.post("", status_code=200, response_model=AcceptedResponse)
async def create_composition(payload: CompositionRequest, request: Request, response: Response, config: Config) -> AcceptedResponse:
    """持久化后按模式准备画面与文字，共用 IMS、ZOS 和终态通知。"""
    record = await request.app.state.video_composition.accept(payload, str(request.base_url).rstrip("/"), await request.json(), config=config)
    response.headers["Location"] = f"/api/v1/video-compositions/{record['task_id']}"
    return AcceptedResponse(data=record["task_id"])


@router.post("/playground", status_code=200, response_model=AcceptedResponse)
async def create_playground(payload: PlaygroundRequest, request: Request, response: Response, config: Config) -> AcceptedResponse:
    """从固定样本创建新模板渲染任务，状态与成片仍由原查询接口返回。"""
    record = await request.app.state.video_composition.accept_playground(payload, str(request.base_url).rstrip("/"), config=config)
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


@router.get("/{task_id}/logs", summary="查询任务日志")
def get_composition_logs(
    task_id: UUID,
    module: Annotated[
        Literal["request", "template", "asr", "segmentation", "matching", "timeline", "remotion", "ims", "zos", "error"],
        Query(description="选择日志模块；error 返回该任务全部详细错误文件。"),
    ],
) -> dict:
    """读取本机已落盘日志，不查询数据库；九模块展开引用，error 返回文件名与完整正文。

    供开发和运维排障，无鉴权；未落盘或已清理的任务目录返回 404，不等待日志队列。
    """
    result = execution_log.query_log(str(task_id), module)
    if result is None:
        raise HTTPException(404, "任务日志不可用：尚未落盘、已清理或任务不存在")
    return result
