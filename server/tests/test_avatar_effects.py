"""数字人模板配置保存和校验回归；pytest tests/test_avatar_effects.py，隔离外部服务。"""
import pytest
from google.protobuf.json_format import ParseDict
from generated.imv.template.v1 import template_pb2 as pb
from server.template.avatar import AvatarEffects
from .conftest import template_track
from .template_wire import post_template


def test_saved_effects_roundtrip_and_old_client_update(client):
    """零羽化、关闭开关和另存保留；旧客户端仍携带同屏配置时也不能清掉新增配置。"""
    config = AvatarEffects(opening_enabled=True, opening_duration=3, feather=0, remove_background=True).model_dump(mode="json")
    payload = {"name": "自动数字人", "tracks": [template_track("title"), template_track("subtitle")],
               "split_screen": {"enabled": True}, "avatar_effects": config}
    response = post_template(client, payload)
    assert response.status_code == 201, response.text
    saved = ParseDict(response.json(), pb.SaveTemplateResponse()).template
    assert saved.avatar_effects.HasField("feather") and saved.avatar_effects.feather == 0
    assert ParseDict(client.get(f"/template/{saved.template_id}").json(), pb.GetTemplateResponse()).template == saved
    payload.update(template_id=saved.template_id, name="旧客户端重存")
    del payload["avatar_effects"]
    legacy = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template
    assert legacy.avatar_effects == saved.avatar_effects
    payload["avatar_effects"] = {**config, "opening_enabled": False, "remove_background": False}
    disabled = ParseDict(post_template(client, payload).json(), pb.SaveTemplateResponse()).template.avatar_effects
    assert disabled.HasField("opening_enabled") and not disabled.opening_enabled and not disabled.remove_background
    assert disabled.opening_duration == 3 and disabled.feather == 0


@pytest.mark.parametrize("change", [{"opening_duration": 0}, {"opening_duration": 301}, {"circle_scale": 0.5}, {"feather": -1}])
def test_invalid_effects_rejected_without_saving(client, change):
    """无效配置在保存入口返回 422，数据库不产生模板。"""
    response = post_template(client, {"name": "无效开场", "tracks": [template_track("title"), template_track("subtitle")], "avatar_effects": change})
    assert response.status_code == 422
    assert not client.get("/template").json()["templates"]


def test_invalid_layout_rejected(client):
    """未知模式在协议入口返回 422，不落库，也不悄悄用旧布局。"""
    response = post_template(client, {"name": "错误布局", "tracks": [template_track("title"), template_track("subtitle")],
                                      "split_screen": {"layout": "picture-in-picture"}})
    assert response.status_code == 422
    assert not client.get("/template").json()["templates"]
