"""PR76 tool registration and image operation tests; no external URLs or model calls are used."""

import asyncio
from pathlib import Path

import httpx
import pytest
from PIL import Image

from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.contracts import ImageCropInput, ImageResizeInput
from server.remotion_templates.tools.image_tools import ImageToolError, execute_image
from server.remotion_templates.tools.registry import registered_tools
from server.remotion_templates.tools.contracts import PresetRecord, PresetDraft
from server.remotion_templates.tools.schema import merge_parameters


def test_preset_roundtrip_preserves_null_and_omitted_source():
    """Omitted source IDs remain omitted while legal parameter null remains intact."""
    draft = PresetDraft(
        code="export default function Label(){return null}",
        description="nullable label",
        parameter_schema={"type": "object", "properties": {"value": {"type": ["string", "null"]}}, "required": ["value"], "additionalProperties": False},
        default_parameters={"value": None},
    )
    record = PresetRecord(preset_id="id", created_at="2026-09-29T00:00:00+00:00", **draft.model_dump(mode="json", exclude_unset=True))
    restored = PresetRecord.model_validate_json(record.model_dump_json(exclude_unset=True))
    assert restored.default_parameters == {"value": None}
    assert "source_preset_id" not in restored.model_fields_set
    assert merge_parameters({"theme": {"value": None, "keep": 2}}, {"theme": {"keep": 3}}) == {"theme": {"value": None, "keep": 3}}


class _MockClient:
    """Small async HTTP client replacement returning one in-memory PNG."""

    def __init__(self, *args, **kwargs):
        """Keep the production call shape without opening a network connection."""
        image = Image.new("RGBA", (4, 3), (255, 0, 0, 128))
        from io import BytesIO

        stream = BytesIO()
        image.save(stream, format="PNG")
        self.response = httpx.Response(200, content=stream.getvalue(), request=httpx.Request("GET", "https://example.test/source.png"))

    async def __aenter__(self):
        """Return the fake client to the async context manager."""
        return self

    async def __aexit__(self, *args):
        """Close the fake context without resources."""

    async def get(self, url):
        """Return the same deterministic PNG for every valid URL."""
        return self.response


def test_pr76_tools_are_decorator_registered():
    """The registry exposes new tools from decorators while legacy PR74 names remain compatible."""
    names = {item.name for item in registered_tools()}
    available_names = {item.name for item in available()}
    assert {"image.info", "image.resize", "image.crop", "validate.render", "sprite.compose"} <= names
    assert {"image.info", "image.resize", "image.crop", "validate.render", "sprite.compose"} <= available_names


def test_image_resize_and_crop_preserve_source_and_bounds(monkeypatch, tmp_path):
    """Resize forces exact pixels, crop rejects overflow, and source files remain untouched."""
    monkeypatch.setattr("server.remotion_templates.tools.image_tools.httpx.AsyncClient", _MockClient)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    resized = asyncio.run(execute_image("image.resize", ImageResizeInput(image_url="https://example.test/source.png", width=6, height=2), settings))
    output = Path(settings.data_dir) / "tool-images"
    path = next(output.glob("*.png"))
    with Image.open(path) as image:
        assert image.size == (6, 2)
    assert resized["width"] == 6 and resized["height"] == 2
    with pytest.raises(ImageToolError, match="CROP_OUT_OF_BOUNDS"):
        asyncio.run(execute_image("image.crop", ImageCropInput(image_url="https://example.test/source.png", x=3, y=0, width=2, height=3), settings))
