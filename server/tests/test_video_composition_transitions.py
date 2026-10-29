"""关键词切镜、动态限频、跨渲染器时长及三代任务恢复；执行 uv run --locked pytest tests/test_video_composition_transitions.py。"""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from generated.imv.sprite.v1 import sprite_pb2 as pb
from google.protobuf.json_format import MessageToDict
from sqlalchemy import update

from server.template.schema import effect_catalog
from server.video_composition import remotion, service, store, timeline
from server.video_composition.timeline import build_timeline
from .conftest import published_sprite, remotion_track, template_track


@pytest.fixture
def transition_case(composition_case):
    """纯素材十秒成片，切镜为 2、5、8 秒，字幕含一个 4 秒出现的关键词，配置三个 IMS 候选。"""
    case = composition_case
    case.update(duration_ms=10000, material_durations=[2, 3, 3, 2], matches=[], use_keyword_transitions=True)
    case["request"].update(compositionMode="materials_voice", videoUrl=None,
                           materials=[{"fileUrl": f"https://media.test/{i}.mp4", "type": "video"} for i in range(4)])
    case["segments"] = [{**case["segments"][0], "start_time": 4, "end_time": 7, "group_id": [1, 1]}]
    assets = [item for item in effect_catalog().values() if item.category == "transition/normal"][:3]
    case["template"].update(transition_mode="sequence", transition_duration_seconds=0.2)
    case["template"]["tracks"] += [{**template_track("transition", transition=asset.id),
                                    "id": f"candidate-{i}", "transition_boundary": i + 1, "duration": 0.2}
                                   for i, asset in enumerate(assets)]
    case["template"]["effects"] += [item.model_dump(mode="json") for item in assets]
    case["template"]["effect_ids"] += [item.id for item in assets]
    return case


@pytest.fixture
def spacing_case(transition_case):
    """160 秒的纯素材时间线：首尾近侧为 16／144 秒，六个中间关键词各有相距四秒的前后候选。"""
    case = transition_case
    durations = [4, 12] + [4] * 32 + [14, 2]
    case.update(duration_ms=160000, material_durations=durations, use_transition_spacing=True)
    case["request"]["materials"] = [{"fileUrl": f"https://media.test/{i}.mp4", "type": "video"}
                                    for i in range(len(durations))]
    source = case["segments"][0]
    anchors = [14, 31, 51, 71, 91, 111, 131, 146]
    case["segments"] = [{**source, "segment_id": i + 1, "start_time": start, "end_time": start + 0.25,
                         "group_id": [i + 1, len(anchors)]} for i, start in enumerate(anchors)]
    return case


def applied_transitions(timeline):
    """从实际 IMS 提交内容读出切镜时间、效果和有效时长，不重复实现选择算法。"""
    return [(clip["TimelineOut"], effect["SubType"], effect["Duration"])
            for clip in timeline["VideoTracks"][0]["VideoTrackClips"]
            for effect in clip["Effects"] if effect["Type"] == "DLTransition"]


@pytest.mark.parametrize("anchors,expected", [([4], [2, 5]), ([5], [2, 8]), ([0], [2]),
                                             ([9], [8]), ([4, 4.5], [2, 5]), ([], [2, 5, 8])])
def test_keyword_nearest_cuts_and_whole_video_fallback(transition_case, anchors, expected):
    """跨字幕内部切镜、同刻排除、多关键词去重、首尾单侧及整片无词均遵守约定，音画时序不变。"""
    case = transition_case
    source = case["segments"][0]
    case["segments"] = [{**source, "segment_id": i + 1, "start_time": start, "end_time": start + 0.25,
                         "keyword": source["keyword"] if anchors else "", "group_id": [i + 1, len(anchors) or 1]}
                        for i, start in enumerate(anchors or [4])]
    original = deepcopy(case)
    timeline, _ = build_timeline(**case)
    actual = applied_transitions(timeline)
    assert [time for time, _, _ in actual] == expected
    candidates = [track for track in case["template"]["tracks"] if track["target"] == "transition"]
    assert [effect for _, effect, _ in actual] == [effect_catalog()[track["editor"]["transition"]].effect_id
                                                for track in candidates[:len(expected)]]
    assert [(clip["TimelineIn"], clip["TimelineOut"]) for clip in timeline["VideoTracks"][0]["VideoTrackClips"]] == [(0, 2), (2, 5), (5, 8), (8, 10)]
    assert timeline["AudioTracks"][0]["AudioTrackClips"][0]["TimelineOut"] == 10
    assert case == original


@pytest.mark.parametrize("spaced", [False, True])
def test_standard_uses_roles_and_subtitle_event_start(transition_case, spaced):
    """两种关键词规则均按字幕子句与镜头角色选点；无词动态随机也允许素材间切镜。"""
    case = transition_case
    case["transition_seed"] = "b"
    if spaced:
        case["use_transition_spacing"] = True
    case["request"].update(compositionMode="standard", videoUrl="https://media.test/avatar.mp4")
    first = {**case["segments"][0], "start_time": 1, "end_time": 2,
             "subtitle_parts": [{"text": "丙丁", "start_time": 1, "end_time": 1.5},
                                {"text": "甲乙", "start_time": 1.5, "end_time": 2}]}
    case["segments"] = [first, {**first, "segment_id": 2, "keyword": "", "start_time": 2,
                                "end_time": 3, "subtitle_parts": None}]
    case["matches"] = [{**item, "matched_candidate_url": case["request"]["videoUrl"], "matched_candidate_type": "video"}
                       for item in case["segments"]]
    timeline, _ = build_timeline(**case)
    assert [time for time, _, _ in applied_transitions(timeline)] == ([1] if spaced else [1, 3])
    for item in case["segments"]:
        item["keyword"] = ""
    assert [time for time, _, _ in applied_transitions(build_timeline(**case)[0])] == ([2] if spaced else [1, 2, 3])
    for item in case["matches"]:
        item["matched_candidate_url"] = None
    assert applied_transitions(build_timeline(**case)[0]) == []


@pytest.mark.parametrize("spaced", [False, True])
def test_silent_title_keyword_does_not_disable_all_cut_fallback(transition_case, spaced):
    """无声成片始终从全部切镜选择；标题词不参与，动态规则对这些候选同样限频。"""
    case = transition_case
    case.update(use_transition_spacing=spaced, transition_seed="b")
    case["request"].update(compositionMode="materials_silent", audioUrl=None, text=None,
                           processRules={"videoDuration": 10})
    case["segments"] = []
    timeline, _ = build_timeline(**case, title_keyword="业务")
    assert [time for time, _, _ in applied_transitions(timeline)] == ([5] if spaced else [2, 5, 8])


def test_same_time_ignores_float_accumulation_error(transition_case):
    """纯素材累计时长得到 5.300000000000001，仍视为与关键词的 5.3 秒同刻并跳过。"""
    case = transition_case
    case["material_durations"] = [2.1, 3.2, 2.7, 2]
    case["segments"][0].update(start_time=5.3, end_time=7)
    actual = applied_transitions(build_timeline(**case)[0])
    assert [time for time, _, _ in actual] == pytest.approx([2.1, 8])


def test_keywords_without_role_switches_do_not_trigger_fallback(transition_case):
    """混剪中素材连铺全片时虽有素材切镜，含关键词也不能回退为全部切镜加转场。"""
    case = transition_case
    case["request"].update(compositionMode="standard", videoUrl="https://media.test/avatar.mp4")
    source = case["segments"][0]
    case["segments"] = [{**source, "start_time": 0, "end_time": 5},
                        {**source, "segment_id": 2, "start_time": 5, "end_time": 10, "keyword": ""}]
    case["matches"] = [{**item, "matched_candidate_url": f"https://media.test/{i}.mp4", "matched_candidate_type": "video"}
                       for i, item in enumerate(case["segments"])]
    assert applied_transitions(build_timeline(**case)[0]) == []


@pytest.mark.parametrize("has_keyword,expected", [(False, [5.1]), (True, [])])
@pytest.mark.parametrize("spaced", [False, True])
def test_short_cut_is_skipped_without_advancing_or_searching_farther(transition_case, has_keyword, expected, spaced):
    """过短切镜不占动态间隔或效果顺序；最近切镜不可用也不向更远处补转场。"""
    case = transition_case
    case["transition_seed"] = "b"
    if spaced:
        case["use_transition_spacing"] = True
    case["material_durations"] = [2, 0.1, 3, 4.9]
    case["segments"][0].update(start_time=1, end_time=1.5, keyword="甲乙" if has_keyword else "")
    actual = applied_transitions(build_timeline(**case)[0])
    assert [time for time, _, _ in actual] == expected
    if actual:
        track = next(track for track in case["template"]["tracks"] if track["target"] == "transition")
        assert actual[0][1] == effect_catalog()[track["editor"]["transition"]].effect_id


@pytest.mark.parametrize("mode", ["sequence", "random", None])
def test_candidates_repeat_and_retries_are_stable(transition_case, mode):
    """九个切镜均有转场，三个候选可循环或随机复用；无选择方式的旧模板在新任务中按顺序使用。"""
    case = transition_case
    case["template"]["transition_mode"] = mode
    case["segments"][0]["keyword"] = ""
    case["request"]["materials"] = case["request"]["materials"][:1] * 10
    case["material_durations"] = [1] * 10
    case["transition_seed"] = "repeat-test"
    result = applied_transitions(build_timeline(**case)[0])
    assert len(result) == 9 and len({effect for _, effect, _ in result}) <= 3
    assert applied_transitions(build_timeline(**case)[0]) == result
    if mode != "random":
        assert [effect for _, effect, _ in result] == [effect for _, effect, _ in result[:3]] * 3
    case["template"]["tracks"] = [track for track in case["template"]["tracks"]
                                   if track.get("transition_boundary", 1) == 1]
    single = applied_transitions(build_timeline(**case)[0])
    assert len(single) == 9 and len({effect for _, effect, _ in single}) == 1


@pytest.mark.parametrize("disabled", [True, False])
@pytest.mark.parametrize("spaced", [False, True])
def test_disabled_and_empty_candidates(transition_case, disabled, spaced):
    """关闭或清空候选均无转场；无词回退与动态限频不会绕过模板开关。"""
    case = transition_case
    if spaced:
        case["use_transition_spacing"] = True
    case["segments"][0]["keyword"] = ""
    if disabled:
        case["template"]["transition_enabled"] = False
    else:
        case["template"]["tracks"] = [track for track in case["template"]["tracks"] if track["target"] != "transition"]
    assert applied_transitions(build_timeline(**case)[0]) == []


@pytest.mark.parametrize("duration,distance,count", [
    (10, 3.9, 1), (10, 4, 2), (90, 5.9, 1), (90, 6, 2),
    (160, 7.9, 1), (160, 8, 2), (600, 11.9, 1), (600, 12, 2),
    (160.07, 8.001, 1), (160.07, 8.002, 2),
])
def test_spacing_uses_total_duration_and_clamped_threshold(transition_case, duration, distance, count):
    """两处切镜验证四秒下限、平方根中段和十二秒上限；实际判断不先按十分之一秒取整。"""
    case = transition_case
    case.update(duration_ms=round(duration * 1000), material_durations=[2, distance, duration - 2 - distance],
                use_transition_spacing=True)
    case["request"]["materials"] = case["request"]["materials"][:3]
    case["segments"][0]["keyword"] = ""
    times = [time for time, _, _ in applied_transitions(build_timeline(**case)[0])]
    assert len(times) == count
    assert set(times) <= {2, 2 + distance}
    if count == 2:
        assert times == pytest.approx([2, 2 + distance])


@pytest.mark.parametrize("anchors,expected", [([4], [5]), ([3.5], [2]), ([5], [2]),
                                             ([0], [2]), ([9], [8]), ([4, 4.5], [5])])
def test_spacing_pins_nearest_side_and_deduplicates(transition_case, anchors, expected):
    """单关键词只取近侧；同距优先前侧、同刻仍跳过，首尾共用切镜时只加一次。"""
    case = transition_case
    case["use_transition_spacing"] = True
    source = case["segments"][0]
    case["segments"] = [{**source, "segment_id": i + 1, "start_time": start, "end_time": start + 0.25,
                         "group_id": [i + 1, len(anchors)]} for i, start in enumerate(anchors)]
    assert [time for time, _, _ in applied_transitions(build_timeline(**case)[0])] == expected


def test_spacing_keeps_close_endpoints_without_middle(transition_case):
    """首尾保底仅隔三秒时仍保留两处，即使中间关键词命中其他切镜也不再加。"""
    case = transition_case
    case.update(material_durations=[2, 1, 2, 5], use_transition_spacing=True)
    source = case["segments"][0]
    case["segments"] = [{**source, "segment_id": i + 1, "start_time": start, "end_time": start + 0.25,
                         "group_id": [i + 1, 3]} for i, start in enumerate([1, 4, 6])]
    assert [time for time, _, _ in applied_transitions(build_timeline(**case)[0])] == [2, 5]


@pytest.mark.parametrize("durations,anchors,expected", [
    ([2, 0.1, 3, 4.9], [3], []),
    ([2, 0.1, 17.9, 10, 10, 10], [3, 21, 31, 41], [20, 30, 40]),
], ids=["single-keyword", "short-first-endpoint"])
def test_spacing_does_not_replace_short_nearest_side(transition_case, durations, anchors, expected):
    """最近一侧过短不替换；单词无中间候选，另一个首尾可用时中间合格点仍可入选。"""
    case = transition_case
    case.update(duration_ms=round(sum(durations) * 1000), material_durations=durations, use_transition_spacing=True)
    case["request"]["materials"] = [{"fileUrl": f"https://media.test/{i}.mp4", "type": "video"}
                                    for i in range(len(durations))]
    source = case["segments"][0]
    case["segments"] = [{**source, "segment_id": i + 1, "start_time": start, "end_time": start + 0.25,
                         "group_id": [i + 1, len(anchors)]} for i, start in enumerate(anchors)]
    assert [time for time, _, _ in applied_transitions(build_timeline(**case)[0])] == expected


def test_spacing_random_middle_is_bounded_stable_and_ordered(spacing_case):
    """中间只从原关键词候选抽取，首尾固定、间隔达标，重试一致而不同任务可有不同选择。"""
    case = spacing_case
    original = deepcopy(case)
    observations = set()
    candidates = [track for track in case["template"]["tracks"] if track["target"] == "transition"]
    effects = [effect_catalog()[track["editor"]["transition"]].effect_id for track in candidates]
    for seed in ("a", "b", "c", "d"):
        built, _ = build_timeline(**case, transition_seed=seed)
        actual = applied_transitions(built)
        times = [time for time, _, _ in actual]
        assert len(times) == 8 and times[0] == 16 and times[-1] == 144
        assert set(times[1:-1]) <= {28, 32, 48, 52, 68, 72, 88, 92, 108, 112, 128, 132}
        assert all(right - left >= 8 for left, right in zip(times, times[1:]))
        assert [effect for _, effect, _ in actual] == (effects * 3)[:len(actual)]
        assert applied_transitions(build_timeline(**case, transition_seed=seed)[0]) == actual
        assert built["AudioTracks"][0]["AudioTrackClips"][0]["TimelineOut"] == 160
        observations.add(tuple(times))
    assert len(observations) > 1
    assert case == original


def test_spacing_without_keywords_has_no_pinned_endpoints(transition_case):
    """整片无词时全部切镜参加限频；中间点可胜出并排除首尾，标题关键词不影响选点。"""
    case = transition_case
    case.update(use_transition_spacing=True, transition_seed="b")
    case["segments"][0]["keyword"] = ""
    assert [time for time, _, _ in applied_transitions(build_timeline(**case, title_keyword="业务")[0])] == [5]


@pytest.mark.parametrize("mode", ["sequence", "random"])
@pytest.mark.parametrize("duration", [None, 0.6, 0.01])
@pytest.mark.parametrize("has_keyword", [True, False])
def test_mixed_renderers_share_selection_and_duration(transition_case, mode, duration, has_keyword):
    """IMS 和 Remotion 在同一切镜互斥，资产默认时长与显式时长一致，过短资产跳过不消耗顺序候选。"""
    case = transition_case
    sprite = published_sprite(kind=pb.SPRITE_KIND_TRANSITION_OVERLAY)
    sprite.canvas.preview_frames = 18
    saved = MessageToDict(sprite, preserving_proto_field_name=True)
    clip = remotion_track("overlay-transition", sprite_id=sprite.sprite_id, transition_boundary=2, duration=duration)
    case["template"]["tracks"] = [clip if track.get("transition_boundary") == 2 else track for track in case["template"]["tracks"]]
    case["template"]["transition_mode"] = mode
    case["remotion_assets"] = {sprite.sprite_id: saved}
    case["segments"][0]["keyword"] = "甲乙" if has_keyword else ""
    data = {"request": case["request"], "template": case["template"], "duration_ms": case["duration_ms"],
            "keyword_transitions": True, "output": {key: case[key] for key in ("width", "height", "fps")},
            "segmentation": {"segments": case["segments"]},
            "remotion": {"placements": [{**clip, "editor": {}}], "assets": case["remotion_assets"]}}
    # 多个任务种子覆盖随机重复选中 Remotion 与未选中的两种实际结果。
    for seed in ("a", "b", "c"):
        timeline, _ = build_timeline(**case, transition_seed=seed)
        overlay = remotion.plan(data, 120, timeline=timeline, transition_seed=seed)
        ims_times = [time for time, _, _ in applied_transitions(timeline)]
        instances = overlay.instances if overlay else []
        remotion_times = [(item["start_frame"] + item["duration_frames"] / 2) / 30 for item in instances]
        assert not set(ims_times) & set(remotion_times)
        if duration != 0.01:
            assert sorted(ims_times + remotion_times) == ([2, 5] if has_keyword else [2, 5, 8])
            assert all(item["duration_frames"] == 18 for item in instances)
        else:
            assert instances == []
            if mode == "sequence":
                assert ims_times == [2]


@pytest.mark.parametrize("mode", ["sequence", "random"])
@pytest.mark.parametrize("duration", [None, 0.6])
def test_spacing_mixed_renderers_share_points_and_duration(spacing_case, mode, duration):
    """动态限频在 IMS 与 Remotion 间共享选点；改成 Remotion 候选不改变切镜位置或自身时长。"""
    case = spacing_case
    case["template"]["transition_mode"] = mode
    ims_case = deepcopy(case)
    sprite = published_sprite(kind=pb.SPRITE_KIND_TRANSITION_OVERLAY)
    sprite.canvas.preview_frames = 18
    clip = remotion_track("overlay-transition", sprite_id=sprite.sprite_id, transition_boundary=2, duration=duration)
    case["template"]["tracks"] = [clip if track.get("transition_boundary") == 2 else track for track in case["template"]["tracks"]]
    case["remotion_assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    data = {"request": case["request"], "template": case["template"], "duration_ms": case["duration_ms"],
            "keyword_transitions": True, "transition_spacing": True,
            "output": {key: case[key] for key in ("width", "height", "fps")},
            "segmentation": {"segments": case["segments"]},
            "remotion": {"placements": [{**clip, "editor": {}}], "assets": case["remotion_assets"]}}
    planned_remotion = False
    for seed in ("a", "b", "c"):
        expected = [time for time, _, _ in applied_transitions(build_timeline(**ims_case, transition_seed=seed)[0])]
        built, _ = build_timeline(**case, transition_seed=seed)
        overlay = remotion.plan(data, 200, timeline=built, transition_seed=seed)
        ims_times = [time for time, _, _ in applied_transitions(built)]
        instances = overlay.instances if overlay else []
        remotion_times = [(item["start_frame"] + item["duration_frames"] / 2) / 30 for item in instances]
        assert not set(ims_times) & set(remotion_times)
        assert sorted(ims_times + remotion_times) == expected
        assert all(item["duration_frames"] == 18 for item in instances)
        planned_remotion = planned_remotion or bool(instances)
    assert planned_remotion


@pytest.mark.parametrize("duration_case", json.loads(Path(__file__).with_name("transition_duration_cases.json").read_text()),
                         ids=lambda case: case["name"])
@pytest.mark.parametrize("legacy", [False, True])
def test_duration_defaults_match_client_preview(transition_case, mocker, duration_case, legacy):
    """共享预期约束新旧合成：空时长取发布预览长度，显式时长可长可短；IMS 规划与 Remotion 实例一致。"""
    case = transition_case
    case.update(duration_ms=40000, material_durations=[10] * 4, use_keyword_transitions=not legacy)
    case["segments"][0].update(start_time=15, end_time=17)
    sprite = published_sprite(kind=pb.SPRITE_KIND_TRANSITION_OVERLAY,
                              canvas=(1080, 1920, duration_case["fps"]))
    sprite.canvas.preview_frames = duration_case["preview_frames"]
    clip = remotion_track("remotion-transition", transition_boundary=2, duration=duration_case["duration"])
    ims = next(track for track in case["template"]["tracks"] if track.get("transition_boundary") == 1)
    ims["duration"] = 0.4
    case["template"]["transition_duration_seconds"] = 0.4
    case["template"]["tracks"] = [track for track in case["template"]["tracks"]
                                   if track.get("transition_boundary") is None] + [ims, clip]
    case["remotion_assets"] = {sprite.sprite_id: MessageToDict(sprite, preserving_proto_field_name=True)}
    selection = mocker.spy(timeline, "keyword_transitions")
    built, _ = build_timeline(**case)
    data = {"request": case["request"], "template": case["template"], "duration_ms": case["duration_ms"],
            "keyword_transitions": not legacy, "output": {key: case[key] for key in ("width", "height", "fps")},
            "segmentation": {"segments": case["segments"]},
            "remotion": {"placements": [{**clip, "editor": {}}], "assets": case["remotion_assets"]}}
    overlay = remotion.plan(data, 120, timeline=built)
    actual = [item["duration_frames"] / case["fps"] for item in overlay.instances]
    assert actual == [duration_case["expected_seconds"]]
    assert [seconds for _, _, seconds in applied_transitions(built)] == ([0.4, 0.4] if legacy else [0.4])
    if not legacy:
        assert [track["duration"] for track, _ in selection.spy_return if track["id"] == clip["id"]] == actual
    assert clip["duration"] == duration_case["duration"]


@pytest.mark.anyio
@pytest.mark.parametrize("rules,expected", [("legacy", [2, 5, 8]), ("keywords", [2, 5]), ("spaced", [5])])
async def test_resume_keeps_accepted_rules_and_saved_overlay(transition_case, composition_runtime, monkeypatch, rules, expected):
    """新任务保存动态规则；两代旧快照恢复各自策略，三者均复用已上传产物而不重新渲染。"""
    case = transition_case
    case["template"]["transition_mode"] = "random"
    store.initialize_schema()
    record = store.create(case["request"], composition_runtime.settings.output())
    assert record["data"]["keyword_transitions"] is True
    assert record["data"]["transition_spacing"] is True
    if rules != "spaced":
        # 旧关键词任务仅缺新标识；最早任务两个标识都没有，恢复不按当前默认值补写。
        missing = {"transition_spacing"} if rules == "keywords" else {"keyword_transitions", "transition_spacing"}
        data = {key: value for key, value in record["data"].items() if key not in missing}
        with store.get_engine().begin() as connection:
            connection.execute(update(store.tasks).where(store.tasks.c.task_id == record["task_id"]).values(data=data))
        record = store.get(record["task_id"])
    record = store.advance(record, "assembling", template=case["template"], duration_ms=case["duration_ms"],
                           segmentation={"segments": case["segments"]}, material_durations=case["material_durations"],
                           remotion_result={"video_url": "https://media.test/saved.webm", "duration": 10})
    async def unexpected_render(*args, **kwargs):
        """恢复不应再次编码既有产物。"""
        pytest.fail("重复渲染已保存的 Remotion 视频")
    async def storage(self):
        """隔离 IMS 网络请求。"""
        return "bucket.oss-cn-shanghai.aliyuncs.com"
    monkeypatch.setattr(remotion, "render", unexpected_render)
    monkeypatch.setattr(service.ims.IMS, "storage_location", storage)
    job = service.Job(composition_runtime, record, composition_runtime.settings)
    await job.assemble()
    timeline = job.record["data"]["timeline"]
    assert [time for time, _, _ in applied_transitions(timeline)] == expected
    assert timeline["VideoTracks"][-1]["VideoTrackClips"][0]["MediaURL"] == "https://media.test/saved.webm"
