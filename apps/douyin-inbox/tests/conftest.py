"""子项目独立运行测试时，也必须先隔离共享解析器的配置与账号数据。"""

import os
import tempfile

os.environ["PARSE_VIDEO_DATA_DIR"] = tempfile.mkdtemp(prefix="qiuyuantu-inbox-test-")
os.environ["PARSE_VIDEO_ACCOUNT_DIR"] = tempfile.mkdtemp(prefix="qiuyuantu-account-test-")
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
