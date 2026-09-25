"""重新生成 Noto Serif SC 子集：改了站内文案（web/templates / content/*.toml）后跑一次；这是开发用工具，线上服务不依赖 Python。

需要 Noto Serif CJK SC 的 Bold / Black OTF（https://github.com/notofonts/noto-cjk/tree/main/Serif/OTF/SimplifiedChinese）
放在 fonts/ 目录或用 --src 指定。
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
PKG = os.path.join(ROOT, "web")

ap = argparse.ArgumentParser()
ap.add_argument("--src", default=os.path.join(ROOT, "fonts"), help="存放 NotoSerifCJKsc-Bold.otf / -Black.otf 的目录")
args = ap.parse_args()

text = ""
for f in glob.glob(os.path.join(PKG, "templates", "*.html")) + glob.glob(os.path.join(ROOT, "content", "*.toml")):
    text += open(f, encoding="utf-8").read()
chars = {ch for ch in text if ord(ch) > 0x2000}
chars |= set("0123456789+-–—·…（）()[]{}“”‘’、。，：；！？%×") | {chr(c) for c in range(0x20, 0x7F)}
glyphs = os.path.join(ROOT, "data", "glyphs.txt")
os.makedirs(os.path.dirname(glyphs), exist_ok=True)
open(glyphs, "w", encoding="utf-8").write("".join(sorted(chars)))
print(f"{len(chars)} 个字形")

for weight, name in (("700", "NotoSerifCJKsc-Bold.otf"), ("900", "NotoSerifCJKsc-Black.otf")):
    src = os.path.join(args.src, name)
    if not os.path.exists(src):
        sys.exit(f"缺少 {src}")
    out = os.path.join(PKG, "static", f"noto-serif-sc-{weight}.woff2")
    subprocess.run([sys.executable, "-m", "fontTools.subset", src, f"--text-file={glyphs}", "--flavor=woff2",
                    f"--output-file={out}", "--layout-features=kern,liga,locl", "--no-hinting", "--desubroutinize"], check=True)
    print(out, os.path.getsize(out) // 1000, "KB")
