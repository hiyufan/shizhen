"""读取页面脚本里的 JS 对象字面量（不执行脚本）。

有的平台把页面数据写成 `window.x = {key: 'v', n: '12' * 1, list: [ ... ],}` 这样的字面量：
键不带引号、字符串用单引号和 \\xNN 转义、数字写成 `'12' * 1`、允许尾随逗号，json 解析不了。
这里只认这些写法；遇到函数调用、变量引用之类就抛 ValueError，由调用方当作页面改版处理。
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")
      | (?P<num>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
      | (?P<word>[A-Za-z_$][\w$]*)
      | (?P<punct>[{}\[\]:,*])
    )""",
    re.S | re.X,
)
_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", re.S)
_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_WORDS = {"true": True, "false": False, "null": None, "undefined": None}
_MAX_DEPTH = 64
_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _unescape(body: str) -> str:
    def replace(m: re.Match) -> str:
        code = m.group(1)
        if code[0] in "xu" and len(code) > 1:
            return chr(int(code[1:], 16))
        return _SIMPLE.get(code, code)

    return _ESCAPE.sub(replace, body)


def _number(raw: str) -> int | float | None:
    """JS 的 Number(raw)：空串是 0，不是数字的是 NaN（记成 None）。"""
    raw = raw.strip()
    if not raw:
        return 0
    if not _NUMERIC.fullmatch(raw):
        return None
    number = float(raw)
    return int(number) if number.is_integer() else number


class _Reader:
    def __init__(self, text: str, pos: int) -> None:
        self.text, self.pos, self.depth = text, pos, 0

    def _peek(self) -> tuple[str, str, int]:
        m = _TOKEN.match(self.text, self.pos)
        if not m:
            raise ValueError(f"无法识别的内容：{self.text[self.pos : self.pos + 30]!r}")
        return m.lastgroup or "", m.group(m.lastgroup), m.end()

    def _take(self) -> tuple[str, str]:
        kind, value, end = self._peek()
        self.pos = end
        return kind, value

    def _next_is(self, punct: str) -> bool:
        return self._peek()[:2] == ("punct", punct)

    def value(self) -> Any:
        kind, raw = self._take()
        if kind == "str":
            text = _unescape(raw[1:-1])
            return self._scaled(text) if self._next_is("*") else text
        if kind == "num":
            return self._scaled(raw) if self._next_is("*") else _number(raw)
        if kind == "word" and raw in _WORDS:
            return _WORDS[raw]
        if (kind, raw) == ("punct", "{"):
            return self._nested(self._object)
        if (kind, raw) == ("punct", "["):
            return self._nested(self._array)
        raise ValueError(f"不支持的值：{raw!r}")

    def _nested(self, read) -> Any:
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise ValueError("嵌套过深")
        result = read()
        self.depth -= 1
        return result

    def _scaled(self, raw: str) -> int | float | None:
        """`'1152' * 1` 是数字；`'abc' * 1` 在 JS 里是 NaN，记成 None。"""
        self._take()
        kind, factor = self._take()
        number = _number(raw)
        if kind != "num":
            raise ValueError(f"不支持的乘数：{factor!r}")
        if number is None:
            return None
        product = number * float(factor)
        return int(product) if float(product).is_integer() else product

    def _separator(self, closing: str) -> bool:
        """读掉逗号返回 False；读到结束括号返回 True。"""
        _, raw = self._take()
        if raw == closing:
            return True
        if raw != ",":
            raise ValueError(f"缺少 , 或 {closing}")
        return False

    def _object(self) -> dict:
        result: dict = {}
        while not self._next_is("}"):
            kind, raw = self._take()
            key = _unescape(raw[1:-1]) if kind == "str" else raw
            if self._take() != ("punct", ":"):
                raise ValueError("缺少 :")
            result[key] = self.value()
            if self._separator("}"):
                return result
        self._take()
        return result

    def _array(self) -> list:
        result: list = []
        while not self._next_is("]"):
            result.append(self.value())
            if self._separator("]"):
                return result
        self._take()
        return result


def parse_at(text: str, pos: int) -> Any:
    """从 text[pos] 开始读一个值（对象、数组、字符串或数字）。"""
    return _Reader(text, pos).value()
