"""按公开文档构造真实调用方，验证工具目录、输入输出 Schema 及检查回执兼容。"""

import asyncio
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import get_type_hints

import pytest
from pydantic import BaseModel, TypeAdapter

from server.remotion_templates.tools.catalog import PLAN_TOOL
from server.remotion_templates.tools.registry import get_tool, registered_tools


@pytest.fixture
def published_contract(monkeypatch):
    """加载仓库文档的声明代码，不调用其中占位函数或任何业务实现。"""
    document = Path(__file__).parents[2] / "docs/remotion-agent-tool-contracts.md"
    blocks = re.findall(r"^```python\n(.*?)^```", document.read_text(), re.MULTILINE | re.DOTALL)
    module = ModuleType("imv_published_contract")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile("\n\n".join(blocks), str(document), "exec"), module.__dict__)
    for value in tuple(vars(module).values()):
        if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == module.__name__:
            value.model_rebuild(_types_namespace=vars(module))
    return module


def validation_schema(value):
    """只忽略 Schema 的展示文案，保留字段、枚举、默认值和校验约束。"""
    if isinstance(value, dict):
        return {key: validation_schema(item) for key, item in value.items()
                if not (key in {"description", "title"} and isinstance(item, str))}
    if isinstance(value, list):
        return [validation_schema(item) for item in value]
    return value


@pytest.mark.parametrize("tool", registered_tools(), ids=lambda tool: tool.name)
def test_documented_input_and_output_match_registered_contract(published_contract, tool):
    """文档调用方和运行时对相同工具使用同一组字段、返回类型及严格约束。"""
    declared = get_type_hints(getattr(published_contract, tool.name.replace(".", "_")))
    assert validation_schema(declared["request"].model_json_schema()) == validation_schema(tool.describe()["input_schema"])
    assert validation_schema(TypeAdapter(declared["return"]).json_schema()) == validation_schema(tool.describe()["output_schema"])


@pytest.mark.parametrize("tool", [*registered_tools(), PLAN_TOOL], ids=lambda tool: tool.name)
def test_published_descriptor_accepts_actual_inspection(published_contract, tool):
    """所有公开名称（含宿主计划控制）返回的真实回执都能被文档消费者解析。"""
    response = asyncio.run(get_tool("tools.inspect").invoke(None, {"tool_name": tool.name}))
    assert response["ok"] is True
    descriptor = published_contract.ToolDescriptor.model_validate(response["data"])
    assert descriptor.tool_name == tool.name
    assert descriptor.contract_version == (2 if tool.name == "preset.search" else 1)


def test_documented_search_examples_follow_the_published_version(published_contract):
    """文档中的列表和 ID 读取 JSON 示例必须通过真实工具输入输出校验。"""
    document = Path(__file__).parents[2] / "docs/remotion-agent-tool-contracts.md"
    section = document.read_text().split("### 6.1 search", 1)[1].split("### 6.2 create", 1)[0]
    examples = [json.loads(block) for block in re.findall(r"^```json\n(.*?)^```", section, re.MULTILINE | re.DOTALL)]
    tool = get_tool("preset.search")
    assert len(examples) == 4
    for example in examples:
        if "ok" in example:
            result = tool.output.validate_python(example)
            assert result.data.presets
        else:
            tool.input.model_validate(example)
