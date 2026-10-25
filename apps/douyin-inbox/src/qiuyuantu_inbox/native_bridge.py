"""页面内原生上传与发送桥：私有资源留在页面，外部只接收句柄和有界回执。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from pathlib import Path

from .native_live import CLIENT_JS, UPLOAD_JS

BRIDGE_JS = r"""
const bridgeSymbol = Symbol.for('qiuyuantu.inbox.outbound.v1');
const accountOf = app => String(app?.store?.usersInfoStore?.curLoginUserInfo?.uid || '');
const bridgeAccount = state => {
  if (accountOf(state.app) !== state.account) throw new Error('outbound_account_changed');
  if (state.app.imSdkService.imSdkManager.getImSdkInstance() !== state.sdk) {
    throw new Error('outbound_sdk_instance_changed');
  }
};
const bridgeOwner = (state, args) => {
  bridgeAccount(state);
  if (state.owner !== args.owner || state.closed) throw new Error('outbound_owner_changed');
};
const bridgeTicket = (state, ticket) => {
  bridgeAccount(state);
  if (ticket.cancelled || ticket.owner !== state.owner || state.closed || Date.now() > ticket.deadline) {
    throw new Error('outbound_upload_expired');
  }
};
const contentDigests = content => ({
  md5: String(content.md5 || content.resource_url?.md5 || content.video?.md5 || ''),
  live_vid: String(content.live_photo_video?.vid || ''),
  live_md5: String(content.live_photo_video?.md5 || ''),
});
const snapshot = (state, raw) => {
  const content = JSON.parse(raw.content || '{}');
  return {id: String(raw.serverId || ''), client_id: String(raw.clientId || ''), type: raw.type,
    conversation: String(raw.conversationId || ''), from_me: String(raw.sender) === state.account,
    flight: raw.flightStatus, server_status: raw.serverStatus, awe_type: content.aweType,
    ...contentDigests(content), data: raw.type === 7 ? {text: String(content.text || '').slice(0, 4096)} : {}};
};
const matchingEntry = (state, raw) => {
  const entry = state.entries.get(String(raw?.clientId || ''));
  return entry && raw.conversationId === entry.conversation && String(raw.sender) === state.account ? entry : null;
};
const observeReceipt = (state, raw, source = 'event') => {
  const entry = matchingEntry(state, raw);
  if (!entry) return;
  try {
    const row = snapshot(state, raw);
    if (row.id && row.flight === 4 && row.server_status === 0) {
      entry.receipt = row;
      entry.receiptSource ||= source;
      if (source === 'event') entry.eventReceipt = true;
      entry.ackAt ||= Date.now();
    }
  } catch {entry.observationFailed = true;}
};
const limitedResponse = (state, entry, response) => {
  const status = response?.statusCode ?? response?.status ?? response?.error?.status;
  if (status !== 429) return;
  entry.rateLimited = true;
  const seconds = Math.max(30, Math.min(3600, Number(response.retryAfterSeconds) || 60));
  state.cooldown = Math.max(state.cooldown, Date.now() + seconds * 1000);
};
const pacedWireCall = (state, options) => {
  const entry = state.entries.get(String(options?.message?.clientId || ''));
  if (!entry) return state.originalSend.call(state.sdk, options);
  const scheduled = state.pace.then(async () => {
    if (entry.attempts) {
      entry.retryBlocked++;
      throw new Error('outbound_automatic_retry_blocked');
    }
    const delay = Math.max(0, state.nextSubmit - Date.now(), state.cooldown - Date.now());
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    bridgeAccount(state);
    if (entry.owner !== state.owner || state.closed) throw new Error('outbound_owner_changed');
    if (options.message.conversationId !== entry.conversation || String(options.message.sender) !== state.account) {
      throw new Error('outbound_sender_changed');
    }
    entry.attempts++;
    entry.wireAt = Date.now();
    entry.notifyStarted();
    state.nextSubmit = entry.wireAt + state.interval;
    entry.wire = Promise.resolve(state.originalSend.call(state.sdk, options)).then(response => {
      limitedResponse(state, entry, response);
      return response;
    }, error => {limitedResponse(state, entry, error); throw error;}).finally(() => {entry.wireEnd = Date.now();});
  });
  state.pace = scheduled.catch(() => {});
  return scheduled.then(() => entry.wire);
};
const newBridge = args => {
  const element = document.querySelector('[data-e2e="msg-item-content"], [data-e2e="conversation-item"]');
  const app = pageClient(element), sdk = app.imSdkService.imSdkManager.getImSdkInstance();
  const state = {version: 1, app, sdk, account: args.account, generation: crypto.randomUUID(),
    entries: new Map(), uploads: new Map(), resources: new Map(), activeStreams: 0, waiters: [],
    nextSubmit: 0, cooldown: 0, pace: Promise.resolve(), originalSend: sdk.sendMessage, detach: []};
  return state;
};
const installReceiptListeners = state => {
  if (state.adapterVersion === 2) return;
  state.detach.forEach(detach => detach()); state.detach = [];
  const manager = state.app.imSdkService.imSdkManager;
  for (const name of ['MessageSendSuccess', 'MessageSendFailed', 'MessageChange', 'ReceiveSelfMessage']) {
    if (!Array.isArray(manager.listenerObj[name])) throw new Error('outbound_event_adapter_changed');
    const callback = raw => observeReceipt(state, raw);
    manager.registerEvent(name, callback);
    state.detach.push(() => manager.unRegisterEvent(name, callback));
  }
  state.adapterVersion = 2;
};
const retireOwner = (state, args) => {
  if (state.owner && state.owner !== args.owner) {
    for (const ticket of state.uploads.values()) ticket.cancelled = true;
    state.uploads.clear();
    for (const entry of state.entries.values()) entry.retired = true;
  }
  for (const [id, entry] of state.entries) if (entry.retired && entry.finished) state.entries.delete(id);
};
const sendMethodOwner = sdk => {
  for (let owner = sdk, depth = 0; owner && depth < 10; owner = Object.getPrototypeOf(owner), depth++) {
    if (Object.hasOwn(owner, 'sendMessage')) return owner;
  }
  throw new Error('outbound_sdk_method_missing');
};
const installWire = state => {
  if (!state.methodOwner) {
    state.methodOwner = sendMethodOwner(state.sdk);
    state.wrappedSend = function(options) {
      if (this !== state.sdk) return state.originalSend.call(this, options);
      return pacedWireCall(state, options);
    };
  }
  if (state.sdk.sendMessage !== state.originalSend && state.sdk.sendMessage !== state.wrappedSend) {
    throw new Error('outbound_sdk_adapter_changed');
  }
  const descriptor = Object.getOwnPropertyDescriptor(state.methodOwner, 'sendMessage');
  if (!descriptor.writable) throw new Error('outbound_sdk_method_readonly');
  Object.defineProperty(state.methodOwner, 'sendMessage', {...descriptor, value: state.wrappedSend});
  if (state.sdk.sendMessage !== state.wrappedSend) throw new Error('outbound_sdk_gate_missing');
};
const installBridge = args => {
  let state = window[bridgeSymbol];
  if (state && state.account !== args.account) throw new Error('outbound_account_changed');
  if (!state) {state = newBridge(args); window[bridgeSymbol] = state;}
  bridgeAccount(state);
  retireOwner(state, args);
  installReceiptListeners(state);
  installWire(state);
  Object.assign(state, {owner: args.owner, closed: false, interval: args.interval,
    streamLimit: args.streams, nextSubmit: Math.max(state.nextSubmit, args.next_submit || 0),
    cooldown: Math.max(state.cooldown, args.cooldown || 0)});
  return state;
};
const acquireStream = async (state, ticket) => {
  bridgeTicket(state, ticket);
  if (state.activeStreams < state.streamLimit) {state.activeStreams++; return;}
  await new Promise((resolve, reject) => state.waiters.push({resolve, reject, ticket}));
  try {bridgeTicket(state, ticket);}
  catch (error) {releaseStream(state); throw error;}
};
const releaseStream = state => {
  state.activeStreams--;
  while (state.waiters.length) {
    const next = state.waiters.shift();
    if (next.ticket.cancelled || next.ticket.owner !== state.owner || Date.now() > next.ticket.deadline) {
      next.reject(new Error('outbound_upload_expired'));
      continue;
    }
    state.activeStreams++;
    next.resolve();
    break;
  }
};
const videoPoster = async file => {
  const url = URL.createObjectURL(file), video = document.createElement('video');
  try {
    video.muted = true; video.preload = 'auto';
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('outbound_video_decode_timeout')), 10000);
      const finish = action => {clearTimeout(timer); action();};
      video.onloadeddata = () => finish(resolve);
      video.onerror = () => finish(() => reject(new Error('outbound_video_decode_failed')));
      video.src = url;
    });
    const scale = Math.min(1, 480 / Math.max(video.videoWidth, video.videoHeight));
    const canvas = new OffscreenCanvas(Math.max(1, Math.round(video.videoWidth * scale)),
      Math.max(1, Math.round(video.videoHeight * scale)));
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return await canvas.convertToBlob({type: 'image/jpeg', quality: 0.85});
  } finally {
    video.removeAttribute('src'); video.load(); URL.revokeObjectURL(url);
  }
};
const resourceKey = async (state, file) => {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  return ['v1', state.account, file.type, file.name.split('.').pop().toLowerCase(), hex].join(':');
};
const cachedResource = (state, key) => {
  for (const [id, value] of state.resources) if (value.done && value.expires < Date.now()) state.resources.delete(id);
  return state.resources.get(key);
};
const uploadFile = async (state, ticket, file, uploader, poster) => {
  const key = await resourceKey(state, file) + (poster ? ':' + await resourceKey(state, poster) : '');
  const cached = cachedResource(state, key);
  if (cached) {ticket.cacheHits++; return await cached.task;}
  const task = (async () => {
    await acquireStream(state, ticket);
    const started = performance.now();
    try {
      const content = file.type === 'video/mp4'
        ? JSON.parse(await uploader.uploadVideo({mp4: file,
          thumbBlob: poster ? await posterBlob(poster) : await videoPoster(file)}))
        : JSON.parse(await uploader.uploadImage(file));
      bridgeTicket(state, ticket);
      return content;
    } finally {
      ticket.uploadSeconds += (performance.now() - started) / 1000;
      releaseStream(state);
    }
  })();
  const value = {task, done: false, expires: Date.now() + 3600000};
  state.resources.set(key, value);
  try {
    const content = await task;
    value.done = true;
    for (const [id, item] of state.resources) {
      if (state.resources.size <= 64) break;
      if (item.done) state.resources.delete(id);
    }
    return content;
  } catch (error) {
    if (state.resources.get(key) === value) state.resources.delete(key);
    throw error;
  }
};
const uploadContent = async (state, ticket, files, args) => {
  const uploader = await pageUploader(state.app, args, () => bridgeTicket(state, ticket));
  const contents = await Promise.all(files.map(file => uploadFile(state, ticket, file, uploader,
    ticket.part.live_pair && file.type === 'video/mp4' ? files[0] : null)));
  const digest = content => content.md5 || content.video?.md5 || '';
  if (contents.some((content, index) => digest(content) !== ticket.part.md5[index])) {
    throw new Error('outbound_uploaded_digest_changed');
  }
  return ticket.part.live_pair ? liveResource(contents, ticket.part) : singleResource(contents, ticket.part, files[0]);
};
const liveResource = (contents, part) => {
  if (contents.length !== 2 || !contents[1].video?.tkey) throw new Error('outbound_live_pair_missing');
  return {type: 27, content: {...contents[0], aweType: 2704,
    live_photo_video: {...contents[1].video, vid: part.live_vid, is_new_encrypt: 1}}};
};
const singleResource = (contents, part, file) => {
  if (contents.length !== 1) throw new Error('outbound_single_message_required');
  if (part.video) {
    if (!contents[0].video?.tkey || !contents[0].poster?.oid) throw new Error('outbound_video_resource_missing');
    return {type: 30, content: contents[0]};
  }
  const expected = file.name.toLowerCase().endsWith('.gif') ? 2703 : 2702;
  if (contents[0].aweType !== expected) throw new Error('outbound_image_type_changed');
  return {type: 27, content: contents[0]};
};
const attachUpload = (state, args) => {
  if (state.uploads.has(args.ticket)) throw new Error('outbound_upload_already_attached');
  const input = document.createElement('input');
  input.type = 'file'; input.multiple = true; input.hidden = true;
  input.setAttribute('data-qyt-outbound-upload', args.ticket); document.body.append(input);
  state.uploads.set(args.ticket, {owner: args.owner, deadline: Date.now() + args.timeout,
    part: args.part, cacheHits: 0, uploadSeconds: 0});
};
const prepareUpload = async (state, args) => {
  const ticket = state.uploads.get(args.ticket);
  if (!ticket) throw new Error('outbound_upload_missing');
  bridgeTicket(state, ticket);
  const input = document.querySelector('[data-qyt-outbound-upload="' + args.ticket + '"]');
  const files = Array.from(input?.files || []);
  const expected = ticket.part.live_pair ? 2 : 1;
  if (files.length !== expected) throw new Error('outbound_files_changed');
  const resource = await uploadContent(state, ticket, files, args);
  bridgeTicket(state, ticket);
  ticket.resource = resource;
  ticket.deadline = Date.now() + 3600000;
  return {handle: args.ticket, generation: state.generation,
    upload_seconds: ticket.uploadSeconds, cache_hits: ticket.cacheHits};
};
const removeUpload = (state, args) => {
  document.querySelector('[data-qyt-outbound-upload="' + args.ticket + '"]')?.remove();
  const ticket = state.uploads.get(args.ticket);
  if (ticket && !ticket.resource) {ticket.cancelled = true; state.uploads.delete(args.ticket);}
};
const readyResource = (state, args) => {
  const ticket = state.uploads.get(args.handle);
  return args.generation === state.generation && ticket?.resource &&
    ticket.owner === args.owner && !ticket.cancelled && Date.now() <= ticket.deadline;
};
const startSubmit = async (state, args) => {
  if (state.entries.size >= 8) throw new Error('outbound_observer_backlog');
  if (state.entries.has(args.client_id)) throw new Error('outbound_client_id_already_attempted');
  const element = document.querySelector('.StackLayoutStackChatHeadertitle');
  const {app, sdk, target} = nativeContext(element, args);
  const resource = 'text' in args.part
    ? {type: 7, content: {aweType: 700, type: 0, richTextInfos: [], text: args.part.text}}
    : readyResource(state, args) && state.uploads.get(args.handle).resource;
  if (!resource) throw new Error('outbound_resource_expired');
  const entry = {owner: args.owner, conversation: args.conversation, target, attempts: 0,
    retryBlocked: 0, createdAt: Date.now(), finished: false, failed: false};
  entry.started = new Promise(resolve => {entry.notifyStarted = resolve;});
  state.entries.set(args.client_id, entry);
  const message = await sdk.createMessage({conversation: target, type: resource.type,
    content: JSON.stringify(resource.content), clientId: args.client_id, insert: true});
  entry.message = message;
  if (String(message.sender) !== state.account || message.conversationId !== args.conversation) {
    throw new Error('outbound_sender_changed');
  }
  nativeContext(element, args);
  const manager = app.imSdkService.sendMessageManager;
  const builder = manager.createMessageBuilder({message, conversation: target});
  entry.task = Promise.resolve(manager.sendMessage(builder)).then(result => {
    entry.failed = !result?.success;
    observeReceipt(state, message, 'object');
  }, () => {entry.failed = true;}).finally(() => {entry.finished = true; entry.finishAt = Date.now();
    if (entry.retired) {state.entries.delete(args.client_id); if (args.handle) state.uploads.delete(args.handle);}
  });
  // 发送前钩子完成、SDK 实际提交已启动后才释放 UI；不等待网络回执或发送后钩子。
  await Promise.race([entry.started, entry.task.then(() => {
    if (entry.attempts !== 1) throw new Error('outbound_submit_not_started');
  })]);
  return {client_id: args.client_id, generation: state.generation};
};
const entryTimings = entry => ({
  sdk_build: entry.wireAt ? (entry.wireAt - entry.createdAt) / 1000 : 0,
  sdk_transport: entry.wireEnd ? (entry.wireEnd - entry.wireAt) / 1000 : 0,
  ack_observe: entry.ackAt && entry.wireAt ? (entry.ackAt - entry.wireAt) / 1000 : 0,
});
const pollEntry = (state, id) => {
  const entry = state.entries.get(id);
  if (!entry) return {client_id: id, missing: true};
  const cached = state.sdk.getConversationMessages({conversation: entry.target});
  for (const raw of cached) if (String(raw.clientId) === id) observeReceipt(state, raw, 'cache');
  if (entry.message) observeReceipt(state, entry.message, 'object');
  return {client_id: id, row: entry.receipt || null, attempts: entry.attempts,
    retry_blocked: entry.retryBlocked, event_receipt: !!entry.eventReceipt, receipt_source: entry.receiptSource,
    finished: entry.finished, failed: entry.failed,
    rate_limited: !!entry.rateLimited, sdk_started: (entry.wireAt || 0) / 1000,
    next_submit: state.nextSubmit / 1000, cooldown: state.cooldown / 1000,
    timings: entryTimings(entry)};
};
const forgetEntry = (state, args) => {
  const entry = state.entries.get(args.client_id);
  if (entry && !entry.finished) {entry.retired = true; return;}
  state.entries.delete(args.client_id);
  if (args.handle) state.uploads.delete(args.handle);
};
const closeBridge = state => {
  state.closed = true;
  for (const ticket of state.uploads.values()) if (!ticket.resource) ticket.cancelled = true;
  // 保留围栏：已退出的 manager 可能继续内部重试，不能恢复裸 SDK 后让重试绕过提交预算。
  // 未登记的人工 / 旧流程消息仍直接交给原 SDK。

};
const bridgeOperation = async args => {
  const state = args.operation === 'install' ? installBridge(args) : window[bridgeSymbol];
  if (!state) throw new Error('outbound_bridge_missing');
  bridgeOwner(state, args);
  const operations = {
    install: () => ({generation: state.generation}),
    attach: () => attachUpload(state, args),
    upload: () => prepareUpload(state, args),
    remove: () => removeUpload(state, args),
    check: () => !!readyResource(state, args),
    submit: () => startSubmit(state, args),
    poll: () => args.ids.map(id => pollEntry(state, id)),
    forget: () => forgetEntry(state, args),
    close: () => closeBridge(state),
  };
  if (!operations[args.operation]) throw new Error('outbound_operation_unknown');
  return await operations[args.operation]();
};
"""


class NativeBridge:
    def __init__(self, browser, ui: asyncio.Lock):
        self.browser, self.ui = browser, ui
        self.owner = uuid.uuid4().hex
        self.installed = False

    async def call(self, operation: str, **kwargs):
        from .browser import JS

        args = {"operation": operation, "owner": self.owner, "account": self.browser.account} | kwargs
        script = "async args=>{" + JS + CLIENT_JS + UPLOAD_JS + BRIDGE_JS + "return await bridgeOperation(args);}"
        return await self.browser.page.evaluate(script, args)

    async def install(self, next_submit: float = 0, cooldown: float = 0) -> None:
        await self.call(
            "install",
            streams=self.browser.settings.upload_streams,
            interval=self.browser.settings.send_interval * 1000,
            next_submit=next_submit * 1000,
            cooldown=cooldown * 1000,
        )
        self.installed = True

    async def upload(self, row: dict, files: list[Path]) -> dict:
        part = json.loads(row["payload"])
        args = {
            "ticket": row["token"],
            "part": part | {"client_id": row["client_id"]},
            "timeout": self.browser.settings.upload_timeout * 1000,
        }
        try:
            async with self.ui:
                await self.call("attach", **args)
                await self.browser.page.locator(f'[data-qyt-outbound-upload="{row["token"]}"]').set_input_files(
                    [str(path) for path in files]
                )
            return await asyncio.wait_for(self.call("upload", **args), timeout=self.browser.settings.upload_timeout)
        finally:
            with contextlib.suppress(Exception):
                await self.call("remove", **args)

    async def ready(self, row: dict) -> bool:
        return await self.call("check", handle=row["handle"], generation=row["generation"])

    async def submit(self, job: dict, row: dict, part: dict) -> None:
        await self.call(
            "submit",
            conversation=job["conversation"],
            client_id=row["client_id"],
            handle=row["handle"],
            generation=row["generation"],
            part=part,
        )

    async def poll(self, ids: list[str]) -> list[dict]:
        return await self.call("poll", ids=ids)

    async def forget(self, row: dict) -> None:
        await self.call("forget", client_id=row["client_id"], handle=row["handle"])

    async def close(self) -> None:
        if self.installed:
            await self.call("close")
            self.installed = False
