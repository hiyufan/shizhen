"""评论详情延迟加载、原图身份与动态资源缺失的网页契约。"""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.comment_media import COMMENT_JS, read_comment_status

FIXTURE = r"""
const scenario = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const uri = 'tos-cn-i-p14lwwcsbr/image';
const url = `https://p11-sign.douyinpic.com/obj/${uri}?x-signature=example`;
const args = {account: '100', sender: '200', conversation: '0:1:100:200', message: '123', url};
const card = {itemId: '456', comment_id: '789', comment_url: {uri, url_list: [url]},
  authentication_token: 'must-stay-in-page'};
const comment = {cid: '789', aweme_id: '456', image_list: [{origin_url: {
  uri, url_list: [`https://p9-sign.douyinpic.com/${uri}~tplv-resize.image`]}}], video_list: null};
const source = {serverId: '123', sender: '200', conversationId: args.conversation, type: 105};
const target = {id: args.conversation, type: 1};
let login = '100';
const uncached = scenario.startsWith('fetch') || scenario.endsWith('_after_read');
const result = {status_code: 0, comments: [comment], secret: 'must-stay-in-page'};
const calls = [];
if (scenario === 'sender') source.sender = '300';
if (scenario === 'account') login = '300';
if (scenario === 'own') args.sender = source.sender = '100';
if (scenario === 'group') target.type = 2;
if (scenario === 'target') target.id = 'other';
if (scenario === 'message') source.serverId = 'other';
if (scenario === 'type') source.type = 77;
if (scenario === 'conversation') source.conversationId = 'other';
if (scenario === 'actor') args.conversation = source.conversationId = target.id = '0:1:100:300';
if (scenario === 'card_id') card.comment_id = 'invalid';
if (scenario === 'card_url') card.comment_url.url_list = ['https://p9.douyinpic.com/other'];
if (scenario === 'other_work') comment.aweme_id = 'other';
if (scenario === 'other_image') comment.image_list[0].origin_url.uri = 'other';
if (scenario === 'processed_preview') {
  args.url = card.comment_url.url_list[0] = url.replace('/image?', '/image~tplv.jpg?');
}
if (scenario === 'preview_query') args.url = card.comment_url.url_list[0] = url + '&imageView2=thumbnail';
if (scenario === 'motion') comment.video_list = [{video: {}}];
if (scenario === 'image_motion') comment.image_list[0].live_photo_type = 1;
if (scenario === 'unknown_motion') comment.video_list = {};
if (scenario === 'fetch_rejected') result.status_code = 1;
if (scenario === 'fetch_invalid') delete result.status_code;
if (scenario === 'fetch_other' || scenario === 'missing') result.comments = [];
if (scenario === 'fetch_too_many') result.comments = Array(21).fill(comment);
source.content = JSON.stringify(card);
const sdk = {getConversation: () => target, getConversationMessages: () => [source]};
const app = {store: {usersInfoStore: {curLoginUserInfo: {get uid() {return login;}}},
  awemeStore: {commentMap: new Map(uncached ? [] : [['456789', result.comments]])}},
  imSdkService: {imSdkManager: {getImSdkInstance: () => sdk}},
  requestManager: {request: async options => {
    calls.push(options);
    if (scenario === 'account_after_read') login = '300';
    if (scenario === 'card_after_read') {card.comment_id = '999'; source.content = JSON.stringify(card);}
    return scenario === 'fetch_wrapped' ? {data: result} : result;
  }}};
const document = {querySelector: () => ({})};
const pageClient = () => app;
"""


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("static", "static"),
        ("fetch", "static"),
        ("fetch_wrapped", "static"),
        ("sender", "unknown"),
        ("own", "unknown"),
        ("group", "unknown"),
        ("target", "unknown"),
        ("message", "unknown"),
        ("type", "unknown"),
        ("conversation", "unknown"),
        ("actor", "unknown"),
        ("card_id", "unknown"),
        ("card_url", "unknown"),
        ("other_work", "unknown"),
        ("other_image", "unknown"),
        ("processed_preview", "unknown"),
        ("preview_query", "unknown"),
        ("motion", "motion"),
        ("image_motion", "motion"),
        ("unknown_motion", "unknown"),
        ("fetch_rejected", "unknown"),
        ("fetch_invalid", "unknown"),
        ("fetch_too_many", "unknown"),
        ("card_after_read", "unknown"),
        ("missing", "unavailable"),
        ("fetch_other", "unavailable"),
        ("account", "comment_account_changed"),
        ("account_after_read", "comment_account_changed"),
    ],
)
def test_comment_verification_is_bound_to_original_and_reloads_missing_metadata(scenario, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("网页 JS 契约检查需要 Node.js，CI 提供 Node.js 22")
    script = FIXTURE + "(async()=>{" + COMMENT_JS + "})().then(status=>console.log(JSON.stringify({status,calls})))"
    script += ".catch(error=>console.log(JSON.stringify({status:error.message,calls})));"
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "must-stay-in-page" not in result.stdout
    data = json.loads(result.stdout)
    assert data["status"] == expected
    assert len(data["calls"]) == int(scenario.startswith("fetch") or scenario.endswith("_after_read"))
    if data["calls"]:
        assert data["calls"][0] == {
            "method": "GET",
            "url": "/aweme/v1/web/comment/list/",
            "noStatusCode": True,
            "data": {
                "aweme_id": "456",
                "cursor": 0,
                "count": 1,
                "item_type": 0,
                "insert_ids": "789",
                "whale_cut_token": "",
                "cut_version": 1,
                "rcFT": "",
            },
        }


async def test_comment_query_has_bounded_timeout():
    cancelled = asyncio.Event()

    async def stalled(*_):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    page = SimpleNamespace(evaluate=AsyncMock(side_effect=stalled))
    with pytest.raises(asyncio.TimeoutError):
        await read_comment_status(page, {"account": "100"}, 0.01)
    assert cancelled.is_set()
