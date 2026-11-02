"""意见反馈页 /feedback 和提交接口；存储与通知站长见 site_feedback.py。"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import feedback, site_feedback, stats
from . import limits
from .auth import SITE_AUTH
from .rendering import page_context, render, render_404

router = APIRouter()


@router.api_route(
    "/feedback", methods=["GET", "HEAD"], include_in_schema=False, response_class=HTMLResponse, dependencies=SITE_AUTH
)
async def feedback_page(request: Request):
    if not site_feedback.enabled():
        return render_404(request)
    ctx = page_context(
        request,
        title="意见反馈 - 求原图",
        description="页面异常、下载或转换不对、功能建议，都可以在这里告诉求原图。",
        path="/feedback",
    )
    ctx.update(
        {
            "kinds": site_feedback.KINDS,
            "form_token": site_feedback.form_token(),
            "body_max": site_feedback.BODY_MAX,
            "from_result": request.query_params.get("from") == "result",
        }
    )
    return render(request, "feedback.html", ctx)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=16)
    body: str = Field(max_length=site_feedback.BODY_MAX * 2)  # 先放宽，去掉首尾空白后再按字数判断
    email: str = Field(default="", max_length=254)
    page: str = Field(default="", max_length=500)
    link: str = Field(default="", max_length=2000)
    token: str = Field(max_length=100)


def _same_origin(request: Request) -> bool:
    # 和 /api/visit 一样：只收本站页面发来的请求
    origin = request.headers.get("origin", "")
    site = request.headers.get("sec-fetch-site", "same-origin")
    return origin == str(request.base_url).rstrip("/") and site == "same-origin"


def _checked_body(req: MessageRequest) -> str:
    if req.kind not in site_feedback.KINDS:
        raise HTTPException(400, "请选择反馈类型")
    body = req.body.strip()
    if not body:
        raise HTTPException(400, "写一下遇到了什么情况")
    if len(body) > site_feedback.BODY_MAX:
        raise HTTPException(400, f"内容太长了，请控制在 {site_feedback.BODY_MAX} 字以内")
    return body


@router.post("/api/feedback/message", include_in_schema=False, dependencies=SITE_AUTH)
async def submit_message(request: Request, req: MessageRequest, ip: str = Depends(limits.message_limit)):
    if not site_feedback.enabled():
        raise HTTPException(404, "Not Found")
    if not _same_origin(request):
        raise HTTPException(403, "Invalid origin")
    if not site_feedback.valid_form_token(req.token):
        raise HTTPException(400, "表单已失效，刷新页面后再提交一次")
    body = _checked_body(req)
    email = req.email.strip()
    if email and not feedback.valid_email(email):
        raise HTTPException(400, "邮箱格式不对")
    link = req.link.strip() if re.match(r"^https?://\S+$", req.link.strip()) else ""
    await asyncio.to_thread(
        site_feedback.add_message,
        req.kind,
        body,
        page=site_feedback.clean_page(req.page),
        browser=site_feedback.browser_label(request.headers.get("user-agent", "")),
        link=link,
        email=email,
        ip_hash=stats.hash_ip(ip),
    )
    site_feedback.wake()
    return {"ok": True, "email": bool(email)}
