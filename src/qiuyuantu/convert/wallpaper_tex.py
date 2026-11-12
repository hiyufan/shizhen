"""Wallpaper Engine TEXV0005 静态纹理与内嵌视频，只解码经过边界检查的数据。"""

from __future__ import annotations

import io
import struct

from PIL import Image

MAX_PIXELS = 16_777_216
MAX_BYTES = 128 << 20


class Reader:
    def __init__(self, content: bytes):
        self.content = content
        self.position = 0

    def read(self, count: int) -> bytes:
        if count < 0 or count > MAX_BYTES or self.position + count > len(self.content):
            raise ValueError("壁纸文件不完整或数据长度无效")
        start = self.position
        self.position += count
        return self.content[start : self.position]

    def integer(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def string(self) -> str:
        count = self.integer()
        if not 1 <= count <= 255:
            raise ValueError("壁纸文件名称长度无效")
        try:
            return self.read(count).decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("壁纸文件名称编码无效") from exc


def dimensions(width: int, height: int) -> None:
    if not 1 <= width <= 16384 or not 1 <= height <= 16384 or width * height > MAX_PIXELS:
        raise ValueError("壁纸图片尺寸超过限制")


def _lz4_length(reader: Reader, initial: int) -> int:
    total = initial
    if initial == 15:
        while True:
            value = reader.read(1)[0]
            total += value
            if total > MAX_BYTES:
                raise ValueError("纹理解压数据超过限制")
            if value != 255:
                break
    return total


def decompress(content: bytes, expected: int) -> bytes:
    """LZ4 原始块；每次扩展都检查输出预算，拒绝截断与越界回引用。"""
    if not 1 <= expected <= MAX_BYTES:
        raise ValueError("纹理解压数据超过限制")
    reader = Reader(content)
    output = bytearray()
    while reader.position < len(content):
        token = reader.read(1)[0]
        count = _lz4_length(reader, token >> 4)
        if len(output) + count > expected:
            raise ValueError("纹理解压长度无效")
        output.extend(reader.read(count))
        if reader.position == len(content):
            break
        offset = int.from_bytes(reader.read(2), "little")
        count = _lz4_length(reader, token & 15) + 4
        if not 1 <= offset <= len(output) or len(output) + count > expected:
            raise ValueError("纹理解压引用无效")
        # 重叠的匹配先展开一段再重复，避免按像素在 Python 中逐字节循环。
        block = output[-offset:]
        output.extend((block * ((count + offset - 1) // offset))[:count])
    if len(output) != expected:
        raise ValueError("纹理解压长度无效")
    return bytes(output)


def _mipmap(reader: Reader, version: int) -> tuple[int, int, bytes]:
    if version == 4:
        if (reader.integer(), reader.integer()) != (1, 2):
            raise ValueError("视频纹理参数无效")
        for _ in range(4096):
            if reader.read(1) == b"\0":
                break
        else:
            raise ValueError("视频纹理参数超过限制")
        if reader.integer() != 1:
            raise ValueError("视频纹理参数无效")
    width, height = reader.integer(), reader.integer()
    dimensions(width, height)
    compressed, expected = (reader.integer(), reader.integer()) if version >= 2 else (0, 0)
    if compressed not in (0, 1) or expected > MAX_BYTES:
        raise ValueError("纹理压缩参数无效")
    content = reader.read(reader.integer())
    return width, height, decompress(content, expected) if compressed else content


def _raw_image(fmt: int, width: int, height: int, content: bytes) -> Image.Image:
    if fmt in (0, 9):
        mode = {0: "RGBA", 9: "L"}[fmt]
        size = width * height * {0: 4, 9: 1}[fmt]
        if len(content) != size:
            raise ValueError("纹理像素长度与尺寸不符")
        return Image.frombytes(mode, (width, height), content)
    if fmt not in (4, 6, 7):
        raise ValueError("暂不支持这种纹理像素格式")
    code, block = {4: (b"DXT5", 16), 6: (b"DXT3", 16), 7: (b"DXT1", 8)}[fmt]
    size = ((width + 3) // 4) * ((height + 3) // 4) * block
    if len(content) != size:
        raise ValueError("纹理压缩像素长度与尺寸不符")
    # 构造最小 DDS 头，复用 Pillow 的 BC 解码，避免引入新的原生解码库。
    header = [124, 0x81007, height, width, size, 0, 0, *([0] * 11), 32, 4]
    dds = b"DDS " + struct.pack("<20I", *header) + code + struct.pack("<10I", *([0] * 10))
    image = Image.open(io.BytesIO(dds + content))
    image.load()
    return image


def _header(reader: Reader) -> tuple[int, int, int, int]:
    if reader.read(18) != b"TEXV0005\0TEXI0001\0":
        raise ValueError("暂不支持这个 TEX 文件版本")
    fmt, flags, tw, th, width, height, _ = (reader.integer() for _ in range(7))
    dimensions(tw, th)
    dimensions(width, height)
    if width > tw or height > th or flags & 4:
        raise ValueError("暂不支持动画图集纹理，请提取独立图片或视频")
    return fmt, flags, width, height


def _container(reader: Reader) -> tuple[int, tuple[int, int, bytes]]:
    magic = reader.read(9)
    if magic not in (b"TEXB0001\0", b"TEXB0002\0", b"TEXB0003\0", b"TEXB0004\0"):
        raise ValueError("暂不支持这个 TEX 数据版本")
    version = int(chr(magic[7]))
    if reader.integer() != 1:
        raise ValueError("暂不支持多层或动画纹理")
    image_format = reader.integer() if version >= 3 else 0xFFFFFFFF
    is_video = reader.integer() if version == 4 else 0
    version = 4 if version == 4 and is_video and image_format == 0xFFFFFFFF else min(version, 3)
    count = reader.integer()
    if not 1 <= count <= 16:
        raise ValueError("纹理层级数量无效")
    mipmaps = []
    total = 0
    for _ in range(count):
        mipmap = _mipmap(reader, version)
        total += len(mipmap[2])
        if total > MAX_BYTES:
            raise ValueError("纹理总像素数据超过限制")
        mipmaps.append(mipmap)
    return image_format, mipmaps[0]


def decode(content: bytes) -> tuple[bytes, str]:
    reader = Reader(content)
    fmt, flags, width, height = _header(reader)
    image_format, (mw, mh, data) = _container(reader)
    if flags & 32:
        if len(data) < 12 or data[4:8] != b"ftyp":
            raise ValueError("纹理没有包含有效的 MP4 视频")
        return data, ".mp4"
    if image_format != 0xFFFFFFFF:
        image = Image.open(io.BytesIO(data))
        dimensions(*image.size)
        image.load()
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("暂不支持动画图集纹理")
    else:
        image = _raw_image(fmt, mw, mh, data)
    if width > image.width or height > image.height:
        raise ValueError("纹理显示尺寸与像素数据不符")
    output = io.BytesIO()
    image.crop((0, 0, width, height)).convert("RGBA").save(output, format="PNG")
    return output.getvalue(), ".png"
