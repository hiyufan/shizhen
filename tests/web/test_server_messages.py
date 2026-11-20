"""接口和后台任务返回中文提示，英文页面由前端按 locales/ui.en.json 翻译（见 static/js/i18n.js 的 serverMessage）。

报错文案改了没跟着改译文时，英文用户只会看到笼统的「操作失败」，这里把两边对上。
"""

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from qiuyuantu.convert import config, fetch, jobs
from qiuyuantu.web import limits, rendering

ROOT = Path(__file__).parents[2] / "src/qiuyuantu"
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


def _rate_limited(limit) -> str:
    with pytest.raises(HTTPException) as caught:
        for _ in range(limit.burst + 1):
            limit.hit("203.0.113.9")
    return caught.value.detail


def _dynamic_messages(monkeypatch) -> list[tuple[str, str]]:
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
        ("配好 2/7 张", "Paired 2/7 images"),
        ("7 张一起拉，稍等", "Downloading 7 images together"),
        ("2.5 MB 有点胖，瘦个身重来：宽 360 px、12 fps", "Retrying at 360 px and 12 fps"),
        (_rate_limited(limits.RateLimit("t", 1, 60)), "Too many requests"),
        ("现在解析的人太多了，8 秒后再试", "Too many people are parsing"),
        (streams.value.detail, "downloads in progress"),
        (limits.Concurrency("任务", 2)._busy, "tasks in progress"),
        (quota.value.detail, "Come back tomorrow"),
        (fetch.too_large_message(512e6, 300 << 20, True), "even the lowest quality"),
        (fetch.too_large_message(512e6, 300 << 20, False), "Try a lower quality"),
        ("原视频超过 300 MB，不支持转换", "cannot be converted"),
        ("视频没下完整，多半是超过了服务器 300 MB 的上限", "did not download completely"),
        (minutes, " min,"),
        (seconds, " s,"),
    ]


def test_server_messages_keep_numbers_in_english(monkeypatch, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    shutil.copyfile(ROOT / "static/js/i18n.js", tmp_path / "i18n.js")
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    (tmp_path / rendering.UI_MESSAGES_MODULE).write_text(rendering.ui_messages_module(), encoding="utf-8")
    script = """
import { readFileSync } from 'node:fs';
globalThis.document = { documentElement: { lang: 'en' } };
const { serverMessage } = await import('./i18n.js');
const messages = JSON.parse(readFileSync(0, 'utf8'));
console.log(JSON.stringify(messages.map(message => serverMessage(message))));
"""
    cases = _dynamic_messages(monkeypatch)
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=tmp_path,
        input=json.dumps([message for message, _ in cases]),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    numbers = re.compile(r"\d+(?:\.\d+)?")
    for (message, expected), translated in zip(cases, json.loads(result.stdout), strict=True):
        assert expected in translated, (message, translated)
        assert numbers.findall(translated) == numbers.findall(message), (message, translated)
        assert not re.search(r"[\u3400-\u9fff]", translated), (message, translated)
