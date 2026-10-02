"""测试隔离：配置在 import 时就读环境变量，必须赶在被测模块加载前改好。

data/ 是挂进线上容器的目录，里面有真正的 secret.key，测试绝不能碰它。
"""

import os
import tempfile

os.environ["PARSE_VIDEO_DATA_DIR"] = tempfile.mkdtemp(prefix="shizhen-test-")
os.environ["PARSE_VIDEO_SECRET"] = "test-secret"
for _name in (
    "PARSE_VIDEO_STATS_TOKEN",
    "PARSE_VIDEO_RELAY_CN",
    "PARSE_VIDEO_RELAY_TOKEN",
    "PARSE_VIDEO_EDGE_IMG",
    "PARSE_VIDEO_USERNAME",
    "PARSE_VIDEO_PASSWORD",
    "PARSE_VIDEO_TRUST_PROXY",
    "PARSE_VIDEO_PROXY",
    "PARSE_VIDEO_PROXY_CN",
):
    os.environ.pop(_name, None)
