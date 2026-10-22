"""HTTP contracts for device-owned template works, buffered job events, uploads and bounded artifacts.

JSON routes of a work require the caller's `X-IMV-Device` header and treat another device's work as missing.
Browser-embedded reads (preview page, fonts, reference image, artifact download) are addressed by unguessable IDs
and need no header, like the public-read objects behind them.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from ..client_config import parse_config
from .settings import ClientSettings
from .evidence import digest
from .fonts import SYSTEM_FONT_ID, Font, FontExists, FontRejected, FontStore, Slot, cards
from .history import WorkEvent
from .media import save_image
from .object_store import ObjectStoreError
from .models import (
    Asset,
    GenerateTemplateRequest,
    PublicJob,
    PublicVersion,
    TaskMessage,
    TemplateProject,
)
from .provider import ExecutionFailure
from .runtime import Runtime
from .store import InstanceInUse, NotFound
from .stream import event_stream
from .tool_validation import ValidationUnavailable
from .tools.contracts import CodeValidationReport, ComponentDefinition

# 按接口职责设置标签，供模板服务的 Swagger 分组展示。
router = APIRouter()
logger = logging.getLogger(__name__)


async def runtime_for(state) -> Runtime:
    """Start only the feature-local runtime on first use; unrelated routes need no model or database.

    Requests that arrive while the first one initializes wait for it, so one runtime (one write lock, one worker,
    one sweep) ever exists per app and recovery never interrupts a job another runtime has just claimed.
    """
    if not hasattr(state, "runtime"):
        if not hasattr(state, "runtime_starting"):
            state.runtime_starting = asyncio.Lock()
        async with state.runtime_starting:
            if not hasattr(state, "runtime"):
                try:
                    service = state.build_runtime()
                    await asyncio.to_thread(service.initialize)
                except InstanceInUse as exc:
                    raise HTTPException(503, str(exc)) from exc
                except (OSError, ImportError, ValueError, SQLAlchemyError) as exc:
                    raise HTTPException(
                        503,
                        "Remotion 存储不可用：请检查 MySQL、ZOS 配置与可写的本地数据目录。",
                    ) from exc
                state.runtime = service
                # Overlay tasks left unfinished by the previous process continue as soon as the runtime exists.
                service.overlays.resume()
                service.start_maintenance()
    return state.runtime


async def runtime(request: Request) -> Runtime:
    """Resolve the runtime owned by the app serving this request."""
    return await runtime_for(request.app.state)


Service = Annotated[Runtime, Depends(runtime)]


async def device(service: Service, value: Annotated[UUID, Header(alias="X-IMV-Device")]) -> UUID:
    """The client device that owns the request's works; each use refreshes its activity time."""
    await asyncio.to_thread(service.store.touch, value)
    return value


Device = Annotated[UUID, Depends(device)]


def require_sandbox(service: Runtime) -> None:
    """Refuse work that needs the isolated validator on a host that cannot start it, before anything is queued.

    Only call this where rendering is certain: a model turn may still just answer or ask a question, which
    needs no validator, so free-text messages are left to fail at the tool call with VALIDATION_UNAVAILABLE.
    """
    problem = service.sandbox_problem()
    if problem is not None:
        raise HTTPException(503, f"校验环境不可用：{problem}")


def parameters_need_sandbox(service: Runtime, base_id: UUID | None, work_id: UUID | None = None) -> bool:
    """Whether a parameter patch on this base renders in the sandbox.

    PR76 versions whose player bundle reads injected defaults are revised instantly; older ones are rebuilt in the
    sandbox. Without an explicit base the work's current version is the base. A missing work or version needs the
    sandbox check here and is then reported by the edit itself.
    """
    try:
        if base_id is None and work_id is not None:
            base_id = service.store.project(work_id).current_version_id
        if base_id is None:
            return True
        version = service.store.version(base_id)
        return not service.instant(version)
    except (NotFound, ValueError):
        return True


def client_config(value: Annotated[str | None, Header(alias="X-Remotion-Config")] = None) -> ClientSettings | None:
    """模型配置只来自当前请求头，不进入作品、聊天或任务输入持久化。"""
    return parse_config(value, ClientSettings)


Config = Annotated[ClientSettings | None, Depends(client_config)]


@router.get("/capabilities", tags=["服务能力"], summary="查询服务能力")
def capabilities(service: Service, config: Config) -> dict:
    """查询支持的输入类型、可用字体及模型配置是否就绪。

    当前支持文字描述和图片，不支持视频；响应不包含模型密钥。
    """
    return {
        "inputs": ["description", "images"],
        "video_supported": False,
        "models_configured": (service.settings.model_copy(update=config.model_dump()) if config is not None else service.settings).models_configured,
        "fonts": [
            {"family": family, "weights": sorted({weight for font in files for weight in font.weights})}
            for family, files in cards(FontStore.for_settings(service.settings).listing()).items()
        ],
    }


@router.post(
    "/assets",
    response_model=Asset,
    status_code=201,
    tags=["参考素材"],
    summary="上传参考图片",
)
async def upload(service: Service, owner: Device, file: Annotated[UploadFile, File()]) -> Asset:
    """通过 multipart 表单的 `file` 字段上传单帧 PNG、JPEG 或 WebP 图片。

    图片经校验后统一保存为 PNG，返回的 `id` 可用于创建作品或后续消息 `images` 中的 `asset_id`。
    默认大小上限为 10 MiB；超限返回 413，内容无效返回 422。
    上传后 24 小时仍未被任何作品或消息引用的图片（例如消息提交失败）由服务端每小时的清理回收。
    """
    try:
        content = await file.read(service.settings.max_upload_bytes + 1)
        if len(content) > service.settings.max_upload_bytes:
            raise HTTPException(413, "Image exceeds the upload size limit.")
        try:
            return await asyncio.to_thread(save_image, content, service.store, service.settings, owner)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, "参考图暂时无法保存，请稍后重试。") from exc
    finally:
        await file.close()


@router.post("/works", status_code=202, tags=["模板作品"], summary="创建作品并生成模板")
async def create(
    request: GenerateTemplateRequest, service: Service, owner: Device, config: Config
) -> dict[str, TemplateProject | PublicJob]:
    """根据文字描述和／或参考图片创建作品，并提交异步生成任务。

    `description` 与 `images`（每条最多 4 张，不能重复）至少提供一种；旧客户端的单张 `image` 按一张处理。可通过 `composition` 设置画布和时长。
    返回 202 及 `work`、`job`，随后使用任务 ID 查询进度；模型未配置时返回 503。
    """
    if (request.composition.width, request.composition.height, request.composition.fps) != (1080, 1920, 30):
        raise HTTPException(422, "主画布固定为 1080×1920、30 FPS")
    settings = service.settings.model_copy(update=config.model_dump()) if config is not None else service.settings
    if not settings.models_configured:
        raise HTTPException(503, "请配置 Agent 模型及密钥")
    require_sandbox(service)
    for image in request.images:
        service.store.asset(image.asset_id, owner)
    project, job = service.store.create(request, owner)
    service.notify(job.id, config)
    return {"work": project, "job": PublicJob.from_job(job)}


@router.get(
    "/works/{work_id}",
    response_model=TemplateProject,
    tags=["模板作品"],
    summary="查询作品详情",
)
def work(work_id: UUID, service: Service, owner: Device) -> TemplateProject:
    """查询作品的原始输入、创建时间和当前成功版本 ID。

    尚无成功版本时 `current_version_id` 为 null；生成失败或取消不会覆盖已有成功版本。
    """
    return service.store.project(work_id, owner)


@router.post(
    "/works/{work_id}/messages",
    response_model=PublicJob,
    status_code=202,
    tags=["模板作品"],
    summary="提问、修改模板或回答澄清问题",
)
async def edit(work_id: UUID, request: TaskMessage, service: Service, owner: Device, config: Config) -> PublicJob:
    """通过参数补丁 `parameters` 或自然语言 `instruction` 修改模板，两者必须二选一。

    自然语言修改由 `instruction` 和／或参考图片 `images`（`POST /assets` 返回的 ID，最多 4 张、不能重复）组成，至少提供一项；
    本次任务连同作品的原参考图一起读取这些图片，图片不能与参数补丁同时提交。
    默认基于当前成功版本，也可用 `base_version_id` 指定历史成功版本。
    回答问题时用 instruction 和／或 images 提交答案，并用 reply_to_job_id 绑定提出问题的任务；回答沿用该任务已有的参考图片。
    图片不存在时返回 404，属于正在删除的会话时返回 409。
    参数修改保留 TSX：组合版本（PR76）只做参数 Schema 校验并立即封存新默认值，不进入任务队列、不启动沙箱、不重新打包；
    更早的版本仍经渲染检查后保存。都不调用 Actor 或 Judge。
    手动修订成为后续自然语言修改的基线；问题已过期或已有运行任务时返回 409。
    更早版本的参数修改在入队前检查校验环境，缺失时返回 503；提问、澄清回复与自然语言修改不需要校验器即可回答，不受此限制。
    纯问答或明确保持现状时返回 answered 终态，回答通过 message 和会话历史提供，不产生新版本。
    尚无成功版本的会话也可继续提问或描述生成需求；参数修改仍须已有成功版本。
    """
    service.store.project(work_id, owner)
    # Deciding may download the base's player bundle; doing it in a thread also warms the cache `_start` reads.
    if request.parameters is not None and await asyncio.to_thread(
        parameters_need_sandbox, service, request.base_version_id, work_id,
    ):
        require_sandbox(service)
    try:
        return PublicJob.from_job(service.message(work_id, request, config))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/works/{work_id}/versions",
    response_model=list[PublicVersion],
    tags=["模板版本"],
    summary="查询作品版本列表",
)
def versions(work_id: UUID, service: Service, owner: Device) -> list[PublicVersion]:
    """按版本号升序返回可用版本，包含各版本代码、配置及来源 source。

    agent 为通过生成验收的版本，user_parameters 为通过渲染检查的用户修订。
    候选修复过程与验收报告仅供服务内部使用。
    """
    return [
        PublicVersion.from_version(item) for item in service.store.versions(work_id, owner)
    ]


@router.get(
    "/versions/{version_id}",
    response_model=PublicVersion,
    tags=["模板版本"],
    summary="查询版本详情",
)
def version(version_id: UUID, service: Service, owner: Device) -> PublicVersion:
    """获取成功版本的 TSX 代码、可编辑参数 schema、默认参数、模板规格。

    版本发布后保持不变；需要调整时应向所属作品提交新的修改任务。
    """
    return PublicVersion.from_version(service.store.version(version_id, owner))


@router.get(
    "/jobs/{job_id}",
    response_model=PublicJob,
    tags=["生成任务"],
    summary="查询任务状态",
)
def job(job_id: UUID, service: Service, owner: Device) -> PublicJob:
    """查询任务是否排队、处理中、需要补充信息或已有可用结果。内部修复过程不对外展示。

    成功时返回 `result_version_id`；状态为 `needs_input` 时，通过 `questions` 查看待补充的问题。
    `answered` 表示已直接回答，正文在 `message`，`result_version_id` 为 null，不代表生成了模板。
    """
    return PublicJob.from_job(service.store.job(job_id, owner))


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=PublicJob,
    tags=["生成任务"],
    summary="取消生成任务",
)
async def cancel(job_id: UUID, service: Service, owner: Device) -> PublicJob:
    """取消排队或运行中的任务，并等待正在执行的模型请求或渲染子进程停止。

    重复取消安全；已结束的任务返回原有状态，不影响已发布的成功版本。
    """
    service.store.job(job_id, owner)
    return PublicJob.from_job(await service.cancel(job_id))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=PublicJob,
    status_code=202,
    tags=["生成任务"],
    summary="重试生成任务",
)
async def retry(job_id: UUID, service: Service, owner: Device, config: Config) -> PublicJob:
    """为失败、已取消或中断的任务创建一次新的执行，返回新的任务 ID。

    保留原任务记录及产物，不从中断位置续跑；不符合重试条件时返回 409。
    更早版本的参数修订任务重试需要渲染校验环境，缺失时返回 503；组合版本的参数修订与对话类任务仍可重试，环境问题在校验工具调用时报告。
    """
    if service.store.inputs(job_id).mode == "parameters" and await asyncio.to_thread(
        parameters_need_sandbox, service, service.store.job(job_id, owner).base_version_id,
    ):
        require_sandbox(service)
    service.store.job(job_id, owner)
    return PublicJob.from_job(service.retry(job_id, config=config))


def artifact_path(service: Runtime, version_id: UUID, filename: str) -> Path:
    """Resolve only sealed accepted output; failed attempts have no public download route."""
    version = service.store.version(version_id)
    if not re.fullmatch(
        r"Template\.tsx|Export\.tsx|interactive\.js|preview\.mp4|frame-\d+\.png",
        filename,
    ):
        raise NotFound("artifact not found")
    try:
        directory = service.store.accepted(version)
    except ObjectStoreError as exc:
        raise HTTPException(503, "Artifact storage is unavailable; retry later.") from exc
    except (ValueError, OSError) as exc:
        raise NotFound("accepted artifact unavailable") from exc
    if filename not in version.validation.artifacts:
        raise NotFound("artifact not found")
    return directory / filename


@router.get("/jobs/{job_id}/artifacts", tags=["生成产物"], summary="查询可用结果产物")
def artifacts(job_id: UUID, service: Service, owner: Device) -> list[dict]:
    """任务有可用结果时返回 TSX、PNG 和 MP4 下载链接；否则返回空列表。

    不返回内部候选、失败代码、验证报告或修复次数。
    """
    job = service.store.job(job_id, owner)
    if job.result_version_id is None:
        return []
    version = service.store.version(job.result_version_id)
    result = []
    for name in version.validation.artifacts:
        if not re.fullmatch(
            r"Template\.tsx|Export\.tsx|preview\.mp4|frame-\d+\.png", name
        ):
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
    "/versions/{version_id}/diagnostics",
    response_model=CodeValidationReport,
    tags=["生成产物"],
    summary="读取成功版本的代码诊断",
)
async def diagnostics(version_id: UUID, service: Service, owner: Device) -> CodeValidationReport:
    """对已验收版本按需重跑一次隔离类型检查，返回契约与 LSP 诊断。

    代码和默认参数取自封存记录，先按现有证据清单校验存储中的封存文件未被修改；
    文件被改写时返回 404，不返回与当前字节不符的陈旧诊断。
    隔离 worker 不可用（例如缺少 Linux 沙箱）时返回 503，调用方应保留代码显示并提供重试。
    """
    version = service.store.version(version_id, owner)
    try:
        accepted = await asyncio.to_thread(service.store.accepted, version)
        export_code = (accepted / "Export.tsx").read_text(encoding="utf-8")
    except ObjectStoreError as exc:
        raise HTTPException(503, "Artifact storage is unavailable; retry later.") from exc
    except (ValueError, OSError) as exc:
        raise NotFound("accepted artifact unavailable") from exc
    component = ComponentDefinition(
        code=version.candidate.tsx_code,
        parameter_schema=version.candidate.config_schema,
        default_parameters=version.candidate.default_config,
    )
    try:
        return await service.code_report(component, export_code=export_code)
    except (ValidationUnavailable, ExecutionFailure) as exc:
        raise HTTPException(503, "代码诊断服务暂不可用，请稍后重试。") from exc


@router.get(
    "/versions/{version_id}/artifacts/{filename}",
    tags=["生成产物"],
    summary="下载可用模板代码或预览",
)
def download(version_id: UUID, filename: str, service: Service) -> FileResponse:
    """下载已验收版本的 Template.tsx、带默认参数的 Export.tsx、preview.mp4 或 frame-N.png。

    下载内容与验收时的文件一致；不存在、被修改或属于内部诊断的文件返回 404。
    """
    return FileResponse(artifact_path(service, version_id, filename), filename=filename)


def _json_script(value: dict) -> str:
    """Serialize page data for a JSON script element; ``<``, ``>``, ``&`` and line separators cannot end it early."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(character, escaped)
    return text


# 组合出的 Sprite 把每个实例放进按布局大小定位的盒子；盒子只是摆放区域，不应裁剪内容（见 `remotion/host-layer.tsx`）。
# 更早封存的实例盒子带 overflow hidden，较旧的预览包也不含宿主的覆盖，所以预览页同样带这条规则。
INSTANCE_BOX_CSS = "div:has(> [data-imv-instance]){overflow:visible !important}"


def font_list(fonts: Sequence[Font]) -> list[dict]:
    """What the preview page needs to register each font file: its card's family, CSS descriptors and served URL.

    Only upright files carry the plain `weight`: player bundles built before cards had styles read `family`, `weight`
    and `url` and would register an italic file as upright, so they skip italic files and slant the text instead.
    """
    return [
        {
            "family": font.family, **({} if font.italic else {"weight": font.weight_text}),
            "descriptors": {"weight": font.weight_text, "style": "italic" if font.italic else "normal"},
            "url": f"/api/templates/fonts/{font.id}",
        }
        for font in fonts if font.has_file
    ]


def player_page(
    script: str, *, overlay: bool = False, host: dict | None = None, fonts: Sequence[Font] = ()
) -> HTMLResponse:
    """Wrap a sealed interactive bundle in the sandboxed player document shared by versions and sprites.

    `overlay` drops the inspection checkerboard so the page can sit transparently over other video. `host`
    (defaults ``config`` and content ``box``) is injected as JSON the player reads at start-up instead of the
    defaults built into the bundle. `fonts` are the downloaded catalog fonts; the player registers each as a lazy
    `FontFace` so it loads only when the effect uses that family.
    """
    script = script.replace("</", "<\\/")
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<title>Remotion 字效预览</title><style>html,body,#root{margin:0;width:100%;height:100%;overflow:hidden}"
        + INSTANCE_BOX_CSS
        + (":root{color-scheme:light}html,body{background:transparent}body{color:#fff;font-family:sans-serif}</style>" if overlay else "body{color:#fff;background-color:#25252b;background-image:conic-gradient(#35353d 25%,transparent 0 50%,#35353d 0 75%,transparent 0);background-size:24px 24px;font-family:sans-serif}</style>")
        +
        '<body><div id="root"></div>'
        + (f'<script type="application/json" id="imv-preview-host">{_json_script(host)}</script>' if host is not None else "")
        + (f'<script type="application/json" id="imv-preview-fonts">{_json_script(font_list(fonts))}</script>' if fonts else "")
        + "<script>" + script + "</script></body></html>",
        headers={
            "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src http: https:; media-src http: https: data:; img-src http: https: data:; connect-src 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get(
    "/versions/{version_id}/preview",
    response_class=HTMLResponse,
    tags=["生成产物"],
    summary="打开交互预览",
)
def preview(version_id: UUID, service: Service, overlay: bool = False) -> HTMLResponse:
    """返回成功版本的隔离播放器，支持实时参数；旧版本缺少预览包时返回 404。

    父页面以 iframe 加载，使用 URL fragment 作为消息通道标识；overlay=true 透明显示，供客户端叠在独立视频上。
    """
    script = artifact_path(service, version_id, "interactive.js").read_text()
    # A parameter revision reuses its base's bundle, so the page supplies this version's own defaults.
    return player_page(
        script,
        overlay=overlay,
        host={"config": service.store.version(version_id).candidate.default_config},
        fonts=FontStore.for_settings(service.settings).listing(),
    )


@router.get(
    "/versions/{version_id}/fonts/{weight}", tags=["生成产物"], summary="读取预览字体"
)
def preview_font(version_id: UUID, weight: int, service: Service) -> FileResponse:
    """仅提供与该成功版本渲染时指纹相同的受管字体；字体变更或字重不支持时返回 404。"""
    version = service.store.version(version_id)
    if weight not in {400, 700}:
        raise NotFound("font not found")
    font = (
        service.settings.font_regular if weight == 400 else service.settings.font_bold
    )
    try:
        if digest(font) != version.validation.runtime.get(f"font_{weight}"):
            raise ValueError("font changed")
    except (OSError, ValueError) as exc:
        raise NotFound("accepted font unavailable") from exc
    return FileResponse(
        font,
        media_type="font/collection",
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
    )


class FontFaceInfo(BaseModel):
    """One style of a font card: its file (`size`, `sha256`) and where the file and its preview strip are served; the
    system default's file is the installed regular font (`size` 0, no `sha256`), or null when the host lacks it."""

    id: str
    slot: Slot
    weights: list[int]
    size: int
    sha256: str
    file_url: str | None
    preview_url: str | None


class FontInfo(BaseModel):
    """A font card as the picker and the library show it: names, description and its styles (`faces`, in the order
    regular, bold, italic, bold italic, any of them missing). `id`, `label`, `style`, `source` and `license` are its
    first style's; `weights` joins the styles' weights, `size` their files; `file_url` is the first style's file and
    `preview_url` the first strip."""

    id: str
    family: str
    label: str
    style: str
    weights: list[int]
    source: str
    license: str
    size: int
    file_url: str | None
    preview_url: str | None
    faces: list[FontFaceInfo]


def installed_system_font(service: Service) -> Path | None:
    """The system font file this host serves; null when it is not installed."""
    path = service.settings.font_regular
    return path if path.is_file() else None


def font_info(files: Sequence[Font], *, system_file: Path | None = None) -> FontInfo:
    """Public view of a font card from its files (one `cards` entry); URLs are paths below this API's `/fonts`.

    A system face has no `file_url` when `system_file` is null, so the client does not offer a specimen that would 404
    on a host without the installed font.
    """
    faces = [
        FontFaceInfo(
            **font.model_dump(include={"id", "slot", "weights", "size", "sha256"}),
            file_url=None if font.source == "system" and system_file is None else f"/api/templates/fonts/{font.id}",
            preview_url=f"/api/templates/fonts/{font.id}/preview" if font.preview else None,
        )
        for font in files
    ]
    first = files[0]
    return FontInfo(
        **first.model_dump(include={"id", "family", "label", "style", "source", "license"}),
        weights=sorted({weight for font in files for weight in font.weights}), size=sum(font.size for font in files),
        file_url=faces[0].file_url, preview_url=next((face.preview_url for face in faces if face.preview_url), None), faces=faces,
    )


@router.get("/fonts", tags=["字体"], summary="字体列表")
def list_fonts(service: Service) -> list[FontInfo]:
    """字体库里的全部字体卡片（预装与用户上传），按登记顺序；每张卡片是一个家族，含常规、粗体、斜体、粗斜体中已有的字形。
    `family` 就是效果的 `fontFamily` 参数取值。字体库所有人共享。"""
    system_file = installed_system_font(service)
    return [font_info(files, system_file=system_file) for files in cards(FontStore.for_settings(service.settings).listing()).values()]


@router.post("/fonts", status_code=201, tags=["字体"], summary="上传字体")
async def upload_font(
    service: Service,
    owner: Device,
    response: Response,
    file: Annotated[UploadFile, File()],
    preview: Annotated[UploadFile | None, File()] = None,
    label: Annotated[str, Form()] = "",
) -> FontInfo:
    """上传一个 TrueType / OpenType 字体（.ttf、.otf），保存到对象存储并登记到共享字体库。

    `preview` 是上传者浏览器用该字体画出的黑色透明底 PNG 预览条（选填，下拉框用它展示字体），`label` 是预览条上画的文字。
    显示名称取字体 name 表里自己的名称（优先中文名），不取文件名；`label` 与它不同时不保存预览条，界面改用文字名称。按文件内容（SHA-256）去重，
    重复上传是幂等的：库里已有同一个文件时返回 200 和它所在的卡片（不论谁上传的，超时后重试也得到同一结果），新登记返回 201；
    与尚未装好的预装字体相同时没有卡片可返回，返回 422 并说明是哪个字体。按文件的样式归入卡片：同一家族的常规、粗体、斜体、粗斜体文件
    加入该家族用户上传的卡片（对应字形还空着时），其他字重（细体、黑体等）单独成卡片、名称带字重；同一字形的其他版本，或与预装字体、
    系统字体、保留名同名时另起家族名登记。返回文件所在的整张卡片。
    文件超过大小上限返回 413，内容无效返回 422；对象存储不可用返回 503。字体不可删除。
    """
    store = FontStore.for_settings(service.settings)
    try:
        content = await file.read(store.max_upload + 1)
        strip = await preview.read(1024 * 1024 + 1) if preview is not None else None
        if len(content) > store.max_upload:
            raise HTTPException(413, "Font exceeds the upload size limit.")
        if strip is not None and len(strip) > 1024 * 1024:
            raise HTTPException(413, "Preview exceeds the size limit.")
        try:
            font = await asyncio.to_thread(store.upload, content, strip or None, label, str(owner))
        except FontExists as exc:
            files = cards(store.listing()).get(exc.font.family, [])
            if not any(item.id == exc.font.id for item in files):
                raise HTTPException(422, str(exc)) from exc
            response.status_code = 200
            return font_info(files, system_file=installed_system_font(service))
        except FontRejected as exc:
            raise HTTPException(422, str(exc)) from exc
        except (OSError, SQLAlchemyError) as exc:
            raise HTTPException(503, "字体暂时无法保存，请稍后重试。") from exc
        return font_info(cards(store.listing())[font.family], system_file=installed_system_font(service))
    finally:
        await file.close()
        if preview is not None:
            await preview.close()


@router.get("/fonts/{font_id}", tags=["字体"], summary="读取字体文件")
async def catalog_font(font_id: str, service: Service) -> FileResponse:
    """提供已登记字体的文件供预览页、字体选择器与字体详情的字样注册（本机还没缓存时先从对象存储取回并校验哈希）；
    系统默认字体提供服务端安装的常规字重文件；未知字体返回 404。

    字体库共享、字体不可变，所以不按设备校验，可以长期缓存。
    """
    if font_id == SYSTEM_FONT_ID:
        regular = service.settings.font_regular
        path = regular if regular.is_file() else None
    else:
        path = await asyncio.to_thread(FontStore.for_settings(service.settings).ensure, font_id)
    if path is None:
        raise NotFound("font not found")
    types = {".ttf": "font/ttf", ".otf": "font/otf"}
    return FileResponse(
        path,
        media_type=types.get(path.suffix.lower(), "font/collection"),
        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=86400"},
    )


@router.get("/fonts/{font_id}/preview", tags=["字体"], summary="读取字体预览条")
async def font_preview(font_id: str, service: Service) -> FileResponse:
    """黑色透明底的 PNG 预览条，页面用 CSS mask 着色；字体不存在或没有预览返回 404。"""
    path = await asyncio.to_thread(FontStore.for_settings(service.settings).preview_path, font_id)
    if path is None:
        raise NotFound("preview not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/assets/{asset_id}", tags=["参考素材"], summary="读取历史参考图片")
def asset_image(asset_id: UUID, service: Service) -> Response:
    """读取已登记、去除元数据的 PNG，供历史聊天显示；缺失返回 404，损坏返回 409。"""
    try:
        payload = service.store.asset_path(asset_id).read_bytes()
    except FileNotFoundError as exc:
        raise NotFound("asset file not found") from exc
    except ValueError as exc:
        raise HTTPException(409, "Stored asset failed integrity verification.") from exc
    except OSError as exc:
        raise HTTPException(503, "Asset storage is unavailable; retry later.") from exc
    return Response(
        payload,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get(
    "/works/{work_id}/stream",
    tags=["聊天会话"],
    summary="订阅聊天事件",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def stream(
    work_id: UUID,
    service: Service,
    owner: Device,
    after: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    last_event_id: Annotated[str | None, Header(max_length=32)] = None,
) -> StreamingResponse:
    """通过 SSE 推送尚未确认送达的公开消息、任务状态和成功版本指针，连接关闭不会停止任务。

    新连接使用 `after`（客户端本地已保存的最后事件编号）；重连优先使用 `Last-Event-ID`。
    每条事件具有稳定 ID，客户端写入本地后用确认接口回传编号，服务端随即删除已送达的事件；空闲时每 15 秒发送心跳注释。
    """
    if last_event_id is not None:
        if not last_event_id.isascii() or not last_event_id.isdecimal() or int(last_event_id) > 9223372036854775807:
            raise HTTPException(422, "Invalid Last-Event-ID.")
        after = int(last_event_id)
    service.store.project(work_id, owner)
    return StreamingResponse(
        event_stream(service.store, work_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/works/{work_id}/events",
    response_model=list[WorkEvent],
    tags=["聊天会话"],
    summary="拉取未确认的聊天事件",
)
def work_events(
    work_id: UUID,
    service: Service,
    owner: Device,
    after: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
) -> list[WorkEvent]:
    """按编号升序返回 `after` 之后尚未确认送达的事件，每次最多一页；返回空列表表示已取完。

    供客户端在后台同步未打开的作品：写入本地后同样用确认接口回传编号。事件内容与 SSE 的 `data` 相同。
    """
    service.store.project(work_id, owner)
    return service.store.work_events(work_id, after)


class Acknowledgement(BaseModel):
    """The last event ID the client has committed to its local database."""

    cursor: int = Field(ge=0, le=9223372036854775807)


@router.post("/works/{work_id}/ack", status_code=204, tags=["聊天会话"], summary="确认事件已送达")
def acknowledge(work_id: UUID, request: Acknowledgement, service: Service, owner: Device) -> Response:
    """客户端把事件写入本地数据库后回传最后的事件编号；服务端删除该编号及之前的事件。

    全部事件送达后，已结束任务的进度、轮次与消息随之删除；最新任务保留输入，供重试与回答澄清使用。
    """
    service.store.acknowledge(work_id, request.cursor, owner)
    return Response(status_code=204)


@router.delete(
    "/works/{work_id}", status_code=204,
    tags=["聊天会话"], summary="删除字效聊天及关联数据",
)
async def delete_work(work_id: UUID, service: Service, owner: Device) -> Response:
    """停止后台任务并删除本设备的会话、所有版本及专属文件；不存在时同样返回 204。

    清理失败返回 503 并写服务端日志（含对象存储等根因），持久化删除标记禁止继续编辑；重试 DELETE 或服务重启后继续清理。
    其他会话引用的图片保留，已保存到资产库的 Sprite 不受影响。
    """
    try:
        if service.store.owner(work_id) != owner:
            return Response(status_code=204)
    except NotFound:
        return Response(status_code=204)
    try:
        await service.delete_work(work_id)
    except (OSError, SQLAlchemyError) as exc:
        logger.exception("Work deletion is incomplete: %s", work_id)
        raise HTTPException(503, "会话数据清理未完成，请重试删除。") from exc
    return Response(status_code=204)
