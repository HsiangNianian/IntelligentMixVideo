"""验证 ASGI 路由、边界和文档；在 server/ 执行 uv run --locked pytest tests/test_api.py。"""

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


# 测试文档包含首页、用户和模板全部路由，且用户 ID 参数定义正确。
def test_api_documentation(client: TestClient) -> None:
    """文档可访问，OpenAPI 声明实际路由及必填整数路径参数。"""
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert set(schema["paths"]) == {
        "/", "/users/", "/users/{user_id}", "/template", "/template/{template_id}",
    }
    parameter = schema["paths"]["/users/{user_id}"]["get"]["parameters"][0]
    assert parameter["name"] == "user_id"
    assert parameter["required"] is True
    assert parameter["schema"]["type"] == "integer"
