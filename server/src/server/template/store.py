# MySQL 模板存储：列表保留每条记录的校验结果，详情只返回有效模板，事务保护写入。

from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from google.protobuf.json_format import ParseError
from sqlalchemy import JSON, Column, MetaData, String, Table, delete, select
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.exc import IntegrityError

from generated.imv.sprite.v1 import sprite_pb2 as sprite_pb

from ..database import get_engine
from ..remotion_templates import placement, sprite_store, sprites
from ..remotion_templates.store import Conflict
from .schema import EffectTrack, Template, TemplateSave, TemplateSummary, completeness_error, effect_catalog

# 模板元数据用于查询和约束，配置 JSON 保留完整编辑字段与可信效果快照。
metadata = MetaData()
templates = Table(
    "templates", metadata,
    Column("template_id", String(36), primary_key=True),
    Column("name", String(100, collation="utf8mb4_bin"), nullable=False, unique=True),
    Column("configuration", JSON, nullable=False),
    Column("created_at", DATETIME(fsp=6), nullable=False),
    Column("updated_at", DATETIME(fsp=6), nullable=False, index=True),
    mysql_charset="utf8mb4",
)
_schema_lock = Lock()
_ready_engine = None
# 占用 VFX 名额的 Sprite 分类；未指定类型的历史资产按视频特效处理，与资产库分类一致。
VFX_KINDS = {sprite_pb.SPRITE_KIND_VIDEO_OVERLAY, sprite_pb.SPRITE_KIND_UNSPECIFIED}


def initialize_schema():
    """首次模板请求创建缺失表；数据库已在应用启动时确保存在，失败后可重试。"""
    global _ready_engine
    engine = get_engine()
    with _schema_lock:
        if _ready_engine is not engine:
            metadata.create_all(engine)
            _ready_engine = engine
    return engine


def to_template(row) -> Template:
    """将 MySQL 的无时区 UTC 字段恢复为带时区的 API 响应。"""
    if not isinstance(row.configuration, dict):
        raise ValueError("模板配置必须为对象")
    if {"template_id", "name", "created_at", "updated_at"} & row.configuration.keys():
        raise ValueError("模板配置不能包含存储元数据")
    template = Template(
        **row.configuration, template_id=row.template_id, name=row.name,
        created_at=row.created_at.replace(tzinfo=UTC),
        updated_at=row.updated_at.replace(tzinfo=UTC),
    )
    issue = completeness_error(template)
    if issue:
        raise ValueError(issue)
    return template


def validation_message(error: ValueError) -> str:
    """业务错误只返回字段位置和原因，不回显整条模板内容。"""
    if isinstance(error, ValidationError):
        return "；".join(f"{'.'.join(map(str, item['loc'])) or 'template'}：{item['msg']}"
                        for item in error.errors(include_input=False, include_url=False))
    return str(error)


def summarize(row) -> TemplateSummary:
    """单条业务校验失败保留摘要；数据库及元数据损坏继续直接报错。"""
    summary = TemplateSummary(
        template_id=row.template_id, name=row.name,
        created_at=row.created_at.replace(tzinfo=UTC), updated_at=row.updated_at.replace(tzinfo=UTC),
    )
    try:
        template = to_template(row)
    except ValueError as error:
        summary.validation_error = validation_message(error)
        return summary
    summary.description = template.description
    summary.track_count = len(template.tracks)
    summary.title = next(track.editor.title for track in template.tracks if track.target == "title" and track.language == "zh")
    summary.subtitle = next(track.editor.subtitle for track in template.tracks if track.target == "subtitle" and track.language == "zh")
    return summary


def list_templates() -> list[TemplateSummary]:
    """按最近更新优先返回模板；同一时间使用 ID 稳定排序。"""
    with initialize_schema().connect() as connection:
        rows = connection.execute(select(templates).order_by(
            templates.c.updated_at.desc(), templates.c.template_id,
        ))
        return [summarize(row) for row in rows]


def get_template(template_id: UUID) -> Template:
    """查找单个模板；不存在返回 404。"""
    with initialize_schema().connect() as connection:
        row = connection.execute(select(templates).where(
            templates.c.template_id == str(template_id),
        )).first()
        if row is None:
            raise HTTPException(404, "模板不存在")
        try:
            return to_template(row)
        except ValueError as error:
            raise HTTPException(409, f"模板 {template_id} 不符合当前规则：{validation_message(error)}") from error


def check_slot(track: EffectTrack, kind: int) -> None:
    """片段占用的模板对象须与 Sprite 分类相符：字效作用于标题或字幕，转场占用视频交界处，其他分类两者都不设置（422）。

    旧 business_target 用途仍可独立叠加，不补写会替代 IMS 文字的 text_target；标题/字幕用途同样只属于字效，
    否则保存成功的模板会在每次合成渲染时失败（422）。
    作用于文字的字效由该文字对象提供文字与关键词（合成时由总线填入），所以不能再覆盖这两个参数（422）。
    """
    legacy_target = track.editor.remotion.business_target if track.editor.remotion else None
    if kind == sprite_pb.SPRITE_KIND_TEXT:
        if track.text_target is None and legacy_target not in {"title", "subtitle"}:
            raise HTTPException(422, f"Remotion 字效「{track.id}」须作用于标题或底部字幕")
        overrides = {item.key for item in track.editor.remotion.parameters} if track.editor.remotion else set()
        if bound := sorted(overrides & {sprites.TEXT_PROP, sprites.KEYWORDS_PROP}):
            raise HTTPException(422, f"Remotion 字效「{track.id}」的文字与关键词取自所作用的对象，不能覆盖 {'、'.join(bound)}")
    elif track.text_target is not None or legacy_target in {"title", "subtitle"}:
        raise HTTPException(422, f"Remotion 片段「{track.id}」不是字效，不能作用于文字")
    if kind == sprite_pb.SPRITE_KIND_TRANSITION_OVERLAY:
        if track.transition_boundary is None:
            raise HTTPException(422, f"Remotion 转场「{track.id}」须指定视频交界处")
    elif track.transition_boundary is not None:
        raise HTTPException(422, f"Remotion 片段「{track.id}」不是转场，不能占用视频交界处")


def save_template(data: TemplateSave) -> Template:
    """原子创建或完整覆盖；锁定待更新行，并以数据库唯一约束处理并发重名。

    Remotion 片段引用的 Sprite 必须已发布，且画布尺寸与帧率一致（422）：叠加渲染要把它们叠成同一个视频，
    不一致的模板到总线渲染时才会失败，所以在保存时拒绝。每个片段的参数覆盖与宿主变换也按所引用 Sprite 的
    发布参数契约检查（422）。片段按分类占用模板对象：字效作用于标题或字幕（不再覆盖文字与关键词）、转场占用视频交界处，
    Remotion 视频特效与 IMS VFX 合计最多一个（422）。被模板引用的 Sprite 不能删除（`templates_using`），
    只有与删除同时发生的保存才可能留下失效引用，届时总线渲染以 sprite_missing 报告。
    """
    # 创建和更新都检查必需文字对象，检查通过后才访问资产及模板数据库。
    if issue := completeness_error(data):
        raise HTTPException(422, issue)
    clips = [track for track in data.tracks if track.target == "remotion"]
    clip_ids = {str(track.sprite_id) for track in clips}
    if clip_ids:
        found = sprite_store.records(clip_ids)
        if unknown := clip_ids - found.keys():
            raise HTTPException(422, f"Remotion 资产尚未发布：{'、'.join(sorted(unknown))}")
        published = {}
        for sprite_id, record in found.items():
            try:
                published[sprite_id] = sprites.published_from(record)
            except (Conflict, ValueError, ParseError, TypeError):
                raise HTTPException(422, f"Remotion 资产记录已损坏：{sprite_id}") from None
        canvases = {(item.canvas.width, item.canvas.height, item.canvas.fps) for item in published.values()}
        if len(canvases) > 1:
            sizes = "、".join(sorted(f"{width}x{height}@{fps}" for width, height, fps in canvases))
            raise HTTPException(422, f"Remotion 资产的画布尺寸或帧率不一致，无法叠成同一个视频：{sizes}")
        for track in clips:
            try:
                placement.resolve(published[str(track.sprite_id)], track.editor.remotion)
            except ValueError as exc:
                raise HTTPException(422, f"Remotion 片段「{track.id}」：{exc}") from None
            check_slot(track, published[str(track.sprite_id)].kind)
        effects = sum(published[str(track.sprite_id)].kind in VFX_KINDS for track in clips)
        if effects + sum(track.target == "vfx" for track in data.tracks) > 1:
            raise HTTPException(422, "VFX 最多一个，Remotion 视频特效也占用这个名额")
    template_id = str(data.template_id or uuid4())
    catalog = effect_catalog()
    configuration = data.model_dump(mode="json", by_alias=True, exclude={"template_id", "name"})
    configuration["effects"] = [catalog[key].model_dump(mode="json") for key in data.effect_ids]
    try:
        with initialize_schema().begin() as connection:
            now = datetime.now(UTC).replace(tzinfo=None)
            values = {"name": data.name, "configuration": configuration, "updated_at": now}
            if data.template_id:
                existing = connection.execute(select(templates.c.configuration).where(
                    templates.c.template_id == template_id,
                ).with_for_update()).first()
                if existing is None:
                    raise HTTPException(404, "模板不存在")
                # 旧客户端省略新配置时保留；开关的显式 false 仍正常保存。
                for field in ("split_screen", "avatar_effects"):
                    if field not in data.model_fields_set and field in existing.configuration:
                        configuration[field] = existing.configuration[field]
                if data.split_screen is not None and "layout" not in data.split_screen.model_fields_set:
                    configuration["split_screen"]["layout"] = existing.configuration.get("split_screen", {}).get("layout", "split")
                connection.execute(templates.update().where(
                    templates.c.template_id == template_id,
                ).values(**values))
            else:
                connection.execute(templates.insert().values(
                    template_id=template_id, created_at=now, **values,
                ))
            row = connection.execute(select(templates).where(
                templates.c.template_id == template_id,
            )).one()
            result = to_template(row)
        return result
    except IntegrityError as exc:
        if exc.orig.args[0] == 1062:
            raise HTTPException(409, "模板名称已存在，请使用其他名称") from None
        raise


def templates_using(sprite_id: str) -> list[str]:
    """按名称排序返回 `remotion` 轨道引用该 Sprite 的模板；按原始配置检查，无效模板里的引用同样计入。"""
    with initialize_schema().connect() as connection:
        rows = connection.execute(select(templates.c.name, templates.c.configuration)).all()
    return sorted(
        row.name for row in rows
        if isinstance(row.configuration, dict) and any(
            isinstance(track, dict) and track.get("target") == "remotion" and track.get("sprite_id") == sprite_id
            for track in row.configuration.get("tracks") or []
        )
    )


def delete_template(template_id: UUID) -> None:
    """事务删除指定模板；重复删除返回 404，不删除任何其他记录。"""
    with initialize_schema().begin() as connection:
        result = connection.execute(delete(templates).where(
            templates.c.template_id == str(template_id),
        ))
        if result.rowcount == 0:
            raise HTTPException(404, "模板不存在")
