"""重新取得固定的官方 libpag 运行库，校验 npm 发布包并保留完整许可证。"""

import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path

from qiuyuantu.utils import create_async_client

VERSION = "4.5.85"
URL = f"https://registry.npmjs.org/libpag/-/libpag-{VERSION}.tgz"
SHA512 = (
    "1303141c18c141df448bc2f084aaf2f33635b664a39bbdde8f00655e0e7f41292d"
    "51659f7c520fbeb1c9dbdfd972c069002f1d5319fcb9478d65f040bf12dfa5"
)
DESTINATION = Path(__file__).resolve().parents[1] / "src/qiuyuantu/convert/vendor"
FILES = {
    "package/lib/libpag.min.js": "libpag.min.js",
    "package/lib/libpag.wasm": "libpag.wasm",
    "package/LICENSE.txt": "LIBPAG-LICENSE",
}


async def main() -> None:
    data = bytearray()
    async with create_async_client(timeout=30, follow_redirects=True) as client, client.stream("GET", URL) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes(65536):
            data.extend(chunk)
            if len(data) > 8 << 20:
                raise ValueError("发布包超过大小限制")
    if hashlib.sha512(data).hexdigest() != SHA512:
        raise ValueError("发布包校验失败；不要使用未核对的新内容覆盖运行库")
    DESTINATION.mkdir(exist_ok=True)
    hashes = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for source, target in FILES.items():
            member = archive.getmember(source)
            if not member.isfile() or member.size > 4 << 20:
                raise ValueError("发布包中的文件无效")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("发布包缺少运行文件")
            content = stream.read()
            (DESTINATION / target).write_bytes(content)
            hashes[target] = hashlib.sha256(content).hexdigest()
    manifest = {"name": "libpag", "version": VERSION, "url": URL, "tarball_sha512": SHA512, "files_sha256": hashes}
    (DESTINATION / "LIBPAG-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"version": VERSION, "files": hashes}))


if __name__ == "__main__":
    asyncio.run(main())
