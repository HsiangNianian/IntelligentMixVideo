"""HTTP contracts for local template works, durable job events, uploads and bounded artifacts."""

import re
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from .evidence import verify_artifacts
from .media import save_image
from .models import (
    Asset,
    GenerateTemplateRequest,
    GenerationJob,
    PublicJob,
    PublicVersion,
    TaskMessage,
    TemplateProject,
)
from .runtime import Runtime
from .store import NotFound

# 按接口职责设置标签，供模板服务的 Swagger 分组展示。
router = APIRouter()


async def runtime(request: Request) -> Runtime:
    """Start only the feature-local runtime on first use; unrelated routes need no model or database."""
    state = request.app.state
    if not hasattr(state, "runtime"):
        service = state.build_runtime()
        try:
            service.initialize()
        except (OSError, ImportError) as exc:
            raise HTTPException(
                503,
                "Template runtime requires Linux and a writable local data folder owned by one process.",
            ) from exc
        state.runtime = service
    return state.runtime


Service = Annotated[Runtime, Depends(runtime)]


@router.get("/capabilities", tags=["服务能力"], summary="查询服务能力")
def capabilities(service: Service) -> dict:
    """查询支持的输入类型、可用字体及模型配置是否就绪。

    当前支持文字描述和图片，不支持视频；响应不包含模型密钥。
    """
    return {
        "inputs": ["description", "image"],
        "video_supported": False,
        "models_configured": service.settings.models_configured,
        "fonts": [{"family": "Noto Sans CJK SC", "weights": [400, 700]}],
    }


@router.post(
    "/assets",
    response_model=Asset,
    status_code=201,
    tags=["参考素材"],
    summary="上传参考图片",
)
async def upload(service: Service, file: Annotated[UploadFile, File()]) -> Asset:
    """通过 multipart 表单的 `file` 字段上传单帧 PNG、JPEG 或 WebP 图片。

    图片经校验后统一保存为 PNG，返回的 `id` 可用于创建作品时的 `image.asset_id`。
    默认大小上限为 10 MiB；超限返回 413，内容无效返回 422。
    """
    try:
        content = await file.read(service.settings.max_upload_bytes + 1)
        if len(content) > service.settings.max_upload_bytes:
            raise HTTPException(413, "Image exceeds the upload size limit.")
        try:
            return save_image(content, service.store, service.settings)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


@router.post("/works", status_code=202, tags=["模板作品"], summary="创建作品并生成模板")
async def create(
    request: GenerateTemplateRequest, service: Service
) -> dict[str, TemplateProject | PublicJob]:
    """根据文字描述和／或参考图片创建作品，并提交异步生成任务。

    `description` 与 `image` 至少提供一种；可通过 `composition` 设置画布和时长。
    返回 202 及 `work`、`job`，随后使用任务 ID 查询进度；模型未配置时返回 503。
    """
    if not service.settings.models_configured:
        raise HTTPException(
            503, "Configure actor and vision models in the server environment."
        )
    if request.image:
        service.store.asset(request.image.asset_id)
    project, job = service.store.create(request)
    service.notify()
    return {"work": project, "job": PublicJob.from_job(job)}


@router.get(
    "/works",
    response_model=list[TemplateProject],
    tags=["模板作品"],
    summary="查询作品列表",
)
def works(service: Service) -> list[TemplateProject]:
    """按创建顺序从新到旧返回最近 100 个作品。

    列表包含作品信息和当前成功版本 ID，完整代码需通过版本详情接口获取。
    """
    return service.store.projects()


@router.get(
    "/works/{work_id}",
    response_model=TemplateProject,
    tags=["模板作品"],
    summary="查询作品详情",
)
def work(work_id: UUID, service: Service) -> TemplateProject:
    """查询作品的原始输入、创建时间和当前成功版本 ID。

    尚无成功版本时 `current_version_id` 为 null；生成失败或取消不会覆盖已有成功版本。
    """
    return service.store.project(work_id)


@router.post(
    "/works/{work_id}/messages",
    response_model=PublicJob,
    status_code=202,
    tags=["模板作品"],
    summary="修改模板或回答澄清问题",
)
async def edit(work_id: UUID, request: TaskMessage, service: Service) -> PublicJob:
    """通过参数补丁 `parameters` 或自然语言 `instruction` 修改模板，两者必须二选一。

    默认基于当前成功版本，也可用 `base_version_id` 指定历史成功版本。
    回答问题时用 instruction 提交答案，并用 reply_to_job_id 绑定提出问题的任务。
    参数修改保留 TSX 并重新验收；问题已过期或已有运行任务时返回 409。
    """
    try:
        return PublicJob.from_job(service.message(work_id, request))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/works/{work_id}/versions",
    response_model=list[PublicVersion],
    tags=["模板版本"],
    summary="查询作品版本列表",
)
def versions(work_id: UUID, service: Service) -> list[PublicVersion]:
    """按版本号升序返回作品所有通过验收的版本，包含各版本代码和配置。

    候选修复过程与验收报告仅供服务内部使用，公开接口只返回可用结果。
    """
    return [
        PublicVersion.from_version(item) for item in service.store.versions(work_id)
    ]


@router.get(
    "/versions/{version_id}",
    response_model=PublicVersion,
    tags=["模板版本"],
    summary="查询版本详情",
)
def version(version_id: UUID, service: Service) -> PublicVersion:
    """获取成功版本的 TSX 代码、可编辑参数 schema、默认参数、模板规格。

    版本发布后保持不变；需要调整时应向所属作品提交新的修改任务。
    """
    return PublicVersion.from_version(service.store.version(version_id))


@router.get(
    "/jobs/{job_id}",
    response_model=PublicJob,
    tags=["生成任务"],
    summary="查询任务状态",
)
def job(job_id: UUID, service: Service) -> PublicJob:
    """查询任务是否排队、处理中、需要补充信息或已有可用结果。内部修复过程不对外展示。

    成功时返回 `result_version_id`；状态为 `needs_input` 时，通过 `questions` 查看待补充的问题。
    """
    return PublicJob.from_job(service.store.job(job_id))


@router.get("/jobs/{job_id}/events", tags=["生成任务"], summary="查询任务进度事件")
def events(
    job_id: UUID, service: Service, after: Annotated[int, Query(ge=0)] = 0
) -> dict:
    """按事件 ID 升序返回指定游标之后的任务进度记录，每次最多 100 条。

    首次使用 `after=0`，后续将返回的 `next_cursor` 作为 `after` 继续轮询；此接口不是 SSE 长连接。
    """
    records = service.store.events(job_id, after)
    return {
        "events": [
            {
                "id": identifier,
                "job": PublicJob.from_job(GenerationJob.model_validate_json(data)),
            }
            for identifier, data in records
        ],
        "next_cursor": records[-1][0] if records else after,
    }


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=PublicJob,
    tags=["生成任务"],
    summary="取消生成任务",
)
async def cancel(job_id: UUID, service: Service) -> PublicJob:
    """取消排队或运行中的任务，并等待正在执行的模型请求或渲染子进程停止。

    重复取消安全；已结束的任务返回原有状态，不影响已发布的成功版本。
    """
    return PublicJob.from_job(await service.cancel(job_id))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=PublicJob,
    status_code=202,
    tags=["生成任务"],
    summary="重试生成任务",
)
async def retry(job_id: UUID, service: Service) -> PublicJob:
    """为失败、已取消或中断的任务创建一次新的执行，返回新的任务 ID。

    保留原任务记录及产物，不从中断位置续跑；不符合重试条件时返回 409。
    """
    return PublicJob.from_job(service.retry(job_id))


def artifact_path(service: Runtime, version_id: UUID, filename: str) -> Path:
    """Resolve only sealed accepted output; failed attempts have no public download route."""
    version = service.store.version(version_id)
    if not re.fullmatch(r"Template\.tsx|preview\.mp4|frame-\d+\.png", filename):
        raise NotFound("artifact not found")
    directory = service.store.root / "accepted" / str(version.id)
    try:
        verify_artifacts(version.candidate, version.spec, version.validation, directory)
    except (ValueError, OSError) as exc:
        raise NotFound("accepted artifact unavailable") from exc
    if filename not in version.validation.artifacts:
        raise NotFound("artifact not found")
    return directory / filename


@router.get("/jobs/{job_id}/artifacts", tags=["生成产物"], summary="查询可用结果产物")
def artifacts(job_id: UUID, service: Service) -> list[dict]:
    """任务有可用结果时返回 TSX、PNG 和 MP4 下载链接；否则返回空列表。

    不返回内部候选、失败代码、验证报告或修复次数。
    """
    job = service.store.job(job_id)
    if job.result_version_id is None:
        return []
    version = service.store.version(job.result_version_id)
    result = []
    for name in version.validation.artifacts:
        if not re.fullmatch(r"Template\.tsx|preview\.mp4|frame-\d+\.png", name):
            continue
        artifact_path(service, version.id, name)
        result.append(
            {
                "name": name,
                "url": f"/api/templates/versions/{version.id}/artifacts/{name}",
            }
        )
    return result


@router.get(
    "/versions/{version_id}/artifacts/{filename}",
    tags=["生成产物"],
    summary="下载可用模板代码或预览",
)
def download(version_id: UUID, filename: str, service: Service) -> FileResponse:
    """下载已验收版本的 Template.tsx、preview.mp4 或 frame-N.png。

    下载内容与验收时的文件一致；不存在、被修改或属于内部诊断的文件返回 404。
    """
    return FileResponse(artifact_path(service, version_id, filename), filename=filename)
