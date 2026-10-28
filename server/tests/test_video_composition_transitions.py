"""关键词切镜、候选复用、前后端共享时长及恢复回归；执行 uv run --locked pytest tests/test_video_composition_transitions.py。"""
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


def test_standard_uses_roles_and_subtitle_event_start(transition_case):
    """旧短句按实际含词子句起点找切镜；混剪跳过素材到素材，即使素材 URL 等于数字人也能区分角色。"""
    case = transition_case
    case["request"].update(compositionMode="standard", videoUrl="https://media.test/avatar.mp4")
    first = {**case["segments"][0], "start_time": 1, "end_time": 2,
             "subtitle_parts": [{"text": "丙丁", "start_time": 1, "end_time": 1.5},
                                {"text": "甲乙", "start_time": 1.5, "end_time": 2}]}
    case["segments"] = [first, {**first, "segment_id": 2, "keyword": "", "start_time": 2,
                                "end_time": 3, "subtitle_parts": None}]
    case["matches"] = [{**item, "matched_candidate_url": case["request"]["videoUrl"], "matched_candidate_type": "video"}
                       for item in case["segments"]]
    timeline, _ = build_timeline(**case)
    assert [time for time, _, _ in applied_transitions(timeline)] == [1, 3]
    for item in case["segments"]:
        item["keyword"] = ""
    assert [time for time, _, _ in applied_transitions(build_timeline(**case)[0])] == [1, 2, 3]
    for item in case["matches"]:
        item["matched_candidate_url"] = None
    assert applied_transitions(build_timeline(**case)[0]) == []


def test_silent_title_keyword_does_not_disable_all_cut_fallback(transition_case):
    """无声成片没有口播关键词，即使标题有关键词也在全部切镜应用转场。"""
    case = transition_case
    case["request"].update(compositionMode="materials_silent", audioUrl=None, text=None,
                           processRules={"videoDuration": 10})
    case["segments"] = []
    timeline, _ = build_timeline(**case, title_keyword="业务")
    assert [time for time, _, _ in applied_transitions(timeline)] == [2, 5, 8]


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
def test_short_cut_is_skipped_without_advancing_or_searching_farther(transition_case, has_keyword, expected):
    """过短的最近切镜直接跳过；无词时下一个可用切镜仍用候选一，不消耗交替顺序。"""
    case = transition_case
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
def test_disabled_and_empty_candidates(transition_case, disabled):
    """关闭转场或清空候选均不输出转场，不因无关键词回退而绕过模板开关。"""
    case = transition_case
    case["segments"][0]["keyword"] = ""
    if disabled:
        case["template"]["transition_enabled"] = False
    else:
        case["template"]["tracks"] = [track for track in case["template"]["tracks"] if track["target"] != "transition"]
    assert applied_transitions(build_timeline(**case)[0]) == []


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
@pytest.mark.parametrize("legacy", [True, False])
async def test_resume_keeps_accepted_rules_and_saved_overlay(transition_case, composition_runtime, monkeypatch, legacy):
    """新任务记录规则；缺标识的旧任务组装恢复仍使用原转场，并复用已经上传的叠加产物。"""
    case = transition_case
    case["template"]["transition_mode"] = "random"
    store.initialize_schema()
    record = store.create(case["request"], composition_runtime.settings.output())
    assert record["data"]["keyword_transitions"] is True
    if legacy:
        # 模拟升级前已持久化且没有规则标识的任务，不使用公开请求字段控制恢复策略。
        data = {key: value for key, value in record["data"].items() if key != "keyword_transitions"}
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
    assert [time for time, _, _ in applied_transitions(timeline)] == ([2, 5, 8] if legacy else [2, 5])
    assert timeline["VideoTracks"][-1]["VideoTrackClips"][0]["MediaURL"] == "https://media.test/saved.webm"
