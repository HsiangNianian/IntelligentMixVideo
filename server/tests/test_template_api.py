"""通过真实路由、schema 和 store 验证模板契约、输入校验、持久化与失败恢复。

在 server/ 执行 uv run --locked pytest tests/test_template_api.py -v。
数据库由 conftest.py 提供临时 SQLite；不访问 MySQL、真实 .env 或 SDK 网络资源。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pymysql.err import IntegrityError as MySQLIntegrityError
from pymysql.err import OperationalError as MySQLOperationalError
from sqlalchemy import Engine, event, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError

from server import database
from server.app import app
from server.template import store


# 测试空模板库返回空数组，并在第一次模板请求时创建缺失表。
def test_empty_library_creates_table(client: TestClient, template_db: Engine) -> None:
    """检查空响应契约与真实建表副作用。"""
    assert not inspect(template_db).has_table("templates")
    response = client.get("/template")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == []
    assert inspect(template_db).has_table("templates")


# 测试省略 ID 或传 null 均创建模板，返回 UUID、UTC 时间、默认配置及服务端效果快照。
@pytest.mark.parametrize("include_null_id", [False, True], ids=["omitted-id", "null-id"])
def test_create_template_contract(
    client: TestClient, template_db: Engine, template_payload: dict, include_null_id: bool,
) -> None:
    """同时核对 HTTP 响应、详情、列表和持久化行，避免只验证路由调用。"""
    template_payload.update(name="  中文模板 🎬  ", description="  适合字幕  ")
    if include_null_id:
        template_payload["template_id"] = None
    started = datetime.now(UTC)
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    saved = response.json()
    assert set(saved) == {
        "template_id", "name", "description", "editor", "effect_ids",
        "transition_duration_seconds", "effects", "created_at", "updated_at",
    }
    assert UUID(saved["template_id"]).version == 4
    assert saved["name"] == "中文模板 🎬"
    assert saved["description"] == "适合字幕"
    assert saved["editor"]["titleIn"] == "in/fade_in"
    assert saved["editor"]["titleSize"] == 40
    assert saved["editor"]["subtitleY"] == 82
    assert "title_in" not in saved["editor"]
    assert saved["effect_ids"] == ["in/fade_in"]
    assert saved["transition_duration_seconds"] == 0.5
    assert saved["effects"][0]["parameters"] == {"AaiMotionInEffect": "fade_in"}
    assert saved["effects"][0]["id"] == "in/fade_in"
    assert saved["created_at"] == saved["updated_at"]
    created = datetime.fromisoformat(saved["created_at"])
    assert created.utcoffset() == timedelta(0)
    assert started <= created <= datetime.now(UTC)
    assert client.get(f"/template/{saved['template_id']}").json() == saved
    assert client.get("/template").json() == [saved]
    with template_db.connect() as connection:
        row = connection.execute(select(store.templates)).one()
        assert row.template_id == saved["template_id"]
        assert row.configuration["editor"] == saved["editor"]
        assert row.configuration["effects"] == saved["effects"]


# 测试 POST 携带 ID 完整替换配置并重命名，保留 ID/创建时间并更新时间与效果快照。
def test_update_replaces_complete_configuration(
    client: TestClient, template_db: Engine, template_payload: dict,
) -> None:
    """省略可选字段应恢复默认值，旧效果不能残留在配置或快照中。"""
    created = client.post("/template", json=template_payload).json()
    old_time = datetime(2024, 1, 1)
    with template_db.begin() as connection:
        connection.execute(store.templates.update().values(created_at=old_time, updated_at=old_time))
    payload = {
        "template_id": created["template_id"], "name": "重命名后",
        "editor": {"subtitleIn": "in/blur_in", "subtitleSize": 42},
        "effect_ids": ["in/blur_in"],
    }
    response = client.post("/template", json=payload)
    assert response.status_code == 200
    saved = response.json()
    assert saved["template_id"] == created["template_id"]
    assert saved["name"] == "重命名后"
    assert saved["description"] == ""
    assert saved["editor"]["titleIn"] == ""
    assert saved["editor"]["subtitleIn"] == "in/blur_in"
    assert saved["editor"]["subtitleSize"] == 42
    assert saved["effect_ids"] == ["in/blur_in"]
    assert [effect["id"] for effect in saved["effects"]] == ["in/blur_in"]
    assert saved["transition_duration_seconds"] == 0.5
    assert datetime.fromisoformat(saved["created_at"]) == old_time.replace(tzinfo=UTC)
    assert datetime.fromisoformat(saved["updated_at"]) > old_time.replace(tzinfo=UTC)
    assert client.get(f"/template/{saved['template_id']}").json() == saved
    assert client.get("/template").json() == [saved]
    # 再次保存相同 ID/名称仍是更新，不应产生新记录或与自身重名冲突。
    assert client.post("/template", json=payload).status_code == 200
    assert len(client.get("/template").json()) == 1


# 测试另存为不携带 ID 时创建独立记录，不修改原模板。
def test_save_as_preserves_original(client: TestClient, template_payload: dict) -> None:
    """新模板具有独立 ID，原记录的字段和时间保持不变。"""
    original = client.post("/template", json=template_payload).json()
    template_payload["name"] = "模板副本"
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    copied = response.json()
    assert copied["template_id"] != original["template_id"]
    assert copied["editor"] == original["editor"]
    assert copied["effects"] == original["effects"]
    assert client.get(f"/template/{original['template_id']}").json() == original
    assert len(client.get("/template").json()) == 2


# 测试列表按最近更新倒序排列，同一时间按 ID 排序，更新后移动到列表前面。
def test_list_order_is_stable(
    client: TestClient, template_db: Engine, template_payload: dict,
) -> None:
    """直接设置确定的持久化时间，避免依赖 sleep 或机器时钟精度。"""
    records = [
        client.post("/template", json={**template_payload, "name": name}).json()
        for name in ("甲", "乙", "丙")
    ]
    timestamp = datetime(2024, 1, 1)
    with template_db.begin() as connection:
        for index, record in enumerate(records):
            connection.execute(store.templates.update().where(
                store.templates.c.template_id == record["template_id"],
            ).values(updated_at=timestamp + timedelta(days=index // 2)))
    expected = [records[2]["template_id"], *sorted(record["template_id"] for record in records[:2])]
    assert [record["template_id"] for record in client.get("/template").json()] == expected
    response = client.post("/template", json={
        **template_payload, "template_id": records[0]["template_id"], "name": "更新后的甲",
    })
    assert response.status_code == 200
    assert client.get("/template").json()[0] == response.json()


# 测试新建或重命名与已有模板重名时返回 409，并回滚全部写入。
@pytest.mark.parametrize("updating", [False, True], ids=["duplicate-create", "duplicate-rename"])
def test_duplicate_name_preserves_data(
    client: TestClient, template_payload: dict, updating: bool,
) -> None:
    """通过真实 UNIQUE 约束触发冲突；修改说明和效果也不能部分提交。"""
    client.post("/template", json=template_payload)
    conflicting = {**template_payload, "name": "  测试模板  ", "description": "不能被写入"}
    if updating:
        second = client.post("/template", json={**template_payload, "name": "另一模板"}).json()
        conflicting.update(template_id=second["template_id"], editor={"titleIn": "in/blur_in"}, effect_ids=["in/blur_in"])
    before = client.get("/template").json()
    response = client.post("/template", json=conflicting)
    assert response.status_code == 409
    assert "名称已存在" in response.json()["detail"]
    assert client.get("/template").json() == before
    # 冲突后换一个名字应能正常保存，失败事务不能污染后续请求。
    conflicting["name"] = "冲突恢复后"
    assert client.post("/template", json=conflicting).status_code == (200 if updating else 201)


# 测试两个并发创建同名模板的请求只有一个成功，另一个返回 409。
def test_concurrent_duplicate_create(client: TestClient, template_payload: dict) -> None:
    """验证 HTTP 并发下唯一约束的可见结果；不把 SQLite 的锁机制当作 MySQL 行锁验证。"""
    barrier = Barrier(2, timeout=5)

    def create_same_name() -> int:
        """两个工作线程在同一屏障后提交独立请求，超时会让用例明确失败。"""
        barrier.wait()
        return client.post("/template", json=template_payload).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_same_name) for _ in range(2)]
        statuses = sorted(future.result(timeout=10) for future in futures)
    assert statuses == [201, 409]
    assert len(client.get("/template").json()) == 1


# 测试删除返回无正文的 204，只删除指定模板，重复删除和再次读取均返回 404。
def test_delete_only_requested_template(client: TestClient, template_payload: dict) -> None:
    """保留另一个模板用于检查删除范围及持久化副作用。"""
    target = client.post("/template", json=template_payload).json()
    other = client.post("/template", json={**template_payload, "name": "保留模板"}).json()
    path = f"/template/{target['template_id']}"
    response = client.delete(path)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.get("/template").json() == [other]


# 测试不存在的 UUID 在读取、删除和更新时返回 404；带 ID 的 POST 不能变成创建。
@pytest.mark.parametrize("method", ["GET", "DELETE", "POST"])
def test_missing_template_does_not_upsert(
    client: TestClient, template_payload: dict, method: str,
) -> None:
    """保留已有记录作为对照，失败操作不能新增或误删数据。"""
    original = client.post("/template", json=template_payload).json()
    missing_id = str(uuid4())
    if method == "POST":
        response = client.post("/template", json={**template_payload, "template_id": missing_id})
    else:
        response = client.request(method, f"/template/{missing_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "模板不存在"}
    assert client.get("/template").json() == [original]


# 测试四个接口遇到数据库错误返回 503、隐藏错误细节，并在恢复后允许重试。
@pytest.mark.parametrize("operation,sql,success", [
    ("list", "SELECT", 200), ("detail", "SELECT", 200),
    ("create", "INSERT", 201), ("update", "UPDATE", 200), ("delete", "DELETE", 204),
])
def test_database_failure_rolls_back_and_recovers(
    client: TestClient, template_db: Engine, template_payload: dict,
    operation: str, sql: str, success: int,
) -> None:
    """在真实 SQL 执行后注入失败，确保写入回滚，而非仅检查异常处理函数被调用。"""
    original = client.post("/template", json=template_payload).json()
    before = client.get("/template").json()
    payload = {**template_payload, "name": "失败后重试"}
    if operation == "update":
        payload["template_id"] = original["template_id"]
    path = f"/template/{original['template_id']}" if operation in ("detail", "delete") else "/template"
    method = "POST" if operation in ("create", "update") else "DELETE" if operation == "delete" else "GET"

    def fail_after_sql(connection, cursor, statement, parameters, context, executemany):
        """模拟语句执行后连接出错，以覆盖事务已经产生副作用的失败位置。"""
        if statement.lstrip().upper().startswith(sql):
            raise OperationalError(statement, parameters, MySQLOperationalError(2013, "private-database-detail"))

    event.listen(template_db, "after_cursor_execute", fail_after_sql)
    try:
        response = client.request(method, path, json=payload if method == "POST" else None)
    finally:
        event.remove(template_db, "after_cursor_execute", fail_after_sql)
    assert response.status_code == 503
    assert "private-database-detail" not in response.text
    assert "数据库操作失败" in response.json()["detail"]
    assert client.get("/template").json() == before
    retried = client.request(method, path, json=payload if method == "POST" else None)
    assert retried.status_code == success
    remaining = client.get("/template").json()
    assert len(remaining) == (2 if operation == "create" else 0 if operation == "delete" else 1)
    if operation == "update":
        assert remaining[0]["name"] == "失败后重试"


# 测试非重名的完整性错误返回 503，不能被错误地归类为 409。
def test_other_integrity_error_is_not_name_conflict(
    client: TestClient, template_db: Engine, template_payload: dict,
) -> None:
    """以 MySQL 1048 模拟其他约束错误，并检查请求未留下记录。"""
    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        """仅在 INSERT 之前注入错误，建表和查询仍使用真实数据库。"""
        if context.isinsert:
            raise IntegrityError(statement, parameters, MySQLIntegrityError(1048, "private-integrity-detail"))

    event.listen(template_db, "before_cursor_execute", fail_insert)
    try:
        response = client.post("/template", json=template_payload)
    finally:
        event.remove(template_db, "before_cursor_execute", fail_insert)
    assert response.status_code == 503
    assert "private-integrity-detail" not in response.text
    assert client.get("/template").json() == []


# 测试接口只公开约定的四种操作，创建状态码和 UUID 参数出现在 OpenAPI 中。
def test_template_openapi_contract(client: TestClient) -> None:
    """不允许额外引入 PUT 更新接口或独立的效果目录接口。"""
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/template"]) == {"get", "post"}
    assert set(paths["/template/{template_id}"]) == {"get", "delete"}
    assert "/template/effects" not in paths
    assert {"200", "201", "422"} <= paths["/template"]["post"]["responses"].keys()
    parameter = paths["/template/{template_id}"]["get"]["parameters"][0]
    assert parameter["name"] == "template_id"
    assert parameter["required"] is True
    assert parameter["schema"]["format"] == "uuid"


# 测试未实现的写入方式返回 405，而不是绕过统一 POST 入口。
@pytest.mark.parametrize("method,detail_path", [
    ("PUT", False), ("PATCH", False), ("DELETE", False),
    ("PUT", True), ("PATCH", True), ("POST", True),
])
def test_unsupported_template_method(client: TestClient, method: str, detail_path: bool) -> None:
    """集合与详情路径分别检查允许的方法，拒绝请求后库仍为空。"""
    path = f"/template/{uuid4()}" if detail_path else "/template"
    assert client.request(method, path, json={}).status_code == 405
    assert client.get("/template").json() == []


# 测试每个效果选择字段接受正确分类，并由服务端生成对应 SDK 参数快照。
@pytest.mark.parametrize("field,effect_id,parameters", [
    ("titleFlower", "flower/CS0003-000001", {"EffectColorStyle": "CS0003-000001"}),
    ("subtitleFlower", "flower/CS0003-000001", {"EffectColorStyle": "CS0003-000001"}),
    ("bubble", "bubble/BS0001-000001", {"BubbleStyleId": "BS0001-000001"}),
    ("filter", "filter/m1", {"SubType": "m1"}),
    ("vfx", "vfx/normal/open", {"SubType": "open"}),
    ("transition", "transition/normal/directional", {"SubType": "directional"}),
    ("titleIn", "in/fade_in", {"AaiMotionInEffect": "fade_in"}),
    ("subtitleIn", "in/fade_in", {"AaiMotionInEffect": "fade_in"}),
    ("bubbleIn", "in/fade_in", {"AaiMotionInEffect": "fade_in"}),
    ("titleOut", "out/blur_out", {"AaiMotionOutEffect": "blur_out"}),
    ("subtitleOut", "out/blur_out", {"AaiMotionOutEffect": "blur_out"}),
    ("bubbleOut", "out/blur_out", {"AaiMotionOutEffect": "blur_out"}),
    ("titleLoop", "loop/normal_display", {"AaiMotionLoopEffect": "normal_display"}),
    ("subtitleLoop", "loop/normal_display", {"AaiMotionLoopEffect": "normal_display"}),
    ("bubbleLoop", "loop/normal_display", {"AaiMotionLoopEffect": "normal_display"}),
])
def test_effect_field_generates_snapshot(
    client: TestClient, template_payload: dict, field: str, effect_id: str, parameters: dict,
) -> None:
    """使用固定代表素材检查全部选择字段，不从被测目录动态生成期望结果。"""
    template_payload.update(editor={field: effect_id}, effect_ids=[effect_id])
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    saved = response.json()
    assert saved["editor"][field] == effect_id
    assert len(saved["effects"]) == 1
    effect = saved["effects"][0]
    assert effect["id"] == effect_id
    assert effect["category"] == effect_id.rsplit("/", 1)[0]
    assert effect["effect_id"] == effect_id.rsplit("/", 1)[1]
    assert effect["parameters"] == parameters
    assert client.get(f"/template/{saved['template_id']}").json() == saved


# 测试多个文字角色可复用同一效果，effect_ids 和快照只需保存一份。
def test_shared_effect_and_snake_case_input(client: TestClient, template_payload: dict) -> None:
    """兼容 snake_case 输入，输出仍使用 camelCase，保留示例文字的原始空白。"""
    template_payload["editor"] = {
        "title_in": "in/fade_in", "subtitle_in": "in/fade_in", "bubble_in": "in/fade_in",
        "title_size": 48, "bubble_text": "  保留空白  ",
    }
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    saved = response.json()
    assert all(saved["editor"][f"{role}In"] == "in/fade_in" for role in ("title", "subtitle", "bubble"))
    assert saved["editor"]["titleSize"] == 48
    assert saved["editor"]["bubbleText"] == "  保留空白  "
    assert not any("_" in key for key in saved["editor"])
    assert saved["effect_ids"] == ["in/fade_in"]
    assert len(saved["effects"]) == 1


# 测试未知效果、错误分类、重复 ID、遗漏或额外 ID 均返回 422，非法更新保留原记录。
@pytest.mark.parametrize("editor,effect_ids,message", [
    ({"titleIn": "in/not_in_sdk"}, ["in/not_in_sdk"], "效果不在对应的 SDK 目录中"),
    ({"titleIn": "filter/m1"}, ["filter/m1"], "效果不在对应的 SDK 目录中"),
    ({"titleIn": "in/fade_in"}, ["in/fade_in", "in/fade_in"], "同一个特效不能重复添加"),
    ({"titleIn": "in/fade_in", "filter": "filter/m1"}, ["in/fade_in"], "所选特效与编辑配置不一致"),
    ({"titleIn": "in/fade_in"}, ["in/fade_in", "filter/m1"], "所选特效与编辑配置不一致"),
    ({}, ["in/fade_in"], "所选特效与编辑配置不一致"),
])
def test_invalid_effect_selection_preserves_template(
    client: TestClient, template_payload: dict, editor: dict, effect_ids: list[str], message: str,
) -> None:
    """校验失败不能部分更新名称、编辑配置或效果快照。"""
    original = client.post("/template", json=template_payload).json()
    response = client.post("/template", json={
        **template_payload, "template_id": original["template_id"], "name": "不应保存",
        "editor": editor, "effect_ids": effect_ids,
    })
    assert response.status_code == 422
    assert message in response.json()["detail"][0]["msg"]
    assert client.get("/template").json() == [original]


# 测试标题、字幕和气泡各自禁止循环与入场或出场同时使用。
@pytest.mark.parametrize("role", ["title", "subtitle", "bubble"])
@pytest.mark.parametrize("phase,effect_id", [("In", "in/fade_in"), ("Out", "out/blur_out")])
def test_loop_conflicts_with_entry_or_exit(
    client: TestClient, template_payload: dict, role: str, phase: str, effect_id: str,
) -> None:
    """对每个角色和两种冲突分别验证错误响应及无写入副作用。"""
    template_payload.update(
        editor={f"{role}Loop": "loop/normal_display", f"{role}{phase}": effect_id},
        effect_ids=["loop/normal_display", effect_id],
    )
    response = client.post("/template", json=template_payload)
    assert response.status_code == 422
    assert "循环动效不能与入场、出场同时使用" in response.json()["detail"][0]["msg"]
    assert client.get("/template").json() == []


# 测试同一角色的入场与出场可以组合，另一角色仍可独立使用循环效果。
@pytest.mark.parametrize("role,other_role", [("title", "subtitle"), ("subtitle", "bubble"), ("bubble", "title")])
def test_entry_exit_and_other_role_loop_can_coexist(
    client: TestClient, template_payload: dict, role: str, other_role: str,
) -> None:
    """防止互斥校验扩大到不同角色或误拒绝入场加出场的合法配置。"""
    template_payload.update(
        editor={f"{role}In": "in/fade_in", f"{role}Out": "out/blur_out", f"{other_role}Loop": "loop/normal_display"},
        effect_ids=["out/blur_out", "loop/normal_display", "in/fade_in"],
    )
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    saved = response.json()
    assert [effect["id"] for effect in saved["effects"]] == template_payload["effect_ids"]
    assert all(saved["editor"][field] == value for field, value in template_payload["editor"].items())


# 数值字段的公开约束：独立列出 HTTP 字段与上下限，不读取 schema 生成断言。
NUMERIC_BOUNDS = [
    ("titleSize", 12, 120), ("subtitleSize", 12, 120), ("bubbleSize", 12, 120),
    ("titleX", 0, 100), ("titleY", 0, 100), ("subtitleX", 0, 100),
    ("subtitleY", 0, 100), ("bubbleX", 0, 100), ("bubbleY", 0, 100),
    ("titleInDuration", 0.1, 3), ("titleOutDuration", 0.1, 3),
    ("subtitleInDuration", 0.1, 3), ("subtitleOutDuration", 0.1, 3),
    ("bubbleInDuration", 0.1, 3), ("bubbleOutDuration", 0.1, 3),
    ("transition_duration_seconds", 0.1, 3),
]


# 测试全部字号、坐标和时长恰好位于上下限时均可保存，返回值没有被截断或替换。
@pytest.mark.parametrize("bound_index", [1, 2], ids=["minimum", "maximum"])
def test_numeric_boundaries_are_inclusive(client: TestClient, template_payload: dict, bound_index: int) -> None:
    """一次提交所有同侧边界，检查每个持久化字段的响应值。"""
    for bounds in NUMERIC_BOUNDS:
        field, value = bounds[0], bounds[bound_index]
        target = template_payload if field == "transition_duration_seconds" else template_payload["editor"]
        target[field] = value
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    saved = response.json()
    for bounds in NUMERIC_BOUNDS:
        source = saved if bounds[0] == "transition_duration_seconds" else saved["editor"]
        assert source[bounds[0]] == bounds[bound_index]


# 测试每个数值字段分别拒绝越界、非有限数、非数值和 null，并准确指明错误字段。
@pytest.mark.parametrize("field,minimum,maximum", NUMERIC_BOUNDS)
@pytest.mark.parametrize("invalid", ["below", "above", "NaN", "Infinity", "not-a-number", None])
def test_invalid_numeric_value(
    client: TestClient, template_payload: dict, field: str, minimum: float, maximum: float, invalid: str | None,
) -> None:
    """使用字符串表示非有限值，避免 HTTP 客户端在发送 JSON 前就拒绝序列化。"""
    value = minimum - 1 if invalid == "below" else maximum + 1 if invalid == "above" else invalid
    target = template_payload if field == "transition_duration_seconds" else template_payload["editor"]
    target[field] = value
    response = client.post("/template", json=template_payload)
    assert response.status_code == 422
    expected_location = ["body", field] if target is template_payload else ["body", "editor", field]
    assert any(error["loc"] == expected_location for error in response.json()["detail"])
    assert client.get("/template").json() == []


# 测试 JSON 数字溢出及非标准非有限常量仍返回可解析的 422，不因错误输入回显变成 500。
@pytest.mark.parametrize("field", ["titleX", "titleSize", "transition_duration_seconds"])
@pytest.mark.parametrize("token", ["1e309", "-1e309", "NaN", "Infinity", "-Infinity"])
def test_non_finite_json_number_returns_validation_error(
    client: TestClient, template_payload: dict, field: str, token: str,
) -> None:
    """发送原始 JSON 绕过客户端序列化限制，检查错误位置、数值回显和无写入副作用。"""
    target = template_payload if field == "transition_duration_seconds" else template_payload["editor"]
    target[field] = "NON_FINITE"
    body = json.dumps(template_payload).replace('"NON_FINITE"', token)
    response = client.post("/template", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    location = ["body", field] if target is template_payload else ["body", "editor", field]
    error = next(error for error in response.json()["detail"] if error["loc"] == location)
    assert error["input"] in ("inf", "-inf", "nan")
    assert client.get("/template").json() == []


# 测试未知字段的嵌套输入包含非有限数时，错误响应同样可以序列化。
def test_nested_non_finite_error_input(client: TestClient, template_payload: dict) -> None:
    """保留嵌套错误输入的结构与正常数值，只将非法 JSON 数值转换为文字。"""
    template_payload["unknown"] = {"values": [1.5, "NON_FINITE"]}
    body = json.dumps(template_payload).replace('"NON_FINITE"', "1e309")
    response = client.post("/template", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "unknown"]
    assert error["input"] == {"values": [1.5, "inf"]}
    assert client.get("/template").json() == []


# 测试字号必须是整数，不能静默截断小数。
@pytest.mark.parametrize("field", ["titleSize", "subtitleSize", "bubbleSize"])
def test_fractional_font_size_is_rejected(client: TestClient, template_payload: dict, field: str) -> None:
    """12.5 在数值范围内，但不满足整数字号契约。"""
    template_payload["editor"][field] = 12.5
    response = client.post("/template", json=template_payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "editor", field]
    assert client.get("/template").json() == []


# 测试名称及各类说明文字的最短和最长合法长度，包括可选文字为空。
@pytest.mark.parametrize("maximum", [False, True], ids=["shortest", "longest"])
def test_text_boundaries(client: TestClient, template_payload: dict, maximum: bool) -> None:
    """中文按字符长度计算；空示例文字不能被默认文案替换。"""
    template_payload.update(name="模" * (100 if maximum else 1), description="说" * (1000 if maximum else 0))
    template_payload["editor"].update(
        title="题" * (60 if maximum else 0), subtitle="字" * (100 if maximum else 0),
        bubbleText="泡" * (40 if maximum else 0),
    )
    response = client.post("/template", json=template_payload)
    assert response.status_code == 201
    saved = response.json()
    assert saved["name"] == template_payload["name"]
    assert saved["description"] == template_payload["description"]
    assert all(saved["editor"][field] == template_payload["editor"][field] for field in ("title", "subtitle", "bubbleText"))


# 测试过长文字、空白名称、非法字段类型、空效果列表和超过数量上限均返回 422。
@pytest.mark.parametrize("changes", [
    {"name": ""}, {"name": " \t\n "}, {"name": None}, {"name": "模" * 101},
    {"description": "说" * 1001}, {"description": None},
    {"editor": None}, {"editor": []}, {"editor": {"title": "题" * 61}},
    {"editor": {"subtitle": "字" * 101}}, {"editor": {"bubbleText": "泡" * 41}},
    {"editor": {"titleIn": "x" * 201}},
    {"effect_ids": []}, {"effect_ids": None}, {"effect_ids": "in/fade_in"},
    {"effect_ids": [123]}, {"effect_ids": [f"in/unknown_{index}" for index in range(21)]},
], ids=[
    "empty-name", "whitespace-name", "null-name", "long-name", "long-description", "null-description",
    "null-editor", "list-editor", "long-title", "long-subtitle", "long-bubble", "long-effect-id",
    "empty-effects", "null-effects", "string-effects", "non-string-effect", "too-many-effects",
])
def test_invalid_template_fields(client: TestClient, template_payload: dict, changes: dict) -> None:
    """无效输入必须返回结构化校验错误，且不能产生模板记录。"""
    response = client.post("/template", json={**template_payload, **changes})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert all(error["loc"][0] == "body" for error in response.json()["detail"])
    assert client.get("/template").json() == []


# 测试名称、编辑器配置和效果 ID 列表为必填字段，缺失时返回对应的 missing 错误。
@pytest.mark.parametrize("field", ["name", "editor", "effect_ids"])
def test_missing_required_field(client: TestClient, template_payload: dict, field: str) -> None:
    """区分字段缺失与字段内容非法，确保 API 错误位置可用于前端提示。"""
    del template_payload[field]
    response = client.post("/template", json=template_payload)
    assert response.status_code == 422
    assert any(error["loc"] == ["body", field] and error["type"] == "missing" for error in response.json()["detail"])
    assert client.get("/template").json() == []


# 测试客户端不能注入效果快照、渲染参数、时间戳或未知字段，包括 editor 内部字段。
@pytest.mark.parametrize("field,value,in_editor", [
    ("effects", [], False), ("parameters", {"SubType": "untrusted"}, False),
    ("created_at", "2024-01-01T00:00:00Z", False), ("updated_at", "2024-01-01T00:00:00Z", False),
    ("unknown", True, False), ("parameters", {"SubType": "untrusted"}, True), ("unknown", True, True),
])
def test_server_owned_and_unknown_fields_are_rejected(
    client: TestClient, template_payload: dict, field: str, value: object, in_editor: bool,
) -> None:
    """检查 extra_forbidden 的准确位置，确认外层及嵌套层都执行白名单校验。"""
    target = template_payload["editor"] if in_editor else template_payload
    target[field] = value
    response = client.post("/template", json=template_payload)
    assert response.status_code == 422
    location = ["body", "editor", field] if in_editor else ["body", field]
    assert any(error["loc"] == location and error["type"] == "extra_forbidden" for error in response.json()["detail"])
    assert client.get("/template").json() == []


# 测试详情、删除路径和更新请求体中的非法 UUID 均返回 422，不进入持久化操作。
@pytest.mark.parametrize("method", ["GET", "DELETE", "POST"])
@pytest.mark.parametrize("invalid_id", ["not-a-uuid", "123"])
def test_invalid_template_id(client: TestClient, template_payload: dict, method: str, invalid_id: str) -> None:
    """三种接受模板 ID 的操作采用相同 UUID 契约。"""
    if method == "POST":
        response = client.post("/template", json={**template_payload, "template_id": invalid_id})
    else:
        response = client.request(method, f"/template/{invalid_id}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body" if method == "POST" else "path", "template_id"]
    assert client.get("/template").json() == []


# 测试损坏 JSON、空请求体和非对象 JSON 返回 422，而不是服务端异常。
@pytest.mark.parametrize("body", ['{"name":', "", "null", "[]", '"text"'])
def test_malformed_or_non_object_body(client: TestClient, body: str) -> None:
    """通过原始 HTTP 正文触发解析错误，不由客户端 JSON 序列化提前拦截。"""
    response = client.post("/template", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert client.get("/template").json() == []


# 测试首次建表失败返回 503，不缓存成功状态，恢复后下次请求可以重新建表。
def test_schema_creation_failure_can_retry(client: TestClient, template_db: Engine) -> None:
    """在实际 CREATE TABLE 之前注入故障，移除故障后验证真实表被创建。"""
    def fail_create(connection, cursor, statement, parameters, context, executemany):
        """只阻止建表，保留探测表是否存在的查询。"""
        if statement.lstrip().upper().startswith("CREATE TABLE"):
            raise OperationalError(statement, parameters, MySQLOperationalError(2013, "schema-unavailable"))

    event.listen(template_db, "before_cursor_execute", fail_create)
    try:
        response = client.get("/template")
    finally:
        event.remove(template_db, "before_cursor_execute", fail_create)
    assert response.status_code == 503
    assert store._ready_engine is None
    assert not inspect(template_db).has_table("templates")
    assert client.get("/template").json() == []
    assert inspect(template_db).has_table("templates")


# 测试应用关闭时释放连接池，重新启动后仍可读取已保存的模板。
def test_lifespan_releases_connections_and_preserves_data(template_db: Engine, template_payload: dict) -> None:
    """独立管理两个 ASGI 生命周期，验证连接清理不会删除持久化数据。"""
    with TestClient(app) as first_client:
        response = first_client.post("/template", json=template_payload)
        assert response.status_code == 201
        saved = response.json()
        assert template_db.pool.checkedout() == 0
    assert database._engine is None
    assert template_db.pool.checkedout() == 0
    database._engine = template_db
    with TestClient(app) as second_client:
        assert second_client.get(f"/template/{saved['template_id']}").json() == saved
    assert database._engine is None
