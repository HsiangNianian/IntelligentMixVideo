# 与前端共享真实时间输入，验证顺序拼接、固定时间、应用说明及模板不变性。

import json
from pathlib import Path

import pytest

from server.template.schema import TemplateData, effect_catalog
from server.template.timing import TimelineContext, resolve_template
from .conftest import template_track


@pytest.mark.parametrize("case", json.loads(Path(__file__).with_name("template_sequence_cases.json").read_text()), ids=lambda case: case["name"])
def test_shared_resolution(case):
    """前后端共同约束时间计算结果与字幕全长规则。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    tracks = [template_track("title"), template_track("subtitle")]
    tracks.extend({**template_track("transition", transition=asset.id), "id": f"transition-{index}",
                   "transition_boundary": item["boundary"], "duration": item["duration"]}
                  for index, item in enumerate(case["transitions"]))
    template = TemplateData(name="时间计算", tracks=tracks)
    original = template.model_dump()
    result = resolve_template(template, TimelineContext.model_validate(case["context"]))
    applied = [result.tracks[f"transition-{index}"] for index in range(len(case["transitions"]))]
    assert result.duration == case["duration"]
    assert [[clip["start"], clip["end"]] for clip in result.clips] == case["clips"]
    assert [[track.start, track.end] for track in applied] == case["ranges"]
    assert [track.notice for track in applied] == case["notices"]
    assert (result.tracks["subtitle"].start, result.tracks["subtitle"].end) == (0, case.get("subtitle_end", case["duration"]))
    assert template.model_dump() == original
