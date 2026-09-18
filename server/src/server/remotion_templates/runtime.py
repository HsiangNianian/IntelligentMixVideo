"""Single-process durable job queue; cancellation and failure never replace an accepted version."""

import asyncio
import json
from contextlib import suppress
from uuid import UUID

from .settings import ClientSettings, Settings
from ..file_lock import lock_exclusive
from .harness import Harness
from .models import DialogueOutput, EditTemplateRequest, JobError, JobInput, TaskMessage
from .parameters import parameter_changes
from .provider import Budget, ExecutionFailure, ModelFailure, Provider
from .store import Conflict, Store


class Runtime:
    """Drain local jobs on demand, separately from the harness's model/evidence decision loop."""

    def __init__(self, store: Store, harness: Harness, settings: Settings) -> None:
        """Construct without starting tasks or touching the data directory."""
        self.store, self.harness, self.settings = store, harness, settings
        self.worker: asyncio.Task | None = None
        self.active: asyncio.Task | None = None
        self.active_id: UUID | None = None
        self.lock = None
        self.client_configs: dict[UUID, Settings] = {}

    def initialize(self) -> None:
        """Lock the state directory before recovery so a second server cannot interrupt live jobs."""
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.store.root / "runtime.lock").open("a")
        try:
            lock_exclusive(self.lock)
            self.store.initialize()
            self.store.interrupt_unfinished()
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

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
        job = self.store.enqueue(
            project_id,
            JobInput(
                mode="parameters" if request.parameters is not None else "edit",
                instruction=request.instruction,
                parameters=request.parameters,
            ),
            base_id,
        )
        self.notify(job.id, config)
        return job

    def message(self, project_id: UUID, request: TaskMessage, config: ClientSettings | None = None):
        """Route conversation input to generation, accepted-base edits, or outstanding questions."""
        latest = self.store.latest_job(project_id)
        if request.reply_to_job_id:
            if latest.id != request.reply_to_job_id or latest.status != "needs_input":
                raise Conflict("Question reply is stale or belongs to another task.")
            return self.retry(latest.id, request.instruction, config)
        if latest.status == "needs_input":
            raise Conflict("Answer the outstanding questions using reply_to_job_id.")
        project = self.store.project(project_id)
        if (
            project.current_version_id is None
            and request.base_version_id is None
            and request.instruction is not None
        ):
            # A conversation can start with questions before its first accepted template exists.
            job = self.store.enqueue(
                project_id,
                JobInput(mode="generate", instruction=request.instruction),
                None,
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

    def retry(self, job_id: UUID, answer: str | None = None, config: ClientSettings | None = None):
        """Retry or clarification creates a new identity, never mutating historical execution state."""
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
            inputs.clarifications += [
                json.dumps(
                    {"questions": job.questions, "answer": answer}, ensure_ascii=False
                )
            ]
        result = self.store.enqueue(
            job.project_id,
            inputs,
            job.base_version_id,
            message_text=answer if answer is not None else "重试本次制作。",
        )
        self.notify(result.id, config)
        return result

    async def cancel(self, job_id: UUID):
        """Persist cancellation first so even a late model result cannot publish a revision."""
        self.client_configs.pop(job_id, None)
        job = self.store.update(job_id, status="cancelled", stage="finished")
        if self.active_id == job_id and self.active:
            self.active.cancel()
            with suppress(asyncio.CancelledError):
                await self.active
        return job

    async def _work(self) -> None:
        """Drain FIFO jobs without polling or allowing a failed run to stop subsequent work."""
        while True:
            job = self.store.claim()
            if job is None:
                return
            self.active_id = job.id
            self.active = asyncio.create_task(self._execute(job.id))
            try:
                await self.active
            except asyncio.CancelledError:
                if self.store.job(job.id).status != "cancelled":
                    raise
            finally:
                self.active, self.active_id = None, None

    async def _execute(self, job_id: UUID) -> None:
        """Resolve intent, run a bounded harness and atomically publish only accepted evidence."""
        settings = self.client_configs.pop(job_id, self.settings)
        harness = self.harness if settings is self.settings else Harness(Provider(settings), self.harness.renderer)
        budget = Budget(
            audit_path=self.store.job_dir(job_id) / "audit.jsonl",
            on_progress=lambda phase: self.store.progress(job_id, phase),
        )
        job = self.store.job(job_id)
        context = self.store.conversation(job.project_id)
        try:
            async with asyncio.timeout(settings.job_timeout_seconds):
                job = self.store.job(job_id)
                project = self.store.project(job.project_id)
                inputs = self.store.inputs(job_id)
                images = (
                    [self.store.asset_path(project.request.image.asset_id)]
                    if project.request.image
                    else []
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
                    "accepted_base": base.spec.model_dump() if base else None,
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
                self.store.publish(job_id, candidate, spec, report, attempt_dir)
        except asyncio.CancelledError:
            self.store.update(
                job_id, status="interrupted", stage="finished", usage=budget.summary()
            )
            raise
        except (ModelFailure, TimeoutError, ValueError, Conflict) as exc:
            self.store.update(
                job_id,
                status="failed",
                stage="finished",
                usage=budget.summary(),
                error=JobError(
                    code=exc.code
                    if isinstance(exc, ExecutionFailure)
                    else "timeout"
                    if isinstance(exc, TimeoutError)
                    else "execution_failed",
                    message=str(exc)[:1000] or "Job deadline exceeded.",
                ),
            )
        except Exception:
            # Unexpected errors remain sanitized; individual runs cannot break the queue.
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
            self.store.save_conversation(job.project_id, context)
            final = self.store.job(job_id)
            budget.record(
                "run_finished",
                status=final.status,
                error=final.error.model_dump() if final.error else None,
                usage=budget.summary(),
            )
