import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EffectAsset(_message.Message):
    __slots__ = ("id", "category", "name", "effect_id", "parameters", "preview_url")
    class ParametersEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EFFECT_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_FIELD_NUMBER: _ClassVar[int]
    PREVIEW_URL_FIELD_NUMBER: _ClassVar[int]
    id: str
    category: str
    name: str
    effect_id: str
    parameters: _containers.ScalarMap[str, str]
    preview_url: str
    def __init__(self, id: _Optional[str] = ..., category: _Optional[str] = ..., name: _Optional[str] = ..., effect_id: _Optional[str] = ..., parameters: _Optional[_Mapping[str, str]] = ..., preview_url: _Optional[str] = ...) -> None: ...

class EffectTemplateEditor(_message.Message):
    __slots__ = ("title", "subtitle", "bubble_text", "title_size", "subtitle_size", "bubble_size", "title_x", "title_y", "subtitle_x", "subtitle_y", "bubble_x", "bubble_y", "title_flower", "subtitle_flower", "bubble", "filter", "vfx", "transition", "title_in", "title_out", "title_loop", "subtitle_in", "subtitle_out", "subtitle_loop", "bubble_in", "bubble_out", "bubble_loop", "title_in_duration", "title_out_duration", "subtitle_in_duration", "subtitle_out_duration", "bubble_in_duration", "bubble_out_duration")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_TEXT_FIELD_NUMBER: _ClassVar[int]
    TITLE_SIZE_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_SIZE_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_SIZE_FIELD_NUMBER: _ClassVar[int]
    TITLE_X_FIELD_NUMBER: _ClassVar[int]
    TITLE_Y_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_X_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_Y_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_X_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_Y_FIELD_NUMBER: _ClassVar[int]
    TITLE_FLOWER_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_FLOWER_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_FIELD_NUMBER: _ClassVar[int]
    FILTER_FIELD_NUMBER: _ClassVar[int]
    VFX_FIELD_NUMBER: _ClassVar[int]
    TRANSITION_FIELD_NUMBER: _ClassVar[int]
    TITLE_IN_FIELD_NUMBER: _ClassVar[int]
    TITLE_OUT_FIELD_NUMBER: _ClassVar[int]
    TITLE_LOOP_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_IN_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_OUT_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_LOOP_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_IN_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_OUT_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_LOOP_FIELD_NUMBER: _ClassVar[int]
    TITLE_IN_DURATION_FIELD_NUMBER: _ClassVar[int]
    TITLE_OUT_DURATION_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_IN_DURATION_FIELD_NUMBER: _ClassVar[int]
    SUBTITLE_OUT_DURATION_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_IN_DURATION_FIELD_NUMBER: _ClassVar[int]
    BUBBLE_OUT_DURATION_FIELD_NUMBER: _ClassVar[int]
    title: str
    subtitle: str
    bubble_text: str
    title_size: int
    subtitle_size: int
    bubble_size: int
    title_x: float
    title_y: float
    subtitle_x: float
    subtitle_y: float
    bubble_x: float
    bubble_y: float
    title_flower: str
    subtitle_flower: str
    bubble: str
    filter: str
    vfx: str
    transition: str
    title_in: str
    title_out: str
    title_loop: str
    subtitle_in: str
    subtitle_out: str
    subtitle_loop: str
    bubble_in: str
    bubble_out: str
    bubble_loop: str
    title_in_duration: float
    title_out_duration: float
    subtitle_in_duration: float
    subtitle_out_duration: float
    bubble_in_duration: float
    bubble_out_duration: float
    def __init__(self, title: _Optional[str] = ..., subtitle: _Optional[str] = ..., bubble_text: _Optional[str] = ..., title_size: _Optional[int] = ..., subtitle_size: _Optional[int] = ..., bubble_size: _Optional[int] = ..., title_x: _Optional[float] = ..., title_y: _Optional[float] = ..., subtitle_x: _Optional[float] = ..., subtitle_y: _Optional[float] = ..., bubble_x: _Optional[float] = ..., bubble_y: _Optional[float] = ..., title_flower: _Optional[str] = ..., subtitle_flower: _Optional[str] = ..., bubble: _Optional[str] = ..., filter: _Optional[str] = ..., vfx: _Optional[str] = ..., transition: _Optional[str] = ..., title_in: _Optional[str] = ..., title_out: _Optional[str] = ..., title_loop: _Optional[str] = ..., subtitle_in: _Optional[str] = ..., subtitle_out: _Optional[str] = ..., subtitle_loop: _Optional[str] = ..., bubble_in: _Optional[str] = ..., bubble_out: _Optional[str] = ..., bubble_loop: _Optional[str] = ..., title_in_duration: _Optional[float] = ..., title_out_duration: _Optional[float] = ..., subtitle_in_duration: _Optional[float] = ..., subtitle_out_duration: _Optional[float] = ..., bubble_in_duration: _Optional[float] = ..., bubble_out_duration: _Optional[float] = ...) -> None: ...

class EffectTrack(_message.Message):
    __slots__ = ("id", "target", "start_mode", "start", "duration", "editor")
    ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    START_MODE_FIELD_NUMBER: _ClassVar[int]
    START_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    EDITOR_FIELD_NUMBER: _ClassVar[int]
    id: str
    target: str
    start_mode: str
    start: float
    duration: float
    editor: EffectTemplateEditor
    def __init__(self, id: _Optional[str] = ..., target: _Optional[str] = ..., start_mode: _Optional[str] = ..., start: _Optional[float] = ..., duration: _Optional[float] = ..., editor: _Optional[_Union[EffectTemplateEditor, _Mapping]] = ...) -> None: ...

class TrackList(_message.Message):
    __slots__ = ("tracks",)
    TRACKS_FIELD_NUMBER: _ClassVar[int]
    tracks: _containers.RepeatedCompositeFieldContainer[EffectTrack]
    def __init__(self, tracks: _Optional[_Iterable[_Union[EffectTrack, _Mapping]]] = ...) -> None: ...

class SaveTemplateRequest(_message.Message):
    __slots__ = ("name", "description", "effect_ids", "transition_duration_seconds", "tracks", "template_id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    EFFECT_IDS_FIELD_NUMBER: _ClassVar[int]
    TRANSITION_DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TRACKS_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    effect_ids: _containers.RepeatedScalarFieldContainer[str]
    transition_duration_seconds: float
    tracks: TrackList
    template_id: str
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., effect_ids: _Optional[_Iterable[str]] = ..., transition_duration_seconds: _Optional[float] = ..., tracks: _Optional[_Union[TrackList, _Mapping]] = ..., template_id: _Optional[str] = ...) -> None: ...

class TemplateRecord(_message.Message):
    __slots__ = ("name", "description", "effect_ids", "transition_duration_seconds", "tracks", "template_id", "effects", "created_at", "updated_at")
    NAME_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    EFFECT_IDS_FIELD_NUMBER: _ClassVar[int]
    TRANSITION_DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    TRACKS_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_ID_FIELD_NUMBER: _ClassVar[int]
    EFFECTS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    name: str
    description: str
    effect_ids: _containers.RepeatedScalarFieldContainer[str]
    transition_duration_seconds: float
    tracks: TrackList
    template_id: str
    effects: _containers.RepeatedCompositeFieldContainer[EffectAsset]
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, name: _Optional[str] = ..., description: _Optional[str] = ..., effect_ids: _Optional[_Iterable[str]] = ..., transition_duration_seconds: _Optional[float] = ..., tracks: _Optional[_Union[TrackList, _Mapping]] = ..., template_id: _Optional[str] = ..., effects: _Optional[_Iterable[_Union[EffectAsset, _Mapping]]] = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class ListTemplatesResponse(_message.Message):
    __slots__ = ("templates",)
    TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    templates: _containers.RepeatedCompositeFieldContainer[TemplateRecord]
    def __init__(self, templates: _Optional[_Iterable[_Union[TemplateRecord, _Mapping]]] = ...) -> None: ...

class GetTemplateResponse(_message.Message):
    __slots__ = ("template",)
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    template: TemplateRecord
    def __init__(self, template: _Optional[_Union[TemplateRecord, _Mapping]] = ...) -> None: ...

class SaveTemplateResponse(_message.Message):
    __slots__ = ("template",)
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    template: TemplateRecord
    def __init__(self, template: _Optional[_Union[TemplateRecord, _Mapping]] = ...) -> None: ...
