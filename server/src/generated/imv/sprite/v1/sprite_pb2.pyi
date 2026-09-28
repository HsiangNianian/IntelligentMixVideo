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

class SpriteTarget(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SPRITE_TARGET_UNSPECIFIED: _ClassVar[SpriteTarget]
    SPRITE_TARGET_TITLE: _ClassVar[SpriteTarget]
    SPRITE_TARGET_SUBTITLE: _ClassVar[SpriteTarget]
    SPRITE_TARGET_FILTER: _ClassVar[SpriteTarget]
    SPRITE_TARGET_VIDEO_EFFECT: _ClassVar[SpriteTarget]
    SPRITE_TARGET_TRANSITION: _ClassVar[SpriteTarget]
    SPRITE_TARGET_VIDEO_ENTER: _ClassVar[SpriteTarget]
    SPRITE_TARGET_VIDEO_EXIT: _ClassVar[SpriteTarget]

class OperatorAccess(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OPERATOR_ACCESS_UNSPECIFIED: _ClassVar[OperatorAccess]
    OPERATOR_ACCESS_VISIBLE_READ_ONLY: _ClassVar[OperatorAccess]
    OPERATOR_ACCESS_VISIBLE_EDITABLE: _ClassVar[OperatorAccess]
    OPERATOR_ACCESS_INTERNAL: _ClassVar[OperatorAccess]
SPRITE_KIND_UNSPECIFIED: SpriteKind
SPRITE_KIND_TEXT: SpriteKind
SPRITE_KIND_FILTER_OVERLAY: SpriteKind
SPRITE_KIND_VIDEO_OVERLAY: SpriteKind
SPRITE_KIND_TRANSITION_OVERLAY: SpriteKind
SPRITE_TARGET_UNSPECIFIED: SpriteTarget
SPRITE_TARGET_TITLE: SpriteTarget
SPRITE_TARGET_SUBTITLE: SpriteTarget
SPRITE_TARGET_FILTER: SpriteTarget
SPRITE_TARGET_VIDEO_EFFECT: SpriteTarget
SPRITE_TARGET_TRANSITION: SpriteTarget
SPRITE_TARGET_VIDEO_ENTER: SpriteTarget
SPRITE_TARGET_VIDEO_EXIT: SpriteTarget
OPERATOR_ACCESS_UNSPECIFIED: OperatorAccess
OPERATOR_ACCESS_VISIBLE_READ_ONLY: OperatorAccess
OPERATOR_ACCESS_VISIBLE_EDITABLE: OperatorAccess
OPERATOR_ACCESS_INTERNAL: OperatorAccess

class ScalarValue(_message.Message):
    __slots__ = ("string_value", "number_value", "bool_value")
    STRING_VALUE_FIELD_NUMBER: _ClassVar[int]
    NUMBER_VALUE_FIELD_NUMBER: _ClassVar[int]
    BOOL_VALUE_FIELD_NUMBER: _ClassVar[int]
    string_value: str
    number_value: float
    bool_value: bool
    def __init__(self, string_value: _Optional[str] = ..., number_value: _Optional[float] = ..., bool_value: _Optional[bool] = ...) -> None: ...

class SpriteParameter(_message.Message):
    __slots__ = ("key", "label", "access", "default_value", "minimum", "maximum", "allowed_values")
    KEY_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    ACCESS_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_VALUE_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_VALUES_FIELD_NUMBER: _ClassVar[int]
    key: str
    label: str
    access: OperatorAccess
    default_value: ScalarValue
    minimum: float
    maximum: float
    allowed_values: _containers.RepeatedCompositeFieldContainer[ScalarValue]
    def __init__(self, key: _Optional[str] = ..., label: _Optional[str] = ..., access: _Optional[_Union[OperatorAccess, str]] = ..., default_value: _Optional[_Union[ScalarValue, _Mapping]] = ..., minimum: _Optional[float] = ..., maximum: _Optional[float] = ..., allowed_values: _Optional[_Iterable[_Union[ScalarValue, _Mapping]]] = ...) -> None: ...

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
    __slots__ = ("sprite_id", "source_version_id", "name", "kind", "canvas", "tsx_code", "code_sha256", "parameters", "text_prop", "keywords_prop", "published_at", "preview_sha256", "config_schema_json", "default_config_json", "animation_frames", "static_frame", "bubble_asset_url", "bubble_asset_width", "bubble_asset_height")
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
    def __init__(self, sprite_id: _Optional[str] = ..., source_version_id: _Optional[str] = ..., name: _Optional[str] = ..., kind: _Optional[_Union[SpriteKind, str]] = ..., canvas: _Optional[_Union[SpriteCanvas, _Mapping]] = ..., tsx_code: _Optional[str] = ..., code_sha256: _Optional[str] = ..., parameters: _Optional[_Iterable[_Union[SpriteParameter, _Mapping]]] = ..., text_prop: _Optional[str] = ..., keywords_prop: _Optional[str] = ..., published_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., preview_sha256: _Optional[str] = ..., config_schema_json: _Optional[str] = ..., default_config_json: _Optional[str] = ..., animation_frames: _Optional[int] = ..., static_frame: _Optional[int] = ..., bubble_asset_url: _Optional[str] = ..., bubble_asset_width: _Optional[int] = ..., bubble_asset_height: _Optional[int] = ...) -> None: ...

class SpriteSummary(_message.Message):
    __slots__ = ("sprite_id", "name", "kind", "canvas", "parameters", "source_version_id", "preview_url", "keywords_supported")
    SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    CANVAS_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    SOURCE_VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_URL_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    sprite_id: str
    name: str
    kind: SpriteKind
    canvas: SpriteCanvas
    parameters: _containers.RepeatedCompositeFieldContainer[SpriteParameter]
    source_version_id: str
    preview_url: str
    keywords_supported: bool
    def __init__(self, sprite_id: _Optional[str] = ..., name: _Optional[str] = ..., kind: _Optional[_Union[SpriteKind, str]] = ..., canvas: _Optional[_Union[SpriteCanvas, _Mapping]] = ..., parameters: _Optional[_Iterable[_Union[SpriteParameter, _Mapping]]] = ..., source_version_id: _Optional[str] = ..., preview_url: _Optional[str] = ..., keywords_supported: _Optional[bool] = ...) -> None: ...

class ListSpritesResponse(_message.Message):
    __slots__ = ("sprites",)
    SPRITES_FIELD_NUMBER: _ClassVar[int]
    sprites: _containers.RepeatedCompositeFieldContainer[SpriteSummary]
    def __init__(self, sprites: _Optional[_Iterable[_Union[SpriteSummary, _Mapping]]] = ...) -> None: ...

class PublishSpriteRequest(_message.Message):
    __slots__ = ("source_version_id", "kind", "text_prop", "keywords_prop")
    SOURCE_VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    TEXT_PROP_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_PROP_FIELD_NUMBER: _ClassVar[int]
    source_version_id: str
    kind: SpriteKind
    text_prop: str
    keywords_prop: str
    def __init__(self, source_version_id: _Optional[str] = ..., kind: _Optional[_Union[SpriteKind, str]] = ..., text_prop: _Optional[str] = ..., keywords_prop: _Optional[str] = ...) -> None: ...

class PublishSpriteResponse(_message.Message):
    __slots__ = ("sprite",)
    SPRITE_FIELD_NUMBER: _ClassVar[int]
    sprite: SpriteSummary
    def __init__(self, sprite: _Optional[_Union[SpriteSummary, _Mapping]] = ...) -> None: ...

class SpritePlacement(_message.Message):
    __slots__ = ("id", "sprite_id", "target", "start_mode", "start", "duration", "overrides", "order")
    ID_FIELD_NUMBER: _ClassVar[int]
    SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    START_MODE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    OVERRIDES_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    id: str
    sprite_id: str
    target: SpriteTarget
    start_mode: str
    start: float
    duration: float
    overrides: _containers.RepeatedCompositeFieldContainer[SpriteParameterOverride]
    order: int
    def __init__(self, id: _Optional[str] = ..., sprite_id: _Optional[str] = ..., target: _Optional[_Union[SpriteTarget, str]] = ..., start_mode: _Optional[str] = ..., start: _Optional[float] = ..., duration: _Optional[float] = ..., overrides: _Optional[_Iterable[_Union[SpriteParameterOverride, _Mapping]]] = ..., order: _Optional[int] = ...) -> None: ...

class SpriteParameterOverride(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: ScalarValue
    def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ScalarValue, _Mapping]] = ...) -> None: ...

class StyleSpriteBindings(_message.Message):
    __slots__ = ("style_id", "placements", "revision", "updated_at")
    STYLE_ID_FIELD_NUMBER: _ClassVar[int]
    PLACEMENTS_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    style_id: str
    placements: _containers.RepeatedCompositeFieldContainer[SpritePlacement]
    revision: int
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, style_id: _Optional[str] = ..., placements: _Optional[_Iterable[_Union[SpritePlacement, _Mapping]]] = ..., revision: _Optional[int] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class GetStyleSpritesResponse(_message.Message):
    __slots__ = ("bindings",)
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    bindings: StyleSpriteBindings
    def __init__(self, bindings: _Optional[_Union[StyleSpriteBindings, _Mapping]] = ...) -> None: ...

class SaveStyleSpritesRequest(_message.Message):
    __slots__ = ("style_id", "placements", "expected_revision")
    STYLE_ID_FIELD_NUMBER: _ClassVar[int]
    PLACEMENTS_FIELD_NUMBER: _ClassVar[int]
    EXPECTED_REVISION_FIELD_NUMBER: _ClassVar[int]
    style_id: str
    placements: _containers.RepeatedCompositeFieldContainer[SpritePlacement]
    expected_revision: int
    def __init__(self, style_id: _Optional[str] = ..., placements: _Optional[_Iterable[_Union[SpritePlacement, _Mapping]]] = ..., expected_revision: _Optional[int] = ...) -> None: ...

class SaveStyleSpritesResponse(_message.Message):
    __slots__ = ("bindings",)
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    bindings: StyleSpriteBindings
    def __init__(self, bindings: _Optional[_Union[StyleSpriteBindings, _Mapping]] = ...) -> None: ...

class SpriteRenderInput(_message.Message):
    __slots__ = ("sprite_id", "placement_id", "output", "text", "keywords", "resolved_style", "effect_total_frames", "effect_offset_frames", "render_frame_count")
    SPRITE_ID_FIELD_NUMBER: _ClassVar[int]
    PLACEMENT_ID_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    KEYWORDS_FIELD_NUMBER: _ClassVar[int]
    RESOLVED_STYLE_FIELD_NUMBER: _ClassVar[int]
    EFFECT_TOTAL_FRAMES_FIELD_NUMBER: _ClassVar[int]
    EFFECT_OFFSET_FRAMES_FIELD_NUMBER: _ClassVar[int]
    RENDER_FRAME_COUNT_FIELD_NUMBER: _ClassVar[int]
    sprite_id: str
    placement_id: str
    output: SpriteCanvas
    text: str
    keywords: _containers.RepeatedScalarFieldContainer[str]
    resolved_style: _containers.RepeatedCompositeFieldContainer[SpriteParameterOverride]
    effect_total_frames: int
    effect_offset_frames: int
    render_frame_count: int
    def __init__(self, sprite_id: _Optional[str] = ..., placement_id: _Optional[str] = ..., output: _Optional[_Union[SpriteCanvas, _Mapping]] = ..., text: _Optional[str] = ..., keywords: _Optional[_Iterable[str]] = ..., resolved_style: _Optional[_Iterable[_Union[SpriteParameterOverride, _Mapping]]] = ..., effect_total_frames: _Optional[int] = ..., effect_offset_frames: _Optional[int] = ..., render_frame_count: _Optional[int] = ...) -> None: ...
