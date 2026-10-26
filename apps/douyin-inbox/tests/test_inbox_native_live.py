"""离线执行网页适配器，验证实况类型、摘要、收件人及取消后的发送边界。"""

import json
import shutil
import subprocess

import pytest

from qiuyuantu_inbox.delivery import DELIVERY_JS
from qiuyuantu_inbox.native_live import CLIENT_JS, SEND_JS, UPLOAD_JS

FIXTURE_JS = r"""
const scenario = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const crypto = require('crypto').webcrypto;
const counts = {uploads: 0, sends: 0, created: 0, bitmapClosed: 0};
const args = {account: 'account', conversation: 'chat', upload_token: 'client', deadline: Date.now() + 10000,
  part: {client_id: 'client', live_pair: true, live_vid: 'pair-id', md5: ['image-md5', 'video-md5']}};
let current = {account: 'account', id: 'chat'};
const editor = {innerText: scenario === 'draft' ? 'unsent draft' : ''};
const mockFile = (type, content) => ({type, content,
  arrayBuffer: async function() {return new TextEncoder().encode(this.content).buffer;}});
let input = {files: [mockFile('image/jpeg', 'image'), mockFile('video/mp4', 'video')],
  getAttribute: () => args.upload_token};
if (scenario === 'expired') args.deadline = 0;
const target = {id: 'chat', type: 1};
const sdk = {
  getConversation: () => target,
  createMessage: async options => {
    counts.created++;
    return {...options, conversationId: target.id, sender: scenario === 'sender' ? 'other' : args.account};
  }
};
const manager = {
  createMessageBuilder: options => options,
  sendMessage: async builder => {
    counts.sends++;
    Object.assign(builder.message, {serverId: 'receipt', flightStatus: 4, serverStatus: 0});
    return {success: true};
  }
};
const app = {saasApi: {}, mainOptions: {aid: 1128},
  imSdkService: {imSdkManager: {getImSdkInstance: () => sdk}, sendMessageManager: manager},
  requestManager: {request: async request => {
    if (request.url !== '/aweme/v1/web/im/upload/config/v2') throw Error('unexpected_request');
    return {public_image_config: {}, inner_image_config: {}};
  }}
};
const uploadCacheForTest = () => app[Symbol.for('qiuyuantu.inbox.nativeLiveCache.v1')].values();
const element = {isConnected: true, __reactFiber$test: {memoizedProps: {value: app}}};
const property = () => current;
const conversation = value => value;
const document = {querySelector: selector => {
  if (selector.includes('ChatHeader')) return {};
  if (selector.includes('contenteditable')) return editor;
  if (selector.includes('live-upload')) return input;
  return element;
}};
let releaseImage;
const videoStarted = new Promise(resolve => {releaseImage = resolve;});
class Uploader {
  async uploadImage() {
    counts.uploads++;
    // 如果实现退化为串行上传，这个屏障会使契约测试超时。
    await videoStarted;
    return JSON.stringify({md5: 'image-md5', aweType: 2702, resource_url: {oid: 'image', skey: 'private-image-key'}});
  }
  async uploadVideo() {
    counts.uploads++;
    releaseImage();
    if (scenario === 'recipient') current = {account: 'account', id: 'other'};
    if (scenario === 'cancelled') input = null;
    return JSON.stringify({video: {md5: scenario === 'digest' ? 'wrong' : 'video-md5',
      skey: 'private-video-key', tkey: 'video'}});
  }
}
const load = () => ({default: Uploader});
load.e = async () => {};
const chunks = [];
const loadedChunks = new Set();
chunks.push = entry => {
  const key = entry[0][0];
  if (!loadedChunks.has(key)) {loadedChunks.add(key); entry[2](load);}
  return Array.prototype.push.call(chunks, entry);
};
const window = {'@pc-im/im:fixture': chunks};
const createImageBitmap = async () => ({width: 1000, height: 1500, close: () => {counts.bitmapClosed++;}});
class OffscreenCanvas {
  constructor(width, height) {this.width = width; this.height = height;}
  getContext() {return {drawImage: () => {}};}
  async convertToBlob() {return {};}
}
"""


def run_adapter(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("网页适配器的 JS 契约检查需要 Node.js；CI 提供 Node.js 22")
    script = (
        FIXTURE_JS
        + "\n(async () => {try {const execute = async () => {"
        + CLIENT_JS
        + DELIVERY_JS
        + UPLOAD_JS
        + SEND_JS
        + "}; if (scenario === 'bounded') {"
        + "app[Symbol.for('qiuyuantu.inbox.nativeLiveCache.v1')] = new Map(Array.from({length: 64}, "
        + "(_, i) => ['seed' + i, {content: {}, expires: Date.now() + 3600000}]));}"
        + "let row = await execute();"
        + "if (scenario.startsWith('reuse')) {"
        + "args.part.live_vid = 'new-pair-id'; args.part.client_id = 'new-client';"
        + "if (scenario === 'reuse_changed') input.files[1].content = 'different video';"
        + "if (scenario === 'reuse_account') {args.account = 'second'; current.account = 'second';}"
        + "if (scenario === 'reuse_expired') for (const entry of uploadCacheForTest()) entry.expires = 0;"
        + "if (scenario === 'reuse_digest') args.part.md5[0] = 'wrong';"
        + "row = await execute();}"
        + "console.log(JSON.stringify({row, counts, cacheSize: "
        + "app[Symbol.for('qiuyuantu.inbox.nativeLiveCache.v1')]?.size || 0}));} "
        + "catch (error) {console.log(JSON.stringify({error: error.message, counts}));}})();"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "private-image-key" not in result.stdout
    assert "private-video-key" not in result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("scenario", "error", "uploads"),
    [
        ("success", None, 2),
        ("draft", "native_recipient_or_draft_changed", 0),
        ("expired", "native_upload_expired", 0),
        ("recipient", "native_recipient_or_draft_changed", 2),
        ("cancelled", "native_upload_expired", 2),
        ("digest", "native_uploaded_digest_changed", 2),
        ("sender", "native_sender_changed", 2),
    ],
)
def test_native_send_contract(scenario, error, uploads):
    output = run_adapter(scenario)
    assert output.get("error") == error
    assert output["counts"]["uploads"] == uploads
    assert output["counts"]["sends"] == (0 if error else 1)
    if not error:
        timings = output["row"].pop("timings")
        assert set(timings) == {
            "upload_native_hash",
            "upload_native_config",
            "upload_native_image",
            "upload_native_video",
            "upload_native_assets",
            "upload_native_submit",
        }
        assert all(seconds >= 0 for seconds in timings.values())
        assert output["row"] == {
            "id": "receipt",
            "type": 27,
            "conversation": "chat",
            "from_me": True,
            "flight": 4,
            "server_status": 0,
            "delivery_restricted": False,
            "delivery_code": 0,
            "md5": "image-md5",
            "live_vid": "pair-id",
            "live_md5": "video-md5",
            "awe_type": 2704,
            "data": {},
        }
        assert output["counts"]["bitmapClosed"] == 1


@pytest.mark.parametrize(
    ("scenario", "uploads"), [("reuse", 2), ("reuse_changed", 4), ("reuse_account", 4), ("reuse_expired", 4)]
)
def test_uploaded_resource_cache_is_scoped_and_replaces_pair_id(scenario, uploads):
    output = run_adapter(scenario)
    assert "error" not in output
    assert output["counts"]["uploads"] == uploads
    assert output["counts"]["sends"] == 2
    assert output["row"]["live_vid"] == "new-pair-id"
    assert ("upload_native_reuse" in output["row"]["timings"]) == (scenario == "reuse")


def test_cached_resource_digest_mismatch_never_sends():
    output = run_adapter("reuse_digest")
    assert output["error"] == "native_cached_digest_changed"
    assert output["counts"]["uploads"] == 2
    assert output["counts"]["sends"] == 1


def test_uploaded_cache_cannot_grow_past_limit():
    output = run_adapter("bounded")
    assert "error" not in output
    assert output["cacheSize"] == 64
    assert output["counts"]["sends"] == 1
