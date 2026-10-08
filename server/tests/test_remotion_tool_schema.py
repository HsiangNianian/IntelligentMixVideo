"""PR76 参数 Schema 的引用策略：只解析文档内部引用，外部引用被拒绝且不触发网络读取。

在 server/ 下执行 `uv run --locked pytest tests/test_remotion_tool_schema.py -v`；
用例完全离线，不访问模型、浏览器、数据库或真实网络。
"""

import urllib.request

import pytest
from referencing.exceptions import Unresolvable

from server.remotion_templates.tools.contracts import ComponentDefinition
from server.remotion_templates.tools.schema import (
    validate_component_contract,
    validate_parameters,
)

CODE = "export default function Label(){return <div>Title</div>}"


def component(schema: dict, defaults: dict) -> ComponentDefinition:
    """构造只关心参数 Schema 的最小组件契约。"""
    return ComponentDefinition(code=CODE, parameter_schema=schema, default_parameters=defaults)


@pytest.fixture
def network_reads(monkeypatch):
    """记录 URL 读取尝试；校验一旦需要联网取 Schema 就会失败而不是静默放行。"""
    reads: list[tuple] = []

    def refuse(*args, **kwargs):
        """Fail loudly instead of serving a remote document."""
        reads.append(args)
        raise AssertionError("参数 Schema 校验发起了网络读取")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    return reads


def test_internal_reference_resolves_inside_the_document(network_reads):
    """组合 Sprite 的嵌套 Schema 引用自身 $defs，必须继续可用。"""
    schema = {
        "type": "object",
        "properties": {"title": {"$ref": "#/$defs/imv_0_style"}},
        "$defs": {
            "imv_0_style": {
                "type": "object",
                "properties": {"size": {"type": "integer"}},
                "required": ["size"],
                "additionalProperties": False,
            }
        },
        "required": ["title"],
        "additionalProperties": False,
    }
    assert validate_component_contract(component(schema, {"title": {"size": 48}})) == []
    assert network_reads == []


@pytest.mark.parametrize(
    "reference",
    [
        "http://127.0.0.1:9/schema.json",
        "https://example.com/style.json#/$defs/size",
        "../shared.json",
    ],
)
def test_external_reference_is_rejected_without_reading_it(network_reads, reference):
    """提交的 Schema 不能把 API 进程变成 HTTP 客户端，也不能解析到文档外部。"""
    schema = {
        "type": "object",
        "properties": {"title": {"$ref": reference}},
        "required": ["title"],
        "additionalProperties": False,
    }
    diagnostics = validate_component_contract(component(schema, {"title": "标题"}))
    assert len(diagnostics) == 1
    assert diagnostics[0].field == "/parameter_schema"
    assert "外部引用" in diagnostics[0].message
    assert reference in diagnostics[0].message
    assert network_reads == []


def test_nested_dynamic_reference_is_rejected_too(network_reads):
    """嵌套的 $dynamicRef 走同一个解析器，同样必须拒绝。"""
    schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "object",
                "properties": {"size": {"$dynamicRef": "https://evil.example/size"}},
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }
    diagnostics = validate_component_contract(component(schema, {"title": {}}))
    assert len(diagnostics) == 1
    assert "https://evil.example/size" in diagnostics[0].message
    assert network_reads == []


def test_parameters_helper_refuses_external_reference_without_network(network_reads):
    """直接调用校验函数也不得回退到远程读取；拒绝由引用注册表兜底。"""
    schema = {
        "type": "object",
        "properties": {"title": {"$ref": "http://127.0.0.1:9/schema.json"}},
        "required": ["title"],
        "additionalProperties": False,
    }
    with pytest.raises(Unresolvable):
        validate_parameters(schema, {"title": "标题"})
    assert network_reads == []


def test_unresolvable_internal_reference_reports_a_diagnostic(network_reads):
    """文档内定位不到的引用是契约诊断，不能让解析异常穿透请求。"""
    schema = {
        "type": "object",
        "properties": {"title": {"$ref": "#/$defs/missing"}},
        "required": ["title"],
        "additionalProperties": False,
    }
    diagnostics = validate_component_contract(component(schema, {"title": "标题"}))
    assert len(diagnostics) == 1
    assert "$defs/missing" in diagnostics[0].message
    assert network_reads == []
