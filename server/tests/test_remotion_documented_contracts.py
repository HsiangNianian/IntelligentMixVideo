"""公开工具文档与注册目录、检查回执、调用示例和转换清单的一致性；pytest tests/test_remotion_documented_contracts.py。"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from server.remotion_templates.tools.catalog import PLAN_TOOL, REPORT_TOOL
from server.remotion_templates.tools.contracts import EffectManifest, ToolDescriptor
from server.remotion_templates.tools.registry import get_tool, registered_tools

DOCUMENT = Path(__file__).parents[2] / "docs/remotion-agent-tool-contracts.md"


def test_documented_catalog_matches_callable_tools():
    """文档表格只列实际可调用业务工具，已删除路径不能继续作为可用 API 发布。"""
    names = re.findall(r"^\| `([a-z.]+)` \|", DOCUMENT.read_text(), re.MULTILINE)
    assert set(names) == {tool.name for tool in registered_tools()}
    assert len(names) == len(registered_tools())


@pytest.mark.parametrize("tool", [*registered_tools(), PLAN_TOOL, REPORT_TOOL], ids=lambda tool: tool.name)
def test_descriptor_accepts_actual_inspection(tool):
    """注册业务工具与宿主控制的真实检查回执均符合公开严格类型及各自的实际输入输出 Schema。"""
    response = asyncio.run(get_tool("tools.inspect").invoke(None, {"tool_name": tool.name}))
    assert response["ok"] is True
    descriptor = ToolDescriptor.model_validate(response["data"])
    assert descriptor.tool_name == tool.name
    assert descriptor.contract_version == 1
    assert descriptor.input_schema == tool.input.model_json_schema()


def test_documented_examples_validate_against_the_public_contract():
    """示例中的历史转换清单及全部新工具调用可由真实类型读取，无需模型或渲染器。"""
    blocks = [json.loads(block) for block in re.findall(r"^```json\n(.*?)^```", DOCUMENT.read_text(), re.MULTILINE | re.DOTALL)]
    manifest, calls = blocks
    assert EffectManifest.model_validate(manifest).contract is not None
    assert {item["tool"] for item in calls} == {tool.name for tool in registered_tools()}
    for call in calls:
        get_tool(call["tool"]).input.model_validate(call["arguments"])
