"""平台接收成功但只允许发送方查看时，不能标记接收方已送达。"""

import json
import shutil
import subprocess

import pytest

from qiuyuantu_inbox.browser import acknowledged
from qiuyuantu_inbox.delivery import DELIVERY_JS, DeliveryRestricted


@pytest.mark.parametrize(
    ("ext", "restricted", "code"),
    [
        ({}, False, 0),
        ({"im_callback_status_code": "0", "s:send_response_check_code": "0"}, False, 0),
        ({"im_callback_status_code": "8101", "s:send_response_check_code": "0"}, True, 8101),
        ({"s:send_response_status": "2"}, True, 2),
        ({"s:send_response_check_code": "1"}, True, 1),
        ({"s:visible": "sender"}, True, 0),
        ({"s:visible": "recipient"}, False, 0),
        ({"im_callback_status_code": "unexpected"}, True, 0),
    ],
)
def test_delivery_summary_exports_only_restriction_and_code(ext, restricted, code):
    node = shutil.which("node")
    if not node:
        pytest.skip("需要 Node.js")
    script = "const ext=JSON.parse(require('fs').readFileSync(0,'utf8'));" + DELIVERY_JS
    script += "console.log(JSON.stringify(deliveryStatus({sender:'sender',ext})));"
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(ext | {"private": "must-stay-in-page"}),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {"delivery_restricted": restricted, "delivery_code": code}


@pytest.mark.parametrize("part", [{"text": "hello"}, {"md5": ["digest"]}])
def test_matching_restricted_ack_raises_instead_of_confirming_delivery(part):
    row = {
        "id": "server",
        "conversation": "chat",
        "from_me": True,
        "flight": 4,
        "server_status": 0,
        "md5": "digest",
        "data": {"text": "hello"},
        "delivery_restricted": True,
        "delivery_code": 8101,
    }
    with pytest.raises(DeliveryRestricted) as error:
        acknowledged([row], part, "chat", set())
    assert error.value.code == 8101 and error.value.server_id == "server"
    assert acknowledged([row], part, "other", set()) == []
