"""兼容 GET 分享接口的访问日志过滤；网页本身使用 POST 提交分享文本。"""

import logging
from urllib.parse import unquote, urlsplit


class ShareLinkFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, target, version, status = record.args
            decoded = str(target)
            for _ in range(3):
                decoded = unquote(decoded)
            if "pack_key" in decoded.lower():
                record.args = (address, method, urlsplit(str(target)).path + "?redacted=signal", version, status)
        return True


def install() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, ShareLinkFilter) for item in logger.filters):
        logger.addFilter(ShareLinkFilter())
