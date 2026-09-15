"""合成时间线的确定性、音视频时序、效果和边界测试；执行 uv run --locked pytest -v。"""

from copy import deepcopy

import pytest

from server.template.schema import effect_catalog
from server.video_composition.timeline import build_timeline


def choose(case, key, catalog_id):
    """在独立快照中选择真实目录效果，不修改生产模板或目录。"""
    case["template"]["editor"][key] = catalog_id
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
    assert clips[0]["Effects"] == [{"Type": "Volume", "Gain": 0}]
    assert clips[0]["MediaURL"] == composition_case["request"]["videoUrl"]
    assert clips[0]["Width"] == 1080 and clips[0]["Height"] == 1920
    assert clips[0]["AdaptMode"] == "Cover"
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["Out"] == 8
    subtitles, title, bubbles = [track["SubtitleTrackClips"] for track in timeline["SubtitleTracks"]]
    assert [s["Content"] for s in subtitles] == ["甲乙丙丁。", "戊己庚辛。"]
    assert [(s["TimelineIn"], s["TimelineOut"]) for s in subtitles] == [(1, 3), (4, 6)]
    assert title[0]["Content"] == composition_case["request"]["title"]
    assert (title[0]["TimelineIn"], title[0]["TimelineOut"]) == (1, 3)
    assert bubbles[0]["Content"] == "甲乙"
    assert "BubbleStyleId" not in bubbles[0]
    assert "让每一帧" not in str(timeline) and "选择花字" not in str(timeline)


@pytest.mark.parametrize("title", [None, "", " \n\t"])
def test_blank_title_is_omitted(composition_case, title):
    """空标题不会从模板预览文字回填；字幕和关键词保留。"""
    composition_case["request"]["title"] = title
    timeline, _ = build_timeline(**composition_case)
    assert len(timeline["SubtitleTracks"]) == 2


@pytest.mark.parametrize("kind", ["video", "image"])
def test_material_coverage_preserves_avatar_source_and_url(composition_case, kind):
    """素材从源零点覆盖固定区间，前后恢复对应时刻数字人，图片以时长显示。"""
    url = "https://media.example.test/clip?clip_ms=2000&concat=a%2Fb"
    composition_case["matches"][0].update(matched_candidate_url=url, matched_candidate_type=kind)
    timeline, _ = build_timeline(**composition_case)
    before, material, after = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert (before["In"], before["Out"], after["In"], after["Out"]) == (0, 1, 3, 8)
    assert (material["TimelineIn"], material["TimelineOut"], material["MediaURL"]) == (1, 3, url)
    if kind == "video":
        assert (material["In"], material["Out"]) == (0, 2)
        assert material["Effects"] == [{"Type": "Volume", "Gain": 0}]
    else:
        assert material["Type"] == "Image" and material["Duration"] == 2
        assert "Out" not in material


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
    composition_case["template"]["editor"].update(titleInDuration=3, titleOutDuration=1, titleX=100)
    timeline, _ = build_timeline(**composition_case)
    title = timeline["SubtitleTracks"][1]["SubtitleTrackClips"][0]
    assert (title["AaiMotionIn"], title["AaiMotionOut"]) == (1.5, 0.5)
    assert title["X"] == 0.9999


def test_transitions_only_at_visual_boundaries_and_share_budget(composition_case):
    """连续未命中无转场；一毫秒数字人空隙保留，短片段两侧总预算不超区间。"""
    effect = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    choose(composition_case, "transition", effect.id)
    plain, _ = build_timeline(**composition_case)
    assert "DLTransition" not in str(plain)
    composition_case["segments"][1]["start_time"] = 3.001
    composition_case["matches"][1]["start_time"] = 3.001
    for i, item in enumerate(composition_case["matches"]):
        item.update(matched_candidate_url=f"https://media.example.test/{i}.mp4", matched_candidate_type="video")
    timeline, warnings = build_timeline(**composition_case)
    clips = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in clips] == [(0, 1), (1, 3), (3, 3.001), (3.001, 6), (6, 8)]
    assert warnings and "DLTransition" in str(timeline) and "'Type': 'Transition'" not in str(timeline)
    for i, clip in enumerate(clips):
        before = sum(e["Duration"] for e in clips[i - 1]["Effects"] if e["Type"] == "DLTransition") if i else 0
        after = sum(e["Duration"] for e in clip["Effects"] if e["Type"] == "DLTransition")
        assert before + after <= clip["TimelineOut"] - clip["TimelineIn"] + 1e-9


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
