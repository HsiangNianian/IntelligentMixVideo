"""Linux tool smoke for preset.create → sprite.compose → sprite.create; no model or browser rendering."""

import argparse
import asyncio
import json
from uuid import uuid4

from .harness import Harness
from .provider import Budget, Provider
from .renderer import Renderer
from .settings import load_settings
from .tools.catalog import available
from .tools.contracts import PresetCreateInput, SpriteComposeInput, SpriteCreateInput
from .tools.session import ToolSession


async def run() -> None:
    """Create real Preset/Sprite records under a separate data directory using actual TypeScript checks."""
    settings = load_settings()
    settings.data_dir = settings.data_dir / ("tools-smoke-" + uuid4().hex[:8])
    directory = settings.data_dir / "run"
    directory.mkdir(parents=True)
    service = Harness(Provider(settings), Renderer(settings))
    session = ToolSession(service, None, None, Budget(), directory, [], lambda *_: None, {})
    created = await session.execute("preset.create", PresetCreateInput(
        description="可调文字和颜色的静态标题",
        code='/** Editable title used by the Linux tool smoke. */\nimport React from "react";\n/** Render the supplied title without external resources. */\nexport default function Title(props: {text:string;color:string}) { return <div style={{color:props.color,fontSize:64}}>{props.text}</div>; }',
        parameter_schema={"type":"object","properties":{"text":{"type":"string"},"color":{"type":"string"}},"required":["text","color"],"additionalProperties":False},
        default_parameters={"text":"今日灵感","color":"#ffffff"},
    ), available())
    preset = created["preset"]
    composed = await session.execute("sprite.compose", SpriteComposeInput.model_validate({
        "description":"标题和说明文字组合",
        "instances":[
            {"instance_id":"title","source":{"kind":"stored","preset_id":preset["preset_id"]},"parameters":{"text":"今日灵感"},"layout":{"x":60,"y":100,"width":960,"height":200,"z_index":0},"timing":{"start_frame":0,"duration_frames":150}},
            {"instance_id":"description","source":{"kind":"stored","preset_id":preset["preset_id"]},"parameters":{"text":"从一个小想法开始","color":"#ffff00"},"layout":{"x":60,"y":400,"width":960,"height":200,"z_index":1},"timing":{"start_frame":30,"duration_frames":120}},
        ],
    }), available())
    saved = await session.execute("sprite.create", SpriteCreateInput.model_validate(composed), available())
    print(json.dumps({"status":"passed","preset_id":preset["preset_id"],"sprite_id":saved["sprite"]["sprite_id"],"composition":saved["sprite"]["composition"],"data_dir":str(settings.data_dir),"checks":"real TypeScript and storage; no browser behavior checks"},ensure_ascii=False,indent=2))


def main() -> None:
    """Run only when explicitly requested because this writes test records and may warm Chroma's model cache."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
