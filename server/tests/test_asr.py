"""验证异步 ASR 请求、取消清理、HTTPS 边界、自动配置与 JSON 输出，隔离真实密钥和云服务。

仓库根目录运行：uv run --locked --project server pytest server/tests/test_asr.py -v
"""

import asyncio
import json
import runpy
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


def respond(http, *documents):
    """依次配置未读取的 JSON 响应，返回响应供资源关闭检查。"""
    responses = [
        httpx.Response(200, stream=httpx.ByteStream(json.dumps(document).encode()))
        for document in documents
    ]
    http.side_effect = responses
    return responses


async def test_returns_original_json_and_does_not_send_key_to_download(
    asr, asr_env, asr_http, asr_responses, capsys
):
    """等待 RUNNING 后返回原始结果；只向 ASR 发密钥，并关闭所有响应。"""
    submitted, done, document = asr_responses
    responses = respond(
        asr_http, submitted, {"output": {"task_status": "RUNNING"}}, done, document
    )
    assert await asr.transcribe("https://audio.example/tts.wav") == document
    calls = asr_http.call_args_list
    request = calls[0].args[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
    )
    assert json.loads(request.content) == {
        "model": "fun-asr",
        "input": {"file_urls": ["https://audio.example/tts.wav"]},
        "parameters": {},
    }
    assert request.headers["X-DashScope-Async"] == "enable"
    assert request.headers["Content-Type"] == "application/json"
    assert all(
        call.args[0].headers["Authorization"] == "Bearer test-key"
        for call in calls[:-1]
    )
    assert str(calls[1].args[0].url) == (
        "https://dashscope.aliyuncs.com/api/v1/tasks/task-123"
    )
    assert "Authorization" not in calls[-1].args[0].headers
    assert all(response.is_closed for response in responses)
    asr.asyncio.sleep.assert_awaited_once_with(2)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "task-123" in captured.err


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/audio.wav",
        "invalid",
        "https://",
        "http://audio.example/tts.wav",
        "https://user:password@audio.example/tts.wav",
        "https://:password@audio.example/tts.wav",
        "https://@audio.example/tts.wav",
        "https://:@audio.example/tts.wav",
    ],
)
async def test_invalid_url_fails_before_network(asr, asr_env, asr_http, url):
    """非 HTTPS、缺少主机或包含凭证的音频地址在提交前失败。"""
    with pytest.raises(ValueError, match="HTTPS"):
        await asr.transcribe(url)
    asr_http.assert_not_called()


@pytest.mark.parametrize(
    "wait_seconds", [0, -1, float("nan"), float("inf"), -float("inf")]
)
async def test_invalid_wait_seconds_fails_before_network(
    asr, asr_env, asr_http, wait_seconds
):
    """非正数或非有限等待时间在提交前报错，避免无效调用产生云端任务。"""
    with pytest.raises(ValueError, match="wait_seconds"):
        await asr.transcribe("https://audio.example/tts.wav", wait_seconds=wait_seconds)
    asr_http.assert_not_called()


@pytest.mark.parametrize("value", ["", " "])
async def test_empty_api_key_fails_before_network(asr, asr_env, asr_http, value):
    """空值和全空格密钥明确报错，不提交任务。"""
    asr_env.dashscope_api_key = SecretStr(value)
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        await asr.transcribe("https://audio.example/tts.wav")
    asr_http.assert_not_called()


async def test_absent_api_key_fails_before_network(asr, asr_http, monkeypatch):
    """缺失密钥使用空默认值，并在调用时明确提示所需环境变量。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setattr(asr, "settings", asr.ASRSettings(_env_file=None))
    with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
        await asr.transcribe("https://audio.example/tts.wav")
    asr_http.assert_not_called()


@pytest.mark.parametrize("environment_override", [False, True])
@pytest.mark.parametrize("source_env_present", [False, True])
async def test_config_is_loaded_automatically_once(
    asr, asr_http, asr_responses, tmp_path, monkeypatch, environment_override,
    source_env_present,
):
    """源码配置缺失时不回退 cwd；密钥只加载一次且环境优先，端点始终为北京。"""
    script = tmp_path / "server/src/server/asr/asr.py"
    script.parent.mkdir(parents=True)
    script.write_text(Path(asr.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    env_file = tmp_path / "server/.env"
    if source_env_present:
        env_file.write_text(
            'DASHSCOPE_API_KEY=file-key\nASR_BASE_URL="https://file.example/api/v1/"\n',
            encoding="utf-8",
        )
    (tmp_path / "server/src/.env").write_text(
        "DASHSCOPE_API_KEY=cwd-key\nASR_BASE_URL=https://cwd.example/api/v1\n",
        encoding="utf-8",
    )
    for field, value in {
        "ASR_BASE_URL": "https://environment.example/api/v1",
        "DASHSCOPE_API_KEY": "environment-key",
    }.items():
        if environment_override:
            monkeypatch.setenv(field, value)
        else:
            monkeypatch.delenv(field, raising=False)
    monkeypatch.chdir(tmp_path / "server/src")
    module = runpy.run_path(str(script), run_name="server.asr._config_test")
    env_file.write_text("DASHSCOPE_API_KEY=changed-key\n", encoding="utf-8")
    if not source_env_present and not environment_override:
        with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
            await module["transcribe"]("https://audio.example/tts.wav")
        asr_http.assert_not_called()
        return
    respond(asr_http, *asr_responses)
    assert (
        await module["transcribe"]("https://audio.example/tts.wav") == asr_responses[-1]
    )
    request = asr_http.call_args_list[0].args[0]
    source = "environment" if environment_override else "file"
    assert str(request.url) == (
        "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
    )
    assert request.headers["Authorization"] == f"Bearer {source}-key"


@pytest.mark.parametrize("status", ["FAILED", "CANCELED", "UNKNOWN"])
async def test_terminal_failure(asr, asr_env, asr_http, asr_responses, status):
    """非成功终态立即报错，不继续轮询或重复提交。"""
    respond(asr_http, asr_responses[0], {"output": {"task_status": status}})
    with pytest.raises(RuntimeError, match=status):
        await asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


@pytest.mark.parametrize(
    "results", [[], [{"subtask_status": "FAILED", "code": "FILE_DOWNLOAD_FAILED"}]]
)
async def test_failed_or_missing_subtask(
    asr, asr_env, asr_http, asr_responses, results
):
    """任务成功但子任务失败或缺失时，不能误报转写成功。"""
    respond(
        asr_http,
        asr_responses[0],
        {"output": {"task_status": "SUCCEEDED", "results": results}},
    )
    with pytest.raises(RuntimeError, match="文件识别失败"):
        await asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


async def test_missing_result_url(asr, asr_env, asr_http, asr_responses):
    """成功子任务缺少结果地址时，报错而不发起下载。"""
    asr_responses[1]["output"]["results"][0].pop("transcription_url")
    respond(asr_http, *asr_responses)
    with pytest.raises(RuntimeError, match="缺少转写结果地址"):
        await asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


@pytest.mark.parametrize(
    "result_url",
    [
        "http://results.example/result.json",
        "https://",
        "https://user:password@results.example/result.json",
        "https://:password@results.example/result.json",
        "https://@results.example/result.json",
        "https://:@results.example/result.json",
    ],
)
async def test_invalid_result_url_is_not_downloaded(
    asr, asr_env, asr_http, asr_responses, result_url
):
    """云端返回不安全或不完整的地址时，不尝试下载转写结果。"""
    asr_responses[1]["output"]["results"][0]["transcription_url"] = result_url
    respond(asr_http, *asr_responses)
    with pytest.raises(ValueError, match="HTTPS"):
        await asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


async def test_timeout_keeps_task_id(asr, asr_env, asr_http, asr_responses, mocker):
    """模拟时钟越过预算后停止等待，异常保留任务 ID。"""
    respond(asr_http, asr_responses[0], {"output": {"task_status": "RUNNING"}})
    mocker.patch.object(asr, "monotonic", side_effect=[0, 0, 1801])
    with pytest.raises(TimeoutError, match="task-123"):
        await asr.transcribe("https://audio.example/tts.wav")
    assert asr_http.call_count == 2


@pytest.mark.parametrize("phase", ["response", "polling"])
async def test_in_flight_transcription_yields_and_can_be_cancelled(
    asr, asr_env, asr_http, asr_responses, phase
):
    """读取响应或等待轮询时让出事件循环；取消传播、关闭响应且不重复提交。"""
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        """模拟尚未返回完整响应体的连接，用于验证读取期间的取消清理。"""

        async def __aiter__(self):
            """通知测试已开始读取，然后异步等待释放或取消。"""
            entered.set()
            await release.wait()
            yield b"{}"

    async def wait_for_poll(delay):
        """模拟轮询间隔，保持协程挂起以允许测试从外部取消。"""
        assert delay == 2
        entered.set()
        await release.wait()

    if phase == "response":
        responses = [httpx.Response(200, stream=WaitingStream())]
        asr_http.side_effect = responses
    else:
        responses = respond(
            asr_http, asr_responses[0], {"output": {"task_status": "RUNNING"}}
        )
        asr.asyncio.sleep.side_effect = wait_for_poll

    task = asyncio.create_task(asr.transcribe("https://audio.example/tts.wav"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert all(response.is_closed for response in responses)
        assert asr_http.call_count == len(responses)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("remaining", [0.1, 0, -0.1])
async def test_poll_sleep_respects_remaining_budget(
    asr, asr_env, asr_http, asr_responses, mocker, remaining
):
    """轮询只休眠剩余预算；请求耗尽或超过预算时立即停止，保留任务 ID。"""
    respond(asr_http, asr_responses[0], {"output": {"task_status": "RUNNING"}})
    mocker.patch.object(asr, "monotonic", side_effect=[0, 0, 1 - remaining, 1])
    with pytest.raises(TimeoutError, match="task-123"):
        await asr.transcribe("https://audio.example/tts.wav", wait_seconds=1)
    assert asr_http.call_count == 2
    if remaining > 0:
        asr.asyncio.sleep.assert_awaited_once_with(pytest.approx(remaining))
    else:
        asr.asyncio.sleep.assert_not_called()


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_http_error_closes_response_without_resubmitting(
    asr, asr_env, asr_http, status
):
    """HTTP 错误关闭响应后传播，不重复提交可能计费的任务。"""
    response = httpx.Response(status, stream=httpx.ByteStream(b'{"code":"error"}'))
    asr_http.side_effect = [response]
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await asr.transcribe("https://audio.example/tts.wav")
    assert caught.value.response.status_code == status
    assert response.is_closed
    assert asr_http.call_count == 1


async def test_redirect_is_not_followed(asr, asr_env, asr_http):
    """HTTPS 响应重定向到 HTTP 时停止，防止绕过 URL 校验。"""
    response = httpx.Response(
        302,
        headers={"Location": "http://example.com/unsafe"},
        stream=httpx.ByteStream(b""),
    )
    asr_http.side_effect = [response]
    with pytest.raises(httpx.HTTPStatusError):
        await asr.transcribe("https://audio.example/tts.wav")
    assert response.is_closed
    assert asr_http.call_count == 1


async def test_invalid_json_closes_response(asr, asr_env, asr_http):
    """无效 JSON 解析失败时也关闭响应，保留原解析异常。"""
    response = httpx.Response(200, stream=httpx.ByteStream(b"not-json"))
    asr_http.side_effect = [response]
    with pytest.raises(json.JSONDecodeError):
        await asr.transcribe("https://audio.example/tts.wav")
    assert response.is_closed


async def test_empty_transcripts_are_returned(asr, asr_env, asr_http, asr_responses):
    """无语音时保留服务返回的空结果，不制造文字或时间戳。"""
    asr_responses[-1] = {"transcripts": []}
    respond(asr_http, *asr_responses)
    assert await asr.transcribe("https://audio.example/tts.wav") == {"transcripts": []}


def test_main_writes_json(
    asr_env, asr_http, asr_responses, tmp_path, monkeypatch, capsys
):
    """包的命令行入口在临时目录生成中文可读的 JSON，stdout 不打印结果。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["server.asr", "https://audio.example/tts.wav"])
    asr_responses[-1] = {"transcripts": [{"text": "你好，世界。"}]}
    respond(asr_http, *asr_responses)
    runpy.run_module("server.asr", run_name="__main__")
    content = (tmp_path / "asr_result.json").read_text(encoding="utf-8")
    assert json.loads(content) == asr_responses[-1]
    assert "你好，世界。" in content
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("arguments, status", [([], 2), (["--help"], 0)])
def test_main_usage_does_not_transcribe(
    asr_env, asr_http, monkeypatch, capsys, arguments, status
):
    """缺少音频参数时显示用法并失败，--help 正常退出；两者均不发请求或写结果。"""
    monkeypatch.setattr(sys, "argv", ["server.asr", *arguments])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("server.asr", run_name="__main__")
    assert caught.value.code == status
    captured = capsys.readouterr()
    assert "audio_url" in (captured.err if status else captured.out)
    asr_http.assert_not_called()
    assert not Path("asr_result.json").exists()
