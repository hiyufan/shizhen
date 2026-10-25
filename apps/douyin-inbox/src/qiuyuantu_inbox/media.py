"""有界媒体准备、跨用户复用和过期文件清理。"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import time
import uuid
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from qiuyuantu.convert.fetch import fetch_bytes
from qiuyuantu.parser import parse_video_share_url

from .models import Message, Settings, content_key, media_url
from .store import Store
from .video_gif import encode_gif, gif_key, gif_sources, native_video_key


class UnsupportedMedia(ValueError):
    pass


@dataclass(frozen=True)
class Asset:
    url: str
    video: bool = False
    live: bool = False
    title: str = ""


def preparation_key(message: Message, variant: str = "") -> str:
    key = content_key(message)
    return hashlib.sha256(f"{variant}:{key}".encode()).hexdigest() if variant else key


def video_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        domains = ("douyinvod.com", "douyin.com", "amemv.com", "byteimg.com")
        return parsed.scheme == "https" and any(host == domain or host.endswith("." + domain) for domain in domains)
    except ValueError:
        return False


def snapshot_assets(row: dict) -> list[Asset] | None:
    url, video = row.get("url", ""), row.get("video", "")
    if row.get("is_video"):
        return [Asset(url, video=True, title=str(row.get("title") or ""))] if video_media_url(url) else None
    if not media_url(url) or row.get("live") and not video or video and not video_media_url(video):
        return None
    return [Asset(url)] + ([Asset(video, video=True, live=True)] if video else [])


def assets_from_snapshot(rows: list[dict] | None, settings: Settings) -> list[Asset] | None:
    if not rows:
        return None
    assets = []
    for row in rows:
        parsed = snapshot_assets(row)
        if parsed is None:
            return None
        assets.extend(parsed)
    if any(asset.video and not asset.live for asset in assets) and len(assets) != 1:
        return None
    return assets if len(assets) <= settings.max_files else None


def image_file(source: Path) -> tuple[Path, dict]:
    with Image.open(source) as image:
        frames = getattr(image, "n_frames", 1)
        if image.width * image.height > 40_000_000 or frames * image.width * image.height > 400_000_000:
            raise UnsupportedMedia("图片超过像素限制")
        details = {"width": image.width, "height": image.height, "frames": frames, "format": image.format}
        extension = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}.get(image.format)
        if extension:
            image.verify()
            target = source.with_suffix(extension)
            source.replace(target)
        elif image.format == "WEBP" and frames == 1:
            # 上传 PNG，保留平台给出的 WebP 文件，避免把格式适配称为取得了更高清版本。
            target = source.with_suffix(".png")
            image.save(target, format="PNG")
            source.replace(source.with_suffix(".webp"))
        else:
            raise UnsupportedMedia("暂不支持这种图片格式")
    return target, details


def file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def blocking(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # 线程不能取消；等编码/写文件结束后再删除工作目录，避免后台线程写入已清理的路径。
        with contextlib.suppress(Exception):
            await task
        raise


class Processor:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.settings = store, settings
        self.root = store.root / "artifacts"
        self.root.mkdir(exist_ok=True, mode=0o700)
        self.locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self.downloads = asyncio.Semaphore(3)
        self.encoders = asyncio.Semaphore(settings.encode_workers)
        self.space = asyncio.Lock()
        self.reserved_bytes = 0

    def files(self, manifest: dict) -> list[Path]:
        result = [(self.store.root / name).resolve() for name in manifest["files"]]
        if not all(path.is_relative_to(self.root.resolve()) and path.is_file() for path in result):
            raise UnsupportedMedia("缓存文件已过期或无效")
        return result

    async def describe(self, message: Message, assets: list[Asset] | None = None) -> dict:
        key = content_key(message)
        cached = await asyncio.to_thread(self.store.artifact, key, 0)
        if cached:
            self.files(cached)
            return {
                "prepared": cached,
                "artifact": key,
                "assets": [],
                "selectable": bool(cached.get("live_photo_count") or cached["files"][0].endswith(".mp4")),
            }
        assets = assets if assets is not None else await self._urls(message)
        if not 1 <= len(assets) <= self.settings.max_files:
            raise UnsupportedMedia("素材数量超过限制")
        count = sum(asset.live for asset in assets)
        for index, asset in enumerate(assets):
            if asset.live and (not index or assets[index - 1].video):
                raise UnsupportedMedia("实况元信息配对无效")
        return {
            "metadata_only": True,
            "resolved_at": time.time(),
            "assets": [asdict(asset) for asset in assets],
            "selectable": bool(count or len(assets) == 1 and assets[0].video),
            "live_photo_count": count,
            "title": message.data.get("title") or next((asset.title for asset in assets if asset.title), ""),
        }

    async def prepare(
        self, message: Message, assets: list[Asset] | None = None, *, variant: str = ""
    ) -> tuple[str, dict]:
        key = preparation_key(message, variant)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = await asyncio.to_thread(self.store.artifact, key, 0)
            if cached:
                try:
                    self.files(cached)
                    size = sum(path.stat().st_size for path in (self.root / key).iterdir())
                    await asyncio.to_thread(self.store.cache, key, cached, size, time.time())
                    return key, cached
                except UnsupportedMedia:
                    pass
            return key, await self._create(message, key, assets)

    def _check_space(self) -> None:
        size = sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())
        if size + self.reserved_bytes + self.settings.job_bytes > self.settings.disk_bytes:
            raise OSError("媒体缓存配额不足")
        if shutil.disk_usage(self.root).free < self.reserved_bytes + self.settings.job_bytes + (256 << 20):
            raise OSError("可用磁盘空间不足")

    async def _reserve(self) -> None:
        async with self.space:
            try:
                await asyncio.to_thread(self._check_space)
            except OSError:
                for key in await asyncio.to_thread(self.store.expired_artifacts, time.time(), True):
                    lock = self.locks.setdefault(key, asyncio.Lock())
                    async with lock:
                        if await asyncio.to_thread(self.store.remove_artifact, key):
                            await asyncio.to_thread(shutil.rmtree, self.root / key, True)
                await asyncio.to_thread(self._check_space)
            self.reserved_bytes += self.settings.job_bytes

    async def _urls(self, message: Message) -> list[Asset]:
        if message.kind == "media":
            return [Asset(message.data["url"])]
        kind = "note" if message.data.get("photos") else "video"
        url = (
            message.data["url"]
            if message.kind == "link"
            else (f"https://www.douyin.com/{kind}/{message.data['work_id']}")
        )
        info = await asyncio.wait_for(parse_video_share_url(url), timeout=90)
        if info.images:
            if message.data.get("live") and not any(image.live_photo_url for image in info.images):
                raise RuntimeError("实况视频地址暂未取得")
            assets = []
            for image in info.images:
                assets.append(Asset(image.url))
                if image.live_photo_url:
                    assets.append(Asset(image.live_photo_url, video=True, live=True))
            return assets
        if info.video_url:
            return [Asset(info.video_url, video=True, title=info.title or "")]
        raise UnsupportedMedia("没有可下载的图片或视频")

    async def _download(self, url: str, source: Path, video: bool) -> tuple[Path, dict]:
        limit = self.settings.video_bytes if video else self.settings.file_bytes
        async with self.downloads:
            await asyncio.wait_for(self._fetch(url, source, limit), timeout=90)
        if video:
            with source.open("rb") as file:
                if file.read(12)[4:8] != b"ftyp":
                    raise UnsupportedMedia("视频不是可直接回传的 MP4")
            target = source.with_suffix(".mp4")
            source.replace(target)
            return target, {"format": "MP4", "frames": None}
        target, details = await blocking(image_file, source)
        if target.stat().st_size > limit:
            raise UnsupportedMedia("格式适配后的文件过大")
        return target, details

    async def _fetch(self, url: str, source: Path, limit: int) -> None:
        # 连接尚未建立时可以立即重试一次；下载中断仍交给持久队列退避，避免反复搬运大文件。
        for attempt in range(2):
            try:
                await fetch_bytes(url, source, limit=limit, connect_timeout=self.settings.connect_timeout)
                return
            except (httpx.ConnectTimeout, httpx.ConnectError):
                if attempt:
                    raise

    async def startup_cleanup(self) -> None:
        known = await asyncio.to_thread(self.store.artifact_keys)
        for directory in self.root.iterdir():
            if directory.is_dir() and directory.name not in known:
                await asyncio.to_thread(shutil.rmtree, directory, True)

    async def _create(self, message: Message, key: str, assets: list[Asset] | None = None) -> dict:
        await self._reserve()
        try:
            return await self._build(message, key, assets)
        finally:
            async with self.space:
                self.reserved_bytes -= self.settings.job_bytes

    async def _build(self, message: Message, key: str, assets: list[Asset] | None = None) -> dict:
        assets = assets if assets is not None else await self._urls(message)
        if not 1 <= len(assets) <= self.settings.max_files:
            raise UnsupportedMedia("素材数量超过限制")
        temporary = self.root / f".{key}-{uuid.uuid4().hex}"
        temporary.mkdir(mode=0o700)
        try:
            results = []
            for index, asset in enumerate(assets):
                results.append(await self._download(asset.url, temporary / f"media-{index:02d}.source", asset.video))
                if sum(path.stat().st_size for path in temporary.iterdir()) > self.settings.job_bytes:
                    raise UnsupportedMedia("整条内容超过下载配额")
            destination = self.root / key
            if destination.exists():
                raise UnsupportedMedia("已有缓存正在等待处理，稍后重试")
            manifest = await blocking(self._manifest, key, results)
            manifest["live_photo_count"] = sum(asset.live for asset in assets)
            manifest["live_pairs"] = [[index - 1, index] for index, asset in enumerate(assets) if asset.live]
            manifest["title"] = message.data.get("title") or next((asset.title for asset in assets if asset.title), "")
            await asyncio.to_thread(temporary.rename, destination)
            size = sum(path.stat().st_size for path in destination.iterdir())
            await asyncio.to_thread(self.store.cache, key, manifest, size, time.time())
            return manifest
        finally:
            await asyncio.to_thread(shutil.rmtree, temporary, True)

    @staticmethod
    def _manifest(key: str, results: list[tuple[Path, dict]]) -> dict:
        return {
            "files": [f"artifacts/{key}/{path.name}" for path, _ in results],
            "details": [details for _, details in results],
            "md5": [file_md5(path) for path, _ in results],
        }

    async def native_motion(self, message: Message, downloader) -> tuple[str, dict]:
        key = native_video_key(message)
        async with self.locks.setdefault(key, asyncio.Lock()):
            cached = await asyncio.to_thread(self.store.artifact, key, 0)
            if cached:
                self.files(cached)
                return key, cached
            return key, await self._create_native_motion(key, message, downloader)

    async def _create_native_motion(self, key: str, message: Message, downloader) -> dict:
        await self._reserve()
        temporary = self.root / f".{key}-{uuid.uuid4().hex}"
        try:
            temporary.mkdir(mode=0o700)
            data = await downloader(message)
            if len(data) > self.settings.video_bytes:
                raise UnsupportedMedia("实况视频超过大小限制")
            target = temporary / "motion.mp4"
            await blocking(target.write_bytes, data)
            # 原消息摘要对应加密资源，浏览器适配器先核验；缓存摘要记录解码后的实际 MP4。
            if data[4:8] != b"ftyp":
                raise UnsupportedMedia("实况视频不是 MP4")
            manifest = await blocking(self._manifest, key, [(target, {"format": "MP4", "frames": None})])
            manifest["source_md5"] = message.data["live_md5"]
            await asyncio.to_thread(temporary.rename, self.root / key)
            await asyncio.to_thread(self.store.cache, key, manifest, len(data), time.time())
            return manifest
        finally:
            await asyncio.to_thread(shutil.rmtree, temporary, True)
            async with self.space:
                self.reserved_bytes -= self.settings.job_bytes

    async def gif_variant(self, source: dict) -> tuple[str, dict]:
        live_pairs(source)
        key = gif_key(source, self.settings)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = await asyncio.to_thread(self.store.artifact, key, 0)
            if cached and all(path.is_file() for path in self.files(cached)):
                size = sum(path.stat().st_size for path in self.files(cached))
                await asyncio.to_thread(self.store.cache, key, cached, size, time.time())
                return key, cached
            await self._reserve()
            temporary = self.root / f".{key}-{uuid.uuid4().hex}"
            try:
                temporary.mkdir(mode=0o700)
                results = []
                files = self.files(source)
                for number, index in enumerate(gif_sources(source)):
                    target = temporary / f"clip-{number:02d}.gif"
                    async with self.encoders:
                        details = await encode_gif(files[index], target, self.settings)
                    results.append((target, details))
                    if sum(path.stat().st_size for path, _ in results) > self.settings.job_bytes:
                        raise UnsupportedMedia("GIF 总大小超过配额")
                manifest = await blocking(self._manifest, key, results)
                destination = self.root / key
                if destination.exists():
                    raise UnsupportedMedia("GIF 缓存无效，稍后重试")
                await asyncio.to_thread(temporary.rename, destination)
                await asyncio.to_thread(
                    self.store.cache,
                    key,
                    manifest,
                    sum((destination / path.name).stat().st_size for path, _ in results),
                    time.time(),
                )
                return key, manifest
            finally:
                await asyncio.to_thread(shutil.rmtree, temporary, True)
                async with self.space:
                    self.reserved_bytes -= self.settings.job_bytes

    async def cleanup(self, now: float) -> None:
        expired = await asyncio.to_thread(self.store.expired_artifacts, now)
        for key in expired:
            lock = self.locks.setdefault(key, asyncio.Lock())
            async with lock:
                if key in await asyncio.to_thread(self.store.expired_artifacts, now) and await asyncio.to_thread(
                    self.store.remove_artifact, key
                ):
                    await asyncio.to_thread(shutil.rmtree, self.root / key, True)
        await asyncio.to_thread(self.store.prune, now)


def live_pairs(manifest: dict) -> dict[int, int]:
    files = manifest["files"]
    pairs = manifest.get("live_pairs", [])
    if manifest.get("live_photo_count", 0) != len(pairs):
        raise UnsupportedMedia("实况配对缓存不完整，请重新解析")
    for image, video in pairs:
        if not (0 <= image < video < len(files) and video == image + 1):
            raise UnsupportedMedia("实况配对缓存无效")
        if Path(files[image]).suffix.lower() not in {".jpg", ".png"} or Path(files[video]).suffix.lower() != ".mp4":
            raise UnsupportedMedia("实况配对格式无效")
    if len({index for pair in pairs for index in pair}) != len(pairs) * 2:
        raise UnsupportedMedia("实况配对缓存重复")
    return dict(pairs)


def delivery_parts(manifest: dict, batch_size: int = 3) -> list[dict]:
    files, digests = manifest["files"], manifest["md5"]
    pairs = live_pairs(manifest)
    videos = set(pairs.values())
    parts = []
    for index, (name, digest) in enumerate(zip(files, digests, strict=True)):
        if index in videos:
            continue
        if index in pairs:
            video_index = pairs[index]
            parts.append(
                {
                    "files": [name, files[video_index]],
                    "md5": [digest, digests[video_index]],
                    "live_pair": True,
                    "live_vid": uuid.uuid4().hex,
                    "client_id": str(uuid.uuid4()),
                }
            )
            continue
        video = Path(name).suffix.lower() == ".mp4"
        if (
            video
            or not parts
            or parts[-1].get("video")
            or parts[-1].get("live_pair")
            or len(parts[-1]["files"]) == batch_size
        ):
            parts.append({"files": [], "md5": [], "video": video})
        parts[-1]["files"].append(name)
        parts[-1]["md5"].append(digest)
    return parts
