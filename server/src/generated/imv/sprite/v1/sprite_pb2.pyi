import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SpriteKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SPRITE_KIND_UNSPECIFIED: _ClassVar[SpriteKind]
    SPRITE_KIND_TEXT: _ClassVar[SpriteKind]
    SPRITE_KIND_FILTER_OVERLAY: _ClassVar[SpriteKind]
    SPRITE_KIND_VIDEO_OVERLAY: _ClassVar[SpriteKind]
    SPRITE_KIND_TRANSITION_OVERLAY: _ClassVar[SpriteKind]

class OperatorAccess(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OPERATOR_ACCESS_UNSPECIFIED: _ClassVar[OperatorAccess]
    OPERATOR_ACCESS_VISIBLE_READ_ONLY: _ClassVar[OperatorAccess]
    OPERATOR_ACCESS_VISIBLE_EDITABLE: _ClassVar[OperatorAccess]
    OPERATOR_ACCESS_INTERNAL: _ClassVar[OperatorAccess]

class SpriteParameterFormat(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SPRITE_PARAMETER_FORMAT_UNSPECIFIED: _ClassVar[SpriteParameterFormat]
    SPRITE_PARAMETER_FORMAT_COLOR: _ClassVar[SpriteParameterFormat]
    SPRITE_PARAMETER_FORMAT_INTEGER: _ClassVar[SpriteParameterFormat]
SPRITE_KIND_UNSPECIFIED: SpriteKind
SPRITE_KIND_TEXT: SpriteKind
SPRITE_KIND_FILTER_OVERLAY: SpriteKind
SPRITE_KIND_VIDEO_OVERLAY: SpriteKind
SPRITE_KIND_TRANSITION_OVERLAY: SpriteKind
OPERATOR_ACCESS_UNSPECIFIED: OperatorAccess
OPERATOR_ACCESS_VISIBLE_READ_ONLY: OperatorAccess
OPERATOR_ACCESS_VISIBLE_EDITABLE: OperatorAccess
OPERATOR_ACCESS_INTERNAL: OperatorAccess
SPRITE_PARAMETER_FORMAT_UNSPECIFIED: SpriteParameterFormat
SPRITE_PARAMETER_FORMAT_COLOR: SpriteParameterFormat
SPRITE_PARAMETER_FORMAT_INTEGER: SpriteParameterFormat

class StringList(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, values: _Optional[_Iterable[str]] = ...) -> None: ...

class ScalarValue(_message.Message):
    __slots__ = ("string_value", "number_value", "bool_value", "string_list_value")
    STRING_VALUE_FIELD_NUMBER: _ClassVar[int]
    NUMBER_VALUE_FIELD_NUMBER: _ClassVar[int]
    BOOL_VALUE_FIELD_NUMBER: _ClassVar[int]
    STRING_LIST_VALUE_FIELD_NUMBER: _ClassVar[int]
    string_value: str
    number_value: float
    bool_value: bool
    string_list_value: StringList
    def __init__(self, string_value: _Optional[str] = ..., number_value: _Optional[float] = ..., bool_value: _Optional[bool] = ..., string_list_value: _Optional[_Union[StringList, _Mapping]] = ...) -> None: ...

class SpriteParameter(_message.Message):
    __slots__ = ("key", "label", "access", "default_value", "minimum", "maximum", "allowed_values", "format", "min_length", "max_length", "max_items", "group")
    KEY_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    ACCESS_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_VALUE_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_VALUES_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    MIN_LENGTH_FIELD_NUMBER: _ClassVar[int]
    MAX_LENGTH_FIELD_NUMBER: _ClassVar[int]
    MAX_ITEMS_FIELD_NUMBER: _ClassVar[int]
    GROUP_FIELD_NUMBER: _ClassVar[int]
    key: str
    label: str
    access: OperatorAccess
    default_value: ScalarValue
    minimum: float
    maximum: float
    allowed_values: _containers.RepeatedCompositeFieldContainer[ScalarValue]
    format: SpriteParameterFormat
    min_length: int
    max_length: int
    max_items: int
    group: str
    def __init__(self, key: _Optional[str] = ..., label: _Optional[str] = ..., access: _Optional[_Union[OperatorAccess, str]] = ..., default_value: _Optional[_Union[ScalarValue, _Mapping]] = ..., minimum: _Optional[float] = ..., maximum: _Optional[float] = ..., allowed_values: _Optional[_Iterable[_Union[ScalarValue, _Mapping]]] = ..., format: _Optional[_Union[SpriteParameterFormat, str]] = ..., min_length: _Optional[int] = ..., max_length: _Optional[int] = ..., max_items: _Optional[int] = ..., group: _Optional[str] = ...) -> None: ...

class SpriteBox(_message.Message):
    __slots__ = ("x", "y", "width", "height")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    width: float
    height: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., width: _Optional[float] = ..., height: _Optional[float] = ...) -> None: ...

class SpriteCanvas(_message.Message):
    __slots__ = ("width", "height", "fps", "preview_frames")
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    FPS_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_FRAMES_FIELD_NUMBER: _ClassVar[int]
    width: int
    height: int
    fps: int
    preview_frames: int
    def __init__(self, width: _Optional[int] = ..., height: _Optional[int] = ..., fps: _Optional[int] = ..., preview_frames: _Optional[int] = ...) -> None: ...

class PublishedSprite(_message.Message):
    __slots__ = ("sprite_id", "source_version_id", "name", "kind", "canvas", "tsx_code", "code_sha256", "parameters", "text_prop", "keywords_prop", "published_at", "preview_sha256", "config_schema_json", "default_config_json", "animation_frames", "static_frame", "bubble_asset_url", "bubble_asset_width", "bubble_asset_height", "content_box")
    SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CANVAS_FIELD_NUMBER: _ClassVar[int]
    TSX_CODE_FIELD_NUMBER: _ClassVar[int]
    CODE_SHA256_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    TEXT_PROP_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_PROP_FIELD_NUMBER: _ClassVar[int]
    PUBLISHED_AT_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_SHA256_FIELD_NUMBER: _ClassVar[int]
    CONFIG_SCHEMA_JSON_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_CONFIG_JSON_FIELD_NUMBER: _ClassVar[int]
    ANIMATION_FRAMES_FIELD_NUMBER: _ClassVar[int]
    STATIC_FRAME_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_ASSET_URL_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_ASSET_WIDTH_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_ASSET_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_BOX_FIELD_NUMBER: _ClassVar[int]
    sprite_id: str
    source_version_id: str
    name: str
    kind: SpriteKind
    canvas: SpriteCanvas
    tsx_code: str
    code_sha256: str
    parameters: _containers.RepeatedCompositeFieldContainer[SpriteParameter]
    text_prop: str
    keywords_prop: str
    published_at: _timestamp_pb2.Timestamp
    preview_sha256: str
    config_schema_json: str
    default_config_json: str
    animation_frames: int
    static_frame: int
    bubble_asset_url: str
    bubble_asset_width: int
    bubble_asset_height: int
    content_box: SpriteBox
    def __init__(self, sprite_id: _Optional[str] = ..., source_version_id: _Optional[str] = ..., name: _Optional[str] = ..., kind: _Optional[_Union[SpriteKind, str]] = ..., canvas: _Optional[_Union[SpriteCanvas, _Mapping]] = ..., tsx_code: _Optional[str] = ..., code_sha256: _Optional[str] = ..., parameters: _Optional[_Iterable[_Union[SpriteParameter, _Mapping]]] = ..., text_prop: _Optional[str] = ..., keywords_prop: _Optional[str] = ..., published_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., preview_sha256: _Optional[str] = ..., config_schema_json: _Optional[str] = ..., default_config_json: _Optional[str] = ..., animation_frames: _Optional[int] = ..., static_frame: _Optional[int] = ..., bubble_asset_url: _Optional[str] = ..., bubble_asset_width: _Optional[int] = ..., bubble_asset_height: _Optional[int] = ..., content_box: _Optional[_Union[SpriteBox, _Mapping]] = ...) -> None: ...

class SpriteSummary(_message.Message):
    __slots__ = ("sprite_id", "name", "kind", "canvas", "parameters", "source_version_id", "preview_url", "keywords_supported", "content_box", "line_count")
    SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CANVAS_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_URL_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    CONTENT_BOX_FIELD_NUMBER: _ClassVar[int]
    LINE_COUNT_FIELD_NUMBER: _ClassVar[int]
    sprite_id: str
    name: str
    kind: SpriteKind
    canvas: SpriteCanvas
    parameters: _containers.RepeatedCompositeFieldContainer[SpriteParameter]
    source_version_id: str
    preview_url: str
    keywords_supported: bool
    content_box: SpriteBox
    line_count: int
    def __init__(self, sprite_id: _Optional[str] = ..., name: _Optional[str] = ..., kind: _Optional[_Union[SpriteKind, str]] = ..., canvas: _Optional[_Union[SpriteCanvas, _Mapping]] = ..., parameters: _Optional[_Iterable[_Union[SpriteParameter, _Mapping]]] = ..., source_version_id: _Optional[str] = ..., preview_url: _Optional[str] = ..., keywords_supported: _Optional[bool] = ..., content_box: _Optional[_Union[SpriteBox, _Mapping]] = ..., line_count: _Optional[int] = ...) -> None: ...

class ListSpritesResponse(_message.Message):
    __slots__ = ("sprites",)
    SPRITES_FIELD_NUMBER: _ClassVar[int]
    sprites: _containers.RepeatedCompositeFieldContainer[SpriteSummary]
    def __init__(self, sprites: _Optional[_Iterable[_Union[SpriteSummary, _Mapping]]] = ...) -> None: ...

class PublishSpriteRequest(_message.Message):
    __slots__ = ("source_version_id", "kind", "text_prop", "keywords_prop", "target_sprite_id", "name")
    SOURCE_VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_PROP_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_PROP_FIELD_NUMBER: _ClassVar[int]
    TARGET_SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    source_version_id: str
    kind: SpriteKind
    text_prop: str
    keywords_prop: str
    target_sprite_id: str
    name: str
    def __init__(self, source_version_id: _Optional[str] = ..., kind: _Optional[_Union[SpriteKind, str]] = ..., text_prop: _Optional[str] = ..., keywords_prop: _Optional[str] = ..., target_sprite_id: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class PublishSpriteResponse(_message.Message):
    __slots__ = ("sprite",)
    SPRITE_FIELD_NUMBER: _ClassVar[int]
    sprite: SpriteSummary
    def __init__(self, sprite: _Optional[_Union[SpriteSummary, _Mapping]] = ...) -> None: ...

class RenameSpriteRequest(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class RenameSpriteResponse(_message.Message):
    __slots__ = ("sprite",)
    SPRITE_FIELD_NUMBER: _ClassVar[int]
    sprite: SpriteSummary
    def __init__(self, sprite: _Optional[_Union[SpriteSummary, _Mapping]] = ...) -> None: ...

class SpriteParameterOverride(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: ScalarValue
    def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ScalarValue, _Mapping]] = ...) -> None: ...

class SpriteRenderInput(_message.Message):
    __slots__ = ("sprite_id", "placement_id", "output", "text", "keywords", "resolved_style", "effect_total_frames", "callback_url", "transform", "cues")
    SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    PLACEMENT_ID_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_STYLE_FIELD_NUMBER: _ClassVar[int]
    EFFECT_TOTAL_FRAMES_FIELD_NUMBER: _ClassVar[int]
    CALLBACK_URL_FIELD_NUMBER: _ClassVar[int]
    TRANSFORM_FIELD_NUMBER: _ClassVar[int]
    CUES_FIELD_NUMBER: _ClassVar[int]
    sprite_id: str
    placement_id: str
    output: SpriteCanvas
    text: str
    keywords: _containers.RepeatedScalarFieldContainer[str]
    resolved_style: _containers.RepeatedCompositeFieldContainer[SpriteParameterOverride]
    effect_total_frames: int
    callback_url: str
    transform: SpriteHostTransform
    cues: _containers.RepeatedCompositeFieldContainer[SpriteCue]
    def __init__(self, sprite_id: _Optional[str] = ..., placement_id: _Optional[str] = ..., output: _Optional[_Union[SpriteCanvas, _Mapping]] = ..., text: _Optional[str] = ..., keywords: _Optional[_Iterable[str]] = ..., resolved_style: _Optional[_Iterable[_Union[SpriteParameterOverride, _Mapping]]] = ..., effect_total_frames: _Optional[int] = ..., callback_url: _Optional[str] = ..., transform: _Optional[_Union[SpriteHostTransform, _Mapping]] = ..., cues: _Optional[_Iterable[_Union[SpriteCue, _Mapping]]] = ...) -> None: ...

class SpriteCue(_message.Message):
    __slots__ = ("text", "keywords", "start_frame", "end_frame", "sentence")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_FIELD_NUMBER: _ClassVar[int]
    START_FRAME_FIELD_NUMBER: _ClassVar[int]
    END_FRAME_FIELD_NUMBER: _ClassVar[int]
    SENTENCE_FIELD_NUMBER: _ClassVar[int]
    text: str
    keywords: _containers.RepeatedScalarFieldContainer[str]
    start_frame: int
    end_frame: int
    sentence: int
    def __init__(self, text: _Optional[str] = ..., keywords: _Optional[_Iterable[str]] = ..., start_frame: _Optional[int] = ..., end_frame: _Optional[int] = ..., sentence: _Optional[int] = ...) -> None: ...

class SpriteHostTransform(_message.Message):
    __slots__ = ("x", "y", "scale", "rotation", "opacity")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    SCALE_FIELD_NUMBER: _ClassVar[int]
    ROTATION_FIELD_NUMBER: _ClassVar[int]
    OPACITY_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    scale: float
    rotation: float
    opacity: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., scale: _Optional[float] = ..., rotation: _Optional[float] = ..., opacity: _Optional[float] = ...) -> None: ...

class SpriteRenderAccepted(_message.Message):
    __slots__ = ("task_id",)
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    def __init__(self, task_id: _Optional[str] = ...) -> None: ...

class SpriteRenderTask(_message.Message):
    __slots__ = ("task_id", "status", "video_url", "error_code", "error_message")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    VIDEO_URL_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    status: str
    video_url: str
    error_code: str
    error_message: str
    def __init__(self, task_id: _Optional[str] = ..., status: _Optional[str] = ..., video_url: _Optional[str] = ..., error_code: _Optional[str] = ..., error_message: _Optional[str] = ...) -> None: ...
