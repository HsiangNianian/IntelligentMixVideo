"""源码服务启动后从当前模板库补画 1 秒 PNG；独立 wheel 可导入随包截图。"""

import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..config_base import ENV_FILE
from ..remotion_templates.settings import Settings
from ..startup.settings import ServerSettings
from . import store
from .covers import MAX_BYTES
from .schema import TemplateCover

log = logging.getLogger(__name__)
# 源码部署直接复用仓库里的编辑预览；wheel 没有前端源码。
RENDER_SCRIPT = ENV_FILE.parent.parent / "client/scripts/backfill-template-covers.mjs"
PACKAGE_ARCHIVE = Path(__file__).with_name("template-covers-1s.zip")


def _pending(template_id: str, updated_at: datetime) -> bool:
    """在模板行锁内匹配时间并登记待补图状态。用户保存产生的版本始终优先。"""
    with store.initialize_schema().begin() as connection:
        target = store.templates.c.template_id == template_id
        row = connection.execute(select(store.templates.c.updated_at, store.templates.c.cover).where(target).with_for_update()).first()
        if row is None or row.updated_at.replace(tzinfo=UTC) != updated_at:
            return False
        if row.cover:
            return row.cover["revision"] == template_id and row.cover["source"] == "builtin" and not row.cover.get("published_revision")
        connection.execute(store.templates.update().where(target).values(cover=TemplateCover(revision=template_id).model_dump()))
        return True


def _render_covers(stop: Event) -> None:
    """登记旧模板并复用一个浏览器渲染整批；编辑器保存生成的版本由客户端独立重试。"""
    pending = False
    for template in store.list_templates():
        if stop.is_set():
            return
        if template.validation_error:
            continue
        if template.cover is None:
            if _pending(str(template.template_id), template.updated_at):
                pending = True
        # 初版补图用模板 ID 当发布版本，可能早于 IMS 文字绘制。修正一次后版本推进，后续启动跳过。
        elif template.cover.revision == str(template.template_id) and template.cover.source == "builtin":
            pending = True
    if not pending or stop.is_set():
        return

    env = os.environ.copy()
    browser = shutil.which(str(Settings().browser_executable))
    if browser:
        env["IMV_BROWSER_EXECUTABLE"] = str(Path(browser).resolve())
    api = f"http://127.0.0.1:{ServerSettings().port}"
    with subprocess.Popen(["bun", str(RENDER_SCRIPT), "--api", api, "--startup"],
                          cwd=RENDER_SCRIPT.parent.parent, env=env) as worker:
        while worker.poll() is None:
            if stop.wait(0.25):
                worker.terminate()
                try:
                    worker.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    worker.kill()
                return
        if worker.returncode:
            log.error("模板封面补图失败，请检查渲染日志、Bun 客户端依赖与 Chrome 配置")


def import_covers(stop: Event) -> None:
    """源码启动时直接渲染；只有随包后端从自带归档恢复，失败独立于 API 启动。"""
    if RENDER_SCRIPT.is_file():
        try:
            _render_covers(stop)
        except (OSError, ValueError, SQLAlchemyError):
            log.exception("启动模板封面补图失败")
        return
    if not PACKAGE_ARCHIVE.is_file():
        return
    try:
        with ZipFile(PACKAGE_ARCHIVE) as bundle:
            entries = json.loads(bundle.read("template-covers-1s-png/manifest.json"))
            if not isinstance(entries, dict):
                raise ValueError("封面清单应按模板 ID 索引")
            imported = 0
            for key, timestamp in entries.items():
                if stop.is_set():
                    break
                try:
                    template_id = UUID(key)
                    if not _pending(str(template_id), datetime.fromisoformat(timestamp)):
                        continue
                    name = f"template-covers-1s-png/{template_id}.png"
                    with bundle.open(name) as image:
                        content = image.read(MAX_BYTES + 1)
                    if len(content) > MAX_BYTES:
                        raise ValueError("PNG 超出大小限制")
                    store.publish_cover(template_id, template_id, content)
                    imported += 1
                except (HTTPException, ValueError, TypeError, KeyError, BadZipFile, OSError, SQLAlchemyError):
                    log.exception("模板 %s 封面更新失败", key)
            log.info("模板封面自动导入完成，共导入 %d 张", imported)
    except (OSError, BadZipFile, KeyError, ValueError):
        log.exception("无法读取模板封面部署包")
