"""模板 API：统一 /template 前缀，POST 通过可选 template_id 区分创建与更新。"""

from uuid import UUID

from fastapi import APIRouter, Response

from . import store
from .schema import Template, TemplateSave

# 静态集合路径和 UUID 详情路径组成约定的四个接口，无效果目录接口。
router = APIRouter(prefix="/template", tags=["模板管理"])


@router.get("", response_model=list[Template])
def list_templates() -> list[Template]:
    """返回共享模板库，空库返回空数组。"""
    return store.list_templates()


@router.post("", response_model=Template, responses={201: {"model": Template}})
def save_template(data: TemplateSave, response: Response) -> Template:
    """无 ID 创建返回 201；有 ID 完整更新返回 200，包含重命名。"""
    result = store.save_template(data)
    response.status_code = 200 if data.template_id else 201
    return result


@router.get("/{template_id}", response_model=Template)
def get_template(template_id: UUID) -> Template:
    """返回完整配置和服务端效果快照，供编辑器回填。"""
    return store.get_template(template_id)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: UUID) -> None:
    """删除模板，成功不返回响应体。"""
    store.delete_template(template_id)
