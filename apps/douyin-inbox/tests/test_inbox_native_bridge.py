"""运行真实 JS 桥的离线契约，验证 detached 上传、回执与 SDK 内部重试围栏。"""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qiuyuantu_inbox.delivery import DELIVERY_JS
from qiuyuantu_inbox.models import Settings
from qiuyuantu_inbox.native_bridge import BRIDGE_JS, NativeBridge
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
  if (scenario.startsWith('restricted')) {
    message.ext = {im_callback_status_code: '8101', 's:visible': message.sender};
  }
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
const mock = (name, type, content) => ({name, size: content.length, ...mockFile(type, content)});
const execute = async () => {
  const base = {owner: 'owner', account: 'account', interval: 1, streams: 2,
    workers: 2, bytes: 100, operation: 'install'};
  const call = (operation, extra = {}) => bridgeOperation({...base, operation, ...extra});
  if (scenario === 'sealed') {
    const prototype = {sendMessage: sdk.sendMessage};
    delete sdk.sendMessage; Object.setPrototypeOf(sdk, prototype); Object.preventExtensions(sdk);
  }
  const state = installBridge(base);
  if (scenario === 'restricted_upgrade') {
    state.adapterVersion = 2;
    app.imSdkService.imSdkManager.listenerObj.MessageSendSuccess.push(raw => {
      const entry = state.entries.get(raw.clientId);
      if (entry) entry.receipt = {id: raw.serverId};
    });
    installBridge(base);
  }
  if (scenario.startsWith('budget_')) return await budgetScenario(state, call, base);
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
    await call('reserve', {ticket: id, timeout: 10000, part, bytes: files.reduce((sum, file) => sum + file.size, 0)});
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
  const eventRestricted = state.entries.get('client')?.receipt?.delivery_restricted;
  const observed = await call('poll', {ids: ['client']});
  const content = state.uploads.get('first').resource.content;
  return {observed, counts, eventRestricted, active: state.activeStreams, cache: state.resources.size,
    contentType: {type: state.uploads.get('first').resource.type, awe: content.aweType}};
};
"""

BUDGET_FIXTURE = r"""
const tick = () => new Promise(resolve => setTimeout(resolve, 1));
const budgetScenario = async (state, call, base) => {
  const reserve = (ticket, bytes, warm = false) => call('reserve', {ticket, bytes, warm, timeout: 10000,
    part: {files: ['a.jpg'], md5: ['image-md5']}});
  if (['budget_shared', 'budget_count'].includes(scenario)) {
    const size = scenario === 'budget_shared' ? 50 : 1;
    await reserve('warm', size, true);
    await reserve('first', size);
    let admitted = false;
    const second = reserve('second', size).then(() => {admitted = true;});
    await tick();
    const before = {usage: uploadUsage(state), admitted};
    const warmSkipped = !await reserve('extra-warm', 1, true);
    await call('remove', {ticket: 'warm'});
    await second;
    return {before, after: uploadUsage(state), warmSkipped, created: counts.created};
  }
  if (['budget_queued_cancel', 'budget_queued_forget'].includes(scenario)) {
    await reserve('first', 100);
    const cancelled = reserve('cancelled', 50).catch(error => error.message);
    if (scenario === 'budget_queued_forget') {
      await call('forget', {client_id: 'cancelled', handle: 'cancelled'});
    } else {
      await call('remove', {ticket: 'cancelled'});
    }
    const error = await cancelled;
    await call('remove', {ticket: 'first'});
    await reserve('next', 100);
    return {error, usage: uploadUsage(state), queued: state.uploadBudget.waiters.length};
  }
  if (scenario === 'budget_stream_priority') {
    const ticket = warm => ({owner: base.owner, warm, deadline: Date.now() + 10000});
    await acquireStream(state, ticket(false)); await acquireStream(state, ticket(false));
    const order = [];
    const warm = acquireStream(state, ticket(true)).then(() => order.push('warm'));
    const formal = acquireStream(state, ticket(false)).then(() => order.push('formal'));
    releaseStream(state); await formal; releaseStream(state); await warm;
    releaseStream(state); releaseStream(state);
    return {order, active: state.activeStreams};
  }
  return await runningBudgetScenario(state, call, base, reserve);
};
const runningBudgetScenario = async (state, call, base, reserve) => {
  let release, started;
  const began = new Promise(resolve => {started = resolve;});
  const gate = new Promise(resolve => {release = resolve;});
  const pair = scenario === 'budget_pair_failure';
  Uploader.prototype.uploadImage = async () => {
    if (pair) throw new Error('image_failed');
    started(); await gate;
    return JSON.stringify({md5: 'image-md5', aweType: 2702});
  };
  Uploader.prototype.uploadVideo = async () => {
    started(); await gate;
    return JSON.stringify({video: {md5: 'video-md5', tkey: 'private-video-key'}});
  };
  const part = {files: ['a.jpg'], md5: ['image-md5']};
  const files = [mock('a.jpg', 'image/jpeg', 'image')];
  if (pair) {
    Object.assign(part, {files: ['a.jpg', 'a.mp4'], md5: ['image-md5', 'video-md5'], live_pair: true});
    files.push(mock('a.mp4', 'video/mp4', 'video'));
  }
  await call('reserve', {ticket: 'warm', bytes: pair ? 10 : 5, warm: true, timeout: 10000, part});
  await call('attach', {ticket: 'warm'});
  inputs.get('warm').files = files;
  const pending = call('upload', {ticket: 'warm'}).catch(error => error.message);
  await began;
  if (scenario === 'budget_restart') {
    base.owner = 'new-owner'; await call('install');
  } else if (!pair) {
    await call('remove', {ticket: 'warm'});
  }
  let admitted = false;
  const formal = reserve('formal', 100).then(() => {admitted = true;});
  await tick();
  const before = {usage: uploadUsage(state), admitted};
  release();
  const error = await pending;
  await formal;
  return {before, error, after: uploadUsage(state), inputs: inputs.size};
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
        + DELIVERY_JS
        + UPLOAD_JS
        + BRIDGE_JS
        + BRIDGE_FIXTURE
        + BUDGET_FIXTURE
        + "execute().then(result=>console.log(JSON.stringify(result)),"
        + "error=>console.log(JSON.stringify({error:error.message,counts})));"
    )
    result = subprocess.run(
        [node, "-e", script], input=json.dumps(scenario), text=True, capture_output=True, check=True, timeout=10
    )
    assert "private-image-key" not in result.stdout and "private-video-key" not in result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize("scenario", ["restricted", "restricted_upgrade"])
def test_native_bridge_preserves_restricted_result_despite_successful_sdk_ack(scenario):
    result = run_bridge(scenario)
    assert result["eventRestricted"]
    row = result["observed"][0]["row"]
    assert row["delivery_restricted"] and row["delivery_code"] == 8101
    assert result["counts"]["sends"] == 1


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


@pytest.mark.parametrize(("scenario", "size"), [("budget_shared", 100), ("budget_count", 2)])
def test_warmup_and_formal_uploads_share_one_byte_and_worker_pool(scenario, size):
    result = run_bridge(scenario)
    assert result == {
        "before": {"usage": {"count": 2, "bytes": size}, "admitted": False},
        "after": {"count": 2, "bytes": size},
        "warmSkipped": True,
        "created": 0,
    }


@pytest.mark.parametrize("scenario", ["budget_cancel", "budget_restart", "budget_pair_failure"])
def test_budget_is_held_until_actual_upload_finishes_even_after_cancel_or_owner_change(scenario):
    result = run_bridge(scenario)
    assert result["before"] == {"usage": {"count": 1, "bytes": 10 if "pair" in scenario else 5}, "admitted": False}
    assert result["after"] == {"count": 1, "bytes": 100}
    assert result["inputs"] == 0
    assert result["error"] == ("image_failed" if "pair" in scenario else "outbound_upload_expired")


@pytest.mark.parametrize("scenario", ["budget_queued_cancel", "budget_queued_forget"])
def test_queued_cancellation_does_not_consume_budget_or_block_future_upload(scenario):
    assert run_bridge(scenario) == {
        "error": "outbound_upload_expired",
        "usage": {"count": 1, "bytes": 100},
        "queued": 0,
    }


def test_formal_stream_waiter_precedes_speculative_waiter():
    assert run_bridge("budget_stream_priority") == {"order": ["formal", "warm"], "active": 0}


async def test_budget_wait_and_file_handoff_share_one_upload_deadline(tmp_path):
    file = tmp_path / "a.jpg"
    file.write_bytes(b"image")

    async def reserve(operation, **kwargs):
        if operation == "reserve":
            await asyncio.sleep(0.03)
            return True
        return None

    async def handoff(_files):
        await asyncio.sleep(0.03)

    input_files = AsyncMock(side_effect=handoff)
    page = SimpleNamespace(locator=lambda selector: SimpleNamespace(set_input_files=input_files))
    bridge = NativeBridge(SimpleNamespace(page=page, settings=Settings(upload_timeout=0.05)), asyncio.Lock())
    bridge.call = AsyncMock(side_effect=reserve)
    with pytest.raises(asyncio.TimeoutError):
        await bridge.upload({"token": "ticket", "client_id": "client", "payload": '{"files":["a.jpg"]}'}, [file])
    operations = [call.args[0] for call in bridge.call.await_args_list]
    assert operations[-1] == "remove" and "upload" not in operations
    assert not bridge.ui.locked()


async def test_warmup_skipped_by_shared_budget_never_hands_files_to_page(tmp_path):
    file = tmp_path / "a.jpg"
    file.write_bytes(b"image")
    page = SimpleNamespace(locator=AsyncMock())
    bridge = NativeBridge(SimpleNamespace(page=page, settings=Settings()), asyncio.Lock())
    bridge.call = AsyncMock(return_value=False)
    assert await bridge.warm({"files": ["a.jpg"]}, [file]) is None
    assert [call.args[0] for call in bridge.call.await_args_list] == ["reserve", "remove"]
    page.locator.assert_not_called()
