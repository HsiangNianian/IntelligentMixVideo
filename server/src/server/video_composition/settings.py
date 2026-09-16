"""合成配置读取固定的 server/.env 和优先级更高的环境变量，不保存云端凭证到任务。"""

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from ..config_base import CommonSettings
from .schema import MediaURL, media_url


class Settings(CommonSettings):
    """受理前验证必要配置；竖屏和运行参数均有默认值，IMS 地域与 Endpoint 从环境读取。"""

    model_config = SettingsConfigDict(populate_by_name=True)

    match_base_url: MediaURL = Field(validation_alias="SEGMENT_MATCH_BASE_URL")
    match_authorization: SecretStr = Field(default=SecretStr(""), validation_alias="SEGMENT_MATCH_AUTHORIZATION")
    composition_public_base_url: str = ""
    ims_access_key_id: SecretStr = Field(validation_alias="ALIBABA_CLOUD_ACCESS_KEY_ID")
    ims_access_key_secret: SecretStr = Field(validation_alias="ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    ims_security_token: SecretStr = Field(default=SecretStr(""), validation_alias="ALIBABA_CLOUD_SECURITY_TOKEN")
    ims_region_id: str = Field(default="cn-shanghai", pattern=r"^[a-z][a-z0-9-]+$", validation_alias="MIX_VIDEO_ALIYUN_IMS_REGION_ID")
    ims_endpoint: str = Field(default="ice.cn-shanghai.aliyuncs.com", validation_alias="MIX_VIDEO_ALIYUN_IMS_ENDPOINT")
    composition_width: int = Field(default=1080, ge=2)
    composition_height: int = Field(default=1920, ge=2)
    composition_fps: int = Field(default=30, ge=1, le=60)
    composition_concurrency: int = Field(default=2, ge=1, le=16)
    composition_http_timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    composition_poll_seconds: float = Field(default=2, gt=0, le=60, allow_inf_nan=False)
    composition_asr_wait_seconds: float = Field(default=1800, gt=0, le=86400, allow_inf_nan=False)
    composition_match_wait_seconds: float = Field(default=1800, gt=0, le=86400, allow_inf_nan=False)
    composition_render_wait_seconds: float = Field(default=3600, gt=0, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def regional_endpoint(self) -> "Settings":
        """地域与官方 Endpoint 必须一致，避免向错误服务主机发送签名请求。"""
        if self.ims_endpoint != f"ice.{self.ims_region_id}.aliyuncs.com":
            raise ValueError("IMS Endpoint 必须是不带协议或路径且与地域一致的官方主机名")
        return self

    @field_validator("ims_access_key_id", "ims_access_key_secret")
    @classmethod
    def credential_not_empty(cls, value: SecretStr) -> SecretStr:
        """缺少静态或临时凭证时拒绝受理，避免 SDK 回退到其他凭证来源。"""
        if not value.get_secret_value().strip():
            raise ValueError("IMS 凭证不能为空")
        return value

    @field_validator("match_authorization")
    @classmethod
    def header_value(cls, value: SecretStr) -> SecretStr:
        """鉴权头允许留空，不允许换行注入。"""
        if any(c in value.get_secret_value() for c in "\r\n"):
            raise ValueError("素材匹配鉴权头无效")
        return value

    @field_validator("match_base_url")
    @classmethod
    def service_base(cls, value: str) -> str:
        """接受服务源或以 /api/v1 结尾的 API 地址，统一保留部署前缀以免重复拼接。"""
        if urlsplit(value).query:
            raise ValueError("匹配 Base URL 不能包含查询参数")
        return value.rstrip("/").removesuffix("/api/v1")

    @field_validator("composition_public_base_url")
    @classmethod
    def public_base(cls, value: str) -> str:
        """允许留空回退到请求地址；公网地址保留部署前缀，不接受凭证、查询或片段。"""
        if not value:
            return value
        media_url(value)
        if urlsplit(value).query:
            raise ValueError("合成公网 Base URL 不能包含查询参数")
        return value.rstrip("/")

    def output(self) -> dict:
        """冻结实际输出规格与地域；VOD 存储在组装时查询，不包含任何密钥。"""
        return dict(width=self.composition_width, height=self.composition_height,
                    fps=self.composition_fps, region_id=self.ims_region_id)
