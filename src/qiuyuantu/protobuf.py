"""有界读取动画和贴纸协议的 protobuf 字段，不执行平台提供的代码。"""

import struct


def varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("protobuf 字段被截断")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("protobuf 整数超过限制")


def fields(data: bytes, *, limit: int = 100_000) -> list[tuple[int, int | bytes | float]]:
    offset = 0
    result = []
    while offset < len(data):
        tag, offset = varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not number or len(result) >= limit:
            raise ValueError("protobuf 字段数量或编号无效")
        value, offset = _value(data, offset, wire)
        result.append((number, value))
    return result


def _value(data: bytes, offset: int, wire: int) -> tuple[int | bytes | float, int]:
    if wire == 0:
        return varint(data, offset)
    if wire == 2:
        size, offset = varint(data, offset)
    elif wire in {1, 5}:
        size = 8 if wire == 1 else 4
    else:
        raise ValueError("不支持的 protobuf 字段编码")
    end = offset + size
    if end > len(data):
        raise ValueError("protobuf 字段长度无效")
    raw = data[offset:end]
    return (struct.unpack("<f", raw)[0] if wire == 5 else raw), end
