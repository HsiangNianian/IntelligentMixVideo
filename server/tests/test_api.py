"""验证 ASGI 路由、边界和文档；在 server/ 执行 uv run --locked pytest tests/test_api.py。"""

from importlib.metadata import version

import pytest
from fastapi.testclient import TestClient


# 测试首页和用户列表的状态码、JSON 类型与示例响应。
@pytest.mark.parametrize("path,message", [("/", "首页"), ("/users/", "用户列表")])
def test_static_routes(client: TestClient, path: str, message: str) -> None:
    """首页与列表返回约定的 JSON 消息，而不是未注册路由或错误内容类型。"""
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"msg": message}


# 测试用户路径参数接受普通整数及边界整数。
@pytest.mark.parametrize("user_id", [0, 1, -1, 42, 2**63])
def test_integer_user_id(client: TestClient, user_id: int) -> None:
    """当前契约接受任意整数，包括零、负数及大整数，并保留数值类型。"""
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 200
    assert response.json() == {"user_id": user_id}


# 测试非法用户 ID 返回指向对应参数的 422。
@pytest.mark.parametrize("user_id", ["abc", "1.5", "true", "%20"])
def test_invalid_user_id(client: TestClient, user_id: str) -> None:
    """非法参数返回指向 user_id 的 422，不能变成 500 或静默转换。"""
    response = client.get(f"/users/{user_id}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["path", "user_id"]


# 测试用户列表缺少结尾斜杠时重定向到规范路径。
def test_users_trailing_slash(client: TestClient) -> None:
    """列表缺少末尾斜杠时重定向到规范路径，避免误匹配详情接口。"""
    response = client.get("/users", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].endswith("/users/")


# 测试只读示例路由拒绝 POST 写入。
@pytest.mark.parametrize("path", ["/", "/users/", "/users/42"])
def test_unsupported_method(client: TestClient, path: str) -> None:
    """示例接口只读，未实现的写入请求必须返回 405。"""
    assert client.post(path, json={}).status_code == 405


# 测试未知地址返回 404，而不是首页内容。
def test_unknown_route(client: TestClient) -> None:
    """不存在的路径返回 404，不由首页兜底为成功。"""
    assert client.get("/not-found").status_code == 404


# 测试 Vite、现有 Tauri 来源和 Windows 客户端动态端口均能通过模板请求预检。
@pytest.mark.parametrize("origin", [
    "http://localhost:1420", "http://localhost:4173", "tauri://localhost", "http://tauri.localhost",
    "http://localhost:1", "http://localhost:49152", "http://localhost:65535",
])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_local_client_cors(client: TestClient, origin: str, method: str) -> None:
    """验证实际预检响应与普通响应的来源头，不把 CORS 通过等同于桌面预览验证。"""
    response = client.options("/template", headers={
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "Content-Type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert method in response.headers["access-control-allow-methods"]
    assert client.get("/template", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin


# 测试不受信任的网页来源和无效 localhost 端口都无法通过跨域预检。
@pytest.mark.parametrize("origin", [
    "https://example.com", "http://example.com:49152", "https://localhost:49152",
    "http://localhost.example.com:49152", "http://localhost:49152.example.com",
    "http://localhost:49152/path", "http://localhost:49152/", "http://localhost:49152?x=1",
    "http://localhost@evil.example:49152", "http://127.0.0.1:49152", "http://[::1]:49152",
    "http://localhost:not-a-port", "http://localhost:0", "http://localhost:65536",
    "http://localhost:999999", "null",
])
def test_disallowed_origin_cors_is_rejected(client: TestClient, origin: str) -> None:
    """不受信网页及无效 localhost 端口不能获得跨域访问权限。"""
    response = client.options("/template", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in client.get("/template", headers={"Origin": origin}).headers


# 测试文档包含首页、用户、模板和切片全部路由，且用户 ID 参数定义正确。
def test_api_documentation(client: TestClient) -> None:
    """文档可访问，OpenAPI 声明实际路由及必填整数路径参数。"""
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == version("imv-server")
    assert set(schema["paths"]) == {
        "/", "/users/", "/users/{user_id}", "/template", "/template/{template_id}", "/segmentations",
    }
    parameter = schema["paths"]["/users/{user_id}"]["get"]["parameters"][0]
    assert parameter["name"] == "user_id"
    assert parameter["required"] is True
    assert parameter["schema"]["type"] == "integer"


def test_template_agent_mount_preserves_shared_routes(
    client: TestClient, template_payload: dict,
) -> None:
    """合并后生成服务保留独立文档，模板库持久化与切片路由仍使用原有契约。"""
    created = client.post("/template", json=template_payload)
    assert created.status_code == 201
    saved = created.json()

    docs = client.get("/api/templates/docs")
    assert docs.status_code == 200
    assert "/api/templates/openapi.json" in docs.text
    response = client.get("/api/templates/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert {"/works", "/assets", "/jobs/{job_id}"} <= set(schema["paths"])
    assert "/template" not in schema["paths"]

    restored = client.get(f"/template/{saved['template_id']}")
    assert restored.status_code == 200
    assert restored.json() == saved
    assert client.post("/segmentations", json={}).status_code == 422
