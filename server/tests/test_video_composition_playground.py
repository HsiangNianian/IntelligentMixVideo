"""操练场复用、输入门禁与恢复回归；真实事务配合隔离上游，执行 uv run --locked pytest tests/test_video_composition_playground.py。"""

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from server.video_composition import playground, service, store
from server.video_composition.schema import CompositionRequest, PlaygroundRequest
from .test_video_composition import finished, upstreams

BASE = "/api/v1/video-compositions"


@pytest.fixture(autouse=True)
def sample_files(tmp_path, monkeypatch):
    """每例使用独立样本目录，不读取随包媒体和真实任务。"""
    monkeypatch.setattr(playground, "SAMPLES", tmp_path)


def sample(composition_case, composition_settings, monkeypatch, mode="standard", **overrides):
    """登记包含旧渲染产物的成功样本，确认新任务只复制准备结果。"""
    store.initialize_schema()
    request = deepcopy(composition_case["request"])
    request["callbackUrl"] = "https://notify.example.test/old-business"
    if mode != "standard":
        request.pop("text")
        request["materials"] = [{"fileUrl": "https://media.test/a.jpg", "type": "image"}] * 3
        if mode == "materials_voice":
            request["copy"] = composition_case["request"]["text"]
        else:
            request.pop("audioUrl")
            request["processRules"] = {"videoDuration": 8}
    request = CompositionRequest.model_validate(request).model_dump(mode="json", by_alias=True)
    segments = [{**item, "text_en": f"Sentence {item['segment_id']}.", "keyword_en": ""}
                for item in composition_case["segments"]] if mode != "materials_silent" else []
    prepared = {"duration_ms": 8000, "segmentation": {"segments": segments, "title_keyword": "",
                "title_en": "Example title", "title_keyword_en": ""},
                "matches": composition_case["matches"], "material_durations": [3, 3, 3],
                "template": composition_case["template"], "remotion_result": {"url": "https://old.test/overlay"},
                "timeline": {"old": True}, "ims_job_id": "old-job", "result": {"videoUrl": "https://old.test/video"},
                "match_callback_token": "old-token", **overrides}
    record = store.create(request, composition_settings.output())
    record = store.advance(record, "completed", status="succeeded", **prepared)
    # 基准请求的回调意图不能被新任务复制；源记录保持原样。
    (playground.SAMPLES / f"{mode}.json").write_text(json.dumps({key: record[key] for key in ("task_id", "status", "data")}))
    return record


@pytest.mark.parametrize("mode", ["standard", "materials_voice", "materials_silent"])
def test_playground_reuses_prepared_content(upstreams, client, composition_case, composition_settings, monkeypatch, mode):
    """三种完整样本都跳过成本调用，创建独立任务并由原 GET 返回 playground 成片。"""
    source = sample(composition_case, composition_settings, monkeypatch, mode)
    monkeypatch.delenv("SEGMENT_MATCH_BASE_URL")
    monkeypatch.delenv("IMV_LLM_API_KEY")

    def unexpected(*args, **kwargs):
        """此入口不允许重新翻译或探测已准备的媒体。"""
        pytest.fail("操练场重复执行内容准备")

    monkeypatch.setattr(service, "translate", unexpected)
    monkeypatch.setattr(service.media, "material_durations", unexpected)
    response = client.post(f"{BASE}/playground", json={"mode": mode, "styleId": composition_case["request"]["styleId"]})
    assert response.status_code == 200, response.text
    task_id = response.json()["data"]
    assert task_id != source["task_id"] and response.headers["Location"] == f"{BASE}/{task_id}"
    result = finished(client, task_id)
    assert result["status"] == "succeeded", result
    assert result["result"]["videoUrl"].endswith(f"/imv/playground/{task_id}.mp4")
    saved = store.get(task_id)["data"]
    assert saved["playground"] == {"mode": mode, "source_task_id": source["task_id"]}
    assert saved["segmentation"] == source["data"]["segmentation"]
    assert saved["request"]["callbackUrl"] is None and "match_callback_token" not in saved
    assert saved["remotion_result"] is None and "old" not in saved["timeline"]
    assert saved["ims_job_id"] == "ims-job"
    assert upstreams["asr_calls"] == upstreams["segment_calls"] == 0
    assert upstreams["posts"] == upstreams["gets"] == []
    assert store.get(source["task_id"])["data"]["remotion_result"] == source["data"]["remotion_result"]


@pytest.mark.parametrize("invalid", [{"mode": "bad"}, {"styleId": "bad"}, {"text": "不接受输入覆盖"}])
def test_playground_rejects_invalid_request(upstreams, client, composition_case, invalid):
    """非法模式、模板编号和多余字段不能生成任务。"""
    response = client.post(f"{BASE}/playground", json={"mode": "standard", "styleId": composition_case["request"]["styleId"], **invalid})
    assert response.status_code == 422
    assert store.pending([], 100) == [] and upstreams["submits"] == []


@pytest.mark.parametrize("condition", ["missing", "unfinished", "untranslated", "missing_keyword_en", "unmatched", "wrong_mode"])
def test_playground_requires_ready_sample(upstreams, client, composition_case, composition_settings, monkeypatch, condition):
    """缺样本、未完成、缺英文、缺匹配或错误模式都明确拒绝，不回退普通合成。"""
    if condition != "missing":
        overrides = {"segmentation": {"segments": composition_case["segments"]}} if condition == "untranslated" else {"matches": []} if condition == "unmatched" else {}
        source = sample(composition_case, composition_settings, monkeypatch, "materials_voice" if condition == "wrong_mode" else "standard", **overrides)
        if condition == "missing_keyword_en":
            source["data"]["segmentation"]["segments"][0].pop("keyword_en")
        if condition == "unfinished":
            source["status"] = "processing"
        (playground.SAMPLES / "standard.json").write_text(json.dumps({key: source[key] for key in ("task_id", "status", "data")}))
    response = client.post(f"{BASE}/playground", json={"mode": "standard", "styleId": composition_case["request"]["styleId"]})
    assert response.status_code == 409
    assert upstreams["asr_calls"] == upstreams["segment_calls"] == 0 and upstreams["submits"] == []


@pytest.mark.anyio
async def test_playground_freezes_template_and_resumes(upstreams, composition_case, composition_settings, composition_runtime, monkeypatch):
    """入队后样本与模板变化不影响任务，恢复仍用固定快照且不会复用旧 IMS 句柄。"""
    from server.template.schema import TemplateSave
    from server.template.store import get_template, save_template

    source = sample(composition_case, composition_settings, monkeypatch)
    original = get_template(composition_case["request"]["styleId"])
    chosen = save_template(TemplateSave.model_validate({**original.model_dump(include=set(TemplateSave.model_fields)),
                                                       "template_id": None, "name": "新选择的模板"}))
    payload = PlaygroundRequest(mode="standard", styleId=chosen.template_id)
    record = await composition_runtime.accept_playground(payload, "http://testserver")
    before = deepcopy(record["data"]["template"])
    assert before["name"] == "新选择的模板"
    assert record["data"]["request"]["styleId"] != source["data"]["request"]["styleId"]
    template = get_template(payload.style_id)
    save_template(TemplateSave.model_validate({**template.model_dump(include=set(TemplateSave.model_fields)), "name": "修改后的模板"}))
    (playground.SAMPLES / "standard.json").unlink()
    assert record["data"]["playground"]["source_task_id"] == source["task_id"]
    await composition_runtime._execute(store.get(record["task_id"]))
    saved = store.get(record["task_id"])
    assert saved["status"] == "succeeded", saved["data"].get("error")
    assert saved["data"]["template"] == before
    assert upstreams["asr_calls"] == upstreams["segment_calls"] == 0


def test_bundled_sample_needs_no_source_database(monkeypatch):
    """实际封存的 IP口播样本中英文完整，换库部署无需任务 ID 配置，也不读取来源任务。"""
    monkeypatch.setattr(playground, "SAMPLES", Path(playground.__file__).with_name("playground_samples"))
    monkeypatch.setattr(store, "get", lambda *args: pytest.fail("固定样本不应查询来源任务"))
    style_id = uuid4()
    prepared = playground.prepared_sample(PlaygroundRequest(mode="standard", styleId=style_id))
    assert prepared["request"]["styleId"] == str(style_id) and prepared["request"]["callbackUrl"] is None
    assert prepared["segmentation"]["segments"] and prepared["segmentation"]["title_en"]
    assert len(prepared["matches"]) == len(prepared["segmentation"]["segments"])
    assert set(prepared) == {"request", "duration_ms", "segmentation", "matches", "playground"}
