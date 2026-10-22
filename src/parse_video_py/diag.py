"""站长诊断：在服务器上看看各平台到底返回了什么。

    python -m parse_video_py.diag <分享链接>
    docker compose exec app python -m parse_video_py.diag <分享链接>

会打印：出口 IP 与归属地、解析结果或错误原因、以及小红书 / B站 原始响应的状态码、最终地址和页面标题。
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import sys
from importlib.metadata import PackageNotFoundError, version

from . import parse_video_share_url
from .parser.errors import ParseError
from .utils import create_async_client, current_source, extract_url, is_youtube_url

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"


async def egress() -> None:
    for name, source in (("默认出口", ""), ("国内平台出口", "bilibili")):
        token = current_source.set(source)
        try:
            async with create_async_client(timeout=20) as c:
                try:
                    d = (await c.get("https://myip.ipip.net/json")).json().get("data", {})
                    print(f"[{name}] {d.get('ip')}  {' '.join(d.get('location') or [])}")
                except Exception:
                    d = (
                        await c.get("http://ip-api.com/json/?fields=query,country,regionName,isp,hosting&lang=zh-CN")
                    ).json()
                    where = f"{d.get('country')} {d.get('regionName')}  {d.get('isp')}"
                    print(f"[{name}] {d.get('query')}  {where}  机房={d.get('hosting')}")
        except Exception as e:  # noqa: BLE001
            print(f"[{name}] 探测失败: {e}")
        finally:
            current_source.reset(token)


async def raw(url: str, source: str) -> None:
    token = current_source.set(source)
    try:
        async with create_async_client(follow_redirects=True, timeout=30) as c:
            r = await c.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        title = re.search(r"<title>(.*?)</title>", r.text, re.S)
        print(f"[原始响应] {r.status_code}  最终地址: {str(r.url)[:100]}")
        print(f"           标题: {(title.group(1).strip() if title else '无')[:60]}  长度: {len(r.text)}")
        for marker in ("__INITIAL_STATE__", "noteDetailMap", "验证", "captcha", "登录", "404"):
            if marker in r.text:
                print(f"           页面包含: {marker}")
    except Exception as e:  # noqa: BLE001
        print(f"[原始响应] 失败: {e}")
    finally:
        current_source.reset(token)


def youtube_setup() -> None:
    """只报告依赖与配置是否存在，不输出 cookies、代理凭证或 PO Token。"""
    from .convert import config, youtube_native
    from .utils import youtube_proxy

    for package in ("yt-dlp", "yt-dlp-ejs", "bgutil-ytdlp-pot-provider"):
        try:
            installed = version(package)
        except PackageNotFoundError:
            installed = "未安装"
        print(f"[YouTube 依赖] {package}={installed}")
    runtime = next(iter(config.ytdlp_runtime_opts().get("js_runtimes", {})), "未安装")
    route = "专用代理" if youtube_proxy("https://www.youtube.com/") else "默认出口"
    print(f"[YouTube 配置] JS={runtime} PO服务={'已配置' if config.POT_URL else '未配置'} 出口={route}")
    print(f"[YouTube 兜底] 原生会话={'已配置' if youtube_native.enabled() else '未配置或已关闭'}")


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    url = extract_url(" ".join(sys.argv[1:])) or sys.argv[1]
    print(f"链接: {url}\n")
    from .convert import relay
    from .utils import proxy_for

    route = (
        "中继 " + relay.RELAY_URL
        if relay.enabled()
        else ("代理 " + proxy_for("bilibili") if proxy_for("bilibili") else "服务器直连")
    )
    print(f"国内平台走: {route}")
    if is_youtube_url(url):
        youtube_setup()
    await egress()
    print()
    try:
        info = await parse_video_share_url(url)
        d = dataclasses.asdict(info)
        print(
            f"[解析成功] 平台={d['source']} 标题={d['title'][:30]!r} 视频={'有' if d['video_url'] else '无'} "
            f"图片={len(d['images'])} 清晰度={[f['label'] for f in d['formats']]}"
        )
    except ParseError as e:
        print(f"[解析失败] 原因={e.reason}  {e}")
    except Exception as e:  # noqa: BLE001
        print(f"[解析失败] {type(e).__name__}: {e}")
    print()
    if "xiaohongshu.com" in url or "xhslink" in url:
        await raw(url, "redbook")
    elif "bilibili.com" in url or "b23.tv" in url:
        bv = re.search(r"BV\w+", url)
        if bv:
            await raw(f"https://api.bilibili.com/x/web-interface/view?bvid={bv.group(0)}", "bilibili")


if __name__ == "__main__":
    asyncio.run(main())
