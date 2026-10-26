"""服务端与任务日志共用的凭证脱敏；保留原始业务数据。"""

import json
import re
from math import isfinite
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel


def sanitize(value, *, media_url: bool = False):
    """递归清理日志数据中的凭证字段和 URL 鉴权；不修改原始业务数据。"""
    sensitive = r"[\w-]*(?:authorization|password|secret|token|signature|api[_-]?key|access[_-]?key)[\w-]*"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.fullmatch(sensitive, key, re.I) else sanitize(child, media_url=key in ("videoUrl", "audioUrl", "fileUrl", "audio_url"))
                for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(child) for child in value]
    if isinstance(value, float) and not isfinite(value):
        return str(value)
    if isinstance(value, str):
        # SDK 的 Timeline 等字段是二次序列化的 JSON；解析后中文可读且内部凭证也能脱敏。
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except ValueError:
                pass
            else:
                if isinstance(decoded, (dict, list)):
                    return sanitize(decoded)
        def url(match):
            """业务媒体直链保留实际签名供播放；其他链接删除鉴权查询串，始终删除用户信息。"""
            try:
                parts = urlsplit(match.group())
                return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, parts.query if media_url else "", ""))
            except ValueError:
                return "[INVALID URL]"

        if media_url and value.startswith(("http://", "https://")):
            return url(re.match(r".+", value))
        value = re.sub(r'https?://[^\s\"\'<>]+', url, value)
        value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"',;]+", "[REDACTED]", value)
        # 仅从字段边界匹配；长篇无空格正文不能在每个字符位置重新回溯整段内容。
        return re.sub(rf'''(?i)(?<![\w-])(["']?{sensitive}["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}}]+)''',
                      r"\1[REDACTED]", value)
    return value
