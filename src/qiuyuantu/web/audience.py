"""第一方交互证据；不接收输入内容、完整来源、设备指纹或访问链接。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool

from .. import attribution, audience, stats
from . import limits

router = APIRouter()
activity_limit = limits.RateLimit("访问验证", burst=12, per=60)


class Activity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: StrictBool
    interaction: StrictBool
    automated: StrictBool


@router.post("/api/visit", dependencies=[Depends(activity_limit)])
async def record_visit(request: Request, activity: Activity):
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    if not stats.enabled() or not stats.counted(limits.client_ip(request)):
        return response
    origin = request.headers.get("origin", "")
    if origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Invalid origin")
    if request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise HTTPException(403, "Invalid origin")
    visit = attribution.decode(request.cookies.get(attribution.COOKIE, ""))
    if visit is None or not activity.visible or not activity.interaction:
        return response
    evidence = audience.agent_evidence(request.headers.get("user-agent", ""))
    status = "machine" if activity.automated or evidence.status == "machine" else "human"
    if status == "human" and not audience.browser_agent(request.headers.get("user-agent", "")):
        return response
    stats.record_activity(visit, status)
    response.set_cookie(
        audience.COOKIE,
        audience.activity_cookie(visit.id, status),
        max_age=audience.MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
    )
    return response
