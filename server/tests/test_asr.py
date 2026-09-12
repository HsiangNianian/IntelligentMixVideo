"""验证 ASR 请求、失败边界、配置与 JSON 输出，隔离真实密钥和云服务。

仓库根目录运行：uv run --locked --project server pytest server/tests/test_asr.py -v
"""

import io
import json
import runpy
import sys
from pathlib import Path
from urllib.error import HTTPError

import dotenv
import pytest

from server import asr


def respond(http, *documents):
    """依次配置 JSON 响应，并返回流供资源关闭检查。"""
    streams = [io.BytesIO(json.dumps(document).encode()) for document in documents]
    http.side_effect = streams
    return streams


def test_returns_original_json_and_does_not_send_key_to_download(
    asr_env, asr_http, asr_responses, capsys
):
    """等待 RUNNING 后返回原始结果；只向 ASR 发密钥，并关闭所有响应流。"""
    submitted, done, document = asr_responses
    streams = respond(
        asr_http, submitted, {"output": {"task_status": "RUNNING"}}, done, document
    )
    assert asr.transcribe("https://audio.example/tts.wav") == document
    calls = asr_http.call_args_list
    request = calls[0].args[0]
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "model": "fun-asr",
        "input": {"file_urls": ["https://audio.example/tts.wav"]},
        "parameters": {},
    }
    assert request.get_header("X-dashscope-async") == "enable"
    assert request.get_header("Content-type") == "application/json"
    assert all(
        call.args[0].get_header("Authorization") == "Bearer test-key"
        for call in calls[:-1]
    )
    assert (
        calls[1].args[0].full_url
        == "https://dashscope.aliyuncs.com/api/v1/tasks/task-123"
    )
    assert calls[-1].args[0].get_header("Authorization") is None
    assert all(stream.closed for stream in streams)
    asr.time.sleep.assert_called_once_with(2)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "task-123" in captured.err


@pytest.mark.parametrize("url", ["file:///tmp/audio.wav", "invalid", "https://"])
def test_invalid_url_fails_before_network(asr_env, asr_http, url):
    """非 HTTP(S) 或缺少主机的地址在提交前失败。"""
    with pytest.raises(ValueError, match="音频 URL"):
        asr.transcribe(url)
    asr_http.assert_not_called()


@pytest.mark.parametrize("field", ["DASHSCOPE_API_KEY", "ASR_BASE_URL"])
@pytest.mark.parametrize("value", [None, "", " "])
def test_missing_config_fails_before_network(
    asr_env, asr_http, monkeypatch, field, value
):
    """缺失、空值和全空格配置明确报错，不提交任务。"""
    if value is None:
        monkeypatch.delenv(field)
    else:
        monkeypatch.setenv(field, value)
    with pytest.raises(ValueError, match=field):
        asr.transcribe("https://audio.example/tts.wav")
    asr_http.assert_not_called()


@pytest.mark.parametrize("mode", ["default", "explicit", "environment"])
def test_config_source(asr_env, asr_http, asr_responses, monkeypatch, mode):
    """从根目录或显式文件读配置，进程环境变量优先于文件。"""
    config_file = asr_env if mode != "explicit" else asr_env.with_name("custom.env")
    config_file.write_text(
        'DASHSCOPE_API_KEY=file-key\nASR_BASE_URL="https://file.example/api/v1/"\n',
        encoding="utf-8",
    )
    if mode != "environment":
        monkeypatch.delenv("DASHSCOPE_API_KEY")
        monkeypatch.delenv("ASR_BASE_URL")
    respond(asr_http, *asr_responses)
    kwargs = {"env_file": config_file} if mode == "explicit" else {}
    asr.transcribe("https://audio.example/tts.wav", **kwargs)
    request = asr_http.call_args_list[0].args[0]
    expected_base = (
        "https://dashscope.aliyuncs.com/api/v1"
        if mode == "environment"
        else "https://file.example/api/v1"
    )
    expected_key = "test-key" if mode == "environment" else "file-key"
    assert request.full_url == f"{expected_base}/services/audio/asr/transcription"
    assert request.get_header("Authorization") == f"Bearer {expected_key}"


@pytest.mark.parametrize("status", ["FAILED", "CANCELED", "UNKNOWN"])
def test_terminal_failure(asr_env, asr_http, asr_responses, status):
    """非成功终态立即报错，不继续轮询或重复提交。"""
    respond(asr_http, asr_responses[0], {"output": {"task_status": status}})
    with pytest.raises(RuntimeError, match=status):
        asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


@pytest.mark.parametrize(
    "results", [[], [{"subtask_status": "FAILED", "code": "FILE_DOWNLOAD_FAILED"}]]
)
def test_failed_or_missing_subtask(asr_env, asr_http, asr_responses, results):
    """任务成功但子任务失败或缺失时，不能误报转写成功。"""
    respond(
        asr_http,
        asr_responses[0],
        {"output": {"task_status": "SUCCEEDED", "results": results}},
    )
    with pytest.raises(RuntimeError, match="文件识别失败"):
        asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


def test_missing_result_url(asr_env, asr_http, asr_responses):
    """成功子任务缺少结果地址时，报错而不发起下载。"""
    asr_responses[1]["output"]["results"][0].pop("transcription_url")
    respond(asr_http, *asr_responses)
    with pytest.raises(RuntimeError, match="缺少转写结果地址"):
        asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


def test_timeout_keeps_task_id(asr_env, asr_http, asr_responses, monkeypatch):
    """模拟时钟越过预算后停止等待，异常保留任务 ID。"""
    respond(asr_http, asr_responses[0], {"output": {"task_status": "RUNNING"}})
    ticks = iter([0, 0, 1801])
    monkeypatch.setattr(asr.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="task-123"):
        asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


def test_http_error_propagates_without_resubmitting(asr_env, asr_http):
    """HTTP 错误向调用方传播，不重复提交可能计费的任务。"""
    asr_http.side_effect = HTTPError(
        "https://example.com", 401, "Unauthorized", {}, None
    )
    with pytest.raises(HTTPError) as caught:
        asr.transcribe("https://audio.example/tts.wav")
    assert caught.value.code == 401
    assert asr_http.call_count == 1


def test_invalid_json_closes_response(asr_env, asr_http):
    """无效 JSON 解析失败时也关闭响应，保留原解析异常。"""
    response = io.BytesIO(b"not-json")
    asr_http.side_effect = [response]
    with pytest.raises(json.JSONDecodeError):
        asr.transcribe("https://audio.example/tts.wav")
    assert response.closed


def test_empty_transcripts_are_returned(asr_env, asr_http, asr_responses):
    """无语音时保留服务返回的空结果，不制造文字或时间戳。"""
    asr_responses[-1] = {"transcripts": []}
    respond(asr_http, *asr_responses)
    assert asr.transcribe("https://audio.example/tts.wav") == {"transcripts": []}


def test_main_writes_json(tmp_path, monkeypatch, capsys):
    """命令行在临时目录生成中文可读的 JSON，stdout 不打印结果。"""
    script = Path(asr.__file__).resolve()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(script), "https://audio.example/tts.wav"])
    monkeypatch.setenv("ASR_BASE_URL", "https://example.com/api/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    # runpy 独立加载模块，用假配置读取器隔离仓库中的真实 .env。
    monkeypatch.setattr(dotenv, "dotenv_values", lambda _: {})
    document = {"transcripts": [{"text": "你好，世界。"}]}
    responses = iter(
        [
            {"output": {"task_id": "task-123"}},
            {
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [
                        {
                            "subtask_status": "SUCCEEDED",
                            "transcription_url": "https://example.com/result.json",
                        }
                    ],
                }
            },
            document,
        ]
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(next(responses)).encode()),
    )
    runpy.run_path(str(script), run_name="__main__")
    content = (tmp_path / "asr_result.json").read_text(encoding="utf-8")
    assert json.loads(content) == document
    assert "你好，世界。" in content
    assert capsys.readouterr().out == ""
