"""Single-process durable job queue; cancellation and failure never replace an accepted version."""

import asyncio
import json
from contextlib import suppress
from uuid import UUID

from ..settings import Settings
from .harness import Harness
from .models import EditTemplateRequest, JobError, JobInput, TaskMessage
from .provider import Budget, ModelFailure
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

    def initialize(self) -> None:
        """Lock the state directory before recovery so a second server cannot interrupt live jobs."""
        import fcntl

        self.store.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.store.root / "runtime.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.store.initialize()
            self.store.interrupt_unfinished()
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    def notify(self) -> None:
        """Create a drain task only when work exists; no idle loop or application lifecycle hook."""
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self._work())

    def edit(self, project_id: UUID, request: EditTemplateRequest):
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
        self.notify()
        return job

    def message(self, project_id: UUID, request: TaskMessage):
        """Route task input to an accepted-version edit or the exact outstanding question set."""
        latest = self.store.latest_job(project_id)
        if request.reply_to_job_id:
            if latest.id != request.reply_to_job_id or latest.status != "needs_input":
                raise Conflict("Question reply is stale or belongs to another task.")
            return self.retry(latest.id, request.instruction)
        if latest.status == "needs_input":
            raise Conflict("Answer the outstanding questions using reply_to_job_id.")
        return self.edit(
            project_id,
            EditTemplateRequest.model_validate(
                request.model_dump(exclude={"reply_to_job_id"})
            ),
        )

    def retry(self, job_id: UUID, answer: str | None = None):
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
        result = self.store.enqueue(job.project_id, inputs, job.base_version_id)
        self.notify()
        return result

    async def cancel(self, job_id: UUID):
        """Persist cancellation first so even a late model result cannot publish a revision."""
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
        budget = Budget()
        job = self.store.job(job_id)
        context = self.store.conversation(job.project_id)
        try:
            async with asyncio.timeout(self.settings.job_timeout_seconds):
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
                patch = inputs.parameters
                questions = []
                if inputs.mode == "generate":
                    analysis = await self.harness.analyze(
                        json.dumps(
                            {
                                "request": project.request.model_dump(mode="json"),
                                "clarifications": inputs.clarifications,
                            },
                            ensure_ascii=False,
                        ),
                        budget,
                        images,
                        context=context,
                    )
                    spec, questions = analysis.spec, analysis.questions
                    if spec and spec.composition != project.request.composition:
                        raise ValueError(
                            "Analysis changed the requested composition dimensions or timing."
                        )
                elif inputs.mode == "edit":
                    if base is None:
                        raise ValueError("Edit requires an accepted version.")
                    decision = await self.harness.edit(
                        json.dumps(
                            {
                                "instruction": inputs.instruction,
                                "base_spec": base.spec.model_dump(),
                                "config_schema": base.candidate.config_schema,
                                "default_config": base.candidate.default_config,
                                "clarifications": inputs.clarifications,
                            },
                            ensure_ascii=False,
                        ),
                        budget,
                        context=context,
                        base=base,
                    )
                    spec, patch, questions = (
                        decision.spec or base.spec,
                        decision.parameters,
                        decision.questions,
                    )
                else:
                    if base is None:
                        raise ValueError("Parameter edit requires an accepted version.")
                    spec = base.spec
                    context.append(
                        [
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {"parameters": patch}, ensure_ascii=False
                                ),
                            }
                        ]
                    )
                if questions:
                    self.store.update(
                        job_id,
                        status="needs_input",
                        stage="finished",
                        questions=questions,
                        usage=vars(budget),
                    )
                    return
                if spec is None:
                    raise ValueError("No actionable specification was produced.")
                directory = self.store.job_dir(job_id)
                directory.mkdir(parents=True, exist_ok=True)

                def stage(name: str, attempt: int) -> None:
                    """Record private stage progress and current budget at each decision boundary."""
                    self.store.update(
                        job_id, stage=name, attempts=attempt, usage=vars(budget)
                    )

                candidate, spec, report, attempt_dir = await self.harness.generate(
                    spec,
                    budget,
                    directory,
                    images,
                    stage,
                    base=base.candidate if base else None,
                    parameter_patch=patch,
                    context=context,
                    intent={
                        "original_request": project.request.model_dump(mode="json")
                        if base is None
                        else None,
                        "instruction": inputs.instruction,
                        "parameters": patch,
                        "clarifications": inputs.clarifications,
                        "accepted_target": base.spec.model_dump() if base else None,
                    },
                )
                self.store.update(job_id, usage=vars(budget))
                self.store.publish(job_id, candidate, spec, report, attempt_dir)
        except asyncio.CancelledError:
            self.store.update(
                job_id, status="interrupted", stage="finished", usage=vars(budget)
            )
            raise
        except (ModelFailure, TimeoutError, ValueError, Conflict) as exc:
            self.store.update(
                job_id,
                status="failed",
                stage="finished",
                usage=vars(budget),
                error=JobError(
                    code="timeout"
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
                usage=vars(budget),
                error=JobError(
                    code="internal_error",
                    message="Execution failed unexpectedly; inspect server diagnostics.",
                ),
            )
        finally:
            self.store.save_conversation(job.project_id, context)
