"""运行真实 JS 桥的离线契约，验证 detached 上传、回执与 SDK 内部重试围栏。"""

import json
import shutil
import subprocess

import pytest

from qiuyuantu_inbox.native_bridge import BRIDGE_JS
from qiuyuantu_inbox.native_live import CLIENT_JS, UPLOAD_JS
from test_inbox_native_live import FIXTURE_JS

BRIDGE_FIXTURE = r"""
app.store = {usersInfoStore: {curLoginUserInfo: {get uid() {return current.account;}}}};
app.imSdkService.imSdkManager.listenerObj = Object.fromEntries(
  ['MessageSendSuccess', 'MessageSendFailed', 'MessageChange', 'ReceiveSelfMessage'].map(name => [name, []]));
app.imSdkService.imSdkManager.registerEvent = (name, callback) => {
  app.imSdkService.imSdkManager.listenerObj[name].push(callback);
};
app.imSdkService.imSdkManager.unRegisterEvent = () => {};
const saved = [];
sdk.getConversationMessages = () => saved;
sdk.sendMessage = async ({message}) => {
  counts.sends++;
  if (scenario === 'retry') throw Error('wire_failed');
  if (scenario === 'cooldown') return {statusCode: 429, retryAfterSeconds: 120};
  Object.assign(message, {serverId: 'receipt-' + counts.sends, flightStatus: 4, serverStatus: 0});
  saved.push(message);
  for (const callback of app.imSdkService.imSdkManager.listenerObj.MessageSendSuccess) callback(message);
  return {success: true};
};
manager.sendMessage = async ({message}) => {
  try {return await sdk.sendMessage({message});}
  catch {return await sdk.sendMessage({message});}
};
const inputs = new Map();
const originalSelector = document.querySelector;
document.querySelector = selector => {
  if (selector.includes('outbound-upload')) {
    const id = selector.match(/="([^"]+)"/)[1]; return inputs.get(id);
  }
  return originalSelector(selector);
};
document.body = {append: input => {inputs.set(input.id, input);}};
document.createElement = type => {
  if (type === 'video') return {videoWidth: 20, videoHeight: 30,
    set src(value) {queueMicrotask(() => this.onloadeddata());}, removeAttribute() {}, load() {}};
  return {setAttribute(name, value) {this.id = value;}, remove() {inputs.delete(this.id);}};
};
URL.createObjectURL = () => 'local'; URL.revokeObjectURL = () => {};
const mock = (name, type, content) => ({name, ...mockFile(type, content)});
const execute = async () => {
  const base = {owner: 'owner', account: 'account', interval: 1, streams: 2, operation: 'install'};
  const call = (operation, extra = {}) => bridgeOperation({...base, operation, ...extra});
  if (scenario === 'sealed') {
    const prototype = {sendMessage: sdk.sendMessage};
    delete sdk.sendMessage; Object.setPrototypeOf(sdk, prototype); Object.preventExtensions(sdk);
  }
  const state = installBridge(base);
  if (scenario === 'stream_cancel') {
    const first = {owner: base.owner, deadline: Date.now() + 1000};
    const second = {...first}, third = {...first};
    await acquireStream(state, first); await acquireStream(state, second);
    const pending = acquireStream(state, third).catch(error => error.message);
    third.cancelled = true; releaseStream(state);
    const error = await pending; releaseStream(state);
    return {active: state.activeStreams, error};
  }
  const part = scenario.startsWith('image') ? {files: ['a.jpg'], md5: ['image-md5']}
    : scenario === 'video' ? {files: ['a.mp4'], md5: ['video-md5'], video: true}
    : {live_pair: true, live_vid: 'paired', md5: ['image-md5', 'video-md5']};
  const files = scenario.startsWith('image') ? [mock('a.jpg', 'image/jpeg', 'image')]
    : scenario === 'video' ? [mock('a.mp4', 'video/mp4', 'video')]
    : [mock('a.jpg', 'image/jpeg', 'image'), mock('a.mp4', 'video/mp4', 'video')];
  const upload = async id => {
    await call('attach', {ticket: id, timeout: 10000, part});
    inputs.get(id).files = files;
    return await call('upload', {ticket: id, part: {...part, client_id: id}});
  };
  if (scenario === 'detached') current.id = 'other-chat';
  const resource = await upload('first');
  await call('remove', {ticket: 'first'});
  if (scenario === 'reuse') await upload('second');
  if (scenario === 'image_shared') await Promise.all([upload('second'), upload('third')]);
  if (scenario === 'detached') current.id = 'chat';
  if (scenario === 'owner') installBridge({...base, owner: 'next-owner'});
  if (scenario === 'account') current.account = 'other';
  if (scenario === 'sdk_change') app.imSdkService.imSdkManager.getImSdkInstance = () => ({});
  if (scenario === 'cancel') state.uploads.get('first').cancelled = true;
  if (scenario === 'reload') resource.generation = 'new-page';
  if (scenario === 'close') await call('close');
  await call('submit', {...resource, part, client_id: 'client', conversation: 'chat'});
  // 等 SDK manager 内部重试结束，但不依赖它返回来观察平台回执。
  await new Promise(resolve => setTimeout(resolve, 15));
  const observed = await call('poll', {ids: ['client']});
  const content = state.uploads.get('first').resource.content;
  return {observed, counts, active: state.activeStreams, cache: state.resources.size,
    contentType: {type: state.uploads.get('first').resource.type, awe: content.aweType}};
};
"""


def run_bridge(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("JS 桥契约检查需要 Node.js")
    fixture = FIXTURE_JS.replace("await videoStarted;", "if (!scenario.startsWith('image')) await videoStarted;")
    fixture = fixture.replace("return JSON.stringify({video:", "return JSON.stringify({poster: {oid: 'poster'}, video:")
    script = (
        fixture
        + CLIENT_JS
        + UPLOAD_JS
        + BRIDGE_JS
        + BRIDGE_FIXTURE
        + "execute().then(result=>console.log(JSON.stringify(result)),"
        + "error=>console.log(JSON.stringify({error:error.message,counts})));"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "private-image-key" not in result.stdout and "private-video-key" not in result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize("scenario", ["success", "video", "image", "detached", "reuse", "image_shared", "sealed"])
def test_uploads_and_receipts_use_native_formats_without_ui_upload(scenario):
    result = run_bridge(scenario)
    assert "error" not in result, result
    assert result["active"] == 0
    assert result["observed"][0]["row"]["client_id"] == "client"
    assert result["observed"][0]["attempts"] == 1
    assert result["counts"]["sends"] == 1
    if scenario == "video":
        assert result["contentType"] == {"type": 30}
    if scenario == "reuse":
        assert result["counts"]["uploads"] == 2
    if scenario == "image_shared":
        assert result["counts"]["uploads"] == 1


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("digest", "outbound_uploaded_digest_changed"),
        ("owner", "outbound_owner_changed"),
        ("account", "outbound_account_changed"),
        ("sdk_change", "outbound_sdk_instance_changed"),
        ("cancel", "outbound_resource_expired"),
        ("reload", "outbound_resource_expired"),
        ("close", "outbound_owner_changed"),
        ("sender", "outbound_sender_changed"),
        ("draft", "native_recipient_or_draft_changed"),
    ],
)
def test_changed_identity_or_media_stops_before_wire(scenario, error):
    result = run_bridge(scenario)
    assert result["error"] == error
    assert result["counts"]["sends"] == 0


def test_sdk_automatic_retry_is_observed_and_blocked():
    result = run_bridge("retry")
    assert result["counts"]["sends"] == 1
    assert result["observed"][0]["retry_blocked"] == 1
    assert result["observed"][0]["failed"]
    assert result["observed"][0]["row"] is None


def test_explicit_rate_limit_creates_account_cooldown():
    result = run_bridge("cooldown")
    entry = result["observed"][0]
    assert entry["rate_limited"]
    assert entry["cooldown"] - entry["sdk_started"] >= 119


def test_cancelled_waiter_releases_physical_upload_slot():
    assert run_bridge("stream_cancel") == {"active": 0, "error": "outbound_upload_expired"}
