"""Explicit live acceptance script: python -m server.templates.smoke --live; artifacts stay under templates/.data/."""

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from ..settings import load_settings
from .harness import Harness
from .media import save_image
from .models import (
    CompositionConfig,
    EditTemplateRequest,
    GenerateTemplateRequest,
    ImageReference,
)
from .provider import Provider
from .renderer import Renderer
from .runtime import Runtime
from .store import Store


async def run() -> None:
    """Use configured models for text, parameters, natural-language edits and an image-only round trip."""
    settings = load_settings()
    settings.data_dir = settings.data_dir / f"smoke-{uuid4().hex[:8]}"
    service = Runtime(
        Store(settings.data_dir),
        Harness(Provider(settings), Renderer(settings)),
        settings,
    )
    service.initialize()
    print(f"Artifacts: {settings.data_dir}", flush=True)

    async def finish(identifier: UUID):
        """Poll a real local run with an outer deadline and print only status and public diagnostics."""
        previous = None
        async with asyncio.timeout(settings.job_timeout_seconds + 10):
            while True:
                job = service.store.job(identifier)
                state = (job.status, job.stage, job.attempts)
                if state != previous:
                    print(
                        json.dumps(
                            {
                                "job": str(job.id),
                                "status": job.status,
                                "stage": job.stage,
                                "attempts": job.attempts,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    previous = state
                if job.status not in {"queued", "running"}:
                    if job.status != "succeeded":
                        raise RuntimeError(
                            job.model_dump_json(exclude={"created_at", "updated_at"})
                        )
                    print(
                        json.dumps({"usage": job.usage}, ensure_ascii=False), flush=True
                    )
                    return service.store.version(job.result_version_id)
                await asyncio.sleep(0.5)

    composition = CompositionConfig(width=360, height=640, duration_in_frames=12)
    project, job = service.store.create(
        GenerateTemplateRequest(
            description="制作静态文字模板，唯一文字是‘今日灵感’，Noto Sans CJK SC 粗体，字号48，白色，水平垂直居中，宽度90%，无其他装饰，透明背景，全程显示。",
            composition=composition,
        )
    )
    service.notify()
    original = await finish(job.id)
    revised_job = service.edit(
        project.id, EditTemplateRequest(parameters={"0_text": "新的灵感"})
    )
    revised = await finish(revised_job.id)
    if revised.candidate.tsx_code != original.candidate.tsx_code:
        raise RuntimeError("Parameter edit changed TSX bytes.")
    natural_job = service.edit(
        project.id,
        EditTemplateRequest(
            instruction="把文字颜色改成黄色 #FFFF00，其余全部保持不变。"
        ),
    )
    natural = await finish(natural_job.id)
    if natural.candidate.tsx_code != revised.candidate.tsx_code:
        raise RuntimeError("Natural-language scalar edit did not preserve source.")
    original_job = service.store.job(original.job_id)
    frame = (
        service.store.job_dir(original.job_id)
        / f"attempt-{original_job.attempts}"
        / "frame-0.png"
    )
    asset = save_image(frame.read_bytes(), service.store, settings)
    _, image_job = service.store.create(
        GenerateTemplateRequest(
            image=ImageReference(asset_id=asset.id), composition=composition
        )
    )
    service.notify()
    await finish(image_job.id)
    print(
        "PASS: text generation, parameter edit, natural-language edit and image-only generation.",
        flush=True,
    )


def main() -> None:
    """Require explicit opt-in because this script sends images/text and consumes provider tokens."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
