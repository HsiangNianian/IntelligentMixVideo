from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class DisplayCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DISPLAY_CATEGORY_UNSPECIFIED: _ClassVar[DisplayCategory]
    DISPLAY_CATEGORY_TITLE: _ClassVar[DisplayCategory]
    DISPLAY_CATEGORY_SUBTITLE: _ClassVar[DisplayCategory]
    DISPLAY_CATEGORY_GENERAL_TEXT: _ClassVar[DisplayCategory]
    DISPLAY_CATEGORY_MIXED_TEXT: _ClassVar[DisplayCategory]
    DISPLAY_CATEGORY_KEYWORD: _ClassVar[DisplayCategory]

class TextRole(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TEXT_ROLE_UNSPECIFIED: _ClassVar[TextRole]
    TEXT_ROLE_TITLE: _ClassVar[TextRole]
    TEXT_ROLE_SUBTITLE: _ClassVar[TextRole]
    TEXT_ROLE_GENERAL_TEXT: _ClassVar[TextRole]
    TEXT_ROLE_KEYWORD: _ClassVar[TextRole]

class MotionPhase(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MOTION_PHASE_UNSPECIFIED: _ClassVar[MotionPhase]
    MOTION_PHASE_ENTER: _ClassVar[MotionPhase]
    MOTION_PHASE_HOLD: _ClassVar[MotionPhase]
    MOTION_PHASE_EXIT: _ClassVar[MotionPhase]

class ValueUnit(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    VALUE_UNIT_UNSPECIFIED: _ClassVar[ValueUnit]
    VALUE_UNIT_PIXEL: _ClassVar[ValueUnit]
    VALUE_UNIT_NORMALIZED: _ClassVar[ValueUnit]
    VALUE_UNIT_DEGREE: _ClassVar[ValueUnit]
    VALUE_UNIT_RATIO: _ClassVar[ValueUnit]
DISPLAY_CATEGORY_UNSPECIFIED: DisplayCategory
DISPLAY_CATEGORY_TITLE: DisplayCategory
DISPLAY_CATEGORY_SUBTITLE: DisplayCategory
DISPLAY_CATEGORY_GENERAL_TEXT: DisplayCategory
DISPLAY_CATEGORY_MIXED_TEXT: DisplayCategory
DISPLAY_CATEGORY_KEYWORD: DisplayCategory
TEXT_ROLE_UNSPECIFIED: TextRole
TEXT_ROLE_TITLE: TextRole
TEXT_ROLE_SUBTITLE: TextRole
TEXT_ROLE_GENERAL_TEXT: TextRole
TEXT_ROLE_KEYWORD: TextRole
MOTION_PHASE_UNSPECIFIED: MotionPhase
MOTION_PHASE_ENTER: MotionPhase
MOTION_PHASE_HOLD: MotionPhase
MOTION_PHASE_EXIT: MotionPhase
VALUE_UNIT_UNSPECIFIED: ValueUnit
VALUE_UNIT_PIXEL: ValueUnit
VALUE_UNIT_NORMALIZED: ValueUnit
VALUE_UNIT_DEGREE: ValueUnit
VALUE_UNIT_RATIO: ValueUnit

class Composition(_message.Message):
    __slots__ = ("width", "height", "fps", "duration_in_frames")
    WIDTH_FIELD_NUMBER: _ClassVar[int]
    HEIGHT_FIELD_NUMBER: _ClassVar[int]
    FPS_FIELD_NUMBER: _ClassVar[int]
    DURATION_IN_FRAMES_FIELD_NUMBER: _ClassVar[int]
    width: int
    height: int
    fps: float
    duration_in_frames: int
    def __init__(self, width: _Optional[int] = ..., height: _Optional[int] = ..., fps: _Optional[float] = ..., duration_in_frames: _Optional[int] = ...) -> None: ...

class MotionSegment(_message.Message):
    __slots__ = ("phase", "start_frame", "end_frame", "description")
    PHASE_FIELD_NUMBER: _ClassVar[int]
    START_FRAME_FIELD_NUMBER: _ClassVar[int]
    END_FRAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    phase: MotionPhase
    start_frame: int
    end_frame: int
    description: str
    def __init__(self, phase: _Optional[_Union[MotionPhase, str]] = ..., start_frame: _Optional[int] = ..., end_frame: _Optional[int] = ..., description: _Optional[str] = ...) -> None: ...

class TextLayer(_message.Message):
    __slots__ = ("id", "sample_text", "start_frame", "end_frame", "role", "text_prop_key", "motion")
    ID_FIELD_NUMBER: _ClassVar[int]
    SAMPLE_TEXT_FIELD_NUMBER: _ClassVar[int]
    START_FRAME_FIELD_NUMBER: _ClassVar[int]
    END_FRAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    TEXT_PROP_KEY_FIELD_NUMBER: _ClassVar[int]
    MOTION_FIELD_NUMBER: _ClassVar[int]
    id: str
    sample_text: str
    start_frame: int
    end_frame: int
    role: TextRole
    text_prop_key: str
    motion: _containers.RepeatedCompositeFieldContainer[MotionSegment]
    def __init__(self, id: _Optional[str] = ..., sample_text: _Optional[str] = ..., start_frame: _Optional[int] = ..., end_frame: _Optional[int] = ..., role: _Optional[_Union[TextRole, str]] = ..., text_prop_key: _Optional[str] = ..., motion: _Optional[_Iterable[_Union[MotionSegment, _Mapping]]] = ...) -> None: ...

class ScalarValue(_message.Message):
    __slots__ = ("string_value", "number_value", "bool_value")
    STRING_VALUE_FIELD_NUMBER: _ClassVar[int]
    NUMBER_VALUE_FIELD_NUMBER: _ClassVar[int]
    BOOL_VALUE_FIELD_NUMBER: _ClassVar[int]
    string_value: str
    number_value: float
    bool_value: bool
    def __init__(self, string_value: _Optional[str] = ..., number_value: _Optional[float] = ..., bool_value: _Optional[bool] = ...) -> None: ...

class EditableProp(_message.Message):
    __slots__ = ("prop_key", "layer_id", "display_name", "default_value", "minimum", "maximum", "min_length", "max_length", "allowed_values", "unit")
    PROP_KEY_FIELD_NUMBER: _ClassVar[int]
    LAYER_ID_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    DEFAULT_VALUE_FIELD_NUMBER: _ClassVar[int]
    MINIMUM_FIELD_NUMBER: _ClassVar[int]
    MAXIMUM_FIELD_NUMBER: _ClassVar[int]
    MIN_LENGTH_FIELD_NUMBER: _ClassVar[int]
    MAX_LENGTH_FIELD_NUMBER: _ClassVar[int]
    ALLOWED_VALUES_FIELD_NUMBER: _ClassVar[int]
    UNIT_FIELD_NUMBER: _ClassVar[int]
    prop_key: str
    layer_id: str
    display_name: str
    default_value: ScalarValue
    minimum: float
    maximum: float
    min_length: int
    max_length: int
    allowed_values: _containers.RepeatedCompositeFieldContainer[ScalarValue]
    unit: ValueUnit
    def __init__(self, prop_key: _Optional[str] = ..., layer_id: _Optional[str] = ..., display_name: _Optional[str] = ..., default_value: _Optional[_Union[ScalarValue, _Mapping]] = ..., minimum: _Optional[float] = ..., maximum: _Optional[float] = ..., min_length: _Optional[int] = ..., max_length: _Optional[int] = ..., allowed_values: _Optional[_Iterable[_Union[ScalarValue, _Mapping]]] = ..., unit: _Optional[_Union[ValueUnit, str]] = ...) -> None: ...

class RemotionTemplateVersion(_message.Message):
    __slots__ = ("version_id", "name", "description", "composition", "text_layers", "editable_props", "display_category", "export_tsx_ref", "export_tsx_sha256")
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    TEXT_LAYERS_FIELD_NUMBER: _ClassVar[int]
    EDITABLE_PROPS_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_CATEGORY_FIELD_NUMBER: _ClassVar[int]
    EXPORT_TSX_REF_FIELD_NUMBER: _ClassVar[int]
    EXPORT_TSX_SHA256_FIELD_NUMBER: _ClassVar[int]
    version_id: str
    name: str
    description: str
    composition: Composition
    text_layers: _containers.RepeatedCompositeFieldContainer[TextLayer]
    editable_props: _containers.RepeatedCompositeFieldContainer[EditableProp]
    display_category: DisplayCategory
    export_tsx_ref: str
    export_tsx_sha256: str
    def __init__(self, version_id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., composition: _Optional[_Union[Composition, _Mapping]] = ..., text_layers: _Optional[_Iterable[_Union[TextLayer, _Mapping]]] = ..., editable_props: _Optional[_Iterable[_Union[EditableProp, _Mapping]]] = ..., display_category: _Optional[_Union[DisplayCategory, str]] = ..., export_tsx_ref: _Optional[str] = ..., export_tsx_sha256: _Optional[str] = ...) -> None: ...

class MediaFile(_message.Message):
    __slots__ = ("url", "sha256", "mime_type", "codec", "pixel_format")
    URL_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    MIME_TYPE_FIELD_NUMBER: _ClassVar[int]
    CODEC_FIELD_NUMBER: _ClassVar[int]
    PIXEL_FORMAT_FIELD_NUMBER: _ClassVar[int]
    url: str
    sha256: str
    mime_type: str
    codec: str
    pixel_format: str
    def __init__(self, url: _Optional[str] = ..., sha256: _Optional[str] = ..., mime_type: _Optional[str] = ..., codec: _Optional[str] = ..., pixel_format: _Optional[str] = ...) -> None: ...

class ColorAndMask(_message.Message):
    __slots__ = ("color_video", "mask_video")
    COLOR_VIDEO_FIELD_NUMBER: _ClassVar[int]
    MASK_VIDEO_FIELD_NUMBER: _ClassVar[int]
    color_video: MediaFile
    mask_video: MediaFile
    def __init__(self, color_video: _Optional[_Union[MediaFile, _Mapping]] = ..., mask_video: _Optional[_Union[MediaFile, _Mapping]] = ...) -> None: ...

class OverlayMedia(_message.Message):
    __slots__ = ("composition", "alpha_video", "color_and_mask")
    COMPOSITION_FIELD_NUMBER: _ClassVar[int]
    ALPHA_VIDEO_FIELD_NUMBER: _ClassVar[int]
    COLOR_AND_MASK_FIELD_NUMBER: _ClassVar[int]
    composition: Composition
    alpha_video: MediaFile
    color_and_mask: ColorAndMask
    def __init__(self, composition: _Optional[_Union[Composition, _Mapping]] = ..., alpha_video: _Optional[_Union[MediaFile, _Mapping]] = ..., color_and_mask: _Optional[_Union[ColorAndMask, _Mapping]] = ...) -> None: ...

class FixedTextLayer(_message.Message):
    __slots__ = ("id", "text", "start_frame", "end_frame", "role")
    ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    START_FRAME_FIELD_NUMBER: _ClassVar[int]
    END_FRAME_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    id: str
    text: str
    start_frame: int
    end_frame: int
    role: TextRole
    def __init__(self, id: _Optional[str] = ..., text: _Optional[str] = ..., start_frame: _Optional[int] = ..., end_frame: _Optional[int] = ..., role: _Optional[_Union[TextRole, str]] = ...) -> None: ...

class PublishedRemotionAsset(_message.Message):
    __slots__ = ("asset_id", "version_id", "name", "description", "display_category", "overlay", "text_layers", "cover_url", "preview_url")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_CATEGORY_FIELD_NUMBER: _ClassVar[int]
    OVERLAY_FIELD_NUMBER: _ClassVar[int]
    TEXT_LAYERS_FIELD_NUMBER: _ClassVar[int]
    COVER_URL_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_URL_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    version_id: str
    name: str
    description: str
    display_category: DisplayCategory
    overlay: OverlayMedia
    text_layers: _containers.RepeatedCompositeFieldContainer[FixedTextLayer]
    cover_url: str
    preview_url: str
    def __init__(self, asset_id: _Optional[str] = ..., version_id: _Optional[str] = ..., name: _Optional[str] = ..., description: _Optional[str] = ..., display_category: _Optional[_Union[DisplayCategory, str]] = ..., overlay: _Optional[_Union[OverlayMedia, _Mapping]] = ..., text_layers: _Optional[_Iterable[_Union[FixedTextLayer, _Mapping]]] = ..., cover_url: _Optional[str] = ..., preview_url: _Optional[str] = ...) -> None: ...

class RemotionAssetRef(_message.Message):
    __slots__ = ("asset_id", "version_id")
    ASSET_ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_ID_FIELD_NUMBER: _ClassVar[int]
    asset_id: str
    version_id: str
    def __init__(self, asset_id: _Optional[str] = ..., version_id: _Optional[str] = ...) -> None: ...
