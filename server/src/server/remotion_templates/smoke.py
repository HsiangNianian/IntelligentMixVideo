"""Explicit live acceptance script: python -m server.remotion_templates.smoke --live.

It uses the configured MySQL and object store under one throwaway device and deletes its works when it ends; job
scratch and the read cache stay under a `smoke-*` folder of the data directory. It never runs startup recovery, so a
backend serving the same database keeps its running jobs.
"""

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from .settings import load_settings
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
    # Create the tables only: recovery would interrupt the jobs of a backend that shares this database.
    service.store.initialize()
    print(f"Scratch: {settings.data_dir}", flush=True)

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

    # The smoke run owns its works through one throwaway device and removes them at the end.
    device = uuid4()
    created: list[UUID] = []
    try:
        await scenario(service, settings, device, finish, created)
    finally:
        for work_id in created:
            await service.delete_work(work_id)


async def scenario(service: Runtime, settings, device: UUID, finish, created: list[UUID]) -> None:
    """Generate, edit by parameters and by instruction, then generate from an image of the first result."""
    composition = CompositionConfig(width=360, height=640, duration_in_frames=12)
    project, job = service.store.create(
        GenerateTemplateRequest(
            description="制作静态文字模板，唯一文字是‘今日灵感’，Noto Sans CJK SC 粗体，字号48，白色，水平垂直居中，宽度90%，无其他装饰，透明背景，全程显示。",
            composition=composition,
        ),
        device,
    )
    created.append(project.id)
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
    # Job scratch is gone once a job ends; the sealed frame is read back through the verified cache.
    frame = await asyncio.to_thread(service.store.accepted, original) / "frame-0.png"
    asset = save_image(frame.read_bytes(), service.store, settings, device)
    image_work, image_job = service.store.create(
        GenerateTemplateRequest(
            images=[ImageReference(asset_id=asset.id)], composition=composition
        ),
        device,
    )
    created.append(image_work.id)
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
