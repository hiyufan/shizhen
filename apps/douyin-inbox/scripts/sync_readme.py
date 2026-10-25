"""从子项目 README 生成主 README 的可折叠嵌入内容，支持 CI 只读检查。"""

import argparse
import posixpath
import re
from pathlib import Path

BEGIN = "<!-- BEGIN DOUYIN-INBOX README -->"
END = "<!-- END DOUYIN-INBOX README -->"


def embedded_readme(source: str) -> str:
    def root_link(match: re.Match) -> str:
        label, target = match.groups()
        if target.startswith(("/", "#")) or ":" in target:
            return match[0]
        path = posixpath.normpath(posixpath.join("apps/douyin-inbox", target))
        return f"{label}({path})"

    lines, fenced = [], False
    for line in source.strip().splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            line = re.sub(r"(!?\[[^\]\n]*\])\(([^)\s]+)\)", root_link, line)
            if line.startswith("#"):
                line = "##" + line
        lines.append(line)
    return (
        BEGIN
        + "\n<details>\n<summary><b>查看账号助手的安装、使用与运维说明</b></summary>\n\n"
        + "\n".join(lines)
        + "\n\n</details>\n"
        + END
    )


def sync(root: Path, check: bool) -> bool:
    readme = root / "README.md"
    source = (root / "apps/douyin-inbox/README.md").read_text(encoding="utf-8")
    current = readme.read_text(encoding="utf-8")
    before, separator, rest = current.partition(BEGIN)
    if not separator or END not in rest:
        raise ValueError("主 README 缺少账号助手嵌入标记")
    _, _, after = rest.partition(END)
    expected = before + embedded_readme(source) + after
    if expected == current:
        return True
    if check:
        return False
    readme.write_text(expected, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查，不修改主 README")
    args = parser.parse_args()
    if not sync(Path(__file__).resolve().parents[3], args.check):
        parser.exit(1, "README 嵌入内容未同步，请运行 apps/douyin-inbox/scripts/sync_readme.py\n")


if __name__ == "__main__":
    main()
