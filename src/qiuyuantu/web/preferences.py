"""页面偏好与翻译；Cookie 只保存白名单选项，不参与接口行为。"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

from fastapi import Request
from jinja2 import pass_context
from markupsafe import Markup


@functools.cache
def english() -> dict[str, str]:
    path = Path(__file__).resolve().parent.parent / "locales"
    return json.loads((path / "en.json").read_text(encoding="utf-8")) | ui_english()


@functools.cache
def ui_english() -> dict[str, str]:
    path = Path(__file__).resolve().parent.parent / "locales" / "ui.en.json"
    return json.loads(path.read_text(encoding="utf-8"))


def browser_language(header: str) -> str:
    """按权重挑首个支持的语言；其它语言用英语，没有偏好时保留中文。"""
    choices = []
    for entry in header.split(","):
        tag, *params = entry.strip().lower().split(";")
        quality = 1.0
        try:
            for param in params:
                if param.strip().startswith("q="):
                    quality = float(param.strip()[2:])
        except ValueError:
            continue
        if tag and 0 < quality <= 1:
            choices.append((quality, tag.split("-")[0]))
    for _, tag in sorted(choices, key=lambda item: -item[0]):
        if tag in {"zh", "en"}:
            return tag
    return "en" if choices else "zh"


def preferences(request: Request) -> dict[str, str]:
    language = request.cookies.get("qyt_language", "auto")
    theme = request.cookies.get("qyt_theme", "auto")
    language = language if language in {"zh", "en"} else "auto"
    return {
        "language": language,
        "locale": browser_language(request.headers.get("accept-language", "")) if language == "auto" else language,
        "theme": theme if theme in {"light", "dark"} else "auto",
    }


def translate(value, locale: str):
    if locale != "en" or not isinstance(value, str):
        return value
    translated = english().get(value)
    if translated is None:
        translated = _formatted_text(value)
    return Markup(translated) if isinstance(value, Markup) else translated


def _formatted_text(value: str) -> str:
    if value.endswith(" - 求原图"):
        return english().get(value[:-6], value[:-6]) + " — Qiuyuantu"
    patterns = (
        (r"^(\d+) 月 (\d+) 日(.*)$", r"\1/\2\3"),
        (r"^([\d.]+) 秒$", r"\1 s"),
    )
    for pattern, replacement in patterns:
        if re.match(pattern, value):
            return re.sub(pattern, replacement, value)
    return value


@pass_context
def t(context, value, **values):
    result = translate(value, context.get("locale", "zh"))
    return result.format_map(values) if values else result


def status_headline(snap: dict) -> str:
    from .. import status

    n = snap["n_checked"]
    if not n:
        return "Collecting check results. Checks start five minutes after startup, then run hourly."
    when = translate(status.bj_time(snap["checked_at"], snap["now"]), "en")
    if not snap["down"]:
        return f"Checked {when}: all {n} platforms are operational."
    down = ", ".join(translate(label, "en") for label in snap["down"])
    return f"Checked {when}: {down} failed; the other {n - len(snap['down'])} platforms are operational."


def translate_data(value, locale: str):
    """结构化数据使用同一份译文，保留 URL、数值和 schema 的键。"""
    if isinstance(value, dict):
        return {key: locale if key == "inLanguage" else translate_data(item, locale) for key, item in value.items()}
    if isinstance(value, list):
        return [translate_data(item, locale) for item in value]
    return translate(value, locale)
