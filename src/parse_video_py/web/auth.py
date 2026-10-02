"""访问控制：整站可选的 Basic Auth（私有部署用），以及站长页的统计口令。"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .. import stats


def _basic_auth_dependencies() -> list:
    """配了 PARSE_VIDEO_USERNAME / PASSWORD 就整站要登录；没配返回空列表，路由照常公开。"""
    username = os.getenv("PARSE_VIDEO_USERNAME")
    password = os.getenv("PARSE_VIDEO_PASSWORD")
    if not (username and password):
        return []

    security = HTTPBasic()

    def verify(credentials: HTTPBasicCredentials = Depends(security)) -> HTTPBasicCredentials:
        user_ok = secrets.compare_digest(credentials.username, username)
        pass_ok = secrets.compare_digest(credentials.password, password)
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials

    return [Depends(verify)]


# 模块加载时构建一次，各个 router 共用
SITE_AUTH = _basic_auth_dependencies()


def require_stats_token(request: Request, token: str = "") -> None:
    """站长页：口令可以放在 ?token= 或 Authorization: Bearer 里。口令不对时假装页面不存在。"""
    auth = request.headers.get("authorization", "")
    bearer = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not stats.check_token(token or bearer):
        raise HTTPException(404, "Not Found")


def require_stats_enabled() -> None:
    """没配统计口令时，站长相关的页面都当不存在。"""
    if not stats.enabled():
        raise HTTPException(404, "Not Found")
