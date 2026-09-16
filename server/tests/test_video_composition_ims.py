"""IMS 官方 SDK 请求映射和配置测试；截获 call_api_async，不产生云端调用或账单。"""

import json

import pytest

from server.video_composition.ims import IMS, submission
from server.video_composition.settings import Settings


@pytest.mark.anyio
async def test_official_sdk_async_request_and_response(composition_settings, monkeypatch):
    """通过真实 SDK 模型核对 API 动作、token、Timeline、输出规格和实际返回层级。"""
    provider = IMS(composition_settings)
    calls = []

    async def call(params, request, runtime):
        """只拦截最底层 API 发送，模型构造、字段转换和反序列化仍由官方 SDK 执行。"""
        calls.append((params, request, runtime))
        if params.action == "SubmitMediaProducingJob":
            return {"body": {"JobId": "ims-123"}, "statusCode": 200}
        return {"body": {"MediaProducingJob": {"JobId": "ims-123", "Status": "Success", "Duration": 3.125,
                                               "MediaURL": "https://test-output.oss-cn-shanghai.aliyuncs.com/actual.mp4"}}, "statusCode": 200}

    monkeypatch.setattr(provider.client, "call_api_async", call)
    timeline = {"VideoTracks": [{"VideoTrackClips": [{"MediaURL": "https://media.test/片.mp4?x=a%2Fb"}]}]}
    payload = submission("stable-task-uuid", timeline, composition_settings.output(), "test-vod.oss-cn-shanghai.aliyuncs.com")
    assert await provider.submit(payload) == {"JobId": "ims-123"}
    result = await provider.get("ims-123")
    assert result["MediaProducingJob"]["Duration"] == 3.125
    params, request, runtime = calls[0]
    assert params.action == "SubmitMediaProducingJob" and params.method == "POST"
    assert request.query["ClientToken"] == "stable-task-uuid"
    assert json.loads(request.body["Timeline"]) == timeline
    output = json.loads(request.query["OutputMediaConfig"])
    assert (output["Width"], output["Height"], output["Video"]["Fps"]) == (1080, 1920, 30)
    assert output["StorageLocation"] == "test-vod.oss-cn-shanghai.aliyuncs.com"
    assert output["FileName"] == "stable-task-uuid.mp4"
    assert request.query["OutputMediaTarget"] == "vod-media"
    assert output["VodTemplateGroupId"] == "VOD_NO_TRANSCODE"
    assert provider.client._retry_options.retryable is False and runtime.connect_timeout == 1000
    assert calls[1][0].action == "GetMediaProducingJob" and calls[1][1].query["JobId"] == "ims-123"
    assert provider.client._region_id == "cn-shanghai"
    assert provider.client._endpoint == "ice.cn-shanghai.aliyuncs.com"


@pytest.mark.parametrize("key,value", [
    ("COMPOSITION_FPS", "0"), ("COMPOSITION_FPS", "61"), ("COMPOSITION_WIDTH", "1"),
    ("COMPOSITION_CONCURRENCY", "0"), ("COMPOSITION_POLL_SECONDS", "nan"),
    ("COMPOSITION_MATCH_WAIT_SECONDS", "-1"),
    ("SEGMENT_MATCH_BASE_URL", "https://user:pass@host.test"),
    ("SEGMENT_MATCH_BASE_URL", "https://host.test/?token=secret"),
    ("SEGMENT_MATCH_AUTHORIZATION", "Bearer test\r\nx: secret"), ("ALIBABA_CLOUD_ACCESS_KEY_SECRET", " "),
    ("MIX_VIDEO_ALIYUN_IMS_ENDPOINT", "https://ice.cn-shanghai.aliyuncs.com"),
    ("MIX_VIDEO_ALIYUN_IMS_ENDPOINT", "ice.cn-beijing.aliyuncs.com"),
    ("MIX_VIDEO_ALIYUN_IMS_ENDPOINT", "example.com"), ("MIX_VIDEO_ALIYUN_IMS_REGION_ID", ""),
])
def test_settings_reject_invalid_configuration(composition_settings, monkeypatch, key, value):
    """数值范围、URL、地域和凭证验证失败时隐藏具体配置输入。"""
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError) as error:
        Settings()
    assert "input_value" not in str(error.value)


def test_settings_env_priority_and_default_output(composition_settings, monkeypatch, tmp_path):
    """固定的 server/.env 与环境变量复用切片启动方式，输出可覆盖且快照不含鉴权。"""
    (tmp_path / "server/.env").write_text("COMPOSITION_WIDTH=720\nCOMPOSITION_HEIGHT=1280\n", encoding="utf-8")
    monkeypatch.setenv("COMPOSITION_WIDTH", "1080")
    settings = Settings()
    assert settings.output() == {"width": 1080, "height": 1280, "fps": 30, "region_id": "cn-shanghai"}
    assert "secret" not in json.dumps(settings.output())


def test_defaults_do_not_require_operational_environment(composition_settings, monkeypatch):
    """移除运行参数后，输出规格、并发和全部等待预算使用既有代码默认值。"""
    import os

    for key in list(os.environ):
        if key.startswith("COMPOSITION_"):
            monkeypatch.delenv(key)
    settings = Settings()
    assert settings.output() == {"width": 1080, "height": 1920, "fps": 30, "region_id": "cn-shanghai"}
    assert (settings.composition_concurrency, settings.composition_http_timeout_seconds,
            settings.composition_poll_seconds) == (2, 30, 2)
    assert (settings.composition_asr_wait_seconds, settings.composition_match_wait_seconds,
            settings.composition_render_wait_seconds) == (1800, 1800, 3600)


def test_configured_region_and_endpoint_reach_sdk_and_output(composition_settings, monkeypatch):
    """新增 MIX_VIDEO 配置实际控制 SDK 与输出快照地域，不再使用写死的上海值。"""
    monkeypatch.setenv("MIX_VIDEO_ALIYUN_IMS_REGION_ID", "cn-beijing")
    monkeypatch.setenv("MIX_VIDEO_ALIYUN_IMS_ENDPOINT", "ice.cn-beijing.aliyuncs.com")
    settings = Settings()
    provider = IMS(settings)
    assert provider.client._region_id == "cn-beijing"
    assert provider.client._endpoint == "ice.cn-beijing.aliyuncs.com"
    assert settings.output()["region_id"] == "cn-beijing"
    restored = IMS(settings, region_id="cn-shanghai")
    assert restored.client._region_id == "cn-shanghai"
    assert restored.client._endpoint == "ice.cn-shanghai.aliyuncs.com"


@pytest.mark.parametrize("suffix", ["", "/", "/api/v1", "/api/v1/"])
def test_matching_base_preserves_deployment_prefix(composition_settings, monkeypatch, suffix):
    """服务根地址和完整 API Base 均解析为相同部署前缀，不重复 /api/v1。"""
    monkeypatch.setenv("SEGMENT_MATCH_BASE_URL", f"https://host.test/feisu/assets-library{suffix}")
    assert Settings().match_base_url == "https://host.test/feisu/assets-library"


@pytest.mark.anyio
@pytest.mark.parametrize("default", [False, True])
async def test_vod_storage_selection(composition_settings, monkeypatch, default):
    """排除自有 OSS 和异常存储；优先默认 VOD，否则沿用首个可用项。"""
    provider = IMS(composition_settings)

    async def call(params, request, runtime):
        """返回真实 GetStorageList 模型结构，确保调用只读接口。"""
        assert params.action == "GetStorageList"
        return {"body": {"StorageInfoList": [
            {"StorageType": "user_oss_bucket", "Status": "normal", "StorageLocation": "user", "DefaultStorage": True},
            {"StorageType": "vod_oss_bucket", "Status": "abnormal", "StorageLocation": "bad"},
            {"StorageType": "vod_oss_bucket", "Status": "normal", "StorageLocation": "first"},
            {"StorageType": "vod_oss_bucket", "Status": "Normal", "StorageLocation": "second", "DefaultStorage": default},
        ]}, "statusCode": 200}

    monkeypatch.setattr(provider.client, "call_api_async", call)
    assert await provider.storage_location() == ("second" if default else "first")


@pytest.mark.anyio
async def test_no_vod_storage_fails(composition_settings, monkeypatch):
    """没有可用存储时明确失败，不创建 Bucket 或使用未经选择的路径。"""
    provider = IMS(composition_settings)

    async def call(params, request, runtime):
        """返回空存储列表。"""
        return {"body": {"StorageInfoList": []}, "statusCode": 200}

    monkeypatch.setattr(provider.client, "call_api_async", call)
    with pytest.raises(ValueError, match="没有可用"):
        await provider.storage_location()


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["success", "query-url", "empty", "deleted", "wrong-id", "bad-url"])
async def test_source_url_from_official_media_info(composition_settings, monkeypatch, case):
    """通过官方 IMS 模型取源文件签名地址，拒绝缺失、已删除、错媒资和非法 URL。"""
    provider = IMS(composition_settings)

    async def call(params, request, runtime):
        """校验刷新参数并返回官方 FileUrl 字段，不使用 InputURL 或封面图。"""
        assert params.action == "GetMediaInfo"
        assert request.query["MediaId"] == "ims-media"
        assert request.query["OutputType"] == "oss" and request.query["AuthTimeout"] == "3600"
        return {"body": {"MediaInfo": {
            "MediaId": "other" if case == "wrong-id" else "ims-media",
            "FileInfoList": [] if case == "empty" else [{"FileBasicInfo": {
                "FileType": "source_file", "FileStatus": "Deleted" if case == "deleted" else "Normal",
                "FileUrl": "invalid" if case == "bad-url" else (
                    "https://media.test/out.mp4?source=http://origin.test/a&Signature=a%2Fb" if case == "query-url"
                    else "http://media.test/out.mp4?Signature=a%2Fb&Expires=123"
                ),
            }}],
        }}, "statusCode": 200}

    monkeypatch.setattr(provider.client, "call_api_async", call)
    if case == "success":
        assert await provider.result_url("ims-media") == "https://media.test/out.mp4?Signature=a%2Fb&Expires=123"
    elif case == "query-url":
        assert await provider.result_url("ims-media") == "https://media.test/out.mp4?source=http://origin.test/a&Signature=a%2Fb"
    else:
        with pytest.raises(ValueError):
            await provider.result_url("ims-media")


@pytest.mark.parametrize('value,expected', [
    ('', ''), ('https://public.example.test/', 'https://public.example.test'),
    ('https://public.example.test/imv/', 'https://public.example.test/imv'),
    ('http://127.0.0.1:8000/imv', 'http://127.0.0.1:8000/imv'),
])
def test_public_base_accepts_empty_or_deployment_prefix(composition_settings, monkeypatch, value, expected):
    """空配置保留请求地址回退，合法地址只去尾部斜线并保留协议、端口和部署前缀。"""
    monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', value)
    assert Settings().composition_public_base_url == expected


@pytest.mark.parametrize('value', [
    'public.example.test', 'ftp://public.example.test', 'https://user:password@public.example.test',
    'https://public.example.test/?token=secret', 'https://public.example.test/#fragment',
    ' https://public.example.test', 'https://public.example.test/invalid path',
])
def test_public_base_rejects_invalid_urls(composition_settings, monkeypatch, value):
    """拒绝错误协议、凭证、查询、片段和空白，避免生成无法回调或暴露凭证的地址。"""
    monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', value)
    with pytest.raises(ValueError):
        Settings()


def test_public_base_environment_overrides_dotenv(composition_settings, monkeypatch, tmp_path):
    """从固定的 server/.env 加载公网地址，进程环境可覆盖或显式留空恢复请求地址。"""
    (tmp_path / 'server/.env').write_text('COMPOSITION_PUBLIC_BASE_URL=https://from-file.example.test/prefix/\n')
    assert Settings().composition_public_base_url == 'https://from-file.example.test/prefix'
    monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', 'https://from-env.example.test/')
    assert Settings().composition_public_base_url == 'https://from-env.example.test'
    monkeypatch.setenv('COMPOSITION_PUBLIC_BASE_URL', '')
    assert Settings().composition_public_base_url == ''
