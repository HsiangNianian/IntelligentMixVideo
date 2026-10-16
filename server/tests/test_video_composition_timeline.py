"""合成时间线的确定性、音视频时序、效果与对象规则测试；执行 uv run --locked pytest -v。"""

from copy import deepcopy

import pytest
from server.template.schema import EffectTemplateEditor, TemplateSave, effect_catalog
from server.video_composition.timeline import build_timeline, format_subtitle_keyword

from .conftest import remotion_track, template_track


@pytest.mark.parametrize("title_on,subtitle_on", [(True, True), (True, False), (False, True), (False, False)])
def test_bilingual_timeline_uses_independent_styles_and_synchronized_times(composition_case, title_on, subtitle_on):
    """英文使用实际译文、独立样式和原片段区间；停用英文即使快照有译文也不输出。"""
    case = composition_case
    english = {}
    for role, enabled in (("title", title_on), ("subtitle", subtitle_on)):
        source = next(track for track in case["template"]["tracks"] if track["target"] == role)
        english[role] = deepcopy(source)
        english[role].update(id=f"{role}-en", language="en", enabled=enabled)
        english[role]["editor"].update({role: "Must not render this example", f"{role}Size": 20,
                                       f"{role}Y": 17 if role == "title" else 90, f"{role}Font": "Source Han Sans CN"})
        case["template"]["tracks"].append(english[role])
    case["segments"][0].update(text_en="Don't stop!", keyword_en="stop")
    case["segments"][1].update(text_en="Keep going.", keyword_en="")
    original = deepcopy(case)
    timeline, _ = build_timeline(**case, title_en="Business title", title_keyword_en="Business")
    clips = [clip for row in timeline["SubtitleTracks"] for clip in row["SubtitleTrackClips"]]
    translated = [clip for clip in clips if clip.get("AdaptMode") == "AutoScale"]
    assert [clip["Content"] for clip in translated] == (["Business title"] if title_on else []) + (["Don't stop!", "Keep going."] if subtitle_on else [])
    assert all((clip["TextWidth"], clip["FontSize"], clip["Font"]) == (0.9, 20, "Source Han Sans CN") for clip in translated)
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in translated] == ([(1, 3)] if title_on else []) + ([(1, 3), (4, 6)] if subtitle_on else [])
    assert any(clip["Content"] == "甲乙丙丁" for clip in clips)
    assert case == original


@pytest.mark.parametrize("role", ["title", "subtitle"])
def test_missing_english_never_falls_back_to_sample(composition_case, role):
    """启用英文但译文缺失时拒绝组装；关闭后既不需要译文也不借用模板示例。"""
    source = next(track for track in composition_case["template"]["tracks"] if track["target"] == role)
    english = {**deepcopy(source), "id": "english", "language": "en"}
    composition_case["template"]["tracks"].append(english)
    with pytest.raises(ValueError, match="缺少英文"):
        build_timeline(**composition_case)
    english["enabled"] = False
    timeline, _ = build_timeline(**composition_case)
    assert all(clip.get("AdaptMode") != "AutoScale" for row in timeline["SubtitleTracks"] for clip in row["SubtitleTrackClips"])


@pytest.mark.parametrize("title", [None, "", "   "])
def test_bilingual_empty_title_does_not_require_translation(composition_case, title):
    """未提供实际标题时，即使开启双语也不要求译文或输出模板示例。"""
    composition_case["request"]["title"] = title
    source = next(track for track in composition_case["template"]["tracks"] if track["target"] == "title")
    source["editor"]["title"] = "标题示例不应出现在成片"
    composition_case["template"]["tracks"].append({**deepcopy(source), "id": "title-en", "language": "en"})
    timeline, _ = build_timeline(**composition_case)
    clips = [clip for row in timeline["SubtitleTracks"] for clip in row["SubtitleTrackClips"]]
    assert all(clip.get("AdaptMode") != "AutoScale" for clip in clips)
    assert source["editor"]["title"] not in [clip["Content"] for clip in clips]


@pytest.mark.parametrize("mode", ["materials_voice", "materials_silent"])
def test_material_defaults_use_business_text_without_modifying_template(composition_case, mode):
    """缺少文字对象时补默认样式；无声只显示标题，有声字幕仍依切片时间。"""
    case = composition_case
    case["request"].update(videoUrl=None,
                           audioUrl=case["request"]["audioUrl"] if mode == "materials_voice" else None,
                           processRules={"videoDuration": 8},
                           materials=[{"fileUrl": "https://media.test/a.mp4", "type": "video"}])
    case["template"]["tracks"] = []
    case["material_durations"] = [10]
    original = deepcopy(case)
    timeline, _ = build_timeline(**case)
    title = timeline["SubtitleTracks"][0]["SubtitleTrackClips"][0]
    assert title["Content"] == case["request"]["title"]
    assert (title["FontSize"], title["X"], title["Y"]) == (40, 0.5, 0.08)
    assert (title["TimelineIn"], title["TimelineOut"]) == (0, 8)
    assert "AaiMotionInEffect" not in title
    if mode == "materials_voice":
        subtitles = timeline["SubtitleTracks"][1]["SubtitleTrackClips"]
        assert [item["Content"] for item in subtitles] == ["甲乙丙丁", "戊己庚辛"]
        assert [(item["TimelineIn"], item["TimelineOut"]) for item in subtitles] == [(1, 3), (4, 6)]
        assert (subtitles[0]["FontSize"], subtitles[0]["X"], subtitles[0]["Y"]) == (26, 0.5, 0.82)
    else:
        assert len(timeline["SubtitleTracks"]) == 1 and timeline["AudioTracks"] == []
    assert case == original


@pytest.mark.parametrize("duration", [3, 2.99])
def test_silent_respects_title_timing(composition_case, duration):
    """无声模式遵循标题时间，使用持续到结束时覆盖完整终点。"""
    case = composition_case
    case["request"].update(videoUrl=None, audioUrl=None, processRules={"videoDuration": duration},
                           materials=[{"fileUrl": "https://media.test/a.jpg", "type": "image"}])
    case["duration_ms"] = duration * 1000
    case["material_durations"] = [3]
    case["template"]["tracks"][0]["editor"]["subtitleIn"] = "in/fade_in"
    case["template"]["tracks"][1].update(start=0, duration=None)
    case["template"]["tracks"][1]["editor"].update(titleKeyword="业务", titleKeywordBold=True)
    timeline, _ = build_timeline(**case)
    title = timeline["SubtitleTracks"][0]["SubtitleTrackClips"][0]
    assert title["Content"] == case["request"]["title"]
    assert (title["TimelineIn"], title["TimelineOut"]) == (0, duration)
    case["template"]["tracks"][1].update(start=1, duration=1)
    title = build_timeline(**case)[0]["SubtitleTracks"][0]["SubtitleTrackClips"][0]
    assert (title["TimelineIn"], title["TimelineOut"]) == (1, 2)


def test_silent_without_business_text_skips_text_timing(composition_case):
    """无业务文字的短视频不应用标题或字幕动画，素材总长仍按请求保留。"""
    case = composition_case
    case["request"].update(videoUrl=None, audioUrl=None, title=None, processRules={"videoDuration": 0.01},
                           materials=[{"fileUrl": "https://media.test/a.jpg", "type": "image"}])
    case["duration_ms"] = 10
    case["material_durations"] = [1]
    case["template"]["tracks"] = [track for track in case["template"]["tracks"] if track["target"] in ("title", "subtitle")]
    case["template"]["tracks"][0]["editor"]["subtitleIn"] = "in/fade_in"
    timeline, warnings = build_timeline(**case)
    assert timeline["SubtitleTracks"] == []
    assert timeline["VideoTracks"][0]["VideoTrackClips"][0]["TimelineOut"] == 0.01
    assert warnings == []


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
    """全未命中保留业务字幕、标题及模板气泡文字，数字人保留全长及尾部静音。"""
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
    title, subtitles, bubbles = [track["SubtitleTrackClips"] for track in timeline["SubtitleTracks"]]
    assert [s["Content"] for s in subtitles] == ["甲乙丙丁", "戊己庚辛"]
    assert [(s["TimelineIn"], s["TimelineOut"]) for s in subtitles] == [(1, 3), (4, 6)]
    assert title[0]["Content"] == composition_case["request"]["title"]
    assert (title[0]["TimelineIn"], title[0]["TimelineOut"]) == (1, 3)
    assert len(bubbles) == 1
    assert bubbles[0]["Content"] == composition_case["template"]["tracks"][2]["editor"]["bubbleText"]
    assert (bubbles[0]["TimelineIn"], bubbles[0]["TimelineOut"]) == (0, 8)
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
    title, subtitles, bubbles = [track["SubtitleTrackClips"] for track in timeline["SubtitleTracks"]]
    assert [(clip["Content"], clip["TimelineIn"], clip["TimelineOut"]) for clip in subtitles] == [
        ("甲乙？", 1, 2), ("丙丁?", 2, 3), ("戊己庚辛?", 4, 6),
    ]
    assert title[0]["Content"] == original["request"]["title"]
    assert [clip["Content"] for clip in bubbles] == [original["template"]["tracks"][2]["editor"]["bubbleText"]]
    assert composition_case["matches"] == original["matches"]


@pytest.mark.parametrize("text,expected", [
    ("增长3.14%，70，80，90后？", "增长3.14%708090后？"),
    ("价格１２．３元，真的?", "价格１２．３元真的?"),
    (".你好.世界．结束。", "你好世界结束"),
])
def test_subtitle_preserves_only_decimal_points(composition_case, text, expected):
    """最终字幕保留数字内部的小数点和既有问号，继续去掉逗号及普通句点，不改变时间。"""
    composition_case["segments"][0].update(text=text, keyword="")
    composition_case["matches"][0].update(text=text)
    timeline, _ = build_timeline(**composition_case)
    clip = timeline["SubtitleTracks"][1]["SubtitleTrackClips"][0]
    assert (clip["Content"], clip["TimelineIn"], clip["TimelineOut"]) == (expected, 1, 3)


@pytest.mark.parametrize("text,keyword,expected", [
    ("70，80，90后滴滴我。", "70，80，90后", r"{\b1}708090后{\b0}滴滴我"),
    ("第1、2、3名获奖。", "第1、2、3名", r"{\b1}第123名{\b0}获奖"),
    ("增长3.14%，太好了？", "3.14%", r"增长{\b1}3.14%{\b0}太好了？"),
    ("增长３．１４%，太好了？", "３．１４%", r"增长{\b1}３．１４%{\b0}太好了？"),
    ("你好，。", "，。", "你好"),
])
def test_subtitle_cleans_display_keyword_without_changing_source(composition_case, text, keyword, expected):
    """含标点关键词与字幕同步清洗并正确高亮，原切片和召回词不变，空展示词不生成样式。"""
    composition_case["segments"][0].update(text=text, keyword=keyword)
    composition_case["matches"][0].update(text=text)
    composition_case["template"]["tracks"][0]["editor"]["subtitleKeywordBold"] = True
    original = deepcopy(composition_case)
    timeline, _ = build_timeline(**composition_case)
    clip = timeline["SubtitleTracks"][1]["SubtitleTrackClips"][0]
    assert (clip["Content"], clip["TimelineIn"], clip["TimelineOut"]) == (expected, 1, 3)
    assert composition_case == original


def test_subtitle_keyword_requires_original_match_in_part(composition_case):
    """去标点后相同的另一个子段不能抢走关键词，只有首个含原词的子段继承高亮。"""
    text = "708090后70，80，90后70，80，90后"
    composition_case["matches"][0].update(text=text)
    composition_case["segments"][0].update(text=text, keyword="70，80，90后", subtitle_parts=[
        {"text": "708090后", "start_time": 1, "end_time": 1.5},
        {"text": "70，80，90后", "start_time": 1.5, "end_time": 2},
        {"text": "70，80，90后", "start_time": 2, "end_time": 3},
    ])
    composition_case["template"]["tracks"][0]["editor"]["subtitleKeywordBold"] = True
    timeline, _ = build_timeline(**composition_case)
    clips = timeline["SubtitleTracks"][1]["SubtitleTrackClips"]
    assert [clip["Content"] for clip in clips] == ["708090后", r"{\b1}708090后{\b0}", "708090后", "戊己庚辛"]


def test_subtitle_keyword_styles_apply_only_to_matching_text(composition_case):
    """组合样式只标记字幕首个关键词，空关键词和标题维持原文，气泡显示模板文字。"""
    composition_case["template"]["tracks"][0]["editor"].update(
        subtitleKeywordBold=True, subtitleKeywordItalic=True,
        subtitleKeywordUnderline=True, subtitleKeywordStrikeout=True,
        subtitleKeywordColor="#12AB34",
        subtitleKeywordSize=64,
    )
    timeline, _ = build_timeline(**composition_case)
    title, subtitles, bubbles = [row["SubtitleTrackClips"] for row in timeline["SubtitleTracks"]]
    assert subtitles[0]["Content"] == r"{\1c&34AB12&\fs64\b1\i1\u1\s1}甲乙{\1c\fs\b0\i0\u0\s0}丙丁"
    assert subtitles[1]["Content"] == "戊己庚辛"
    assert [clip["Content"] for clip in bubbles] == [composition_case["template"]["tracks"][2]["editor"]["bubbleText"]]
    assert title[0]["Content"] == composition_case["request"]["title"]


def test_subtitle_keyword_styles_keep_first_occurrence_and_reject_missing_keyword():
    """重复词只处理首次出现的位置，启用样式后拒绝不属于字幕的关键词。"""
    config = EffectTemplateEditor(subtitle_keyword_bold=True)
    assert format_subtitle_keyword("甲乙甲乙", "甲乙", config) == r"{\b1}甲乙{\b0}甲乙"
    assert format_subtitle_keyword("甲乙", "", config) == "甲乙"
    assert format_subtitle_keyword("甲乙", "甲", EffectTemplateEditor()) == "甲乙"
    assert format_subtitle_keyword("甲乙甲乙", "甲乙", EffectTemplateEditor(subtitle_keyword_color="#12ab34")) == (
        r"{\1c&34AB12&}甲乙{\1c}甲乙"
    )
    with pytest.raises(ValueError, match="关键词不在字幕文字中"):
        format_subtitle_keyword("甲乙", "丙", config)


def test_subtitle_keyword_style_marks_first_matching_part(composition_case):
    """原切片分为多个短句时，局部样式只作用于首次包含关键词的短句。"""
    composition_case["segments"][0]["subtitle_parts"] = [
        {"text": "甲乙？", "start_time": 1, "end_time": 2},
        {"text": "甲乙丙丁。", "start_time": 2, "end_time": 3},
    ]
    composition_case["template"]["tracks"][0]["editor"]["subtitleKeywordBold"] = True
    timeline, _ = build_timeline(**composition_case)
    subtitles = timeline["SubtitleTracks"][1]["SubtitleTrackClips"]
    assert [clip["Content"] for clip in subtitles] == [r"{\b1}甲乙{\b0}？", "甲乙丙丁", "戊己庚辛"]


@pytest.mark.parametrize("manual_keyword", ["", "重点"])
@pytest.mark.parametrize("result,expected", [
    ({"title_keyword": "内容"}, r"重点{\1c&34AB12&\fs72\b1}内容{\1c\fs\b0} 重点内容"),
    ({"title_keyword": ""}, "重点内容 重点内容"),
    ({}, "重点内容 重点内容"),
])
def test_title_keyword_styles_only_use_segmentation(composition_case, manual_keyword, result, expected):
    """仅标记切片标题词首次出现的位置；缺失或空词不回退模板词或标题前两字。"""
    editor = composition_case["template"]["tracks"][1]["editor"]
    editor.update(titleKeyword=manual_keyword, titleKeywordBold=True, titleKeywordColor="#12AB34", titleKeywordSize=72)
    composition_case["request"]["title"] = "重点内容 重点内容"
    original = deepcopy(composition_case)
    timeline, _ = build_timeline(**composition_case, **result)
    assert timeline["SubtitleTracks"][0]["SubtitleTrackClips"][0]["Content"] == expected
    assert composition_case == original


@pytest.mark.parametrize("title", [None, "", " \n\t"])
def test_blank_title_is_omitted(composition_case, title):
    """空标题不会从模板预览文字回填；字幕和关键词保留。"""
    composition_case["request"]["title"] = title
    timeline, _ = build_timeline(**composition_case)
    assert len(timeline["SubtitleTracks"]) == 2


@pytest.mark.parametrize("present,styled,mode,start,duration,interval", [
    (False, False, "seconds", 0, None, None),
    (True, False, "seconds", 2, 3, (2, 5)),
    (True, True, "percent", 50, 2, (4, 6)),
    (True, False, "seconds", 2, None, (2, 8)),
])
def test_bubble_uses_template_text_and_object_timing(composition_case, present, styled, mode, start, duration, interval):
    """气泡只由对象存在与否决定，示例文字按自身时间显示一次，样式和动画可选。"""
    bubble = template_track("bubble", bubbleText="模板气泡，不是关键词", bubbleSize=48, bubbleX=30, bubbleY=40)
    bubble.update(start_mode=mode, start=start, duration=duration)
    composition_case["template"]["tracks"] = [bubble] if present else []
    if styled:
        asset = next(item for item in effect_catalog().values() if item.category == "bubble")
        choose(composition_case, "bubble", asset.id)
        bubble["editor"]["bubbleIn"] = "in/fade_in"
    original = deepcopy(composition_case)
    timeline, warnings = build_timeline(**composition_case)
    if present:
        row, = timeline["SubtitleTracks"]
        clip, = row["SubtitleTrackClips"]
        assert clip["Content"] == "模板气泡，不是关键词"
        assert (clip["TimelineIn"], clip["TimelineOut"]) == interval
        assert (clip["FontSize"], clip["X"], clip["Y"]) == (48, 0.3, 0.4)
        assert ("BubbleStyleId" in clip) == styled
        if styled:
            assert clip["BubbleStyleId"] == asset.effect_id
            assert clip["AaiMotionIn"] == 0.5
    else:
        assert timeline["SubtitleTracks"] == []
    assert warnings == []
    assert composition_case == original


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
    assert [track["EffectTrackItems"][0]["Type"] for track in timeline["EffectTracks"]] == ["VFX", "Filter"]
    assert all(track["EffectTrackItems"][0]["TimelineOut"] == 8 for track in timeline["EffectTracks"])
    subtitles = timeline["SubtitleTracks"][1]["SubtitleTrackClips"]
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
    title = timeline["SubtitleTracks"][0]["SubtitleTrackClips"][0]
    assert (title["AaiMotionIn"], title["AaiMotionOut"]) == (1.5, 0.5)
    assert title["X"] == 0.9999


def test_transition_reports_unavailable_short_boundary(composition_case):
    """指定交界处相邻片段不足转场最短时长时，保留时间并返回说明。"""
    effect = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    choose(composition_case, "transition", effect.id)
    track = composition_case["template"]["tracks"][-1]
    track.update(transition_boundary=2, duration=1)
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
    assert [clip["Effects"][-1]["Type"] for clip in clips] == ["Volume"] * 5
    assert warnings == ["对象 transition：相邻片段过短，未应用转场"]


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
    """多个滤镜分别应用百分比和秒数，越界截短且完整保留输入快照。"""
    asset = next(item for item in effect_catalog().values() if item.category == "filter")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", filter=asset.id).model_dump(by_alias=True)
    apply_tracks(composition_case, [
        {"id": "effect-a", "target": "filter", "start_mode": "percent", "start": 25, "duration": 3, "editor": editor},
        {"id": "effect-b", "target": "filter", "start_mode": "seconds", "start": 6, "duration": 5, "editor": editor},
        {"id": "effect-c", "target": "filter", "start_mode": "seconds", "start": 20, "duration": None, "editor": editor},
    ])
    original = deepcopy(composition_case)
    timeline, warnings = build_timeline(**composition_case)
    effects = [row["EffectTrackItems"][0] for row in timeline["EffectTracks"]]
    assert [(item["TimelineIn"], item["TimelineOut"]) for item in effects] == [(2, 5), (6, 8)]
    assert all(item["SubType"] == asset.effect_id for item in effects)
    assert any("effect-b" in item and "缩短" in item for item in warnings)
    assert any("effect-c" in item and "没有可显示" in item for item in warnings)
    assert composition_case == original


def test_remotion_tracks_do_not_enter_the_ims_timeline(composition_case):
    """Remotion 片段由叠加视频服务渲染：快照里出现它们时，IMS 时间线与不含它们时完全一致，也不会报错。"""
    expected = build_timeline(**deepcopy(composition_case))
    composition_case["template"]["tracks"] += [remotion_track("clip-a", start=2), remotion_track("clip-b", duration=1.5)]
    assert build_timeline(**composition_case) == expected


@pytest.mark.parametrize(("role", "layer"), [("title", 0), ("subtitle", 1)])
def test_remotion_text_effect_replaces_the_ims_text(composition_case, role, layer):
    """使用 Remotion 字效的标题或字幕由叠加视频显示：IMS 时间线去掉这段文字，其他文字对象保持不变。"""
    expected, _ = build_timeline(**deepcopy(composition_case))
    composition_case["template"]["tracks"].append(remotion_track("clip", text_target=role))
    timeline, _ = build_timeline(**composition_case)
    assert timeline["SubtitleTracks"] == [row for index, row in enumerate(expected["SubtitleTracks"]) if index != layer]
    assert {key: value for key, value in timeline.items() if key != "SubtitleTracks"} == \
        {key: value for key, value in expected.items() if key != "SubtitleTracks"}


def test_text_rules_keep_business_content_and_separate_styles(composition_case):
    """标题采用指定区间，唯一字幕使用完整文案时间并保留样式。"""
    title = EffectTemplateEditor(title="示例标题", subtitle="", bubble_text="", title_in="in/fade_in")
    subtitle = EffectTemplateEditor(title="", subtitle="示例字幕", bubble_text="", subtitle_in="in/fade_in")
    apply_tracks(composition_case, [
        {"id": "title", "target": "title", "start_mode": "percent", "start": 50, "duration": 2, "editor": title.model_dump(by_alias=True)},
        {"id": "subtitle", "target": "subtitle", "start_mode": "seconds", "start": 0, "duration": None, "editor": subtitle.model_dump(by_alias=True)},
    ])
    timeline, _ = build_timeline(**composition_case)
    title_clips, first = [row["SubtitleTrackClips"] for row in timeline["SubtitleTracks"]]
    assert (title_clips[0]["TimelineIn"], title_clips[0]["TimelineOut"]) == (4, 6)
    assert title_clips[0]["Content"] == composition_case["request"]["title"]
    assert [(item["TimelineIn"], item["TimelineOut"]) for item in first] == [(1, 3), (4, 6)]
    assert first[0]["Y"] == 0.82
    assert [item["Content"] for item in first] == ["甲乙丙丁", "戊己庚辛"]
    assert "示例" not in str(timeline)


@pytest.mark.parametrize("kind", ["video", "image"])
def test_transition_uses_selected_boundary_and_preserves_audio(composition_case, kind):
    """只在指定交界处应用保存时长，保留其他素材、音频和输入快照。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    editor = EffectTemplateEditor(title="", subtitle="", bubble_text="", transition=asset.id)
    apply_tracks(composition_case, [{"id": "transition", "target": "transition", "transition_boundary": 1, "duration": 1, "editor": editor.model_dump(by_alias=True)}])
    for index, match in enumerate(composition_case["matches"]):
        match.update(matched_candidate_url=f"https://media.example.test/{index}", matched_candidate_type=kind)
    original = deepcopy(composition_case)
    timeline, _ = build_timeline(**composition_case)
    assert composition_case == original
    clips = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in clips] == [(0, 1), (1, 3), (3, 4), (4, 6), (6, 8)]
    assert clips[0]["Effects"][-1] == {"Type": "DLTransition", "SubType": asset.effect_id, "Duration": 0.5}
    assert not any(effect["Type"] == "DLTransition" for clip in clips[1:] for effect in clip["Effects"])
    assert clips[-1]["Effects"] == [
        {"Type": "Background", "SubType": "Blur", "Radius": 0.1}, {"Type": "Volume", "Gain": 0},
    ]
    assert (clips[2]["In"], clips[2]["Out"], clips[4]["In"], clips[4]["Out"]) == (3, 4, 6, 8)
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["TimelineOut"] == 8
    composition_case["template"]["tracks"][0]["duration"] = 0.1
    changed, _ = build_timeline(**composition_case)
    assert changed["VideoTracks"][0]["VideoTrackClips"][0]["Effects"][-1]["Duration"] == 0.1


@pytest.mark.parametrize("mode", [None, "sequence", "random"])
def test_disabled_transitions_preserve_candidates(composition_case, mode):
    """统一关闭后 IMS 成片没有转场，重新开启继续使用已保存的候选。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    apply_tracks(composition_case, [{**template_track("transition", transition=asset.id), "duration": 1}])
    composition_case["template"].update(transition_mode=mode, transition_enabled=False)
    for index, match in enumerate(composition_case["matches"]):
        match.update(matched_candidate_url=f"https://media.example.test/{index}", matched_candidate_type="video")
    original = deepcopy(composition_case)
    disabled, _ = build_timeline(**composition_case)
    assert not any(effect["Type"] in ("Transition", "DLTransition")
                   for clip in disabled["VideoTracks"][0]["VideoTrackClips"] for effect in clip["Effects"])
    assert composition_case == original
    composition_case["template"]["transition_enabled"] = True
    enabled, _ = build_timeline(**composition_case)
    assert any(effect["Type"] in ("Transition", "DLTransition")
               for clip in enabled["VideoTracks"][0]["VideoTrackClips"] for effect in clip["Effects"])


def test_transition_uses_only_selected_boundaries(composition_case):
    """两个交界处分别应用保存的效果与时长，其他交界处保持直接切换。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    tracks = [{**template_track("transition", transition=asset.id), "id": f"transition-{boundary}", "transition_boundary": boundary, "duration": seconds} for boundary, seconds in ((1, 0.2), (2, 0.4))]
    apply_tracks(composition_case, tracks)
    for index, match in enumerate(composition_case["matches"]):
        match.update(matched_candidate_url=f"https://media.example.test/{index}", matched_candidate_type="video")
    clips = build_timeline(**composition_case)[0]["VideoTracks"][0]["VideoTrackClips"]
    assert [[item["Duration"] for item in clip["Effects"] if item["Type"] == "DLTransition"] for clip in clips] == [[0.2], [0.4], [], [], []]
    apply_tracks(composition_case, tracks[1:])
    clips = build_timeline(**composition_case)[0]["VideoTracks"][0]["VideoTrackClips"]
    assert [[item["Duration"] for item in clip["Effects"] if item["Type"] == "DLTransition"] for clip in clips] == [[], [0.4], [], [], []]


@pytest.mark.parametrize("mode", ["sequence", "random"])
def test_transition_list_selects_all_material_boundaries(composition_case, mode):
    """顺序选择循环应用，随机选择各候选一次，时长统一且重试结果一致。"""
    from server.template.transitions import select_transitions

    assets = [item for item in effect_catalog().values() if item.category == "transition/normal"][:3]
    tracks = [{**template_track("transition", transition=asset.id), "id": f"candidate-{index}",
               "transition_boundary": index + 1, "duration": 0.2} for index, asset in enumerate(assets)]
    apply_tracks(composition_case, tracks)
    composition_case["template"].update(transition_mode=mode, transition_duration_seconds=0.2)
    composition_case["transition_seed"] = "composition-task"
    for index, match in enumerate(composition_case["matches"]):
        match.update(matched_candidate_url=f"https://media.example.test/{index}", matched_candidate_type="video")
    original = deepcopy(composition_case)
    timeline, _ = build_timeline(**composition_case)
    assert build_timeline(**composition_case)[0] == timeline
    assert composition_case == original
    clips = timeline["VideoTracks"][0]["VideoTrackClips"]
    selected = select_transitions(tracks, len(clips) - 1, mode, "composition-task")
    effects = [[item for item in clip["Effects"] if item["Type"] == "DLTransition"] for clip in clips]
    assert [len(items) for items in effects] == ([1, 1, 1, 1, 0] if mode == "sequence" else [1, 1, 1, 0, 0])
    assert [items[0]["SubType"] for items in effects if items] == [
        effect_catalog()[item["editor"]["transition"]].parameters["SubType"] for item, _ in selected
    ]
    assert all(items[0]["Duration"] == 0.2 for items in effects if items)
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["TimelineOut"] == 8


def test_transition_selection_combines_ims_and_remotion():
    """混合列表保持同一个选择顺序，每个连接只选一个效果，随机结果依任务标识变化。"""
    from server.template.transitions import select_transitions

    candidates = [{"id": f"item-{index}", "target": target, "transition_boundary": index + 1}
                  for index, target in enumerate(["transition", "remotion", "transition"])]
    assert [item["id"] for item, _ in select_transitions(candidates, 5, "sequence", "task")] == [
        "item-0", "item-1", "item-2", "item-0", "item-1"]
    random = select_transitions(candidates, 10, "random", "task")
    assert random == select_transitions(candidates, 10, "random", "task")
    assert random != select_transitions(candidates, 10, "random", "another-task")
    assert len(random) == len(candidates)
    assert {item["id"] for item, _ in random} == {item["id"] for item in candidates}
    assert [boundary for _, boundary in random] == [1, 2, 3]
    assert select_transitions([], 5, "random", "task") == []
    assert select_transitions(candidates, 0, "sequence", "task") == []


def test_transition_does_not_split_single_clip_and_still_validates_effect(composition_case):
    """无素材切换时明确报告交界处缺失，仍拒绝损坏的效果快照。"""
    asset = next(item for item in effect_catalog().values() if item.category == "transition/normal")
    choose(composition_case, "transition", asset.id)
    composition_case["template"]["tracks"][-1].update(duration=3)
    timeline, warnings = build_timeline(**composition_case)
    clip, = timeline["VideoTracks"][0]["VideoTrackClips"]
    assert (clip["In"], clip["Out"]) == (0, 8)
    assert clip["Effects"] == [
        {"Type": "Background", "SubType": "Blur", "Radius": 0.1}, {"Type": "Volume", "Gain": 0},
    ] and warnings == ["对象 transition：交界处 1 不存在，未应用转场"]
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
