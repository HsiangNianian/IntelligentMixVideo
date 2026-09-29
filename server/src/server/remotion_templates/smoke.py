"""Explicit Linux live smoke: three-layer composition, manual parameters and natural-language revision."""

import argparse
import asyncio
from copy import deepcopy
import json
from uuid import uuid4

from .harness import Harness
from .models import EditTemplateRequest, GenerateTemplateRequest
from .provider import Provider
from .renderer import Renderer
from .runtime import Runtime
from .settings import load_settings
from .store import Store


def replace_title(values: dict) -> dict:
    """Build a nested parameter patch for the requested title while preserving every other value."""
    changed = deepcopy(values)
    found = False

    def walk(value):
        """Replace only the exact title text inside nested object/array parameter values."""
        nonlocal found
        if value == "今日灵感":
            found = True
            return "新的灵感"
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    changed = walk(changed)
    if not found:
        raise RuntimeError("Generated parameters did not expose the requested editable title")
    return {key: value for key, value in changed.items() if value != values[key]}


async def run() -> None:
    """Use a dedicated data subtree and real configured Linux tools/models; never overwrite existing works."""
    settings = load_settings()
    settings.data_dir = settings.data_dir / f"smoke-{uuid4().hex[:8]}"
    service = Runtime(Store(settings.data_dir), Harness(Provider(settings), Renderer(settings)), settings)
    service.initialize()
    print(f"Smoke artifacts: {settings.data_dir}", flush=True)

    async def finish(job_id):
        """Wait within the task deadline and print only public status and stable error codes."""
        async with asyncio.timeout(settings.job_timeout_seconds + 10):
            previous = None
            while True:
                job = service.store.job(job_id)
                if job.status != previous:
                    print(json.dumps({"job": str(job.id), "status": job.status, "error": job.error.code if job.error else None}), flush=True)
                    previous = job.status
                if job.status not in {"queued", "running"}:
                    if job.status != "succeeded":
                        raise RuntimeError(f"Smoke failed: {job.status}; inspect {service.store.job_dir(job.id)}")
                    return service.store.version(job.result_version_id)
                await asyncio.sleep(0.2)

    try:
        project, job = service.store.create(GenerateTemplateRequest(description="制作5秒竖屏组合：上方标题‘今日灵感’，前15帧线性渐显；下方说明文字‘从一个小想法开始’，从第30帧显示到第150帧。两个实例独立暴露文字与颜色参数。请用行为断言验证文字、渐显和开始/结束边界。"))
        service.notify()
        original = await finish(job.id)
        revised = await finish(service.edit(project.id, EditTemplateRequest(parameters=replace_title(original.candidate.default_config))).id)
        if revised.candidate.tsx_code != original.candidate.tsx_code:
            raise RuntimeError("Manual parameters changed component source")
        natural = await finish(service.edit(project.id, EditTemplateRequest(instruction="把标题改成黄色 #FFFF00，保留当前‘新的灵感’文字及其他内容和动画。")).id)
        for version in (original, revised, natural):
            if version.spec.schema_version != "2" or not version.validation.passed:
                raise RuntimeError("Missing PR76 publication evidence")
            print(f"Version {version.number}: {version.id}; Player and Export are sealed", flush=True)
        print("PASS: composition, manual parameters, natural-language revision and sealed preview artifacts", flush=True)
    finally:
        if service.active is not None and not service.active.done():
            service.active.cancel()
        if service.worker is not None:
            await asyncio.gather(service.worker, return_exceptions=True)
        if service.lock is not None:
            service.lock.close()


def main() -> None:
    """Require explicit opt-in because the live smoke consumes configured model tokens."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
