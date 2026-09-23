"""隔离 HTTP 与 S3，验证新成片只公开指定对象且返回可匿名读取的固定地址。"""

import httpx
import pytest

from server.video_composition import zos
from server.video_composition.settings import ZosSettings


@pytest.mark.parametrize("public_status", [206, 403])
def test_copy_video_publishes_only_composition_object(composition_settings, monkeypatch, public_status):
    """下载保留 IMS 签名，上传指定对象为公共读；匿名读取失败不得返回成功地址。"""
    task_id = "11111111-1111-4111-8111-111111111111"
    source = "https://ims.example.test/output.mp4?Signature=source"
    key = f"imv/video_composition/{task_id}.mp4"
    public = f"https://archives.hangzhou7.zos.ctyun.cn/{key}"
    requests = []
    uploaded = {}

    def handle(request):
        """模拟源文件和无需凭据的公开读取。"""
        requests.append(request)
        if str(request.url) == source:
            return httpx.Response(200, content=b"video", headers={"Content-Length": "5"})
        assert str(request.url) == public and request.headers["Range"] == "bytes=0-0"
        assert "authorization" not in request.headers
        return httpx.Response(public_status, content=b"v" if public_status == 206 else b"")

    class Storage:
        """记录对象级上传参数与实际读取的字节。"""

        def close(self):
            """模拟释放 S3 客户端资源。"""

        def upload_fileobj(self, file, bucket, object_key, ExtraArgs):
            """S3 传输读取完整文件后记录 ACL。"""
            uploaded.update(data=file.read(), bucket=bucket, key=object_key, extra=ExtraArgs)

        def head_object(self, *, Bucket, Key):
            """模拟上传后的对象长度核验。"""
            assert Bucket == "archives" and Key == key
            return {"ContentLength": len(uploaded["data"])}

    real_client = httpx.Client
    monkeypatch.setattr(zos.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))

    def storage_client(service, **kwargs):
        """核对服务端凭据与 ZOS 的虚拟主机、校验和兼容设置。"""
        assert service == "s3" and kwargs["endpoint_url"] == "https://hangzhou7.zos.ctyun.cn"
        assert kwargs["region_name"] == "hangzhou-7"
        assert kwargs["config"].s3["addressing_style"] == "virtual"
        assert kwargs["config"].request_checksum_calculation == "when_required"
        return Storage()

    monkeypatch.setattr(zos.boto3, "client", storage_client)

    if public_status == 403:
        with pytest.raises(httpx.HTTPStatusError):
            zos.copy_video(source, task_id, ZosSettings(), 10)
    else:
        assert zos.copy_video(source, task_id, ZosSettings(), 10) == (key, public)
    assert uploaded == {"data": b"video", "bucket": "archives", "key": key,
                        "extra": {"ACL": "public-read", "ContentType": "video/mp4"}}
    assert [str(request.url) for request in requests] == [source, public]
