"""接口和后台任务返回中文提示，英文页面由前端按 locales/ui.en.json 翻译（见 static/js/i18n.js 的 serverMessage）。

报错文案改了没跟着改译文时，英文用户只会看到笼统的「操作失败」，这里把两边对上。
"""

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from qiuyuantu.convert import config, fetch, jobs
from qiuyuantu.web import limits

ROOT = Path(__file__).parents[1] / "src/qiuyuantu"
UI = json.loads((ROOT / "locales/ui.en.json").read_text(encoding="utf-8"))
# 用户可能看到原文的抛出点：任务错误（job.error）、接口的 detail，以及特殊动画子进程转回来的报错
_RAISES = {"ValueError", "RuntimeError", "TooLarge", "HTTPException", "Refused", "DownloadCancelled", "QueueFull"}
# 只在启动时给站长看的，不进页面
_OPERATOR_ONLY = {"SSRF 防线在 uvloop 上不生效：用 python main.py 启动，或给 uvicorn 加 --loop asyncio"}


def _raised_literals():
    files = [*(ROOT / "convert").glob("*.py"), *(ROOT / "web").glob("*.py"), ROOT / "protobuf.py"]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                not isinstance(node, ast.Call)
                or getattr(node.func, "id", getattr(node.func, "attr", "")) not in _RAISES
            ):
                continue
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and re.search(r"[\u4e00-\u9fff]", arg.value)
                ):
                    yield f"{path.relative_to(ROOT)}:{node.lineno}", arg.value


def test_user_facing_errors_have_english_translations():
    missing = [f"{where} {text}" for where, text in _raised_literals() if text not in UI and text not in _OPERATOR_ONLY]
    assert not missing, "\n".join(missing)


def _patterns():
    source = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
    block = source.split("const SERVER_PATTERNS = [", 1)[1].split("\n];", 1)[0]
    pairs = re.findall(r"\[/(.+?)/,\s*'([^']+)'\]", block, re.S)
    assert pairs
    return [(re.compile(pattern.replace("(?<", "(?P<").replace("\\/", "/")), key) for pattern, key in pairs]


def _rate_limited(limit) -> str:
    with pytest.raises(HTTPException) as caught:
        for _ in range(limit.burst + 1):
            limit.hit("203.0.113.9")
    return caught.value.detail


def _dynamic_messages(monkeypatch) -> list[str]:
    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 300)
    minutes = jobs._timeout_message()
    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 45)
    seconds = jobs._timeout_message()
    busy = limits.Concurrency("下载", 1)
    busy.acquire("203.0.113.9")
    with pytest.raises(HTTPException) as streams:
        busy.acquire("203.0.113.9")
    daily = limits.DailyBytes(10 * 10**9)
    daily.add("203.0.113.9", daily.limit)
    with pytest.raises(HTTPException) as quota:
        daily.check("203.0.113.9")
    return [
        _rate_limited(limits.RateLimit("t", 1, 60)),
        streams.value.detail,
        limits.Concurrency("任务", 2)._busy,
        quota.value.detail,
        fetch.too_large_message(512e6, 300 << 20, True),
        fetch.too_large_message(512e6, 300 << 20, False),
        minutes,
        seconds,
    ]


def test_messages_with_numbers_match_a_translated_pattern(monkeypatch):
    patterns = _patterns()
    assert all(key in UI for _, key in patterns)
    for message in _dynamic_messages(monkeypatch):
        match = next((m for pattern, _ in patterns if (m := pattern.fullmatch(message))), None)
        assert match, message
