"""Single-instance durable job queue; cancellation and failure never replace an accepted version.

The queue lives in MySQL. One backend instance owns it: startup interrupts what a previous process left running and
finishes interrupted deletions, and an hourly sweep drops undelivered events, old audit logs, unused uploads and
the works of devices that have been inactive for the retention period.
"""

import asyncio
import json
import logging
import shutil
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from .deletion import finish as finish_deletion
from .settings import ClientSettings, Settings
from .harness import Harness
from .overlay_service import OverlayRenders
from .models import DialogueOutput, EditTemplateRequest, JobError, JobInput, MessageImage, TaskMessage
from .parameters import parameter_changes
from .provider import Budget, ExecutionFailure, ModelFailure, Provider
from .publication import revisable
from .store import Conflict, Store, now
from .tool_validation import ToolValidator, ValidationUnavailable
from .tools.contracts import CodeValidationReport, ComponentDefinition


logger = logging.getLogger("uvicorn.error")

# Host-written meaning of each reference image source, so an image without text still says what it is for.
REFERENCE_MEANINGS = {
    "work_original": "Attached to the request that created this work; it describes the original goal.",
    "message": "Attached by the user to the current instruction as a reference for this change.",
    "clarification_answer": "Attached by the user as their answer (or part of it) to clarifications[clarification].",
}


def reference_images(project, inputs: JobInput) -> list[dict]:
    """Describe every reference image in the order the model receives them, each with its source."""
    entries = [
        {"asset_id": str(image.asset_id), "source": "work_original"} for image in project.request.images
    ]
    entries += [
        image.model_dump(mode="json", exclude_none=True) for image in inputs.images
    ]
    return [{**entry, "meaning": REFERENCE_MEANINGS[entry["source"]]} for entry in entries]


class Runtime:
    """Drain local jobs on demand, separately from the harness's model/evidence decision loop."""

    def __init__(self, store: Store, harness: Harness, settings: Settings) -> None:
        """Construct without starting tasks or touching the data directory."""
        self.store, self.harness, self.settings = store, harness, settings
        self.worker: asyncio.Task | None = None
        self.active: asyncio.Task | None = None
        self.active_id: UUID | None = None
        self.active_done: asyncio.Event | None = None
        self.deletions: dict[UUID, asyncio.Task] = {}
        # Instant parameter revisions in flight, by job; they never enter the FIFO queue.
        self.saves: dict[UUID, asyncio.Task] = {}
        self.maintenance: asyncio.Task | None = None
        self.client_configs: dict[UUID, Settings] = {}
        self.diagnostics_lock = asyncio.Lock()
        self._overlays: OverlayRenders | None = None

    @property
    def overlays(self) -> OverlayRenders:
        """Template overlay-video tasks, created on first use (`runtime_for` does so to start their recovery)."""
        if self._overlays is None:
            self._overlays = OverlayRenders(self.store, self.harness.renderer, self.settings)
        return self._overlays

    def sandbox_problem(self) -> str | None:
        """Why the isolated validator cannot run on this host, or None; routes refuse to queue work when set."""
        return self.harness.renderer.sandbox_problem()

    def initialize(self) -> None:
        """Create the tables, then recover: interrupt jobs a previous process left running and finish deletions."""
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.store.initialize()
        self.store.interrupt_unfinished()
        for work_id in self.store.pending_deletions():
            try:
                finish_deletion(self.store, work_id)
            except Exception:
                logging.getLogger(__name__).exception("Work deletion recovery failed: %s", work_id)

    def start_maintenance(self) -> None:
        """Start the hourly retention sweep once; it lives as long as the event loop."""
        if self.maintenance is None or self.maintenance.done():
            self.maintenance = asyncio.create_task(self._maintain())

    async def _maintain(self) -> None:
        """Run the retention sweep now and then every hour; one failed sweep is logged and retried later."""
        while True:
            try:
                await self.sweep()
            except Exception:
                logger.exception("Remotion retention sweep failed")
            await asyncio.sleep(3600)

    async def sweep(self) -> None:
        """Apply the retention periods: undelivered events, private audit logs, inactive devices and unused uploads.

        An upload no work references (its message was rejected or never sent) is reclaimed after 24 hours.
        """
        stamp = now()
        await asyncio.to_thread(self.store.prune_assets)
        await asyncio.to_thread(self.store.expire_events, stamp - timedelta(days=self.settings.undelivered_event_days))
        cutoff = (stamp - timedelta(days=self.settings.audit_retention_days)).timestamp()
        audit = self.store.root / "audit"
        for path in (audit.iterdir() if audit.is_dir() else ()):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        for work_id in await asyncio.to_thread(self.store.expire_devices, stamp - timedelta(days=self.settings.device_retention_days)):
            await self.delete_work(work_id)

    def notify(self, job_id: UUID | None = None, config: ClientSettings | None = None) -> None:
        """Create a drain task only when work exists; no idle loop or application lifecycle hook."""
        if config is not None and job_id is not None:
            self.client_configs[job_id] = self.settings.model_copy(update=config.model_dump())
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._work())

    def edit(self, project_id: UUID, request: EditTemplateRequest, config: ClientSettings | None = None):
        """Choose a stable accepted base at enqueue time and reject invalid parameter patches early."""
        from .parameters import patch_parameters

        project = self.store.project(project_id)
        base_id = request.base_version_id or project.current_version_id
        if base_id is None:
            raise Conflict("This work has no accepted version to edit.")
        base = self.store.version(base_id)
        if base.project_id != project_id:
            raise Conflict("base version belongs to another work")
        if request.parameters is not None:
            patch_parameters(base.candidate, base.spec, request.parameters)
        images = [image.asset_id for image in request.images]
        inputs = JobInput(
            mode="parameters" if request.parameters is not None else "edit",
            instruction=request.instruction,
            parameters=request.parameters,
            images=[MessageImage(asset_id=image) for image in images],
        )
        return self._start(project_id, inputs, base, config, message_images=images)

    def _start(
        self,
        project_id: UUID,
        inputs: JobInput,
        base,
        config: ClientSettings | None,
        message_text: str | None = None,
        message_images: list[UUID] | None = None,
    ):
        """Queue a job for the worker, or save a PR76 parameter revision at once outside the FIFO queue.

        A parameter revision of a PR76 version whose player bundle reads injected defaults only re-checks the schema
        and seals new defaults, so it must not wait behind another work's generation; it is recorded as running so
        the one-active-job rule still holds. An older bundle would keep showing its compiled defaults, so that
        revision is queued and rebuilt in the sandbox.
        """
        if inputs.mode == "parameters" and base is not None and self.instant(base):
            job = self.store.enqueue(project_id, inputs, base.id, message_text=message_text, running=True)
            task = asyncio.create_task(self._save_parameters(job.id, base, inputs.parameters))
            self.saves[job.id] = task
            task.add_done_callback(lambda _done: self.saves.pop(job.id, None))
            return job
        job = self.store.enqueue(
            project_id, inputs, base.id if base else None,
            message_text=message_text, message_images=message_images,
        )
        self.notify(job.id, config)
        return job

    def instant(self, base) -> bool:
        """Whether a parameter patch on this accepted version is saved at once instead of rendered in the sandbox.

        Only the player bundle decides, so only that file is read (downloaded once into the verified cache).
        """
        if base.spec.schema_version != "2":
            return False
        try:
            return revisable(self.store.version_file(base, "interactive.js").parent)
        except (OSError, ValueError):
            return False

    async def _save_parameters(self, job_id: UUID, base, patch: dict) -> None:
        """Seal the revision off the event loop and publish it; failures are recorded on the job, never raised."""
        try:
            directory = self.store.job_dir(job_id) / f"parameters-{uuid4().hex}"
            accepted = await asyncio.to_thread(self.store.accepted, base)
            result = await asyncio.to_thread(self.harness.presentation.revise, base, accepted, patch, directory)
            await asyncio.to_thread(self.store.publish, job_id, *result)
        except Exception as exc:
            logger.exception("Remotion parameter revision %s failed", job_id)
            self.store.update(job_id, status="failed", stage="finished", error=JobError(
                code="execution_failed", message=str(exc)[:1000] or "Parameter revision failed.",
            ))
        finally:
            shutil.rmtree(self.store.job_dir(job_id), ignore_errors=True)

    def message(self, project_id: UUID, request: TaskMessage, config: ClientSettings | None = None):
        """Route conversation input to generation, accepted-base edits, or outstanding questions."""
        latest = self.store.latest_job(project_id)
        images = [image.asset_id for image in request.images]
        if request.reply_to_job_id:
            if latest.id != request.reply_to_job_id or latest.status != "needs_input":
                raise Conflict("Question reply is stale or belongs to another task.")
            return self.retry(latest.id, request.instruction or "", config, images=images)
        if latest.status == "needs_input":
            raise Conflict("Answer the outstanding questions using reply_to_job_id.")
        project = self.store.project(project_id)
        if (
            project.current_version_id is None
            and request.base_version_id is None
            and request.parameters is None
        ):
            # A conversation can start with questions before its first accepted template exists.
            job = self.store.enqueue(
                project_id,
                JobInput(
                    mode="generate",
                    instruction=request.instruction,
                    images=[MessageImage(asset_id=image) for image in images],
                ),
                None,
                message_images=images,
            )
            self.notify(job.id, config)
            return job
        return self.edit(
            project_id,
            EditTemplateRequest.model_validate(
                request.model_dump(exclude={"reply_to_job_id"})
            ),
            config,
        )

    def retry(
        self,
        job_id: UUID,
        answer: str | None = None,
        config: ClientSettings | None = None,
        *,
        images: list[UUID] | None = None,
    ):
        """Retry or clarification creates a new identity, never mutating historical execution state.

        An answer keeps the task's earlier reference images and may attach more, bound to this answer both
        in the image record and in the clarification entry, so an image-only answer still says what it answers.
        """
        job = self.store.job(job_id)
        allowed = (
            {"needs_input"}
            if answer is not None
            else {"failed", "cancelled", "interrupted"}
        )
        if job.status not in allowed:
            raise Conflict("Job state does not allow this action.")
        if self.store.latest_job(job.project_id).id != job_id:
            raise Conflict("Only the latest task execution may be retried or answered.")
        inputs = self.store.inputs(job_id)
        if answer is not None:
            clarification = {"questions": job.questions, "answer": answer}
            if images:
                clarification["answer_images"] = [str(image) for image in images]
                inputs.images += [
                    MessageImage(asset_id=image, source="clarification_answer", clarification=len(inputs.clarifications))
                    for image in images
                ]
            inputs.clarifications += [json.dumps(clarification, ensure_ascii=False)]
        base = self.store.version(job.base_version_id) if job.base_version_id else None
        return self._start(
            job.project_id, inputs, base, config,
            message_text=answer if answer is not None else "重试本次制作。",
            message_images=images,
        )

    async def code_report(
        self, component: ComponentDefinition, *, export_code: str
    ) -> CodeValidationReport:
        """Check sealed export bytes in disposable storage; bound concurrent read-only workers."""
        if self.diagnostics_lock.locked():
            raise ValidationUnavailable("Code diagnostics are busy; retry later")
        async with self.diagnostics_lock:
            try:
                root = self.store.root / "diagnostics"
                root.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(prefix="read-", dir=root) as directory:
                    validator = ToolValidator(self.harness.renderer, Path(directory))
                    return await validator.code_report(component, export_code=export_code)
            except OSError as exc:
                raise ValidationUnavailable("Code diagnostics storage is unavailable") from exc

    async def cancel(self, job_id: UUID):
        """Persist cancellation first so even a late model result cannot publish a revision."""
        self.client_configs.pop(job_id, None)
        job = self.store.update(job_id, status="cancelled", stage="finished")
        if self.active_id == job_id and self.active:
            # Repeated stop/delete requests must not interrupt the cancellation finalizer.
            if not self.active.cancelling():
                self.active.cancel()
            with suppress(asyncio.CancelledError):
                await self.active
        return job

    async def delete_work(self, work_id: UUID) -> None:
        """Coalesce duplicate requests; disconnecting the HTTP client does not cancel cleanup."""
        task = self.deletions.get(work_id)
        if task is None:
            task = asyncio.create_task(self._delete_work(work_id))
            self.deletions[work_id] = task

            def finished(result: asyncio.Task) -> None:
                """Release completed operations and consume errors even after client disconnect."""
                self.deletions.pop(work_id, None)
                if not result.cancelled():
                    result.exception()

            task.add_done_callback(finished)
        await asyncio.shield(task)

    async def _delete_work(self, work_id: UUID) -> None:
        """Stop writes and wait for queue bookkeeping before removing files or job rows."""
        jobs = self.store.begin_deletion(work_id)
        for job_id in jobs:
            self.client_configs.pop(job_id, None)
            if (save := self.saves.get(job_id)) is not None:
                # The job is cancelled already, so the revision cannot publish; let it finish writing its files.
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(save)
        if self.active_id in jobs and self.active:
            active, done = self.active, self.active_done
            if not active.cancelling():
                active.cancel()
            with suppress(asyncio.CancelledError):
                await active
            if done is not None:
                await done.wait()
        await asyncio.to_thread(finish_deletion, self.store, work_id)

    async def _work(self) -> None:
        """Drain FIFO jobs without polling or allowing a failed run to stop subsequent work."""
        while True:
            job = self.store.claim()
            if job is None:
                return
            self.active_id = job.id
            done = self.active_done = asyncio.Event()
            self.active = asyncio.create_task(self._execute(job.id))
            try:
                await self.active
            except asyncio.CancelledError:
                if self.store.job(job.id).status != "cancelled":
                    raise
            finally:
                self.active, self.active_id, self.active_done = None, None, None
                done.set()

    async def _execute(self, job_id: UUID) -> None:
        """Resolve intent, run a bounded harness and atomically publish only accepted evidence."""
        settings = self.client_configs.pop(job_id, self.settings)
        harness = self.harness if settings is self.settings else Harness(Provider(settings), self.harness.renderer)
        budget = Budget(
            audit_path=self.store.audit_path(job_id),
            on_progress=lambda phase: self.store.progress(job_id, phase),
            on_round=lambda record: self.store.round(job_id, record),
        )
        job = self.store.job(job_id)
        context = self.store.conversation(job.project_id)
        try:
            async with asyncio.timeout(settings.job_timeout_seconds):
                job = self.store.job(job_id)
                project = self.store.project(job.project_id)
                inputs = self.store.inputs(job_id)
                # The work's original image first, then images attached to the messages this task answers.
                references = reference_images(project, inputs)
                # Reference images may have to be downloaded from the object store; keep that off the event loop.
                images = await asyncio.to_thread(
                    lambda: [self.store.asset_path(UUID(entry["asset_id"])) for entry in references]
                )
                base = (
                    self.store.version(job.base_version_id)
                    if job.base_version_id
                    else None
                )
                spec = base.spec if base else None
                patch = inputs.parameters
                intent = {
                    "original_request": project.request.model_dump(mode="json"),
                    "instruction": inputs.instruction,
                    "parameters": patch,
                    "clarifications": inputs.clarifications,
                    # Source stays out of the persisted Outer window; the tool session shows the base's
                    # Presets to the layer that edits code.
                    "accepted_base": base.spec.model_dump(mode="json", exclude_unset=True, exclude={"sprite"}) if base else None,
                    "reference_images": references,
                }
                if patch is None and base and base.source == "user_parameters":
                    baseline = self.store.version(base.agent_base_version_id)
                    intent["user_parameter_changes"] = {
                        "source": "user_parameter_edit",
                        "baseline_version_id": str(baseline.id),
                        "current_version_id": str(base.id),
                        "changes": parameter_changes(
                            baseline.candidate, base.candidate
                        ),
                    }
                # Manual edits are already durable job inputs; only model tasks enter its sliding window.
                if patch is None:
                    context.append(
                        [
                            {
                                "role": "user",
                                "content": json.dumps(intent, ensure_ascii=False),
                            }
                        ]
                    )
                directory = self.store.job_dir(job_id)
                directory.mkdir(parents=True, exist_ok=True)

                def stage(name: str, attempt: int) -> None:
                    """Record private stage progress and current budget at each decision boundary."""
                    self.store.update(
                        job_id, stage=name, attempts=attempt, usage=budget.summary()
                    )

                result = await harness.generate(
                    spec,
                    budget,
                    directory,
                    images,
                    stage,
                    base=base.candidate if base else None,
                    parameter_patch=patch,
                    context=context,
                    intent=intent,
                )
                if isinstance(result, DialogueOutput):
                    self.store.update(
                        job_id,
                        status="answered"
                        if result.answer is not None
                        else "needs_input",
                        stage="finished",
                        answer=result.answer,
                        questions=result.questions,
                        usage=budget.summary(),
                    )
                    return
                candidate, spec, report, attempt_dir = result
                self.store.update(job_id, usage=budget.summary())
                budget.progress("preparing")
                await asyncio.to_thread(self.store.publish, job_id, candidate, spec, report, attempt_dir)
        except asyncio.CancelledError:
            self.store.update(
                job_id, status="interrupted", stage="finished", usage=budget.summary()
            )
            raise
        except (ModelFailure, TimeoutError, ValueError, Conflict) as exc:
            code = (
                exc.code
                if isinstance(exc, ExecutionFailure)
                else "timeout"
                if isinstance(exc, TimeoutError)
                else "execution_failed"
            )
            logger.exception(
                "Remotion job %s failed code=%s audit=%s: %s",
                job_id,
                code,
                self.store.audit_path(job_id),
                str(exc)[:4000] or repr(exc),
            )
            self.store.update(
                job_id,
                status="failed",
                stage="finished",
                usage=budget.summary(),
                error=JobError(
                    code=code,
                    message=str(exc)[:1000] or "Job deadline exceeded.",
                ),
            )
        except Exception:
            # Keep the public job sanitized while retaining the traceback in the FastAPI error log.
            logger.exception(
                "Remotion job %s failed with an unexpected exception audit=%s",
                job_id,
                self.store.audit_path(job_id),
            )
            self.store.update(
                job_id,
                status="failed",
                stage="finished",
                usage=budget.summary(),
                error=JobError(
                    code="internal_error",
                    message="Execution failed unexpectedly; inspect server diagnostics.",
                ),
            )
        finally:
            # Candidates and renders are scratch; the audit log lives outside the job folder until it expires.
            shutil.rmtree(self.store.job_dir(job_id), ignore_errors=True)
            self.store.save_conversation(job.project_id, context)
            final = self.store.job(job_id)
            budget.record(
                "run_finished",
                status=final.status,
                error=final.error.model_dump() if final.error else None,
                usage=budget.summary(),
            )
