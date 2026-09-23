"""合成时间线的确定性、音视频时序、效果与对象规则测试；执行 uv run --locked pytest -v。"""

from copy import deepcopy

import pytest
from server.template.schema import EffectTemplateEditor, TemplateSave, effect_catalog
from server.video_composition.timeline import build_timeline

from .conftest import template_track


def choose(case, key, catalog_id):
    """在独立快照中选择真实目录效果，不修改生产模板或目录。"""
    target = next((role for role in ("title", "subtitle", "bubble") if key.startswith(role)), key)
    track = next((track for track in case["template"]["tracks"] if track["target"] == target), None)
    if track is None:
        track = template_track(target)
        case["template"]["tracks"].append(track)
    track["editor"][key] = catalog_id
    case["template"]["effect_ids"].append(catalog_id)
    case["template"]["effects"].append(effect_catalog()[catalog_id].model_dump(mode="json"))


def test_unmatched_timeline_uses_business_text_and_full_tts(composition_case):
    """全未命中仍正常显示字幕和关键词，数字人保留全长及尾部静音，不含预览文案。"""
    original = deepcopy(composition_case)
    timeline, warnings = build_timeline(**composition_case)
    assert (timeline, warnings) == build_timeline(**composition_case)
    assert composition_case == original
    clips = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert len(clips) == 1
    assert (clips[0]["In"], clips[0]["Out"], clips[0]["TimelineIn"], clips[0]["TimelineOut"]) == (0, 8, 0, 8)
    assert clips[0]["Effects"] == [
        {"Type": "Background", "SubType": "Blur", "Radius": 0.1}, {"Type": "Volume", "Gain": 0},
    ]
    assert clips[0]["MediaURL"] == composition_case["request"]["videoUrl"]
    assert clips[0]["Width"] == 1080 and clips[0]["Height"] == 1920
    assert clips[0]["AdaptMode"] == "Contain"
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["Out"] == 8
    subtitles, title, bubbles = [track["SubtitleTrackClips"] for track in timeline["SubtitleTracks"]]
    assert [s["Content"] for s in subtitles] == ["甲乙丙丁", "戊己庚辛"]
    assert [(s["TimelineIn"], s["TimelineOut"]) for s in subtitles] == [(1, 3), (4, 6)]
    assert title[0]["Content"] == composition_case["request"]["title"]
    assert (title[0]["TimelineIn"], title[0]["TimelineOut"]) == (1, 3)
    assert bubbles[0]["Content"] == "甲乙"
    assert "BubbleStyleId" not in bubbles[0]
    assert "让每一帧" not in str(timeline) and "选择花字" not in str(timeline)


def test_subtitle_parts_keep_adjacent_times_without_changing_other_text(composition_case):
    """字幕短句逐段显示且首尾衔接，标题、关键词与原始匹配切片保持不变。"""
    composition_case["request"]["text"] = "甲乙丙丁。戊己庚辛?"
    composition_case["segments"][1]["text"] = "戊己庚辛?"
    composition_case["matches"][1]["text"] = "戊己庚辛?"
    original = deepcopy(composition_case)
    composition_case["segments"][0]["subtitle_parts"] = [
        {"text": "甲乙？，", "start_time": 1, "end_time": 2},
        {"text": "丙丁?。", "start_time": 2, "end_time": 3},
    ]
    timeline, _ = build_timeline(**composition_case)
    subtitles, title, bubbles = [track["SubtitleTrackClips"] for track in timeline["SubtitleTracks"]]
    assert [(clip["Content"], clip["TimelineIn"], clip["TimelineOut"]) for clip in subtitles] == [
        ("甲乙？", 1, 2), ("丙丁?", 2, 3), ("戊己庚辛?", 4, 6),
    ]
    assert title[0]["Content"] == original["request"]["title"]
    assert bubbles[0]["Content"] == original["segments"][0]["keyword"]
    assert composition_case["matches"] == original["matches"]


@pytest.mark.parametrize("title", [None, "", " \n\t"])
def test_blank_title_is_omitted(composition_case, title):
    """空标题不会从模板预览文字回填；字幕和关键词保留。"""
    composition_case["request"]["title"] = title
    timeline, _ = build_timeline(**composition_case)
    assert len(timeline["SubtitleTracks"]) == 2


@pytest.mark.parametrize("kind", ["video", "image"])
def test_material_coverage_preserves_avatar_source_and_url(composition_case, kind):
    """素材从源零点覆盖固定区间；视频和图片均保持比例并模糊填充留白。"""
    url = "https://media.example.test/clip?clip_ms=2000&concat=a%2Fb"
    composition_case["matches"][0].update(matched_candidate_url=url, matched_candidate_type=kind)
    timeline, _ = build_timeline(**composition_case)
    before, material, after = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert (before["In"], before["Out"], after["In"], after["Out"]) == (0, 1, 3, 8)
    assert (material["TimelineIn"], material["TimelineOut"], material["MediaURL"]) == (1, 3, url)
    assert all((clip["Width"], clip["Height"], clip["AdaptMode"]) == (1080, 1920, "Contain")
               for clip in (before, material, after))
    assert all(clip["Effects"][0] == {"Type": "Background", "SubType": "Blur", "Radius": 0.1}
               for clip in (before, material, after))
    if kind == "video":
        assert (material["In"], material["Out"]) == (0, 2)
        assert material["Effects"][1:] == [{"Type": "Volume", "Gain": 0}]
    else:
        assert material["Type"] == "Image" and material["Duration"] == 2
        assert "Out" not in material
        assert len(material["Effects"]) == 1


@pytest.mark.parametrize("enabled,volume", [(False, 0.1), (True, 0), (True, 0.1), (True, 1)])
def test_music_switch_gain_loop_and_cutoff(composition_case, enabled, volume):
    """只有音乐允许循环，关闭时无 URL 校验或轨道，开启时在 TTS 总长截止。"""
    composition_case["request"]["packRules"] = {"backgroundMusic": {
        "audioSwitch": enabled, "audioUrl": "https://media.example.test/bgm.mp3" if enabled else "not-a-url", "volume": volume,
    }}
    timeline, _ = build_timeline(**composition_case)
    assert len(timeline["AudioTracks"]) == (2 if enabled else 1)
    if enabled:
        music = timeline["AudioTracks"][1]["AudioTrackClips"][0]
        assert music["LoopMode"] is True
        assert music["TimelineOut"] == 8 and "Out" not in music
        assert music["Effects"] == [{"Type": "Volume", "Gain": volume}]
    assert "LoopMode" not in timeline["VideoTracks"][0]["VideoTrackClips"][0]


def test_selected_global_effects_apply_once(composition_case):
    """花字、气泡与全局效果来自选中快照；未选条目不启用，全程滤镜不叠加两次。"""
    for key, category in (("filter", "filter"), ("vfx", "vfx/normal"), ("subtitleFlower", "flower"), ("bubble", "bubble")):
        item = next(asset for asset in effect_catalog().values() if asset.category == category)
        choose(composition_case, key, item.id)
    extra = next(asset for asset in effect_catalog().values() if asset.category == "out")
    composition_case["template"]["effects"].append(extra.model_dump(mode="json"))
    original = deepcopy(composition_case)
    timeline, _ = build_timeline(**composition_case)
    assert [track["EffectTrackItems"][0]["Type"] for track in timeline["EffectTracks"]] == ["Filter", "VFX"]
    assert all(track["EffectTrackItems"][0]["TimelineOut"] == 8 for track in timeline["EffectTracks"])
    subtitles = timeline["SubtitleTracks"][0]["SubtitleTrackClips"]
    assert all("EffectColorStyle" in clip for clip in subtitles)
    subtitles[0]["EffectColorStyle"] = "仅修改第一个字幕"
    assert subtitles[1]["EffectColorStyle"] != subtitles[0]["EffectColorStyle"]
    assert "BubbleStyleId" in timeline["SubtitleTracks"][2]["SubtitleTrackClips"][0]
    assert "AaiMotionOutEffect" not in str(timeline)
    assert composition_case == original


def test_short_text_motions_share_duration_and_position_bounds(composition_case):
    """短字幕的入出同比缩短，100% 坐标映射 0.9999，显式换行保留。"""
    choose(composition_case, "titleOut", "out/fade_out")
    composition_case["template"]["tracks"][1]["editor"].update(titleInDuration=3, titleOutDuration=1, titleX=100)
    timeline, _ = build_timeline(**composition_case)
    title = timeline["SubtitleTracks"][1]["SubtitleTrackClips"][0]
    assert (title["AaiMotionIn"], title["AaiMotionOut"]) == (1.5, 0.5)
    assert title["X"] == 0.9999


def test_transition_ignores_template_boundary_and_skips_short_gaps(composition_case):
    """复现 50% 位置切出 0.32 秒短片段：保留实际素材边界，短间隙跳过转场而非失败。"""
    effect = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    choose(composition_case, "transition", effect.id)
    track = composition_case["template"]["tracks"][-1]
    track.update(start_mode="percent", start=50, duration=1)
    composition_case["duration_ms"] = 30398
    for index, (start, end) in enumerate(((13.4, 15.72), (15.88, 19.16))):
        composition_case["segments"][index].update(start_time=start, end_time=end)
        composition_case["matches"][index].update(start_time=start, end_time=end,
            matched_candidate_url=f"https://media.example.test/{index}.mp4", matched_candidate_type="video")
    timeline, warnings = build_timeline(**composition_case)
    clips = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in clips] == [
        (0, 13.4), (13.4, 15.72), (15.72, 15.88), (15.88, 19.16), (19.16, 30.398),
    ]
    assert [clip["Effects"][-1]["Type"] for clip in clips] == ["DLTransition", "Volume", "Volume", "DLTransition", "Volume"]
    assert len([notice for notice in warnings if "已跳过转场" in notice]) == 2


@pytest.mark.parametrize("change", [
    "empty", "overlap", "out-of-range", "negative-time", "duplicate-id", "missing-effect", "missing-effect-without-title", "wrong-category", "bad-parameters",
    "unknown-type", "nan", "wrong-count", "wrong-id", "wrong-text", "wrong-time",
])
def test_invalid_snapshot_fails_before_ims(composition_case, change):
    """损坏的时间、匹配引用或效果不得静默省略或进入云端渲染。"""
    if change == "empty":
        composition_case["segments"] = []
    elif change == "overlap":
        composition_case["segments"][1]["start_time"] = 2
    elif change == "out-of-range":
        composition_case["duration_ms"] = 5000
    elif change == "negative-time":
        composition_case["segments"][0]["start_time"] = -0.1
    elif change == "duplicate-id":
        composition_case["segments"][1]["segment_id"] = 1
    elif change in ("missing-effect", "missing-effect-without-title"):
        composition_case["template"]["effects"] = []
        if change == "missing-effect-without-title":
            composition_case["request"]["title"] = ""
    elif change == "wrong-category":
        composition_case["template"]["effects"][0]["category"] = "out"
    elif change == "bad-parameters":
        composition_case["template"]["effects"][0]["parameters"] = {"bad": "value"}
    elif change == "unknown-type":
        composition_case["matches"][0].update(matched_candidate_url="https://example.test/a", matched_candidate_type="audio")
    elif change == "nan":
        composition_case["matches"][0]["start_time"] = float("nan")
    elif change == "wrong-count":
        composition_case["matches"].pop()
    elif change == "wrong-id":
        composition_case["matches"][0]["segment_id"] = 2
    elif change == "wrong-text":
        composition_case["matches"][0]["text"] = "错的任务"
    elif change == "wrong-time":
        composition_case["matches"][0]["end_time"] = 3.001
    with pytest.raises(ValueError):
        build_timeline(**composition_case)


def apply_tracks(case: dict, tracks: list[dict]) -> None:
    """通过真实 Schema 和随包目录建立对象快照，不调用外部合成服务。"""
    effect_ids = sorted({value for track in tracks for value in EffectTemplateEditor.model_validate(track["editor"]).selected_effects().values()})
    data = TemplateSave(name="时间规则模板", effect_ids=effect_ids, tracks=tracks)
    case["template"].update(data.model_dump(mode="json", by_alias=True, exclude={"template_id"}))
    case["template"]["effects"] = [effect_catalog()[key].model_dump(mode="json") for key in effect_ids]


def test_independent_effect_rules_reach_composition(composition_case):
    """重复画面特效分别应用百分比和秒数，越界截短且完整保留输入快照。"""
    asset = next(item for item in effect_catalog().values() if item.category == "vfx/normal")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", vfx=asset.id).model_dump(by_alias=True)
    apply_tracks(composition_case, [
        {"id": "effect-a", "target": "vfx", "start_mode": "percent", "start": 25, "duration": 3, "editor": editor},
        {"id": "effect-b", "target": "vfx", "start_mode": "seconds", "start": 6, "duration": 5, "editor": editor},
        {"id": "effect-c", "target": "vfx", "start_mode": "seconds", "start": 20, "duration": None, "editor": editor},
    ])
    original = deepcopy(composition_case)
    timeline, warnings = build_timeline(**composition_case)
    effects = [row["EffectTrackItems"][0] for row in timeline["EffectTracks"]]
    assert [(item["TimelineIn"], item["TimelineOut"]) for item in effects] == [(2, 5), (6, 8)]
    assert all(item["SubType"] == asset.effect_id for item in effects)
    assert any("effect-b" in item and "缩短" in item for item in warnings)
    assert any("effect-c" in item and "没有可显示" in item for item in warnings)
    assert composition_case == original


def test_text_rules_keep_business_content_and_separate_styles(composition_case):
    """标题采用指定区间，字幕与文案时间取交集，同类字幕保留独立位置和文字来源。"""
    title = EffectTemplateEditor(title="示例标题", subtitle="", bubble_text="", title_in="in/fade_in")
    subtitle = EffectTemplateEditor(title="", subtitle="示例字幕", bubble_text="", subtitle_in="in/fade_in")
    apply_tracks(composition_case, [
        {"id": "title", "target": "title", "start_mode": "percent", "start": 50, "duration": 2, "editor": title.model_dump(by_alias=True)},
        {"id": "subtitle-a", "target": "subtitle", "start_mode": "seconds", "start": 2, "duration": 3, "editor": subtitle.model_dump(by_alias=True)},
        {"id": "subtitle-b", "target": "subtitle", "start_mode": "seconds", "start": 0, "duration": None, "editor": subtitle.model_copy(update={"subtitle_y": 50}).model_dump(by_alias=True)},
    ])
    timeline, _ = build_timeline(**composition_case)
    title_clips, first, second = [row["SubtitleTrackClips"] for row in timeline["SubtitleTracks"]]
    assert (title_clips[0]["TimelineIn"], title_clips[0]["TimelineOut"]) == (4, 6)
    assert title_clips[0]["Content"] == composition_case["request"]["title"]
    assert [(item["TimelineIn"], item["TimelineOut"]) for item in first] == [(2, 3), (4, 5)]
    assert [(item["TimelineIn"], item["TimelineOut"]) for item in second] == [(1, 3), (4, 6)]
    assert first[0]["Y"] == 0.82 and second[0]["Y"] == 0.5
    assert [item["Content"] for item in first] == ["甲乙丙丁", "戊己庚辛"]
    assert "示例" not in str(timeline)


@pytest.mark.parametrize("kind", ["video", "image"])
def test_transition_uses_actual_boundaries_and_ignores_template_timing(composition_case, kind):
    """秒数、百分比和时长不影响转场；按真实边界缩短，保留素材源时间、音频和输入快照。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", transition=asset.id)
    apply_tracks(composition_case, [{"id": "transition", "target": "transition", "start_mode": "percent", "start": 25, "duration": 1, "editor": editor.model_dump(by_alias=True)}])
    for index, match in enumerate(composition_case["matches"]):
        match.update(matched_candidate_url=f"https://media.example.test/{index}", matched_candidate_type=kind)
    original = deepcopy(composition_case)
    timeline, _ = build_timeline(**composition_case)
    assert composition_case == original
    clips = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in clips] == [(0, 1), (1, 3), (3, 4), (4, 6), (6, 8)]
    assert [clip["Effects"][-1] for clip in clips[:-1]] == [
        {"Type": "DLTransition", "SubType": asset.effect_id, "Duration": duration}
        for duration in (0.5, 0.5, 0.5, 1)
    ]
    assert clips[-1]["Effects"] == [
        {"Type": "Background", "SubType": "Blur", "Radius": 0.1}, {"Type": "Volume", "Gain": 0},
    ]
    assert (clips[2]["In"], clips[2]["Out"], clips[4]["In"], clips[4]["Out"]) == (3, 4, 6, 8)
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["TimelineOut"] == 8
    for start_mode, start, duration in (("seconds", 100, 0.1), ("percent", 99, 3)):
        composition_case["template"]["tracks"][0].update(start_mode=start_mode, start=start, duration=duration)
        composition_case["template"]["transition_duration_seconds"] = duration
        assert build_timeline(**composition_case)[0] == timeline


def test_transition_does_not_split_single_clip_and_still_validates_effect(composition_case):
    """无素材切换时不人为切开视频；忽略转场时间仍拒绝损坏的效果快照。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    choose(composition_case, "transition", asset.id)
    composition_case["template"]["tracks"][-1].update(start=100, duration=3)
    timeline, warnings = build_timeline(**composition_case)
    clip, = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert (clip["In"], clip["Out"]) == (0, 8)
    assert clip["Effects"] == [
        {"Type": "Background", "SubType": "Blur", "Radius": 0.1}, {"Type": "Volume", "Gain": 0},
    ] and warnings == []
    composition_case["template"]["effects"] = [item for item in composition_case["template"]["effects"] if item["id"] != asset.id]
    with pytest.raises(ValueError, match="模板效果引用"):
        build_timeline(**composition_case)


def test_skipped_object_still_validates_effect_snapshot(composition_case):
    """视频结尾以外的对象仍校验效果引用，损坏快照不能被跳过规则掩盖。"""
    editor = EffectTemplateEditor(title="标题", subtitle="", bubble_text="", title_in="in/fade_in")
    apply_tracks(composition_case, [{"id": "title", "target": "title", "start_mode": "seconds", "start": 100, "duration": 1, "editor": editor.model_dump(by_alias=True)}])
    composition_case["template"]["effects"] = []
    with pytest.raises(ValueError, match="模板效果引用"):
        build_timeline(**composition_case)
