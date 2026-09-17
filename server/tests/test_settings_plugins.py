"""配置目录只公开声明，并可插拔真实 ASR 模型；执行 uv run --locked pytest tests/test_settings_plugins.py。"""

from server import settings_plugins


def test_catalog_excludes_server_values(client, monkeypatch):
    """即使服务端配置含密钥，目录仍只含切片的五个客户端字段和代码默认值。"""
    monkeypatch.setenv("IMV_LLM_API_KEY", "server-secret")
    monkeypatch.setenv("IMV_LLM_MODEL", "private-model")
    response = client.get("/api/settings/plugins")
    assert response.status_code == 200
    assert "server-secret" not in response.text
    assert "private-model" not in response.text
    plugin, = response.json()
    assert plugin["id"] == "segmentation"
    fields = plugin["schema"]["properties"]
    assert set(fields) == {"llm_base_url", "llm_api_key", "llm_model", "llm_timeout_seconds", "llm_max_retries"}
    assert fields["llm_api_key"]["format"] == "password"
    assert fields["llm_timeout_seconds"]["default"] == 120
    assert set(plugin["schema"]["required"]) == {"llm_base_url", "llm_api_key", "llm_model"}


def test_asr_plugin_registration_and_removal(client, monkeypatch):
    """用真实 ASR Settings 生成第二个描述，注册与移除只改变目录，不改业务单例。"""
    from server.asr.asr import ASRSettings, settings

    before = settings.model_dump()
    plugin = {"id": "asr", "name": "语音识别", "schema": ASRSettings.model_json_schema()}
    monkeypatch.setitem(settings_plugins.plugins, "asr", plugin)
    response = client.get("/api/settings/plugins")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["segmentation", "asr"]
    assert response.json()[1]["schema"]["properties"]["dashscope_api_key"]["format"] == "password"
    monkeypatch.delitem(settings_plugins.plugins, "asr")
    assert [item["id"] for item in client.get("/api/settings/plugins").json()] == ["segmentation"]
    assert settings.model_dump() == before
    monkeypatch.delitem(settings_plugins.plugins, "segmentation")
    assert client.get("/api/settings/plugins").json() == []
