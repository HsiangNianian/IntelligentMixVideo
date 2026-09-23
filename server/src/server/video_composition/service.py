"""单进程合成调度：持久化阶段、有限恢复和异步上游调用，退出先收束任务与数据库线程。"""

import asyncio
from datetime import UTC, datetime, timedelta
import logging
import json
import secrets
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import SecretStr, TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from ..segmentation import segment
from ..segmentation.settings import Settings as SegmentationSettings
from ..template.store import get_template
from . import ims, store, zos
from .errors import CompositionError, remaining
from .execution_log import exception_details
from .matching import Matching, payload, validated_matches
from .schema import CompositionRequest, MatchCallback, PositiveSeconds, Segment, TaskResponse
from .settings import ClientSettings, Settings, ZosSettings
from .timeline import build_timeline, validate_segments

logger = logging.getLogger(__name__)

# 首次失败后再投递三次；等待时间落库，由已有调度器恢复，不占用执行名额。
NOTIFICATION_RETRY_DELAYS = (5, 15, 45)


def preflight(config: ClientSettings | None = None) -> Settings:
    """受理前只检查配置，不执行成本调用；延迟导入保持现有 ASR 一次性加载行为。"""
    settings = Settings(**config.model_dump()) if config is not None else Settings()
    ZosSettings()
    SegmentationSettings()
    from ..asr.asr import settings as asr_settings

    if not asr_settings.dashscope_api_key.get_secret_value().strip():
        raise ValueError("缺少 ASR 配置")
    return settings


def deadline(seconds: float) -> str:
    """持久化带时区截止时间，重启不重置预算。"""
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


class LostOwnership(Exception):
    """条件更新失败后停止本执行，迟到结果不得覆盖终态或发起云端提交。"""


class ClientConfigMissing(HTTPException):
    """客户端 IMS 凭据只存在于任务内存中，服务重启后没有快照可用。

    查询可携带请求头重试，因此保持 503；终态通知没有补带凭据的途径，调用方据此判定为最终失败。
    """

    def __init__(self, message: str):
        """固定使用 503，避免查询把可重试的缺配置与任务本身失败混淆。"""
        super().__init__(503, message)


class Runtime:
    """有界并发的进程内调度器；数据库是任务来源，HTTP 查询不会启动处理。"""

    def __init__(self):
        """保留后台协程与线程引用；同一应用进程只有一个调度器。"""
        self.settings: Settings | None = None
        self.active: dict[str, asyncio.Task] = {}
        self.client_configs: dict[str, ClientSettings] = {}
        self.threads: set[asyncio.Task] = set()
        self.thread_limit = asyncio.Semaphore(8)
        self.stopping = False
        self.dispatcher: asyncio.Task | None = None
        self.wake = asyncio.Event()

    async def sync(self, function, *args, **kwargs):
        """在线程中运行完整同步操作；调用方取消后继续跟踪线程，连接不跨 await。"""
        async def invoke():
            """占有并发额度直到真实线程结束；退出后不启动尚在排队的工作。"""
            async with self.thread_limit:
                if self.stopping:
                    raise asyncio.CancelledError
                return await asyncio.to_thread(function, *args, **kwargs)

        task = asyncio.create_task(invoke())
        self.threads.add(task)
        task.add_done_callback(self._thread_done)
        return await asyncio.shield(task)

    def _thread_done(self, task: asyncio.Task) -> None:
        """回收完成引用并消费取消后线程异常，不打印可能包含凭证的原始消息。"""
        self.threads.discard(task)
        if not task.cancelled():
            task.exception()

    async def start(self) -> None:
        """数据库已初始化后建表，并从持久化非终态开始有限恢复。"""
        await self.sync(store.initialize_schema)
        self.dispatcher = asyncio.create_task(self._dispatch())

    async def close(self) -> None:
        """先禁止新工作、取消异步任务，再等真实同步线程结束，之后方可关闭共享 Engine。"""
        self.stopping = True
        tasks = [*self.active.values(), *([self.dispatcher] if self.dispatcher else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*tuple(self.threads), return_exceptions=True)
        self.active.clear()
        self.client_configs.clear()

    async def accept(self, request: CompositionRequest, callback_base_url: str, raw_request: dict | None = None, *, config: ClientSettings | None = None) -> dict:
        """优先保存配置的公网地址；配置检查和事务成功后才返回 ID，由后台继续处理。"""
        if self.stopping:
            raise HTTPException(503, "合成服务正在停止")
        try:
            settings = await self.sync(preflight, config) if config is not None else await self.sync(preflight)
        except (ValueError, RuntimeError):
            raise HTTPException(503, "视频合成配置不完整或无效，请检查服务端 .env") from None
        # 调度器只保留运行策略；每项云端凭据绑定独立任务，不充当其他任务的默认值。
        self.settings = settings.model_copy(update={key: SecretStr("") for key in ("ims_access_key_id", "ims_access_key_secret", "ims_security_token")})
        callback_base_url = settings.composition_public_base_url or callback_base_url
        task_id = str(uuid4())
        output = settings.output()
        if config is not None:
            output["client_config"] = True
            self.client_configs[task_id] = config
        try:
            record = await self.sync(store.create, request.model_dump(mode="json", by_alias=True), output, callback_base_url, raw_request, **({"task_id": task_id} if config is not None else {}))
        except BaseException:
            self.client_configs.pop(task_id, None)
            raise
        self.wake.set()
        return record

    async def log(self, record: dict, event: str, **details) -> None:
        """追加查询/外部调用诊断；日志存储失败只记进程日志，不掩盖原响应或改变终态。"""
        try:
            await self.sync(store.add_log, record, event, details)
        except Exception as exc:
            logger.error("合成任务 %s 执行日志未保存：%s", record["task_id"], type(exc).__name__)

    async def step(self, record: dict, name: str, operation, inputs: dict):
        """记录实际异步/线程步骤的输入、输出与根因；返回原对象，保持原异常与取消语义。"""
        await self.log(record, "step_started", step=name, input=inputs)
        try:
            output = await operation()
        except asyncio.CancelledError:
            await self.log(record, "step_cancelled", step=name)
            raise
        except Exception as exc:
            await self.log(record, "step_failed", step=name, **exception_details(exc))
            raise
        await self.log(record, "step_finished", step=name, output=output)
        return output

    async def http_log(self, record: dict, step: str, response: httpx.Response) -> None:
        """记录真实匹配/通知 HTTP 输入输出，读取响应体后仍供原调用方使用，不记录鉴权头。"""
        await response.aread()
        request = response.request
        try:
            body = response.json()
        except ValueError:
            body = response.text
        await self.log(record, "http_response", step=step,
                       input={"method": request.method, "url": str(request.url),
                              "body": json.loads(request.content) if request.content else None},
                       output={"http_status": response.status_code, "body": body})

    async def response(self, record: dict, *, source: str = "query", config: ClientSettings | None = None) -> TaskResponse:
        """新成片复用持久 ZOS 地址；历史成片沿用 IMS 动态取址。"""
        result = None
        await self.log(record, "response_started", source=source, input={"task_id": record["task_id"]})
        saved = record["data"].get("result") or {}
        if record["status"] == "succeeded" and record["data"].get("zos_object_key") and saved.get("videoUrl"):
            result = {"videoUrl": saved["videoUrl"], "durationSeconds": saved["durationSeconds"]}
        elif (record["status"] == "succeeded" and source == "notification" and saved.get("videoUrl")
                and record["data"].get("video_url_expires_at", "") > datetime.now(UTC).isoformat()):
            result = {"videoUrl": saved["videoUrl"], "durationSeconds": saved["durationSeconds"]}
        elif record["status"] == "succeeded":
            # 客户端凭据只在任务内存中；重启后通知已无取址途径，查询仍可补带请求头重试。
            if source == "notification":
                config = self.client_configs.get(record["task_id"])
            if record["data"]["output"].get("client_config") and config is None:
                await self.log(record, "playback_failed", source=source, http_status=503, reason="client_config_missing")
                raise ClientConfigMissing("成片已完成，播放地址暂不可用，请携带客户端 IMS 配置后重试")
            try:
                settings = await self.sync(lambda: Settings(**config.model_dump()) if config is not None else Settings())
                provider = ims.IMS(settings, region_id=record["data"]["output"]["region_id"])
                async with asyncio.timeout(settings.composition_http_timeout_seconds):
                    url = await self.step(record, "playback", lambda: provider.result_url(record["data"]["result"]["mediaId"]),
                                          {"media_id": record["data"]["result"]["mediaId"], "source": source})
                result = {"videoUrl": url, "durationSeconds": record["data"]["result"]["durationSeconds"]}
            except Exception as exc:
                await self.log(record, "playback_failed", source=source, http_status=503, **exception_details(exc))
                raise HTTPException(503, "成片已完成，播放地址暂不可用，请稍后重试") from None
        response = TaskResponse(
            task_id=record["task_id"], status=record["status"], stage=record["stage"],
            result=result, error=record["data"].get("error"),
            created_at=record["created_at"], updated_at=record["updated_at"],
        )
        await self.log(record, "response_ready", source=source, http_status=200, output=response.model_dump(mode="json", by_alias=True))
        return response

    async def receive_match_callback(self, task_id: str, token: str, callback: MatchCallback, raw: dict | None = None) -> None:
        """用任务令牌鉴权并条件推进；早到、重复回调及补查竞争均由现有版本号收束。"""
        for _ in range(3):
            record = await self.sync(store.get, task_id)
            if record is None:
                raise HTTPException(404, "合成任务不存在")
            data = record["data"]
            expected = data.get("match_callback_token")
            if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
                raise HTTPException(403, "匹配回调令牌无效")
            if data.get("match_id") and data["match_id"] != callback.taskId:
                raise HTTPException(409, "匹配任务 ID 不符")
            await self.log(record, "match_callback_received", input=raw if raw is not None else callback.model_dump(mode="json"))
            if record["stage"] != "matching" or record["status"] in ("succeeded", "failed"):
                await self.log(record, "match_callback_processed", output={"http_status": 200, "status": "ok", "ignored": True})
                return
            if callback.status == "success":
                try:
                    matches = validated_matches(data["segmentation"]["segments"], callback.result.model_dump())
                except (ValueError, KeyError, TypeError):
                    await self.log(record, "match_callback_rejected", http_status=422, reason="匹配结果与输入切片不一致")
                    raise HTTPException(422, "匹配结果与输入切片不一致") from None
                updated = await self.sync(store.advance, record, "assembling", matches=matches, match_id=callback.taskId)
            else:
                updated = await self.sync(store.advance, record, "failed", status="failed", match_id=callback.taskId,
                                          error={"code": "matching_failed", "message": "素材匹配任务失败", "stage": "matching"})
            if updated is not None:
                await self.log(updated, "match_callback_processed", output={"http_status": 200, "status": "ok"})
                self.wake.set()
                return
        raise HTTPException(503, "匹配回调暂未保存，请重试")

    async def _dispatch(self) -> None:
        """按可用名额扫描数据库；故障时暂停推进并保留可恢复状态，不无限创建协程。"""
        while not self.stopping:
            self.wake.clear()
            try:
                capacity = (self.settings.composition_concurrency if self.settings else 2) - len(self.active)
                if capacity > 0:
                    records = await self.sync(store.pending, list(self.active), capacity)
                    if records and self.settings is None and any(not item["data"]["output"].get("client_config") for item in records):
                        settings = await self.sync(Settings)
                        self.settings = settings.model_copy(update={key: SecretStr("") for key in ("ims_access_key_id", "ims_access_key_secret", "ims_security_token")})
                        records = records[:self.settings.composition_concurrency]
                    for record in records:
                        task = asyncio.create_task(self._execute(record))
                        self.active[record["task_id"]] = task
            except Exception as exc:
                logger.error("合成调度暂不可用：%s", type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except TimeoutError:
                pass

    async def _execute(self, record: dict) -> None:
        """每个任务独立保存阶段错误；持久化故障有限重试，之后交由原状态恢复。"""
        job = Job(self, record, self.settings)
        try:
            if record["status"] in ("succeeded", "failed"):
                await self._notify(record)
                return
            # 客户端凭据只在任务内存中；服务重启后缺失即不可恢复，明确失败且不改用服务器账号。
            needs_client_config = bool(record["data"]["output"].get("client_config"))
            config = self.client_configs.get(record["task_id"]) if needs_client_config else None
            if needs_client_config and config is None:
                raise CompositionError("client_config_missing", "服务端重启后不再持有该任务的客户端 IMS 凭据，任务不可恢复，请重新提交", record["stage"])
            job.settings = await self.sync(lambda: Settings(**config.model_dump()) if config is not None else Settings())
            if self.settings is None:
                self.settings = job.settings.model_copy(update={key: SecretStr("") for key in ("ims_access_key_id", "ims_access_key_secret", "ims_security_token")})
            await job.run()
        except LostOwnership:
            pass
        except Exception as exc:
            if isinstance(exc, CompositionError):
                error = exc.error
            else:
                error = {"stage": job.record["stage"], "code": "storage_error" if isinstance(exc, SQLAlchemyError) else "stage_error",
                         "message": "任务存储暂不可用，未继续提交" if isinstance(exc, SQLAlchemyError) else "当前阶段处理失败，请检查输入及服务配置"}
            # 只记录类型和本地 ID，供应商异常常带签名 URL、请求正文或凭证。
            logger.error("合成任务 %s 失败：%s / %s", record["task_id"], error["stage"], type(exc).__name__)
            for attempt in range(3):
                try:
                    await job.save("failed", status="failed", error=error, diagnostics=exception_details(exc))
                    break
                except LostOwnership:
                    break
                except Exception:
                    if attempt == 2:
                        logger.error("合成任务 %s 错误暂无法落库，保留原阶段恢复", record["task_id"])
                    else:
                        await asyncio.sleep(self.settings.composition_poll_seconds if self.settings else 2)
        finally:
            try:
                latest = await self.sync(store.get, record["task_id"])
                if latest and latest["status"] in ("succeeded", "failed") and latest["data"].get("notification_status") != "pending":
                    self.client_configs.pop(record["task_id"], None)
            finally:
                self.active.pop(record["task_id"], None)
                self.wake.set()

    async def _notify(self, record: dict) -> None:
        """终态通知失败后最多重试三次；次数和下次时间落库，不回退合成结果。"""
        try:
            claimed = await self.sync(store.notification_status, record, "sending")
            if claimed is None:
                return
            status = "failed"
            attempt = claimed["data"]["notification_attempts"]
            permanent = False
            details = {"attempt": attempt}
            try:
                result = await self.response(claimed, source="notification")
                # 通知使用调用方的四字段协议，查询及持久化终态仍使用 succeeded。
                body = {"taskId": str(result.task_id), "status": "succeed" if result.status == "succeeded" else "failed",
                        "videoUrl": result.result.video_url if result.result else None,
                        "errorMessage": result.error.message if result.error else None}
                await self.log(claimed, "notification_started", input={"url": claimed["data"]["request"]["callbackUrl"],
                                                                      "body": body})
                async with asyncio.timeout(self.settings.composition_http_timeout_seconds if self.settings else 30):
                    async with httpx.AsyncClient(timeout=self.settings.composition_http_timeout_seconds if self.settings else 30, follow_redirects=False) as client:
                        async with client.stream("POST", claimed["data"]["request"]["callbackUrl"],
                                                 json=body) as response:
                            details["http_status"] = response.status_code
                            if 200 <= response.status_code < 300:
                                status = "sent"
                            await self.http_log(claimed, "notification", response)
            except Exception as exc:
                # 客户端凭据不落库，重启后重试同样取不到地址；按最终失败处理，不做注定失败的重试。
                permanent = isinstance(exc, ClientConfigMissing)
                details.update(exception_details(exc))
                logger.warning("合成任务 %s 终态通知未送达：%s", record["task_id"], type(exc).__name__)
            if status == "failed" and not permanent and attempt <= len(NOTIFICATION_RETRY_DELAYS):
                status = "pending"
                details["next_at"] = deadline(NOTIFICATION_RETRY_DELAYS[attempt - 1])
            await self.sync(store.notification_status, claimed, status, details)
        except Exception as exc:
            await self.log(record, "notification_storage_failed", **exception_details(exc))
            logger.error("合成任务 %s 终态通知状态暂无法保存：%s", record["task_id"], type(exc).__name__)


class Job:
    """一条固定合成流程；当前记录随成功事务更新，进程中断只恢复已确认的上游句柄。"""

    def __init__(self, runtime: Runtime, record: dict, settings: Settings):
        """持有本次业务快照和配置，不共享数据库 Connection。"""
        self.runtime, self.record, self.settings = runtime, record, settings

    async def save(self, stage: str, **data) -> None:
        """任何下一步副作用都以成功的条件更新为前提。"""
        updated = await self.runtime.sync(store.advance, self.record, stage, **data)
        if updated is None:
            raise LostOwnership
        self.record = updated

    async def run(self) -> None:
        """恢复只读查询和确定性组装；无恢复句柄的成本阶段明确标记中断。"""
        if self.record["status"] in ("succeeded", "failed"):
            return
        stage = self.record["stage"]
        await self.runtime.log(self.record, "execution_started", resumed=stage != "queued")
        if stage in ("asr", "segmentation"):
            raise CompositionError("interrupted", "任务在不可恢复的本地阶段中断，未重放成本调用", stage)
        if stage in ("queued", "template"):
            await self.prepare()
            await self.match(submit=True)
        elif stage == "matching":
            await self.match(submit=False)
        if self.record["stage"] == "assembling":
            await self.assemble()
        if self.record["stage"] in ("submitting", "rendering"):
            await self.render()

    async def prepare(self) -> None:
        """先在 template 阶段读取模板，快照落库后进入 ASR；模板读取中断可安全重试。"""
        config = self.runtime.client_configs.get(self.record["task_id"])
        await self.runtime.sync(preflight, config) if config is not None else await self.runtime.sync(preflight)
        if not self.record["data"].get("callback_base_url"):
            raise CompositionError("callback_address_missing", "任务缺少受理时的基础地址，无法生成匹配回调地址", "matching")
        request = CompositionRequest.model_validate(self.record["data"]["request"])
        await self.save("template")
        try:
            template = await self.runtime.step(self.record, "template", lambda: self.runtime.sync(get_template, request.style_id),
                                               {"style_id": str(request.style_id)})
        except HTTPException as exc:
            raise CompositionError("template_not_found" if exc.status_code == 404 else "template_error", "模板不存在或无法读取", "template") from None
        except Exception:
            raise CompositionError("template_error", "模板读取失败", "template") from None
        await self.save("asr", template=template.model_dump(mode="json", by_alias=True))
        from ..asr import transcribe

        asr_result = await self.runtime.step(self.record, "asr",
            lambda: transcribe(request.audio_url, wait_seconds=self.settings.composition_asr_wait_seconds), {"audio_url": request.audio_url})
        duration_ms = asr_result.get("properties", {}).get("original_duration_in_milliseconds")
        if type(duration_ms) is not int or duration_ms <= 0:
            raise CompositionError("audio_duration_missing", "ASR 结果缺少有效的实际音频总时长", "asr")
        await self.save("segmentation", duration_ms=duration_ms)
        segment_input = {"script": request.text, "asr_result": asr_result}
        segmented = await self.runtime.step(self.record, "segmentation", lambda: self.runtime.sync(segment, segment_input), segment_input)
        segments = TypeAdapter(list[Segment]).validate_python(segmented["segments"])
        validate_segments(segments, duration_ms)
        token = secrets.token_urlsafe(32)
        callback_url = f"{self.record['data']['callback_base_url']}/api/v1/video-compositions/{self.record['task_id']}/segment-match-callback?token={token}"
        await self.save("matching", segmentation=segmented, match_callback_token=token,
                        match_request=payload(self.record["task_id"], request, segments, callback_url),
                        match_deadline=deadline(self.settings.composition_match_wait_seconds))

    async def match(self, *, submit: bool) -> None:
        """只提交一次并等待已落库回调；沿用原截止时间，超时持久化标记后只补查一次。"""
        data = self.record["data"]
        async def received(response):
            """匹配 HTTP 完整结果含未命中原因，在 Pydantic 精简字段前记录。"""
            await self.runtime.http_log(self.record, "matching", response)

        async with httpx.AsyncClient(timeout=self.settings.composition_http_timeout_seconds, follow_redirects=False,
                                     event_hooks={"response": [received]}) as client:
            matching = Matching(client, self.settings)
            if submit:
                remaining(data["match_deadline"], "matching")
                try:
                    upstream_id, url = await self.runtime.step(self.record, "match_submit",
                        lambda: matching.submit(data["match_request"]), data["match_request"])
                except CompositionError as exc:
                    if exc.error["code"] != "matching_submission_unknown":
                        raise
                    # 响应丢失时仍允许有效回调确认受理，绝不重发 POST。
                else:
                    await self.save("matching", match_id=upstream_id, match_url=url)
            while True:
                current = await self.runtime.sync(store.get, self.record["task_id"])
                if current is None:
                    raise LostOwnership
                self.record = current
                if current["stage"] != "matching":
                    return
                data = current["data"]
                wait = (datetime.fromisoformat(data["match_deadline"]) - datetime.now(UTC)).total_seconds()
                if wait <= 0:
                    break
                await asyncio.sleep(min(self.settings.composition_poll_seconds, wait))
            if "match_id" not in data:
                raise CompositionError("matching_submission_unknown", "匹配受理结果未知，未自动重复提交", "matching")
            if data.get("match_query_started"):
                raise CompositionError("matching_query_unknown", "单次补查已开始但未保存结果，未重复查询", "matching")
            await self.save("matching", match_query_started=True)
            result = await self.runtime.step(self.record, "match_query",
                lambda: matching.query_once(data["match_id"], data["match_url"]), {"task_id": data["match_id"], "url": data["match_url"]})
            try:
                matches = validated_matches(data["segmentation"]["segments"], result)
            except (ValueError, KeyError, TypeError):
                raise CompositionError("matching_contract_error", "匹配结果缺少合法片段或与输入切片不一致", "matching") from None
            await self.save("assembling", matches=matches)

    async def assemble(self) -> None:
        """组装只读业务快照，提交前持久化完整请求、稳定 ClientToken 和总截止时间。"""
        data = self.record["data"]
        inputs = dict(request=data["request"], template=data["template"], segments=data["segmentation"]["segments"],
                      matches=data["matches"], duration_ms=data["duration_ms"],
                      **{key: data["output"][key] for key in ("width", "height", "fps")})
        await self.runtime.log(self.record, "step_started", step="assembling", input=inputs)
        try:
            timeline, warnings = build_timeline(**inputs)
        except Exception as exc:
            await self.runtime.log(self.record, "step_failed", step="assembling", **exception_details(exc))
            raise
        await self.runtime.log(self.record, "step_finished", step="assembling", output={"timeline": timeline, "warnings": warnings})
        async with asyncio.timeout(self.settings.composition_http_timeout_seconds):
            storage_location = await self.runtime.step(self.record, "ims_storage",
                lambda: ims.IMS(self.settings, region_id=data["output"]["region_id"]).storage_location(), {"region_id": data["output"]["region_id"]})
        await self.save("submitting", timeline=timeline, timeline_warnings=warnings,
                        ims_request=ims.submission(self.record["task_id"], timeline, data["output"], storage_location),
                        ims_deadline=deadline(self.settings.composition_render_wait_seconds), submit_attempts=0)

    async def render(self) -> None:
        """IMS 原生异步提交与有界查询；重启和短暂故障均不更换已持久化的 token/payload。"""
        provider = ims.IMS(self.settings, region_id=self.record["data"]["output"]["region_id"])
        while self.record["stage"] == "submitting":
            data = self.record["data"]
            budget = remaining(data["ims_deadline"], "submitting")
            attempts = data["submit_attempts"]
            if attempts >= 2:
                raise CompositionError("ims_submission_unknown", "IMS 受理结果未知，已停止重试；云端任务可能仍在执行", "submitting")
            await self.save("submitting", submit_attempts=attempts + 1)
            try:
                async with asyncio.timeout(min(budget, self.settings.composition_http_timeout_seconds)):
                    response = await self.runtime.step(self.record, "ims_submit", lambda: provider.submit(data["ims_request"]), data["ims_request"])
                job_id = response["JobId"]
                if not isinstance(job_id, str) or not job_id:
                    raise ValueError("IMS 缺少 JobId")
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if isinstance(status, int) and 400 <= status < 500 and status != 429:
                    raise CompositionError("ims_rejected", f"IMS 拒绝合成请求（HTTP {status}）", "submitting") from None
                await asyncio.sleep(min(self.settings.composition_poll_seconds, remaining(data["ims_deadline"], "submitting")))
                continue
            await self.save("rendering", ims_job_id=job_id)

        data = self.record["data"]
        if data.get("result"):
            await self.wait_for_result(provider)
            return
        failures = 0
        while self.record["stage"] == "rendering":
            budget = remaining(data["ims_deadline"], "rendering")
            try:
                async with asyncio.timeout(min(budget, self.settings.composition_http_timeout_seconds)):
                    response = await self.runtime.step(self.record, "ims_query", lambda: provider.get(data["ims_job_id"]), {"job_id": data["ims_job_id"]})
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if isinstance(status, int) and 400 <= status < 500 and status != 429:
                    raise CompositionError("ims_query_error", f"IMS 查询失败（HTTP {status}）", "rendering") from None
                failures += 1
                remaining(data["ims_deadline"], "rendering")
                if failures >= 3:
                    raise CompositionError("ims_unavailable", "无法确认 IMS 结果，云端任务可能仍在执行", "rendering") from None
            else:
                failures = 0
                job = response["MediaProducingJob"]
                if job.get("JobId") != data["ims_job_id"]:
                    raise CompositionError("ims_contract_error", "IMS 查询任务身份不符", "rendering")
                if job["Status"] == "Success":
                    media_id = job.get("MediaId")
                    if not isinstance(media_id, str) or not media_id:
                        raise CompositionError("ims_contract_error", "IMS 成功结果缺少成片媒资 ID", "rendering")
                    duration = TypeAdapter(PositiveSeconds).validate_python(job["Duration"])
                    # 先保存云端结果供重启恢复；拿到播放地址之前仍处于 processing。
                    await self.save("rendering", result={"mediaId": media_id, "durationSeconds": duration})
                    await self.wait_for_result(provider)
                    return
                if job["Status"] == "Failed":
                    raise CompositionError("ims_failed", "IMS 合成任务失败，请核对媒体可读性和时间线配置", "rendering")
                if job["Status"] not in ("Init", "Queuing", "Processing"):
                    raise CompositionError("ims_contract_error", "IMS 返回未知状态", "rendering")
            await asyncio.sleep(min(self.settings.composition_poll_seconds, remaining(data["ims_deadline"], "rendering")))

    async def wait_for_result(self, provider: ims.IMS) -> None:
        """在原渲染期限内取址并转存 ZOS；恢复不重提渲染，成功后才通知。"""
        data = self.record["data"]
        last_stage = "playback"
        while True:
            try:
                budget = remaining(data["ims_deadline"], "playback")
            except CompositionError:
                if last_stage == "zos_upload":
                    raise CompositionError("zos_upload_timeout", "云端渲染已完成，但成片转存 ZOS 持续失败", "zos_upload") from None
                raise CompositionError("playback_timeout", "云端渲染已完成，但等待成片地址超时", "playback") from None
            try:
                last_stage = "playback"
                async with asyncio.timeout(min(budget, self.settings.composition_http_timeout_seconds)):
                    url = await self.runtime.step(self.record, "playback", lambda: provider.result_url(data["result"]["mediaId"]),
                                                  {"media_id": data["result"]["mediaId"], "source": "completion"})
                last_stage = "zos_upload"
                key, public_url = await self.runtime.step(
                    self.record, "zos_upload",
                    lambda: self.runtime.sync(zos.copy_video, url, self.record["task_id"], ZosSettings(),
                                              self.settings.composition_http_timeout_seconds),
                    {"media_id": data["result"]["mediaId"]},
                )
            except Exception:
                await asyncio.sleep(min(self.settings.composition_poll_seconds, budget))
                continue
            await self.save("completed", status="succeeded", result={**data["result"], "videoUrl": public_url},
                            zos_object_key=key)
            return
