"""自由组件字效（`IMV_REMOTION_AGENT=autonomous`）：工作区清单与配置推导、起始工程、契约探针、会话工具与三层循环的自由组件路径。

执行：在 server/ 下 `uv run --locked pytest tests/test_remotion_free_effects.py -v`。打包需要锁定的 Remotion 依赖（esbuild）；
真实浏览器用例另需 `IMV_TEST_RENDERER=1` 与浏览器、字体路径。离线用例用桩替换隔离校验器，只检验宿主逻辑与协议；模型轮次
是脚本化的回复，不访问模型服务。
"""

import asyncio
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from server.remotion_templates.agent import AgentRun, Layer
from server.remotion_templates.context import AssistantMessage, Conversation, StepWindow, image_message
from server.remotion_templates.fonts import FontStore
from server.remotion_templates.history import reply_text
from server.remotion_templates.models import JobReply
from server.remotion_templates.models import GenerationJob
from server.remotion_templates.provider import Budget, _image_content, expand_image_paths
from server.remotion_templates.runtime import MAX_REFERENCE_IMAGES, earlier_images, outcome_record
from server.remotion_templates.renderer import Renderer
from server.remotion_templates.settings import Settings
from server.remotion_templates.tools.catalog import available
from server.remotion_templates.tools.contracts import (
    CheckResult, CodeValidationReport, Composition, RenderValidationReport, WorkspaceEditInput,
)
from server.remotion_templates.tools.registry import ToolFault
from server.remotion_templates.tools.session import ToolSession
from server.remotion_templates.tools.workspace import (
    CODE, MANIFEST, assemble, contract_probes, parse_manifest, keyframes, motion_facts, restore_files, starter_files, trial_frames,
)

RENDERER_DEPS = Path(__file__).parents[1] / "src" / "server" / "remotion" / "node_modules" / "esbuild"
needs_bundler = pytest.mark.skipif(not RENDERER_DEPS.exists(), reason="install locked Remotion renderer dependencies")
needs_browser = pytest.mark.skipif(
    os.environ.get("IMV_TEST_RENDERER") != "1" or shutil.which("node") is None or not RENDERER_DEPS.exists(),
    reason="set IMV_TEST_RENDERER=1 with Node and the locked Remotion dependencies for the Linux browser integration",
)
BROWSER = {
    name: Path(value)
    for name, value in (("browser_executable", os.environ.get("IMV_BROWSER_EXECUTABLE")), ("font_regular", os.environ.get("IMV_FONT_REGULAR")),
                        ("font_bold", os.environ.get("IMV_FONT_BOLD")))
    if value
}
LIBRARY = ["Noto Sans CJK SC", "Zed Display"]
SHAPES = {
    "one line": set(),
    "two tiers": {"two_tier"},
    "bilingual pair": {"bilingual"},
    "two bilingual groups with a keyword effect": {"two_tier", "bilingual", "keyword_effect"},
}


@pytest.fixture(autouse=True)
def font_library(monkeypatch):
    """The font library holds two families; nothing reads the real catalog."""
    monkeypatch.setattr(FontStore, "active", SimpleNamespace(families=lambda: list(LIBRARY)))


def manifest(files: dict, **changes) -> dict:
    """The workspace manifest with top-level and text fields replaced."""
    data = json.loads(files[MANIFEST])
    text = changes.pop("text", None)
    data |= changes
    if text is not None:
        data["text"] = {**data.get("text", {}), **text}
    return data


def build(files: dict, requested=frozenset(), duration=150, **changes):
    """Assemble the workspace with a changed manifest; returns the draft and the host's notes."""
    if changes:
        files = {**files, MANIFEST: json.dumps(manifest(files, **changes), ensure_ascii=False)}
    return assemble(files, fonts=LIBRARY, requested=set(requested), duration=duration)


@needs_bundler
@pytest.mark.parametrize("shape", list(SHAPES))
def test_every_starter_assembles_with_the_fixed_configuration_names(shape):
    """新作品的起始工程按开关预置文字形状；组装后宿主给出与文字块完全相同的固定配置名，Agent 只拥有自己的样式参数，且从基线还原再组装得到同样的默认值。"""
    requested = SHAPES[shape]
    files = starter_files("text", requested, "Noto Sans CJK SC")
    draft, notes = build(files, requested)
    properties = draft.parameter_schema["properties"]
    assert notes == []
    assert {"text", "fontSize", "keywords", "keywordBold", "keywordColor", "keywordSize"} <= set(properties)
    lines = len(draft.default_parameters.get("lineStartFrames") or [0])
    assert lines == {"one line": 1, "two tiers": 2, "bilingual pair": 2, "two bilingual groups with a keyword effect": 4}[shape]
    if lines == 1:
        assert {"fontFamily", "fontWeight", "color"} <= set(properties)
    else:
        assert {f"line{index}{suffix}" for index in range(1, lines + 1) for suffix in ("Color", "FontFamily", "FontWeight", "SizeRatio")} <= set(properties)
        assert "color" not in properties and "fontFamily" not in properties
    assert ("textEn" in properties) is ("bilingual" in requested)
    assert ("keywordEffect" in properties) is ("keyword_effect" in requested)
    # Every parameter is a top-level parameter of the single instance, titled in Chinese.
    assert [item.targets[0].instance_id for item in draft.bindings] == ["effect"] * len(draft.bindings)
    assert all(properties[item.name]["title"] for item in draft.bindings)
    assert draft.effect["kind"] == "text" and draft.text is None
    again, _ = build(restore_files(draft, draft.default_parameters), requested)
    assert again.default_parameters == draft.default_parameters


@needs_bundler
@pytest.mark.parametrize("kind", ["filter_overlay", "video_overlay", "transition_overlay"])
def test_other_kinds_start_from_their_own_required_parameters(kind):
    """滤镜、视频特效与转场的起始工程铺满画布，并已带上各自分类的必备样式参数。"""
    draft, notes = build(starter_files(kind, set(), "Noto Sans CJK SC"))
    assert notes == [] and draft.kind == kind
    required = {"filter_overlay": {"intensity", "tintColor"}}.get(kind, {"color", "speed"})
    assert required <= set(draft.parameter_schema["properties"])
    assert draft.instances[0].layout.width == 1080 and draft.instances[0].layout.height == 1920


@needs_bundler
def test_the_manifest_is_corrected_where_the_host_can_and_noted():
    """宿主能安全修正的清单问题直接修正并写进说明：字体不在字体库、字重 500、行起点递减、样式数字没有上下界、颜色缺 pattern、示例关键词不在示例文字里、占用配置名。"""
    files = starter_files("text", {"two_tier"}, "Noto Sans CJK SC")
    data = manifest(files, text={"fonts": [{"family": "Comic Sans", "weight": 500}], "line_starts": [10, 5], "keywords": ["不存在"]})
    data["params"] += [{"name": "glowStrength", "schema": {"type": "number"}, "default": 12, "title": "发光强度", "group": "发光"},
                       {"name": "glowColor", "schema": {"type": "string"}, "default": "#FFAA00"},
                       {"name": "fontSize", "schema": {"type": "number", "minimum": 1, "maximum": 2}, "default": 1}]
    draft, notes = assemble({**files, MANIFEST: json.dumps(data, ensure_ascii=False)}, fonts=LIBRARY, requested=set(), duration=150)
    values, properties = draft.default_parameters, draft.parameter_schema["properties"]
    assert values["line1FontFamily"] == values["line2FontFamily"] == "Noto Sans CJK SC"
    assert values["line1FontWeight"] == values["line2FontWeight"] == 400
    assert values["lineStartFrames"] == [0, 5]
    assert (properties["glowStrength"]["minimum"], properties["glowStrength"]["maximum"]) == (0, 24)
    assert properties["glowStrength"]["x-imv-group"] == "发光"
    assert properties["glowColor"]["pattern"].startswith("^#") and properties["glowColor"]["title"] == "glowColor"
    # fontSize stays the host's configuration parameter.
    assert properties["fontSize"]["minimum"] == 24
    assert draft.effect["text"]["keywords"] == []
    for expected in ("one per line", "'Comic Sans' is not in the font library", "weight 500 became 400", "line_starts became [0, 5]",
                     "glowStrength got the range", "Sample keywords", "fontSize was left out"):
        assert any(expected in note for note in notes), (expected, notes)


@needs_bundler
@pytest.mark.parametrize(("requested", "change", "field"), [
    (set(), {"text": {"sample": "一\n二\n三"}}, "/text/sample"),
    ({"two_tier"}, {"text": {"sample": "只有一行", "fonts": [{"family": "Noto Sans CJK SC"}]}}, "/text/sample"),
    ({"bilingual"}, {"text": {"bilingual": False, "sample": "一行"}}, "/text/bilingual"),
    (set(), {"text": {"bilingual": True, "sample": "中文\n还是中文"}}, "/text/sample"),
])
def test_a_sample_that_fits_no_shape_or_misses_a_switch_is_refused(requested, change, field):
    """示例文字的行数对不上任何形状、缺用户打开的双栏或双语，或双语的第二行不是英文时才拒绝，并指出字段。"""
    files = starter_files("text", requested, "Noto Sans CJK SC")
    with pytest.raises(ToolFault) as caught:
        build(files, requested, **change)
    assert caught.value.error.field == field


def test_manifest_errors_name_the_json_position_or_the_field():
    """清单不是 JSON 时给出行列；结构不符时给出 JSON Pointer。"""
    with pytest.raises(ToolFault, match="line 1, column 2"):
        parse_manifest("{oops")
    with pytest.raises(ToolFault) as caught:
        parse_manifest(json.dumps({"kind": "text", "description": "x", "layout": {"x": 0, "y": 0, "width": 10, "height": 10}, "text": {"sample": "a"}}))
    assert caught.value.error.field == "/text/fonts"


@needs_bundler
def test_contract_probes_cover_content_keywords_lines_size_and_every_style_parameter():
    """契约探针：用同形状的另一套文字检查注入内容，用首个示例关键词检查六项关键词样式，所有行同时出现时检查中间帧，并把每个样式参数与关键词字效开关取两端。"""
    draft, _ = build(starter_files("text", {"two_tier", "bilingual", "keyword_effect"}, "Noto Sans CJK SC"), {"two_tier", "bilingual", "keyword_effect"})
    spec = contract_probes(draft)
    assert spec["probe_lines"] == ["注入的第1行文字", "Injected line 2", "注入的第3行文字", "Injected line 4"]
    assert spec["probe"] == {"text": "注入的第1行文字\n注入的第3行文字", "textEn": "Injected line 2\nInjected line 4"}
    assert spec["lines"] == ["示例文字一", "Sample text one", "示例文字二", "Sample text two"]
    assert spec["starts"] == [0, 0, 0, 0] and spec["keyword"] == "示例"
    assert spec["font_size"] == {"default": 96.0, "min": 48}
    assert [item["name"] for item in spec["wiring"]] == ["line1Color", "line2Color", "line3Color", "line4Color", "keywordEffectColor", "keywordEffect"]
    assert spec["base"]["keywords"] == ["示例"]


def subject(x, y=500.0, opacity=1.0, revealed=1.0, width=400.0, name="line 2", text="马上出发"):
    """One measured line, as the validation worker reports it."""
    return {"name": name, "text": text, "x": x, "y": y, "width": width, "opacity": opacity, "revealed": revealed}


def test_motion_facts_say_the_direction_a_line_really_enters_from():
    """运动事实直接说出方向：相对中间帧的静止位置，起点在右就是 right，在下方就是 below；不靠看图或读代码推断。"""
    trace = {"3": [subject(1240.0, opacity=0.5)], "9": [subject(800.0)], "27": [subject(540.0)], "74": [subject(540.0)], "146": [subject(540.0, opacity=0.2)]}
    (fact,) = motion_facts(trace, 30, 74)
    assert "first painted at frame 3 (0.10 s)" in fact and "700 px to the right of its resting position" in fact and "reaching rest by frame 27" in fact
    assert "opacity 0.50 rising to 1.00" in fact and "fades to opacity 0.20 by frame 146" in fact
    (below,) = motion_facts({"0": [subject(540.0, y=620.0)], "74": [subject(540.0)]}, 30, 74)
    assert "120 px below" in below
    (left,) = motion_facts({"0": [subject(-160.0)], "74": [subject(540.0)]}, 30, 74)
    assert "700 px to the left" in left


def test_motion_facts_cover_typing_a_still_line_and_text_never_painted():
    """逐字显现说出已绘制的比例；静止的行说明就在静止位置；从未绘制的文字直接指出。每个主体（行、关键词）各一句。"""
    typing, keyword = motion_facts({"0": [subject(540.0, revealed=0.25), subject(300.0, name="keyword 1", text="马上")], "74": [subject(540.0), subject(300.0, name="keyword 1", text="马上")]}, 30, 74)
    assert "25% of its characters painted" in typing and keyword.startswith("keyword 1") and "at its resting position" in keyword
    (hidden,) = motion_facts({"74": [{**subject(0.0), "x": None, "y": None, "revealed": 0, "opacity": 0}]}, 30, 74)
    assert hidden == "line 2: never painted."


def shot(folder, frame, x=None, y=500, brightness=255):
    """A trial shot on neutral gray: a white block at (x, y), or nothing."""
    image = Image.new("RGB", (720, 640), (128, 128, 128))
    if x is not None:
        image.paste((brightness, brightness, brightness), (x, y, x + 120, y + 40))
    path = folder / f"frame-{frame}.png"
    image.save(path)
    return path


FAST_ENTRANCE = SimpleNamespace(composition=SimpleNamespace(fps=30, duration_frames=150), default_parameters={})


def test_keyframes_crowd_where_the_picture_changes(tmp_path):
    """一段 0.6 秒的快速入场后长时间停留：关键帧集中在入场期间，能看出运动方向；中间帧一定保留，相邻至少隔 3 帧，空白帧不要，总数有上限。"""
    images = {}
    for frame in range(0, 150, 3):
        images[frame] = shot(tmp_path, frame, None) if frame == 0 else shot(tmp_path, frame, max(0, 300 - frame * 20) + 200)
    picked = keyframes(images, FAST_ENTRANCE)
    frames = [frame for frame, _label in picked]
    assert 0 not in frames and 74 not in frames and 75 in frames and frames[0] == 3
    assert len(frames) <= 8 and all(after - before >= 3 for before, after in zip(frames, frames[1:]))
    assert sum(frame <= 15 for frame in frames) >= 4
    assert dict(picked)[75].startswith("middle") and "s" in dict(picked)[3]


def test_a_pulsing_glow_does_not_take_an_entrances_frames(tmp_path):
    """停留时持续的微弱变化（呼吸发光）远小于真正的入场，不抢入场的关键帧。"""
    images = {}
    for frame in range(0, 150, 3):
        if frame <= 18:
            images[frame] = shot(tmp_path, frame, 500 - frame * 15)
        else:
            images[frame] = shot(tmp_path, frame, 230, brightness=240 if frame % 2 else 255)
    frames = [frame for frame, _label in keyframes(images, FAST_ENTRANCE)]
    assert sum(frame <= 18 for frame in frames) >= 5


def test_a_still_effect_only_gets_its_fixed_frames(tmp_path):
    """静止的效果只留首尾可见帧与中间帧，不凑满名额；什么都没画时只留中间帧。"""
    images = {frame: shot(tmp_path, frame, 200) for frame in range(0, 150, 3)}
    assert [frame for frame, _label in keyframes(images, FAST_ENTRANCE)] == [0, 75, 147]
    blank = {frame: shot(tmp_path, frame) for frame in range(0, 150, 3)}
    assert [frame for frame, _label in keyframes(blank, FAST_ENTRANCE)] == [75]


@needs_bundler
def test_trial_shots_are_dense_around_motion_and_cover_the_middle():
    """试拍在开头、结尾和后出现的行之后密集，其余时间稀疏，且一定包含总线定格短字幕的中间帧。"""
    draft, _ = build(starter_files("text", {"two_tier"}, "Noto Sans CJK SC"), {"two_tier"}, duration=900, text={"line_starts": [0, 300]})
    frames = trial_frames(draft)
    assert {0, 3, 42, 899, 449, 303, 342} <= set(frames) and len(frames) < 70
    assert frames == sorted(set(frames)) and max(frames) == 899


# ---- session tools -------------------------------------------------------------------------------------------------


def passing_report(duration=150):
    """A render report in which every check passed."""
    return RenderValidationReport(passed=True, composition=Composition(width=1080, height=1920, fps=30, duration_frames=duration),
                                  code_validation=CodeValidationReport(passed=True, diagnostics=[]),
                                  checks=[CheckResult(name="default_render", status="passed")], tests=[], custom_tests_executed=0)


def stub_checks(current, report=None):
    """Replace a session's isolated checks: captures write frames, renders answer ``report`` and record their options."""
    current.calls = []

    async def valid(_component):
        """Code validation passes for every source."""
        return CodeValidationReport(passed=True, diagnostics=[])

    async def mounted(request, capture=None, **options):
        """Record the options, write the captured frames and answer the configured report."""
        current.calls.append(options)
        if capture is not None:
            capture.directory.mkdir(parents=True, exist_ok=True)
            for frame in capture.frames:
                Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(capture.directory / f"frame-{frame}.png")
        return report or passing_report(request.duration_frames)

    current.validate_code = valid
    current.validate_render = mounted
    current.validator = SimpleNamespace(validate_render=mounted)
    return current


def free_session(tmp_path, intent=None, spec=None, base=None, report=None):
    """A free-form session whose isolated checks are replaced by `stub_checks`."""
    current = ToolSession(SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None), spec, base, Budget(),
                          tmp_path / "run", [], lambda *_: None, intent or {"original_request": {"description": "demo"}}, free=True)
    return stub_checks(current, report)


@needs_bundler
def test_save_runs_the_whole_gate_and_attaches_the_acceptance_sheet(tmp_path):
    """保存走完整门禁（透明度、两种时长、排版、契约探针），通过后记下候选与它的联系表；联系表成为观察图，等待下一轮看过。"""
    current = free_session(tmp_path)
    saved = asyncio.run(current.execute("effect.save", {}, available()))
    options = current.calls[-1]
    assert options["transparency"] is True and options["text_fit"] is True and options["lengths"] == [45, 3600]
    assert options["text_fit_parameters"] == {} and options["effect_contract"]["keyword"] == "示例"
    assert current.latest_sprite_id == saved["sprite_id"] and current.pending_sheet == saved["sprite_id"]
    # By default only the middle frame is looked at; motion comes from measurements, which the stub does not write.
    assert [frame["frame"] for frame in saved["review"]["frames"]] == [74] and saved["motion"] == []
    assert len(current.observations) == len(saved["review"]["frames"])
    assert current.saved_hash == current.workspace_hash() == current.checked_hash


@needs_bundler
def test_a_failed_check_lists_the_problems_and_save_refuses(tmp_path):
    """检查不通过时 validate_check 列出每个问题（不算工具错误），effect_save 拒绝保存并给出同样的问题。"""
    failing = passing_report().model_copy(update={"passed": False, "checks": [CheckResult(name="effect_contract", status="failed", message="unwired glowColor")]})
    current = free_session(tmp_path, report=failing)
    checked = asyncio.run(current.execute("validate.check", {}, available()))
    assert checked["passed"] is False and checked["problems"] == [{"check": "effect_contract", "message": "unwired glowColor"}]
    with pytest.raises(ToolFault, match="unwired glowColor") as caught:
        asyncio.run(current.execute("effect.save", {}, available()))
    assert caught.value.error.code == "CODE_VALIDATION_FAILED" and current.latest_sprite_id is None


@needs_bundler
def test_workspace_edits_are_exact_and_a_broken_manifest_is_not_written(tmp_path):
    """工作区精确替换报告删改行数；写入解析不了的清单时文件保持原样；检查把清单问题当作问题返回。"""
    current = free_session(tmp_path)
    before = current.files[MANIFEST]
    edited = asyncio.run(current.execute("workspace.edit", WorkspaceEditInput(path="Effect.tsx", edits=[{"old_str": "props.color", "new_str": "props.color ?? \"#FFFFFF\""}]), available()))
    assert (edited["lines_removed"], edited["lines_added"]) == (1, 1)
    with pytest.raises(ToolFault):
        asyncio.run(current.execute("workspace.write", {"path": "effect.json", "content": "{"}, available()))
    assert current.files[MANIFEST] == before
    current.files[MANIFEST] = json.dumps(manifest(current.files, text={"sample": "一\n二\n三"}), ensure_ascii=False)
    checked = asyncio.run(current.execute("validate.check", {}, available()))
    assert checked["problems"][0]["check"] == "manifest"


@needs_bundler
def test_an_accepted_free_effect_joins_the_library_once(tmp_path, template_db):
    """采纳的自由组件整份存进 Preset 库（没有角色、按清单描述检索），同样的效果再次采纳不重复存；之后的作品用 library.search 能找到它。"""
    from server.remotion_templates.tools.catalog_store import CatalogStore

    draft, _ = build(starter_files("text", set(), "Noto Sans CJK SC"), description="金色描边标题，逐字上浮入场")
    first, again = CatalogStore().solidify(draft), CatalogStore().solidify(draft)
    assert len(first) == 1 and again == first
    current = free_session(tmp_path)
    found = asyncio.run(current.execute("library.search", {"query": "描边 上浮"}, available()))
    assert [item["preset_id"] for item in found["presets"]] == first
    record = asyncio.run(current.execute("library.search", {"preset_id": first[0]}, available()))
    assert "useTextContent" in record["preset"]["code"]


@needs_bundler
def test_an_edit_can_go_back_to_an_earlier_version(tmp_path):
    """「回到第 n 版」：快照列出作品的自由组件版本，workspace.read 读得到那一版的文件（含那一版用户调过的参数）；更早的组合版本和不存在的序号不可读。"""
    draft, _ = build(starter_files("text", set(), "Noto Sans CJK SC"), description="第一版白字")
    version = lambda number, sprite, values: SimpleNamespace(number=number, spec=SimpleNamespace(sprite=sprite), candidate=SimpleNamespace(default_config=values))
    versions = [version(1, SimpleNamespace(effect=None), {}), version(2, draft, {**draft.default_parameters, "color": "#FF0000"})]
    current = ToolSession(SimpleNamespace(settings=Settings(_env_file=None, data_dir=tmp_path), renderer=None), None, None, Budget(), tmp_path / "run", [], lambda *_: None,
                          {"original_request": {"description": "x"}}, free=True, versions=versions)
    assert current.snapshot()["versions"] == [{"number": 2, "description": "第一版白字"}]
    manifest_file = asyncio.run(current.execute("workspace.read", {"path": "effect.json", "version": 2}, available()))
    assert {item["name"]: item["default"] for item in json.loads(manifest_file["content"])["params"]}["color"] == "#FF0000"
    code_file = asyncio.run(current.execute("workspace.read", {"path": "Effect.tsx", "version": 2}, available()))
    assert code_file["content"] == draft.instances[0].preset.code
    with pytest.raises(ToolFault, match=r"\[2\]"):
        asyncio.run(current.execute("workspace.read", {"path": "Effect.tsx", "version": 1}, available()))


def test_the_runtime_source_can_be_read_but_not_written(tmp_path):
    """Agent 可以读内容运行时 imv 的源码了解类型与实现（像读库的源码），但不能写它。"""
    current = free_session(tmp_path)
    shown = asyncio.run(current.execute("workspace.read", {"path": "imv-runtime.tsx"}, available()))
    assert "export function useTextContent" in shown["content"]
    with pytest.raises(ToolFault):
        asyncio.run(current.execute("workspace.write", {"path": "imv-runtime.tsx", "content": "x"}, available()))


@needs_bundler
def test_previews_render_variants_without_caching_and_are_limited(tmp_path):
    """预览可选长文字与不同长度，只截帧不缓存成验收报告；次数用完后拒绝并提示改用检查与保存。"""
    current = free_session(tmp_path)
    shown = asyncio.run(current.execute("preview.render", {"variant": "short", "frames": [10, 99]}, available()))
    assert shown["duration_frames"] == 45 and [item["frame"] for item in shown["frames"]] == [10, 44]
    assert current.validation_reports == {}
    current.renders_left = 0
    with pytest.raises(ToolFault, match="previews"):
        asyncio.run(current.execute("preview.render", {}, available()))


def test_image_inspect_enlarges_a_region_of_a_reference(tmp_path):
    """放大参考图的局部：裁出比例区域并放大，作为下一轮的观察图；区域越界时拒绝。"""
    reference = tmp_path / "ref.png"
    Image.new("RGB", (400, 800), (10, 20, 30)).save(reference)
    current = free_session(tmp_path)
    current.images = [reference]
    shown = asyncio.run(current.execute("image.inspect", {"image": 1, "x": 0.5, "y": 0.5, "width": 0.25, "height": 0.1}, available()))
    assert (shown["image"], shown["source_width"], shown["source_height"]) == (2, 400, 800)
    assert max(shown["width"], shown["height"]) == 400
    with pytest.raises(ToolFault):
        asyncio.run(current.execute("image.inspect", {"image": 2}, available()))


@needs_bundler
def test_an_edit_restores_the_accepted_files_with_the_users_parameter_changes(tmp_path):
    """修改自由组件版本：工作区从基线的源码与清单还原，用户之后手动调过的参数写回清单的默认值。"""
    draft, _ = build(starter_files("text", {"two_tier"}, "Noto Sans CJK SC"), {"two_tier"})
    values = {**draft.default_parameters, "line2Color": "#FF0000", "fontSize": 120, "text": "新的引导\n新的主标题"}
    current = free_session(tmp_path, spec=SimpleNamespace(sprite=draft, sprite_kind="text"), base=SimpleNamespace(default_config=values))
    data = json.loads(current.files[MANIFEST])
    assert data["text"]["sample"] == "新的引导\n新的主标题" and data["text"]["font_size"] == 120
    assert {item["name"]: item["default"] for item in data["params"]}["line2Color"] == "#FF0000"
    assert current.files[CODE] == draft.instances[0].preset.code


# ---- three-layer loop ----------------------------------------------------------------------------------------------


class ScriptEnd(Exception):
    """The scripted model turns ran out."""


class FakeHarness:
    """Scripted model turns over the real session; records each layer's system prompt, options and window."""

    def __init__(self, responses, tmp_path):
        self.responses = iter(responses)
        self.roles, self.systems, self.options, self.windows, self.tools = [], [], [], [], []
        self.fonts = SimpleNamespace(guide=lambda free=False: "Font library (free)" if free else "Font library")
        self.settings = SimpleNamespace(max_steps=8, max_tooluse=6, enforce_no_progress=False, max_no_progress_turns=6, remotion_agent="autonomous")
        self.renderer = None

    async def _turn(self, system, context, tools, _budget, images, *, phase, **options):
        """Return the next scripted reply for one model role."""
        self.roles.append(phase)
        self.systems.append(system)
        self.tools.append(tools)
        self.options.append({**options, "images": list(images)})
        self.windows.append(context.messages())
        try:
            return next(self.responses)
        except StopIteration:
            raise ScriptEnd from None

    async def finalize_sprite(self, _session, identifier, _budget, _directory):
        """Publish exactly the saved candidate."""
        return {"published": identifier}


def call(identifier, name, arguments):
    """A provider function call."""
    return AssistantMessage(tool_calls=[{"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}])


def report(identifier, status="step_done", summary="done", **extra):
    """The Executor's tools_report_step call."""
    return call(identifier, "tools_report_step", {"status": status, "summary": summary, **extra})


def free_run(monkeypatch, tmp_path, responses, intent=None):
    """An AgentRun on the free-form path with the session's isolated checks replaced."""
    harness = FakeHarness(responses, tmp_path)
    original = ToolSession.__init__

    def init(self, *args, **kwargs):
        """Build the real session, then replace its isolated checks as `free_session` does."""
        original(self, *args, **kwargs)
        stub_checks(self)

    monkeypatch.setattr(ToolSession, "__init__", init)
    budget = Budget()
    run = AgentRun(harness, None, budget, tmp_path, [], lambda *_: None, intent=intent or {"original_request": {"description": "金色标题"}})
    return run, harness, budget


@needs_bundler
def test_every_executor_request_extends_the_previous_one_so_the_prefix_cache_hits(monkeypatch, tmp_path):
    """自由组件路径：委派后宿主建立一步计划，执行层改代码、检查、保存并看过联系表再汇报，宿主发布最新候选。缓存命中靠前缀不变：每次请求的系统提示与工具列表
    完全相同，窗口是上一次窗口的严格延伸（超过八组也不淘汰），请求与文件只在窗口开头给一次，尾部只有每轮变化的状态，图片不在尾部重发；最后的汇报成为给用户的回复。"""
    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(reference)
    edit = lambda identifier: call(identifier, "workspace_edit", {"path": "Effect.tsx", "edits": [{"old_str": "props.color ?? ", "new_str": "props.color ?? "}]})
    run, harness, budget = free_run(monkeypatch, tmp_path, [
        call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "金色粗体标题，逐字上浮"}),
        *[call(f"read-{index}", "workspace_read", {"path": "effect.json"}) for index in range(9)],
        edit("exec-1"),
        call("exec-2", "validate_check", {}),
        call("exec-3", "effect_save", {}),
        report("exec-4", summary="金色粗体标题，逐字上浮入场", choices=["把「逐字」理解为每个字依次上浮"], suggestions=["改成从左滑入", " "]),
    ])
    run.session.images = [reference]
    assert asyncio.run(run.plan_execute()) == {"published": run.session.latest_sprite_id}
    assert [step.id for step in run.state.plan.steps] == ["build"]
    executor = [index for index, role in enumerate(harness.roles) if role == "executor"]
    assert harness.options[0]["detail"] == "low"
    assert len({harness.systems[index] for index in executor}) == 1 and "Current host-owned" not in harness.systems[executor[0]]
    assert len({json.dumps(harness.tools[index]) for index in executor}) == 1
    for before, after in zip(executor, executor[1:]):
        assert harness.windows[after][:len(harness.windows[before])] == harness.windows[before]
    assert len(harness.windows[executor[-1]]) > 16
    # The request and the files come once, first; the tail is only what changes and no image is re-sent after the window.
    first = harness.windows[executor[0]]
    handoff = json.loads(first[0]["content"].split("\n", 1)[1])["detail"]
    assert set(handoff["files"]) == {CODE, MANIFEST} and handoff["outer_brief"] == "金色粗体标题，逐字上浮" and handoff["current_step"]["id"] == "build"
    assert handoff["user_intent"]["original_request"]["description"] == "金色标题"
    tail = harness.options[executor[-1]]["data"]
    assert len(tail) < 500 and "original_request" not in tail and "useTextContent" not in tail
    assert all(harness.options[index]["images"] == [] for index in executor)
    images = lambda window: [item for message in window if isinstance(message["content"], list) for item in message["content"] if item["type"] == "image_path"]
    assert [item["path"] for item in images(first)] == [str(reference)]
    # The turn after the save has the acceptance sheet in the window, once.
    sheet = 1
    assert len(images(harness.windows[executor[-1]])) == 1 + sheet and run.session.sheet_seen == run.session.latest_sprite_id
    assert budget.reply == {"summary": "金色粗体标题，逐字上浮入场", "choices": ["把「逐字」理解为每个字依次上浮"], "suggestions": ["改成从左滑入"]}


@needs_bundler
def test_an_outgrown_window_is_replaced_once_with_the_current_files(monkeypatch, tmp_path):
    """窗口超过预算时才整体压缩一次：换成带当前文件的交接与最新的小交换，之后继续只追加，不会每轮再压缩（那样缓存每轮失效）。"""
    monkeypatch.setattr("server.remotion_templates.agent.WINDOW_BYTES", 30_000)
    run, harness, _ = free_run(monkeypatch, tmp_path, [
        call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "x"}),
        call("exec-1", "workspace_write", {"path": "Effect.tsx", "content": "// 新代码\n" + "x" * 40_000}),
        call("exec-2", "workspace_read", {"path": "effect.json"}),
        call("exec-3", "workspace_read", {"path": "effect.json"}),
        call("exec-4", "workspace_read", {"path": "effect.json"}),
    ])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    executor = [index for index, role in enumerate(harness.roles) if role == "executor"]
    compacted = harness.windows[executor[1]]
    assert compacted[:len(harness.windows[executor[0]])] != harness.windows[executor[0]]
    assert json.loads(compacted[0]["content"].split("\n", 1)[1])["detail"]["files"][CODE].startswith("// 新代码")
    # The whole-file write is not kept; the files are in the handoff.
    assert "x" * 1000 not in json.dumps(compacted[1:], ensure_ascii=False)
    # Even with a handoff over the budget the next turns extend the window instead of compacting again.
    for before, after in zip(executor[1:], executor[2:]):
        assert harness.windows[after][:len(harness.windows[before])] == harness.windows[before]


@needs_bundler
def test_a_step_without_saving_needs_a_passing_check_of_the_files_as_they_stand(monkeypatch, tmp_path):
    """Plan 设计的只带工作区的步骤要求当前文件通过检查：没检查就汇报被拒绝，并指出下一步调用 validate_check。"""
    steps = [{"id": "draft", "goal": "先写静态外观", "tool_modules": ["workspace"], "done_when": "检查通过"},
             {"id": "finish", "goal": "保存", "tool_modules": ["effect"], "done_when": "已保存"}]
    run, _harness, _ = free_run(monkeypatch, tmp_path, [
        call("outer-1", "tools_plan_execute", {"action": "plan", "reason": "多段时间线"}),
        call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": {"goal": "简述", "steps": steps}}),
        report("exec-1"),
    ])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert "validate_check has not passed" in run.session.feedback[0]


@needs_bundler
@pytest.mark.parametrize("edit", [True, False])
def test_the_plan_designs_an_edit_from_the_accepted_versions_frames(monkeypatch, tmp_path, edit):
    """修改任务交给 Plan 时，Plan 也看到基线现在的验收联系表（标注为基线，排在参考图之后）；同一次渲染再给执行层，不重复渲染也不占预览次数。新作品没有基线帧。"""
    draft, _ = build(starter_files("text", set(), "Noto Sans CJK SC"))
    harness = FakeHarness([
        call("outer-1", "tools_plan_execute", {"action": "plan", "reason": "改动涉及多处"}),
        call("plan-1", "tools_plan_execute", {"action": "update_plan", "plan": {"goal": "简述", "steps": [{"id": "fix", "goal": "改", "tool_modules": ["workspace", "effect"], "done_when": "已保存"}]}}),
    ], tmp_path)
    original = ToolSession.__init__
    monkeypatch.setattr(ToolSession, "__init__", lambda self, *args, **kwargs: (original(self, *args, **kwargs), stub_checks(self))[0])
    options = {"base": SimpleNamespace(default_config=draft.default_parameters)} if edit else {}
    run = AgentRun(harness, SimpleNamespace(sprite=draft, sprite_kind="text") if edit else None, Budget(), tmp_path, [], lambda *_: None,
                   intent={"original_request": {"description": "x"}, "instruction": "改", **({"accepted_base": {"name": "v1"}} if edit else {})}, **options)
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    plan = harness.roles.index("plan")
    shown = harness.options[plan]["images"]
    if not edit:
        assert shown == [] and "accepted_version_images" not in harness.options[plan]["data"]
        return
    assert shown == [path for path, _label in run.session.base_frames] and len(shown) == 1
    assert "accepted_version_images" in harness.options[plan]["data"]
    assert len(run.session.calls) == 1 and run.session.renders_left == 12
    # The Executor's first turn gets the same frames in its window.
    assert [item["path"] for item in harness.windows[harness.roles.index("executor")][-1]["content"] if item["type"] == "image_path"] == [str(path) for path in shown]


def test_a_failed_base_render_leaves_the_plan_working(tmp_path):
    """基线帧渲染失败不影响修改任务：Plan 只是看不到它们，原因记入私有审计。"""
    draft, _ = build(starter_files("text", set(), "Noto Sans CJK SC")) if RENDERER_DEPS.exists() else (None, None)
    if draft is None:
        pytest.skip("needs the bundler")
    current = free_session(tmp_path, spec=SimpleNamespace(sprite=draft, sprite_kind="text"), base=SimpleNamespace(default_config=draft.default_parameters))

    async def broken(*_args, **_options):
        """The renderer is unavailable."""
        raise RuntimeError("no browser")

    current.validator = SimpleNamespace(validate_render=broken)
    asyncio.run(current.base_preview())
    assert current.base_frames == [] and current.observations == []


def test_window_images_are_encoded_only_when_the_request_is_sent(tmp_path):
    """窗口里按路径记图片，发送请求时才编码：序列化的窗口保持很小，发出去的是模型能读的 image_url。"""
    path = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), (9, 9, 9)).save(path)
    window = StepWindow()
    window.append([image_message("观察", [path])])
    assert len(window.serialize()) < 300
    messages = window.messages()
    expand_image_paths(messages, "high")
    assert [item["type"] for item in messages[0]["content"]] == ["text", "image_url"]
    assert messages[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@needs_bundler
def test_saving_then_changing_the_files_needs_another_save(monkeypatch, tmp_path):
    """保存后又改了文件，汇报被拒绝，要求重新保存并看新的联系表。"""
    run, _harness, _ = free_run(monkeypatch, tmp_path, [
        call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "x"}),
        call("exec-3", "effect_save", {}),
        call("exec-4", "workspace_edit", {"path": "Effect.tsx", "edits": [{"old_str": "props.color", "new_str": "props.color ?? \"#FFFFFF\""}]}),
        report("exec-5"),
    ])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert "files changed after the latest effect_save" in run.session.feedback[0]


@needs_bundler
def test_a_sheet_replaced_by_a_preview_before_it_was_shown_is_not_seen(monkeypatch, tmp_path):
    """保存后同一轮又做了预览：联系表还没给模型看就被预览图替换，不能算看过，汇报被拒绝并说明联系表在哪一轮。"""
    run, _harness, _ = free_run(monkeypatch, tmp_path, [
        call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "x"}),
        AssistantMessage(tool_calls=[
            {"id": "exec-3", "type": "function", "function": {"name": "effect_save", "arguments": "{}"}},
            {"id": "exec-4", "type": "function", "function": {"name": "preview_render", "arguments": "{}"}},
        ]),
        report("exec-5"),
    ])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert run.session.sheet_seen is None and "middle frame of the latest save" in run.session.feedback[0]


@needs_bundler
def test_once_a_candidate_is_saved_a_question_cannot_replace_it(monkeypatch, tmp_path):
    """先做再问：任务保存过候选后，执行层的 needs_input 被拒绝，要求带着 suggestions 完成。"""
    run, _harness, _ = free_run(monkeypatch, tmp_path, [
        call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "x"}),
        call("exec-3", "effect_save", {}),
        report("exec-4", status="needs_input", questions=["要金色还是银色？"]),
    ])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert "put the open questions into suggestions" in run.session.feedback[0]
    assert run.layer is Layer.EXECUTOR


def intent_message(instruction, images=()):
    """An Outer window entry as the runtime appends it for one task."""
    return {"role": "user", "content": json.dumps({"original_request": {"description": "金色标题"}, "instruction": instruction,
                                                    "reference_images": [{"asset_id": image, "source": "message"} for image in images]}, ensure_ascii=False)}


def test_images_of_earlier_messages_are_sent_again_newest_first():
    """多轮：之前几条消息附的参考图在后续任务里继续发送（新的在前），标明来自之前的指令；总数受上限约束，原图与本条消息的图不重复。"""
    context = Conversation()
    for index in range(MAX_REFERENCE_IMAGES + 2):
        context.append([intent_message(f"第{index}次", [f"image-{index}", "work-image"])])
    found = earlier_images(context, [{"asset_id": "work-image", "source": "work_original"}])
    assert [item["asset_id"] for item in found][:2] == [f"image-{MAX_REFERENCE_IMAGES + 1}", f"image-{MAX_REFERENCE_IMAGES}"]
    assert len(found) == MAX_REFERENCE_IMAGES - 1 and {item["source"] for item in found} == {"earlier_message"}


def test_an_earlier_image_that_can_no_longer_be_read_is_left_out(tmp_path):
    """之前消息的参考图只是上下文：读不到的那张跳过，不让整个任务失败。"""
    from server.remotion_templates.runtime import Runtime
    from server.remotion_templates.store import NotFound

    kept = tmp_path / "kept.png"

    def asset_path(identifier):
        """Only the first image is still stored."""
        if str(identifier).endswith("1"):
            return kept
        raise NotFound("asset not found")

    entries = [{"asset_id": "00000000-0000-4000-8000-000000000001"}, {"asset_id": "00000000-0000-4000-8000-000000000002"}]
    found = Runtime._earlier_paths(SimpleNamespace(store=SimpleNamespace(asset_path=asset_path)), entries)
    assert found == [(entries[0], kept)]


@needs_bundler
def test_how_earlier_tasks_ended_reaches_the_next_tasks_handoff(monkeypatch, tmp_path):
    """多轮：任务结束时宿主往 Outer 窗口写一条结果记录；下一次任务的执行层交接里带着之前每轮的要求与结果（不含当前这一轮）。"""
    job = GenerationJob.model_validate({"id": "00000000-0000-4000-8000-000000000001", "project_id": "00000000-0000-4000-8000-000000000002",
                                        "status": "succeeded", "stage": "finished", "created_at": "2026-10-30T00:00:00Z", "updated_at": "2026-10-30T00:00:00Z",
                                        "reply": {"summary": "金色标题已完成", "suggestions": ["加发光"]}})
    run, harness, _ = free_run(monkeypatch, tmp_path, [call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "加发光"})])
    for message in (intent_message("做一个金色标题"), outcome_record(job), intent_message("加上发光")):
        run.outer.append([message])
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    handoff = json.loads(run.executor_context.messages()[0]["content"].split("\n", 1)[1])["detail"]
    assert handoff["earlier_turns"] == [{"instruction": "做一个金色标题", "outcome": {"status": "succeeded", "summary": "金色标题已完成", "choices": [], "suggestions": ["加发光"]}}]


@needs_bundler
def test_an_edit_starts_from_the_accepted_versions_frames(monkeypatch, tmp_path):
    """修改自由组件版本：执行层第一轮就看到基线现在的验收联系表（不占预览次数），交接里带着还原的文件。"""
    draft, _ = build(starter_files("text", set(), "Noto Sans CJK SC"))
    harness = FakeHarness([call("outer-1", "tools_plan_execute", {"action": "delegate", "reason": "改成红色"})], tmp_path)
    original = ToolSession.__init__
    monkeypatch.setattr(ToolSession, "__init__", lambda self, *args, **kwargs: (original(self, *args, **kwargs), stub_checks(self))[0])
    spec = SimpleNamespace(sprite=draft, sprite_kind="text")
    run = AgentRun(harness, spec, Budget(), tmp_path, [], lambda *_: None, base=SimpleNamespace(default_config=draft.default_parameters),
                   intent={"original_request": {"description": "x"}, "instruction": "改成红色", "accepted_base": {"name": "v1"}})
    with pytest.raises(ScriptEnd):
        asyncio.run(run.plan_execute())
    assert run.free is True and [step.id for step in run.state.plan.steps] == ["edit"]
    assert run.session.observations and all(label.startswith("accepted version") for _path, label in run.session.observations)
    assert run.session.renders_left == 12


def test_presets_remain_the_default_and_older_composed_versions_keep_their_path(tmp_path):
    """默认仍是 Preset 组合；开启自由组件后，更早的组合版本的修改也继续走组合路径。"""
    harness = FakeHarness([], tmp_path)
    harness.settings.remotion_agent = "presets"
    assert AgentRun(harness, None, Budget(), tmp_path, [], lambda *_: None).free is False
    harness.settings.remotion_agent = "autonomous"
    composed = SimpleNamespace(sprite=SimpleNamespace(effect=None, instances=[], text=None, composition=None), sprite_kind="text")
    run = AgentRun(harness, composed, Budget(), tmp_path, [], lambda *_: None, base=SimpleNamespace(default_config={}))
    assert run.free is False and run.session.files is None


def test_a_grouped_style_parameter_is_published_with_its_group():
    """发布时自由组件样式参数的面板分组写进 SpriteParameter.group，模板编辑据此成卡片；没有分组的参数为空串。"""
    from server.remotion_templates.categories import HEX_COLOR
    from server.remotion_templates.sprites import _parameter

    grouped = _parameter("glowColor", {"type": "string", "pattern": HEX_COLOR, "title": "发光颜色", "x-imv-group": "关键词发光"}, "#FFAA00")
    assert grouped.group == "关键词发光" and grouped.label == "发光颜色"
    assert _parameter("fontSize", {"type": "number", "minimum": 24, "maximum": 240}, 96).group == ""


def test_low_detail_images_are_shrunk_for_the_routing_layer(tmp_path):
    """Outer 只拿低细节缩略图：长边缩到 512 像素以内，并标为 low。"""
    path = tmp_path / "large.png"
    Image.new("RGB", (2000, 1000), (1, 2, 3)).save(path)
    (item,) = _image_content([path], label="Reference", detail="low")
    assert item["image_url"]["detail"] == "low"
    import base64, io
    with Image.open(io.BytesIO(base64.b64decode(item["image_url"]["url"].split(",", 1)[1]))) as image:
        assert max(image.size) == 512


def test_the_reply_shows_what_was_built_and_how_it_was_read():
    """成功回复先写做了什么，再列出对歧义的理解，最后提示可继续修改；建议单独作为选项。"""
    text = reply_text(JobReply(summary="金色标题", choices=["把双栏理解为上下两行"], suggestions=["改成银色"]))
    assert text.startswith("金色标题") and "- 把双栏理解为上下两行" in text and "改成银色" not in text


# ---- real browser --------------------------------------------------------------------------------------------------

HARD_CODED = """import React from "react";
import { TextRoot, useTextContent, type EffectProps } from "imv";

export default function Effect(props: EffectProps) {
  const content = useTextContent(props);
  return <TextRoot content={content}>{() => <div style={{ color: props.color }}>示例文字</div>}</TextRoot>;
}
"""

FADES_EARLY = """import React from "react";
import { TextRoot, LineText, useTextContent, useTiming, type EffectProps } from "imv";

export default function Effect(props: EffectProps) {
  const content = useTextContent(props);
  const { frame, duration } = useTiming();
  return <TextRoot content={content}>{(line) => <div style={{ color: props.color, opacity: frame > duration * 0.4 ? 0 : 1 }}><LineText content={content} line={line} /></div>}</TextRoot>;
}
"""

FIXED_SIZE = """import React from "react";
import { TextRoot, LineText, useTextContent, type EffectProps } from "imv";

export default function Effect(props: EffectProps) {
  const content = useTextContent(props);
  return <TextRoot content={content}>{(line) => <div style={{ color: props.color, fontSize: 40 }}><LineText content={content} line={line} /></div>}</TextRoot>;
}
"""

PLAIN_KEYWORDS = """import React from "react";
import { TextRoot, useTextContent, type EffectProps } from "imv";

export default function Effect(props: EffectProps) {
  const content = useTextContent(props);
  return <TextRoot content={content}>{(line) => <div style={{ color: props.color }}>{line.text}</div>}</TextRoot>;
}
"""


SLIDING = """import React from "react";
import { interpolate } from "remotion";
import { TextRoot, LineText, useTextContent, useTiming, type EffectProps } from "imv";

export default function Effect(props: EffectProps) {
  const content = useTextContent(props);
  const { frame } = useTiming();
  const shift = interpolate(frame, [0, 15], [START, 0], { extrapolateRight: "clamp" });
  return <TextRoot content={content}>{(line) => <div style={{ color: props.color, transform: `translateX(${shift}px)` }}><LineText content={content} line={line} /></div>}</TextRoot>;
}
"""


def real_session(tmp_path, requested=frozenset()):
    """A free-form session over the real sandboxed validator."""
    settings = Settings(_env_file=None, data_dir=tmp_path, **BROWSER)
    request = {"description": "测试", **{name: True for name in requested}}
    return ToolSession(SimpleNamespace(settings=settings, renderer=Renderer(settings)), None, None, Budget(), tmp_path / "run", [], lambda *_: None,
                       {"original_request": request}, free=True)


@needs_browser
@pytest.mark.parametrize("shape", ["one line", "two bilingual groups with a keyword effect"])
def test_real_starters_pass_every_check(tmp_path, shape):
    """真实隔离浏览器：起始工程（单行；四行双语带关键词字效）通过保存的全部检查，包括契约探针，并截到验收联系表。"""
    current = real_session(tmp_path, SHAPES[shape])
    checked = asyncio.run(current.execute("validate.check", {}, available()))
    assert checked["passed"] is True, checked
    saved = asyncio.run(current.execute("effect.save", {}, available()))
    assert len(current.observations) == len(saved["review"]["frames"]) == 1 and saved["motion"]


@needs_browser
@pytest.mark.parametrize(("start", "expected"), [(700, "to the right"), (-700, "to the left")])
def test_real_saved_motion_tells_which_side_a_line_slides_in_from(tmp_path, start, expected):
    """真实隔离浏览器：两次实测里模型都把「从左滑入」写反且没发现。现在保存的回执直接量出方向：translateX 正数报告在右，负数报告在左。"""
    current = real_session(tmp_path)
    current.files[CODE] = SLIDING.replace("START", str(start))
    saved = asyncio.run(current.execute("effect.save", {}, available()))
    assert any(f"700 px {expected}" in fact for fact in saved["motion"]), saved["motion"]


@needs_browser
@pytest.mark.parametrize(("code", "extra", "expected"), [
    (HARD_CODED, None, "still shows the sample"),
    (FADES_EARLY, None, "On the middle frame"),
    (FIXED_SIZE, None, "does not make the text smaller"),
    (PLAIN_KEYWORDS, None, "no keyword hit is marked"),
    (None, {"name": "glowColor", "schema": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"}, "default": "#FFAA00", "title": "发光颜色"}, "glowColor"),
])
def test_real_contract_probes_catch_what_templates_would_break_on(tmp_path, code, extra, expected):
    """真实隔离浏览器里的契约探针：写死示例文字、中间帧前就淡出、写死字号、关键词不经 KeywordRun、声明了却没用的参数，各自被指出并给出修法。"""
    current = real_session(tmp_path)
    if code is not None:
        current.files[CODE] = code
    if extra is not None:
        data = json.loads(current.files[MANIFEST])
        data["params"].append(extra)
        current.files[MANIFEST] = json.dumps(data, ensure_ascii=False)
    checked = asyncio.run(current.execute("validate.check", {}, available()))
    assert checked["passed"] is False
    assert any(expected in item["message"] for item in checked["problems"]), checked["problems"]
