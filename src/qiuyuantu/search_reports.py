"""定期同步 Google / Bing 站长报表；网页只读取本地快照。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import search_report_store as store
from . import stats, windsor

SYNC_INTERVAL = 6 * 3600
RETRY_INTERVAL = 15 * 60
LOOKBACK_DAYS = 180
PROVIDERS = {
    "google": ("searchconsole", "PARSE_VIDEO_SEARCH_GOOGLE_SITE"),
    "bing": ("bing_webmaster", "PARSE_VIDEO_SEARCH_BING_SITE"),
}
_syncing: set[str] = set()


def api_key() -> str:
    return os.environ.get("PARSE_VIDEO_WINDSOR_API_KEY", "").strip()


def accounts() -> dict[str, str]:
    return {provider: os.environ.get(env, "").strip() for provider, (_, env) in PROVIDERS.items()}


def enabled() -> bool:
    return stats.enabled() and bool(api_key()) and any(accounts().values())


def _date_range() -> tuple[str, str]:
    # GSC 日期按太平洋时间；排除当天，并显式关闭 Google 尚未定稿的数据。
    end = datetime.now(ZoneInfo("America/Los_Angeles")).date() - timedelta(days=1)
    return (end - timedelta(days=LOOKBACK_DAYS - 1)).isoformat(), end.isoformat()


def _params(provider: str, account: str, kind: str, start: str, end: str) -> dict:
    fields = ["site", "date", "clicks", "impressions"]
    if kind != "daily":
        fields.append("query" if kind == "queries" else "page")
    params = {"select_accounts": account, "date_from": start, "date_to": end}
    if provider == "google":
        fields.extend(["position", "search_type"])
        params.update(include_fresh_data="false", filter='[["search_type","eq","web"]]')
    params["fields"] = ",".join(fields)
    return params


async def _fetch_reports(provider: str, account: str) -> None:
    start, end = _date_range()
    connector = PROVIDERS[provider][0]
    reports = {}
    kinds = ("daily", "queries", "pages") if provider == "google" else ("daily", "queries")
    for kind in kinds:
        rows, limited = await windsor.fetch(connector, api_key(), _params(provider, account, kind, start, end))
        if kind == "daily" and limited:
            raise windsor.ReportError("too_large")
        reports[kind] = {"rows": store.normalize(rows, kind, account, start, end), "limited": limited}
    await asyncio.to_thread(store.save, provider, account, start, end, reports)


async def sync_provider(provider: str, account: str) -> None:
    if provider in _syncing:
        return
    _syncing.add(provider)
    try:
        previous = await asyncio.to_thread(store.status, provider, account)
        interval = RETRY_INTERVAL if previous.get("error") else SYNC_INTERVAL
        if time.time() - previous.get("attempted", 0) < interval:
            return
        await asyncio.to_thread(store.mark_attempt, provider, account, "pending")
        await asyncio.wait_for(_fetch_reports(provider, account), timeout=300)
    except windsor.ReportError as exc:
        await asyncio.to_thread(store.mark_attempt, provider, account, str(exc))
    except asyncio.TimeoutError:
        await asyncio.to_thread(store.mark_attempt, provider, account, "timeout")
    except Exception:
        # 禁止将带凭证的请求 / 上游正文保存在数据库或日志里。
        await asyncio.to_thread(store.mark_attempt, provider, account, "unavailable")
    finally:
        _syncing.discard(provider)


async def sync_once() -> None:
    if enabled():
        await asyncio.to_thread(store.initialize)
        await asyncio.gather(*(sync_provider(provider, account) for provider, account in accounts().items() if account))


async def loop() -> None:
    while True:
        # 本地缓存暂时不可写时也继续重试；不能让一次磁盘异常终止整个后台任务。
        with contextlib.suppress(Exception):
            await sync_once()
        await asyncio.sleep(RETRY_INTERVAL)


def _provider_summary(provider: str, account: str, days: int) -> dict:
    configured = bool(account and api_key())
    result = {"provider": provider, "account": account, "state": "unconfigured", "report": None}
    if not configured:
        return result
    current = store.status(provider, account)
    saved = store.snapshot(provider, account)
    error = current.get("error", "")
    state = "error" if error and error != "pending" else "pending"
    if saved:
        state = "stale" if error or time.time() - saved["synced"] > 2 * SYNC_INTERVAL else "ready"
    if provider in _syncing:
        state = "syncing"
    return {
        **result,
        "state": state,
        "error": error,
        "attempted": current.get("attempted"),
        "report": store.summarize(saved, days) if saved else None,
    }


def summary(days: int = 28) -> dict:
    return {"days": days, "providers": [_provider_summary(p, a, days) for p, a in accounts().items()]}
