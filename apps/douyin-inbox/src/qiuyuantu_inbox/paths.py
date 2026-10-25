"""账号目录独立于安装位置；保留旧数据目录配置的迁移兼容。"""

import os
from pathlib import Path


def account_root() -> Path:
    if explicit := os.environ.get("PARSE_VIDEO_ACCOUNT_DIR"):
        return Path(explicit).expanduser().resolve()
    if legacy := os.environ.get("PARSE_VIDEO_DATA_DIR"):
        return (Path(legacy).expanduser() / "accounts" / "douyin").resolve()
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (data.expanduser() / "qiuyuantu-inbox").resolve()
