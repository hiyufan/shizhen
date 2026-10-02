"""CLI parse 命令核心逻辑"""

import asyncio
import sys
from pathlib import Path
from typing import NoReturn

import typer

from parse_video_py import parse_video_share_url
from parse_video_py.cli.output import output_batch_error, output_result
from parse_video_py.parser.base import VideoInfo
from parse_video_py.utils import extract_url

_CONCURRENCY_LIMIT = 10

ParseOutcome = tuple[VideoInfo | None, str | None]  # (结果, 错误信息)


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _read_inputs_from_file(file_path: str) -> list[str]:
    """从文件读取 URL 列表，每行一个；- 代表 stdin"""
    if file_path == "-":
        lines = sys.stdin.read().splitlines()
    else:
        try:
            lines = Path(file_path).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            _fail(f"无法读取文件: {file_path}")
    return [line.strip() for line in lines if line.strip()]


def _collect_inputs(urls: list[str] | None, file: str | None) -> list[str]:
    if urls and file:
        _fail("不能同时指定链接和文件输入")
    if file:
        return _read_inputs_from_file(file)
    if urls:
        return list(urls)
    _fail("请提供要解析的链接或指定 --file")


async def _parse_single(url: str) -> ParseOutcome:
    """解析单条 URL，返回 (VideoInfo, error_msg)"""
    extracted = extract_url(url)
    if not extracted:
        return None, f"未检测到有效的分享链接: {url}"
    try:
        return await parse_video_share_url(extracted), None
    except Exception as e:  # noqa: BLE001 - 命令行里只打印原因
        return None, str(e)


async def _parse_batch(urls: list[str]) -> list[ParseOutcome]:
    """批量解析 URL（同时最多 _CONCURRENCY_LIMIT 条）"""
    sem = asyncio.Semaphore(_CONCURRENCY_LIMIT)

    async def limited(url: str) -> ParseOutcome:
        async with sem:
            return await _parse_single(url)

    return await asyncio.gather(*(limited(url) for url in urls))


def _print_single(url: str, fmt: str) -> None:
    info, err = asyncio.run(_parse_single(url))
    if err:
        _fail(f"解析失败: {err}")
    output_result(info, fmt)


def _print_batch(urls: list[str], fmt: str) -> None:
    results = asyncio.run(_parse_batch(urls))
    for i, (url, (info, err)) in enumerate(zip(urls, results, strict=True)):
        if i > 0 and fmt == "text":
            print()
        if err:
            output_batch_error(url, err)
        else:
            output_result(info, fmt)
    if all(err for _, err in results):
        _fail(f"所有 {len(urls)} 条解析均失败")


def run_parse(urls: list[str] | None, fmt: str, file: str | None) -> None:
    """parse 命令入口，由 cli/__init__.py 延迟调用"""
    if fmt not in ("json", "text"):
        _fail(f"不支持的输出格式: {fmt}，可选值: json, text")
    inputs = _collect_inputs(urls, file)
    if len(inputs) == 1:
        _print_single(inputs[0], fmt)
    elif inputs:
        _print_batch(inputs, fmt)
