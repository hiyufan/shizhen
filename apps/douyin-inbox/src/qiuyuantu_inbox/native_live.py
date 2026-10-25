"""复用已登录网页客户端配对实况，不导出账号凭证或重建请求签名。"""

from __future__ import annotations

import asyncio
from pathlib import Path

CLIENT_JS = r"""
const pageClient = element => {
  const key = Object.keys(element || {}).find(k => k.startsWith('__reactFiber$'));
  for (let fiber = element?.[key], depth = 0; fiber && depth < 90; fiber = fiber.return, depth++) {
    const value = fiber.memoizedProps?.value;
    if (value?.imSdkService?.imSdkManager && value.saasApi) return value;
  }
  throw new Error('native_client_unavailable');
};
const nativeContext = (element, args) => {
  if (!element?.isConnected) element = document.querySelector('[data-e2e="msg-item-content"]');
  const app = pageClient(element);
  const heading = document.querySelector('.StackLayoutStackChatHeadertitle');
  const current = property(heading, ['curConversation']);
  const info = conversation(current);
  const editor = document.querySelector('[data-e2e="msg-input"] [contenteditable="true"]');
  if (args.upload_token && (Date.now() > args.deadline ||
      document.querySelector('[data-qyt-live-upload]')?.getAttribute('data-qyt-live-upload') !== args.upload_token)) {
    throw new Error('native_upload_expired');
  }
  if (!info || info.id !== args.conversation || info.account !== args.account ||
      !editor || editor.innerText.replace(/\u200b/g, '').trim()) {
    throw new Error('native_recipient_or_draft_changed');
  }
  const sdk = app.imSdkService.imSdkManager.getImSdkInstance();
  const target = sdk.getConversation({conversationId: args.conversation});
  if (!target || target.id !== info.id || target.type !== 1) throw new Error('native_target_changed');
  return {app, sdk, target};
};

"""

UPLOAD_JS = r"""
const timings = {};
const measure = async (name, operation) => {
  const started = performance.now();
  try {return await operation();}
  finally {timings[name] = (performance.now() - started) / 1000;}
};
const posterBlob = async file => {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 480 / Math.max(bitmap.width, bitmap.height));
    const canvas = new OffscreenCanvas(Math.max(1, Math.round(bitmap.width * scale)),
      Math.max(1, Math.round(bitmap.height * scale)));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await canvas.convertToBlob({type: 'image/jpeg', quality: 0.85});
  } finally {bitmap.close();}
};
const uploadCache = app => {
  const symbol = Symbol.for('qiuyuantu.inbox.nativeLiveCache.v1');
  if (!app[symbol]) app[symbol] = new Map();
  const cache = app[symbol];
  for (const [key, entry] of cache) if (entry.expires <= Date.now()) cache.delete(key);
  return cache;
};
const pairKey = async (files, account) => {
  const hashes = await Promise.all(files.map(async file => {
    const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  }));
  return [account, ...hashes].join(':');
};
const pageUploader = async (app, args, guard = () => nativeContext(element, args)) => {
  // 使用当前版本网页实际加载的上传器；上传凭证及加密资源字段始终留在页面内。
  const runtime = Object.keys(window).filter(k => k.startsWith('@pc-im/im:') && Array.isArray(window[k]));
  if (runtime.length !== 1) throw new Error('native_upload_runtime_changed');
  let load;
  const chunks = window[runtime[0]];
  chunks.push([['qyt-live-' + crypto.randomUUID()], {}, r => {load = r;}]);
  chunks.pop();
  if (!load?.e) throw new Error('native_upload_loader_changed');
  await Promise.all([load.e('2443'), load.e('199')]);
  const Uploader = load(88362).default;
  if (!Uploader?.prototype.uploadImage || !Uploader.prototype.uploadVideo) {
    throw new Error('native_upload_adapter_changed');
  }
  guard();
  const config = await measure('upload_native_config', () => app.requestManager.request({method: 'GET',
    url: '/aweme/v1/web/im/upload/config/v2', noStatusCode: true}));
  if (!config?.public_image_config || !config.inner_image_config) throw new Error('native_upload_config_missing');
  guard();
  return new Uploader(args.account, app.mainOptions.aid, config, () => {});
};
const uploadPair = async (app, args) => {
  const input = document.querySelector('[data-qyt-live-upload]');
  const files = Array.from(input?.files || []);
  if (files.length !== 2 || files[0].type !== 'image/jpeg' && files[0].type !== 'image/png' ||
      files[1].type !== 'video/mp4') throw new Error('native_pair_files_invalid');
  const key = await measure('upload_native_hash', () => pairKey(files, args.account));
  nativeContext(element, args);
  const cache = uploadCache(app), cached = cache.get(key);
  const started = performance.now();
  if (cached) {
    if (cached.content.md5 !== args.part.md5[0] || cached.content.live_photo_video?.md5 !== args.part.md5[1]) {
      throw new Error('native_cached_digest_changed');
    }
    cache.delete(key); cache.set(key, cached);
    timings.upload_native_reuse = (performance.now() - started) / 1000;
    return {...cached.content, live_photo_video: {...cached.content.live_photo_video, vid: args.part.live_vid}};
  }
  const uploader = await pageUploader(app, args);
  // 照片上传与视频（含审核封面）互不依赖；只并行上传，不并行提交消息。
  const [imageRaw, videoRaw] = await Promise.all([
    measure('upload_native_image', () => uploader.uploadImage(files[0])),
    measure('upload_native_video', async () => uploader.uploadVideo({
      mp4: files[1], thumbBlob: await posterBlob(files[0])}))
  ]);
  const image = JSON.parse(imageRaw), video = JSON.parse(videoRaw);
  if (image.md5 !== args.part.md5[0] || video.video?.md5 !== args.part.md5[1] ||
      !image.resource_url?.oid || !image.resource_url?.skey || !video.video?.tkey || !video.video?.skey) {
    throw new Error('native_uploaded_digest_changed');
  }
  nativeContext(element, args);
  const content = {...image, aweType: 2704,
    live_photo_video: {...video.video, vid: args.part.live_vid, is_new_encrypt: 1}};
  cache.set(key, {content, expires: Date.now() + 3600000});
  while (cache.size > 64) cache.delete(cache.keys().next().value);
  return content;
};
"""

SEND_JS = r"""
const {app, sdk, target} = nativeContext(element, args);
const content = await measure('upload_native_assets', () => uploadPair(app, args));
// 上传可能耗时，再次核对收件人；上传失败或页面变化时不会提交消息。
nativeContext(element, args);
const outbound = await sdk.createMessage({conversation: target, type: 27,
  content: JSON.stringify(content), clientId: args.part.client_id, insert: true});
if (String(outbound.sender) !== args.account || outbound.conversationId !== args.conversation) {
  throw new Error('native_sender_changed');
}
// 创建本地消息后再核对一次当前账号、会话和草稿，随后只提交这一个客户端消息 ID。
nativeContext(element, args);
const manager = app.imSdkService.sendMessageManager;
const builder = manager.createMessageBuilder({message: outbound, conversation: target});
const response = await measure('upload_native_submit', () => manager.sendMessage(builder));
if (!response?.success) throw new Error('native_pair_rejected');
const delivered = JSON.parse(outbound.content);
return {id: String(outbound.serverId || ''), type: outbound.type,
  conversation: outbound.conversationId, from_me: String(outbound.sender) === args.account,
  flight: outbound.flightStatus, server_status: outbound.serverStatus,
  md5: delivered.md5, live_vid: delivered.live_photo_video?.vid,
  live_md5: delivered.live_photo_video?.md5, awe_type: delivered.aweType, data: {}, timings};
"""


async def _send_pair(page, request: dict, helpers: str) -> dict:
    script = "async(element,args)=>{" + helpers + CLIENT_JS + UPLOAD_JS + SEND_JS + "}"
    return await page.locator('[data-e2e="msg-item-content"]').first.evaluate(script, request)


async def send_live_files(page, request: dict, helpers: str, files: list[Path]) -> dict:
    if len(files) != 2:
        raise ValueError("原生实况必须包含一张照片和对应视频")
    request = request | {"upload_token": request["part"]["client_id"]}
    await page.evaluate(
        """token => {
      if (document.querySelector('[data-qyt-live-upload]')) throw new Error('native_upload_already_active');
      const input = document.createElement('input');
      input.type = 'file'; input.multiple = true; input.hidden = true;
      input.setAttribute('data-qyt-live-upload', token); document.body.append(input);
    }""",
        request["upload_token"],
    )
    try:
        await page.locator("[data-qyt-live-upload]").set_input_files([str(path) for path in files])
        request["deadline"] = await page.evaluate("Date.now() + 170000")
        return await asyncio.wait_for(_send_pair(page, request, helpers), timeout=180)
    finally:
        await page.locator("[data-qyt-live-upload]").evaluate_all("elements => elements.forEach(e => e.remove())")
