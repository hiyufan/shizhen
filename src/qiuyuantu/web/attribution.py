"""把公开页面的访问来源传给统计，接口沿用已签名的访问会话。"""

from fastapi import Request, Response

from .. import attribution, seo

PUBLIC_PATHS = frozenset(seo.all_paths())


def visit_for(request: Request) -> attribution.Visit | None:
    previous = attribution.decode(request.cookies.get(attribution.COOKIE, ""))
    path = request.url.path
    if request.method != "GET" or path not in PUBLIC_PATHS:
        return previous
    evidence = attribution.origin(
        request.headers.get("referer", ""),
        attribution.hostname(seo.SITE_URL) or request.url.hostname or "",
        request.query_params,
    )
    return attribution.choose(previous, path, evidence)


def set_cookie(request: Request, response: Response, visit: attribution.Visit | None) -> None:
    if visit is None or response.status_code != 200 or request.method == "HEAD":
        return
    path = request.url.path
    # 只在页面响应续期。慢接口的旧响应不能覆盖访客刚打开的新推广来源。
    if path not in PUBLIC_PATHS:
        return
    response.set_cookie(
        attribution.COOKIE,
        attribution.encode(visit),
        max_age=attribution.MAX_AGE,
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "private, no-cache"
