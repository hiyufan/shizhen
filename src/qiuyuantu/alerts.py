"""自检告警：出问题了发邮件给站长，恢复了再发一封。

2026-10-15 21:25 宝塔面板「启动防火墙」清掉了 Docker 的 iptables 规则，容器全部断网，解析和每小时的
自检全部超时，12 小时后才有人发现——状态页一直在报失败，只是没人看。现在每轮自检后看一眼：

- 服务器级：连续两轮一半以上的平台都是超时 / 连不上 / 被拦（多半是服务器自己的网络出了问题）
- 单个平台：连续三轮都是解析出错 / 没拿到内容 / 超时 / 连不上 / 被拦（多半是平台改版了）

同一个问题只发一次，恢复了发一封；状态存在 data/alerts.json，部署重启不会重发。
没配 PARSE_VIDEO_ALERT_TO 或发不了信就什么都不做。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from . import feedback
from .convert import config

log = logging.getLogger(__name__)

ALERT_TO = os.environ.get("PARSE_VIDEO_ALERT_TO", "").strip()
STATE_PATH = config.DATA_DIR / "alerts.json"
STATUS_PAGE = "https://ynvan.com/status"

# 服务器自己连不出去时各平台的样子
_NETWORK = {"timeout", "network", "blocked"}
# 单个平台坏了的样子；login（平台要求登录，已知限制）、deleted / canary（自检链接失效）不算
_BROKEN = _NETWORK | {"parse", "empty"}
SERVER_RUNS = 2
PLATFORM_RUNS = 3


def enabled() -> bool:
    return bool(ALERT_TO) and feedback.mail_enabled()


def problems(history: dict[str, list[tuple[bool, str]]]) -> dict[str, str]:
    """{告警键: 说明}。history 是每个平台最近几轮的 (成功, 原因)，新的在前。"""
    found: dict[str, str] = {}
    if history:
        down = [
            p
            for p, runs in history.items()
            if len(runs) >= SERVER_RUNS and all(not ok and r in _NETWORK for ok, r in runs[:SERVER_RUNS])
        ]
        if len(down) >= max(2, (len(history) + 1) // 2):
            found["server"] = f"{len(down)}/{len(history)} 个平台连续 {SERVER_RUNS} 轮自检超时或连不上：" + "、".join(
                down
            )
    for p, runs in history.items():
        if len(runs) >= PLATFORM_RUNS and all(not ok and r in _BROKEN for ok, r in runs[:PLATFORM_RUNS]):
            found[f"platform:{p}"] = f"{p} 连续 {PLATFORM_RUNS} 轮自检失败，最近一次原因 {runs[0][1]}"
    if "server" in found:
        # 服务器断网时每个平台都会跟着报，一封就够了
        found = {k: v for k, v in found.items() if not k.startswith("platform:")}
    return found


def _load() -> dict[str, float]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(active: dict[str, float]) -> None:
    STATE_PATH.write_text(json.dumps(active, ensure_ascii=False), encoding="utf-8")


def _alert_text(new: dict[str, str]) -> tuple[str, str]:
    lines = [f"- {v}" for v in new.values()]
    hint = (
        '服务器级的问题先在容器里连一下外网（docker compose exec app python -c "import urllib.request; '
        "urllib.request.urlopen('https://x.com/robots.txt', timeout=10)\"）。宿主机能上网、容器不能，"
        "多半是防火墙重载清掉了 Docker 的规则：systemctl restart docker（会重启所有容器），"
        "之后确认 1Panel 的 openresty 没有被拉起来。"
        if "server" in new
        else "单个平台多半是改版了，看日志里这个平台的报错。"
    )
    subject = "求原图告警：" + ("服务器可能连不上外网" if "server" in new else "有平台自检连续失败")
    text = "\n".join(["自检发现问题：", *lines, "", hint, "", f"状态页：{STATUS_PAGE}", "", "— 求原图自检"])
    return subject, text


def _recovered_text(gone: list[str], active: dict[str, float]) -> tuple[str, str]:
    lines = [f"- {k}（持续 {(time.time() - active[k]) / 3600:.1f} 小时）" for k in gone]
    text = "\n".join(["这些问题的自检已经恢复正常：", *lines, "", f"状态页：{STATUS_PAGE}", "", "— 求原图自检"])
    return "求原图：自检已恢复", text


async def check(history: dict[str, list[tuple[bool, str]]]) -> None:
    """每轮自检后调用：有新问题发告警，旧问题好了发恢复。发信失败下一轮再试。"""
    if not enabled():
        return
    now = problems(history)
    active = _load()
    new = {k: v for k, v in now.items() if k not in active}
    gone = [k for k in active if k not in now]
    if new:
        subject, text = _alert_text(new)
        await asyncio.to_thread(feedback._send_mail, ALERT_TO, subject, text)
        log.warning("自检告警已发送：%s", "；".join(new.values()))
        active.update({k: time.time() for k in new})
    if gone:
        subject, text = _recovered_text(gone, active)
        await asyncio.to_thread(feedback._send_mail, ALERT_TO, subject, text)
        log.info("自检恢复通知已发送：%s", "、".join(gone))
        for k in gone:
            active.pop(k, None)
    if new or gone:
        _save(active)
