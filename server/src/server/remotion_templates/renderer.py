"""Run generated TSX in a networkless Linux sandbox and bind its artifacts to verified inputs."""

import asyncio
import hashlib
import json
import os
import shutil
import signal
from pathlib import Path

from ..settings import Settings
from .evidence import digest
from .image_comparison import consistency_checks
from .models import (
    Check,
    TemplateCandidate,
    TemplateSpec,
    ValidationReport,
    validation_fingerprint,
)
from .parameters import validate_candidate
from .probes import parameter_probes, pixel_checks


def keyframes(spec: TemplateSpec) -> list[int]:
    """Sample endpoints and each layer/effect boundary; keep all declared motion intervals visible."""
    last = spec.composition.duration_in_frames - 1
    frames = {0, last // 2, last}
    for layer in spec.text_layers:
        for start, end in [
            (layer.start_frame, layer.end_frame),
            *[(motion.start_frame, motion.end_frame) for motion in layer.motion],
        ]:
            frames.update(
                (
                    max(0, start - 1),
                    start,
                    (start + end - 1) // 2,
                    end - 1,
                    min(last, end),
                )
            )
    return sorted(frame for frame in frames if 0 <= frame <= last)


class Renderer:
    """Own subprocess lifetime; no fallback ever executes untrusted code in the API environment."""

    def __init__(self, settings: Settings) -> None:
        """Use only server-owned executable and font paths."""
        self.settings = settings

    def verify_environment(self, report: ValidationReport) -> None:
        """Reject evidence if managed code, dependencies, fonts or executables changed since rendering."""
        paths = {
            "font_400": self.settings.font_regular,
            "font_700": self.settings.font_bold,
            "worker": self.settings.renderer_dir / "worker.mjs",
            "image_comparison": Path(__file__).with_name("image_comparison.py"),
            "probes": Path(__file__).with_name("probes.py"),
            "presentation": self.settings.renderer_dir / "presentation.mjs",
            "preview_host": self.settings.renderer_dir / "preview-host.tsx",
            "dependencies": self.settings.renderer_dir / "bun.lock",
            "browser": self.settings.browser_executable,
            "node_binary": Path(shutil.which("node") or "/usr/bin/node").resolve(),
            "ffprobe": Path(shutil.which("ffprobe") or "/usr/bin/ffprobe").resolve(),
        }
        for name, path in paths.items():
            if report.runtime.get(name) != digest(path):
                raise ValueError(
                    "Renderer environment changed or evidence is missing; revalidate before completion."
                )

    async def media_metadata(self, directory: Path, spec: TemplateSpec) -> Check:
        """Probe the actual MP4 using bounded host tooling; always reap the metadata process."""
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,nb_frames,duration",
            "-of",
            "json",
            str(directory / "preview.mp4"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(10):
                data, _ = await process.communicate()
            stream = json.loads(data)["streams"][0]
            numerator, denominator = map(float, stream["avg_frame_rate"].split("/"))
            expected = spec.composition
            passed = (
                stream["width"] == expected.width
                and stream["height"] == expected.height
                and abs(numerator / denominator - expected.fps) < 0.001
                and int(stream["nb_frames"]) == expected.duration_in_frames
                and abs(
                    float(stream["duration"])
                    - expected.duration_in_frames / expected.fps
                )
                < 0.01
            )
            return Check(
                name="media_metadata",
                status="pass" if passed else "fail",
                detail=json.dumps(stream),
            )
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    def command(self, directory: Path) -> list[str]:
        """Expose system libraries, managed renderer and one writable attempt; hide home and secrets."""
        settings = self.settings
        node = Path(shutil.which("node") or "/usr/bin/node").resolve()
        command = [
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for source in ("/usr", "/etc/fonts", "/etc/ld.so.cache"):
            if Path(source).exists():
                command.extend(("--ro-bind", source, source))
        for source in ("/lib", "/lib64"):
            if Path(source).is_symlink():
                command.extend(("--symlink", os.readlink(source), source))
            elif Path(source).exists():
                command.extend(("--ro-bind", source, source))
        command.extend(("--ro-bind", str(node), "/runtime-node"))
        prlimit = Path(shutil.which("prlimit") or "/usr/bin/prlimit").resolve()
        command.extend(("--ro-bind", str(prlimit), "/runtime-prlimit"))
        if settings.runtime_lib_dir is not None:
            libraries = str(settings.runtime_lib_dir.resolve())
            command.extend(("--ro-bind", libraries, "/runtime-lib"))
        browser = settings.browser_executable.resolve()
        command.extend(("--ro-bind", str(browser.parent), str(browser.parent)))
        command.extend(
            (
                "--ro-bind",
                str(settings.renderer_dir.resolve()),
                "/renderer",
                "--bind",
                str(directory.resolve()),
                "/work",
                "--chdir",
                "/work",
                "--clearenv",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--",
                "/runtime-prlimit",
                "--fsize=536870912",
                "--nofile=1024",
                "--cpu=300",
                "--",
                "/runtime-node",
                "--max-old-space-size=2048",
                "/renderer/worker.mjs",
            )
        )
        if settings.runtime_lib_dir is not None:
            # 只暴露随包共享库，仍清空密钥环境、隔离网络及用户目录。
            boundary = command.index("--clearenv") + 1
            command[boundary:boundary] = ["--setenv", "LD_LIBRARY_PATH", "/runtime-lib"]
        return command

    async def validate(
        self,
        candidate: TemplateCandidate,
        spec: TemplateSpec,
        directory: Path,
        *,
        preserve_code: bool = False,
        extra_frames: list[int] | None = None,
    ) -> tuple[TemplateCandidate, ValidationReport]:
        """Preserve failed artifacts; cancellation/timeout kills and reaps the entire sandbox process group."""
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "candidate.json").write_text(
            candidate.model_dump_json(), encoding="utf-8"
        )
        (directory / "spec.json").write_text(spec.model_dump_json(), encoding="utf-8")
        (directory / "Template.tsx").write_text(candidate.tsx_code, encoding="utf-8")
        report = ValidationReport(fingerprint="", frames=keyframes(spec))
        if extra_frames:
            if any(
                type(frame) is not int
                or not 0 <= frame < spec.composition.duration_in_frames
                for frame in extra_frames
            ):
                raise ValueError(
                    "Supplemental frames must be valid composition frame numbers"
                )
            report.frames = sorted(set(report.frames) | set(extra_frames))
        try:
            validate_candidate(candidate, spec)
            report.checks.append(
                Check(
                    name="configuration",
                    status="pass",
                    detail="Schema controls match the template specification.",
                )
            )
        except ValueError as exc:
            report.checks.append(
                Check(name="configuration", status="fail", detail=str(exc)[:6000])
            )
            report.fingerprint = validation_fingerprint(candidate, spec, report.runtime)
            return candidate, report
        settings = self.settings
        try:
            public = directory / "public"
            public.mkdir()
            for weight, font in (
                (400, settings.font_regular),
                (700, settings.font_bold),
            ):
                data = font.read_bytes()
                (public / f"font-{weight}.ttc").write_bytes(data)
                report.runtime[f"font_{weight}"] = hashlib.sha256(data).hexdigest()
            report.runtime["worker"] = hashlib.sha256(
                (settings.renderer_dir / "worker.mjs").read_bytes()
            ).hexdigest()
            report.runtime["image_comparison"] = digest(
                Path(__file__).with_name("image_comparison.py")
            )
            report.runtime["probes"] = digest(Path(__file__).with_name("probes.py"))
            report.runtime["presentation"] = digest(
                settings.renderer_dir / "presentation.mjs"
            )
            report.runtime["preview_host"] = digest(
                settings.renderer_dir / "preview-host.tsx"
            )
            report.runtime["dependencies"] = hashlib.sha256(
                (settings.renderer_dir / "bun.lock").read_bytes()
            ).hexdigest()
            report.runtime["browser"] = hashlib.sha256(
                settings.browser_executable.read_bytes()
            ).hexdigest()
            report.runtime["node_binary"] = digest(
                Path(shutil.which("node") or "/usr/bin/node").resolve()
            )
            report.runtime["ffprobe"] = digest(
                Path(shutil.which("ffprobe") or "/usr/bin/ffprobe").resolve()
            )
            # User edits authorize their own appearance; do not re-audit parameter/motion semantics.
            probes = [] if preserve_code else parameter_probes(candidate, spec)
            report.frames = sorted(
                set(report.frames) | {probe["frame"] for probe in probes}
            )
            request = {
                "probes": probes,
                "code": candidate.tsx_code,
                "config": candidate.default_config,
                "composition": spec.composition.model_dump(),
                "frames": report.frames,
                "browser": str(settings.browser_executable.resolve()),
            }
            (directory / "request.json").write_text(
                json.dumps(request), encoding="utf-8"
            )
            with (directory / "worker.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    *self.command(directory),
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                )
                try:
                    async with asyncio.timeout(settings.render_timeout_seconds):
                        await process.wait()
                finally:
                    # Kill descendants even if the parent exited; bwrap also owns a PID namespace.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            if process.returncode:
                raise RuntimeError(
                    "Isolated renderer failed; inspect worker.log for environment diagnostics."
                )
            payload = json.loads((directory / "renderer.json").read_text())
            report.checks.extend(
                Check.model_validate(check) for check in payload["checks"]
            )
            report.runtime.update(payload["runtime"])
            formatted = (directory / "Template.tsx").read_text()
            if preserve_code and formatted != candidate.tsx_code:
                raise RuntimeError(
                    "Parameter edits must preserve accepted formatted TSX bytes."
                )
            candidate = candidate.model_copy(update={"tsx_code": formatted})
            if all(check.status == "pass" for check in report.checks):
                report.checks.extend(consistency_checks(directory, spec, report.frames))
                report.checks.append(await self.media_metadata(directory, spec))
                if not preserve_code:
                    report.checks.extend(
                        pixel_checks(directory, spec, report.frames, probes)
                    )
                self.verify_environment(report)
        except (
            OSError,
            RuntimeError,
            TimeoutError,
            ValueError,
            KeyError,
            IndexError,
            ZeroDivisionError,
        ) as exc:
            report.checks.append(
                Check(
                    name="renderer_environment", status="fail", detail=str(exc)[:1000]
                )
            )
        report.fingerprint = validation_fingerprint(candidate, spec, report.runtime)
        (directory / "candidate.json").write_text(
            candidate.model_dump_json(), encoding="utf-8"
        )
        return candidate, report
