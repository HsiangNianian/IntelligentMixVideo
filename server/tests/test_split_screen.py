"""同屏保存、几何、业务时间线及跨布局字幕回归；pytest tests/test_split_screen.py，不访问云服务。"""
import pytest
from google.protobuf.json_format import ParseDict
from generated.imv.template.v1 import template_pb2 as pb

from server.template.split_screen import SplitScreen
from .conftest import template_track
from .template_wire import post_template


def test_protobuf_roundtrip_and_legacy_update(client):
    """配置和显式零值完整保存，旧客户端重存保留配置，关闭再开启保留窗口。"""
    layout = SplitScreen(enabled=True, background_color="#224466").model_dump(mode="json")
    layout["avatar"].update(x=0, radius=0, border_width=0)
    payload = {"name": "同屏模板", "tracks": [template_track("title"), template_track("subtitle")], "split_screen": layout}
    response = post_template(client, payload)
    assert response.status_code == 201, response.text
    saved = ParseDict(response.json(), pb.SaveTemplateResponse()).template
    assert saved.split_screen.avatar.HasField("radius") and saved.split_screen.avatar.radius == 0
    assert ParseDict(client.get(f"/template/{saved.template_id}").json(), pb.GetTemplateResponse()).template == saved
    payload.update(template_id=saved.template_id, name="旧客户端重存")
    del payload["split_screen"]
    updated = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template
    assert updated.split_screen == saved.split_screen
    payload["split_screen"] = {**layout, "enabled": False}
    off = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template.split_screen
    assert not off.enabled and off.avatar == saved.split_screen.avatar


@pytest.mark.parametrize("change", [{"x": 90}, {"width": 0}, {"radius": 10000}, {"border_color": "red"}, {"fit": "Stretch"}])
def test_invalid_window_rejected(client, change):
    """越界和不受支持样式在保存入口拒绝，不写入数据库。"""
    layout = SplitScreen(enabled=True).model_dump(mode="json")
    layout["material"].update(change)
    response = post_template(client, {"name": "无效布局", "tracks": [template_track("title"), template_track("subtitle")], "split_screen": layout})
    assert response.status_code == 422
    assert not ParseDict(client.get("/template").json(), pb.ListTemplatesResponse()).templates
