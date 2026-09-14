"""Versioned API and agent contracts; media observations never imply verified output."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)
]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")]


class Contract(BaseModel):
    """Reject misspelled fields and non-finite values without trimming TSX or copy."""

    model_config = ConfigDict(
        extra="forbid", validate_default=True, allow_inf_nan=False
    )


class CompositionConfig(Contract):
    """Target pixels and frames; bounded even dimensions support the MP4 preview."""

    width: int = Field(default=1080, ge=64, le=3840, multiple_of=2, strict=True)
    height: int = Field(default=1920, ge=64, le=3840, multiple_of=2, strict=True)
    fps: float = Field(default=30, ge=1, le=60)
    duration_in_frames: int = Field(default=150, ge=1, le=1800, strict=True)

    @model_validator(mode="after")
    def bounded_preview(self) -> Self:
        """Limit one preview to thirty seconds and an eight-megapixel canvas."""
        if self.duration_in_frames / self.fps > 30:
            raise ValueError("preview duration must not exceed 30 seconds")
        if self.width * self.height > 8_294_400:
            raise ValueError("canvas must not exceed 8,294,400 pixels")
        return self


class ImageReference(Contract):
    """An uploaded image, resolved and verified by the server rather than by URL."""

    asset_id: UUID


class GenerateTemplateRequest(Contract):
    """MVP supports description and/or image; video remains an unsupported extension."""

    description: Text | None = None
    image: ImageReference | None = None
    composition: CompositionConfig = Field(default_factory=CompositionConfig)

    @model_validator(mode="after")
    def require_input(self) -> Self:
        """Reject empty requests instead of silently generating an arbitrary template."""
        if self.description is None and self.image is None:
            raise ValueError(
                "description or image is required; video is not supported yet"
            )
        return self


class EditTemplateRequest(Contract):
    """Edit a successful version with either natural language or a parameter patch."""

    instruction: Text | None = None
    parameters: dict[str, JsonValue] | None = None
    base_version_id: UUID | None = None

    @model_validator(mode="after")
    def exactly_one_edit(self) -> Self:
        """Prevent contradictory edits and parameter updates which do nothing."""
        if (self.instruction is None) == (self.parameters is None):
            raise ValueError("provide exactly one of instruction or parameters")
        if self.parameters is not None and not self.parameters:
            raise ValueError("parameters must not be empty")
        return self


class TaskMessage(EditTemplateRequest):
    """One task input: edit current accepted output or answer a specific outstanding job's questions."""

    reply_to_job_id: UUID | None = None

    @model_validator(mode="after")
    def answer_is_text(self) -> Self:
        """Question replies are text and cannot also select an edit base or patch parameters."""
        if self.reply_to_job_id and (
            self.parameters is not None or self.base_version_id is not None
        ):
            raise ValueError("question replies require instruction only")
        return self


class TextLayout(Contract):
    """Stable layout uses normalized center coordinates and width on the target canvas."""

    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    width: float = Field(default=0.85, gt=0, le=1)
    align: Literal["left", "center", "right"] = "center"
    rotation: float = Field(default=0, ge=-360, le=360)


class Stroke(Contract):
    """One outline; multiple entries describe layered text outlines."""

    color: Color = "#000000"
    width: float = Field(default=0, ge=0, le=50)


class Shadow(Contract):
    """One text shadow with offsets and blur in target-canvas pixels."""

    color: Color = "#00000080"
    x: float = Field(default=0, ge=-100, le=100)
    y: float = Field(default=4, ge=-100, le=100)
    blur: float = Field(default=8, ge=0, le=100)


class TextStyle(Contract):
    """Typography uses the managed font family and concrete, editable style values."""

    font_family: Literal["Noto Sans CJK SC"] = "Noto Sans CJK SC"
    font_size: float = Field(default=80, gt=0, le=600)
    font_weight: Literal[400, 700] = 700
    color: Color = "#FFFFFF"
    letter_spacing: float = Field(default=0, ge=-20, le=100)
    line_height: float = Field(default=1.2, ge=0.5, le=3)
    strokes: list[Stroke] = Field(default_factory=list, max_length=4)
    shadows: list[Shadow] = Field(default_factory=list, max_length=4)


class MotionSegment(Contract):
    """A frame-driven effect requirement; the end frame is exclusive."""

    phase: Literal["enter", "hold", "exit"]
    start_frame: int = Field(ge=0, strict=True)
    end_frame: int = Field(gt=0, strict=True)
    description: Text

    @model_validator(mode="after")
    def ordered_frames(self) -> Self:
        """Require a nonempty animation interval."""
        if self.end_frame <= self.start_frame:
            raise ValueError("motion end_frame must be after start_frame")
        return self


class TextLayer(Contract):
    """One editable text region; whitespace in the actual copy is significant."""

    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
    text: str = Field(min_length=1, max_length=2000)
    layout: TextLayout = Field(default_factory=TextLayout)
    style: TextStyle = Field(default_factory=TextStyle)
    start_frame: int = Field(default=0, ge=0, strict=True)
    end_frame: int = Field(gt=0, strict=True)
    motion: list[MotionSegment] = Field(default_factory=list, max_length=8)
    decorations: list[Text] = Field(default_factory=list, max_length=8)


class TemplateSpec(Contract):
    """Fixed acceptance target for one execution, shared by generation and review."""

    schema_version: Literal["1"] = "1"
    name: Text
    description: Text
    composition: CompositionConfig
    text_layers: list[TextLayer] = Field(min_length=1, max_length=12)
    assumptions: list[Text] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_layers(self) -> Self:
        """Reject ambiguous IDs, blank copy, and effects outside their layer interval."""
        if len({layer.id for layer in self.text_layers}) != len(self.text_layers):
            raise ValueError("text layer IDs must be unique")
        for layer in self.text_layers:
            if not layer.text.strip():
                raise ValueError("text layer copy must not be blank")
            if (
                not 0
                <= layer.start_frame
                < layer.end_frame
                <= self.composition.duration_in_frames
            ):
                raise ValueError("text layer frames must fit the composition")
            if any(
                motion.start_frame < layer.start_frame
                or motion.end_frame > layer.end_frame
                for motion in layer.motion
            ):
                raise ValueError("motion frames must fit their text layer")
        return self


class DialogueOutput(Contract):
    """A user-facing answer or necessary clarification, separate from generated candidates."""

    answer: Text | None = None
    questions: list[Text] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def one_outcome(self) -> Self:
        """Require exactly one reply kind; a reply cannot claim to contain a candidate."""
        if (self.answer is not None) == bool(self.questions):
            raise ValueError("choose answer or nonempty questions")
        return self


class AnswerReview(Contract):
    """Independent assessment of a proposed answer against user input and accepted facts."""

    status: Literal["pass", "fail", "unknown"]
    detail: str = Field(min_length=1, max_length=3000)



class AnalysisResult(Contract):
    """Either an actionable template specification or concrete questions for the user."""

    spec: TemplateSpec | None = None
    questions: list[Text] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def one_outcome(self) -> Self:
        """Do not mix uncertain copy with a supposedly ready specification."""
        if (self.spec is None) == (not self.questions):
            raise ValueError("return either spec or nonempty questions")
        return self


class TargetReview(Contract):
    """Independent semantic assessment of a model-derived target against authoritative user input."""

    status: Literal["pass", "fail", "unknown"]
    detail: str = Field(min_length=1, max_length=3000)


class TemplateCandidate(Contract):
    """Untrusted model output; schema and rendering checks are performed separately."""

    tsx_code: str = Field(min_length=1, max_length=100_000)
    config_schema: dict[str, JsonValue]
    default_config: dict[str, JsonValue]


class EditDecision(Contract):
    """Resolve a natural-language edit to a parameter patch or a new acceptance target."""

    parameters: dict[str, JsonValue] | None = None
    spec: TemplateSpec | None = None
    questions: list[Text] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def one_action(self) -> Self:
        """Choose one action; code edits and parameter edits cannot run concurrently."""
        if (
            sum(
                (
                    self.parameters is not None,
                    self.spec is not None,
                    bool(self.questions),
                )
            )
            != 1
        ):
            raise ValueError("choose parameters, spec, or questions")
        if self.parameters == {}:
            raise ValueError("parameter patch must not be empty")
        return self


class Check(Contract):
    """A host-owned verdict cites its diagnostics and optional preview frame."""

    name: str
    source: Literal["host", "visual_model"] = "host"
    status: Literal["pass", "fail", "unknown"]
    detail: str
    frame: int | None = None


class VisualCheck(Check):
    """Expose allowed review dimensions in JSON Schema so a model cannot invent check names."""

    name: Literal["text", "layout", "style", "motion", "scope"]


class VisualReview(Contract):
    """Visual findings are assessments, never inferred from compiler success."""

    checks: list[VisualCheck] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def complete_review(self) -> Self:
        """A reviewer must assess every acceptance dimension exactly once."""
        names = [check.name for check in self.checks]
        if len(names) != 5 or set(names) != {
            "text",
            "layout",
            "style",
            "motion",
            "scope",
        }:
            raise ValueError(
                "review must cover text, layout, style, motion, and scope exactly once"
            )
        return self


class ValidationReport(Contract):
    """Every check is bound to code, configuration, specification, fonts, and runtime."""

    fingerprint: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    checks: list[Check] = Field(default_factory=list)
    frames: list[int] = Field(default_factory=list)
    runtime: dict[str, str] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Unknown or missing required evidence cannot approve a candidate."""
        required = {
            "configuration",
            "source_policy",
            "typescript",
            "bundle",
            "render",
            "determinism",
            "media_metadata",
            "transparency",
            "parameter_behavior",
            "motion_evidence",
            "visual_text",
            "visual_layout",
            "visual_style",
            "visual_motion",
            "visual_scope",
        }
        return (
            len({check.name for check in self.checks}) == len(self.checks)
            and required <= {check.name for check in self.checks}
            and all(check.status == "pass" for check in self.checks)
        )


def validation_fingerprint(
    candidate: TemplateCandidate, spec: TemplateSpec, runtime: dict[str, str]
) -> str:
    """Bind evidence to all inputs, including font bytes and renderer dependency versions."""
    payload = {
        "candidate": candidate.model_dump(),
        "spec": spec.model_dump(),
        "runtime": runtime,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Asset(Contract):
    """A verified, normalized uploaded image, addressed independently of its filename."""

    id: UUID
    media_type: Literal["image/png"] = "image/png"
    width: int
    height: int
    sha256: str
    created_at: datetime


class TemplateProject(Contract):
    """One isolated template task; its current pointer advances only after full acceptance."""

    id: UUID
    request: GenerateTemplateRequest
    current_version_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class TemplateVersion(Contract):
    """An immutable accepted revision; historical sources do not create new branches."""

    id: UUID
    project_id: UUID
    job_id: UUID
    number: int
    base_version_id: UUID | None
    candidate: TemplateCandidate
    spec: TemplateSpec
    validation: ValidationReport
    created_at: datetime


class JobError(Contract):
    """A public error code and actionable message, excluding provider secrets."""

    code: str
    message: str


class GenerationJob(Contract):
    """One queued execution; questions and failures retain candidate artifacts."""

    id: UUID
    project_id: UUID
    status: Literal[
        "queued",
        "running",
        "succeeded",
        "answered",
        "failed",
        "cancelled",
        "interrupted",
        "needs_input",
    ]
    stage: Literal[
        "queued", "analyzing", "generating", "validating", "repairing", "finished"
    ]
    base_version_id: UUID | None = None
    result_version_id: UUID | None = None
    attempts: int = 0
    questions: list[str] = Field(default_factory=list)
    answer: Text | None = None
    error: JobError | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def answer_is_terminal_without_version(self) -> Self:
        """A public answer has no render result or outstanding questions; other states cannot publish it."""
        if self.status == "answered":
            if (
                self.answer is None
                or self.result_version_id is not None
                or self.questions
            ):
                raise ValueError(
                    "answered requires an answer without a version or questions"
                )
        elif self.answer is not None:
            raise ValueError("only answered jobs may contain an answer")
        return self


class JobInput(Contract):
    """Persisted execution input allows explicit retries without replaying hidden state."""

    mode: Literal["generate", "edit", "parameters"] = "generate"
    instruction: str | None = None
    parameters: dict[str, JsonValue] | None = None
    clarifications: list[str] = Field(default_factory=list)


class PublicJob(Contract):
    """User-visible execution state excludes internal repairs, evidence, usage and diagnostics."""

    id: UUID
    project_id: UUID
    status: str
    result_version_id: UUID | None
    questions: list[str]
    message: str | None = None

    @classmethod
    def from_job(cls, job: GenerationJob):
        """Expose reviewed answers and terminal notices while keeping candidate diagnostics private."""
        return cls(
            id=job.id,
            project_id=job.project_id,
            status=job.status,
            result_version_id=job.result_version_id,
            questions=job.questions,
            message="本次未能完成模板，请重试；已有结果仍可使用。"
            if job.status in {"failed", "interrupted"}
            else job.answer
            if job.status == "answered"
            else None,
        )


class PublicVersion(Contract):
    """Accepted template payload; internal acceptance reports never enter the API contract."""

    id: UUID
    project_id: UUID
    number: int
    candidate: TemplateCandidate
    spec: TemplateSpec
    created_at: datetime

    @classmethod
    def from_version(cls, version: TemplateVersion):
        """Select only reusable successful template data."""
        return cls.model_validate(version.model_dump(include=set(cls.model_fields)))
