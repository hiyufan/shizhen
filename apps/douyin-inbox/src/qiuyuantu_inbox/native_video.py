"""在已登录页面内复用客户端视频解码 worker，仅返回有界媒体字节。"""

import asyncio
import base64
import hashlib
import uuid

from .native_live import CLIENT_JS

DOWNLOAD_JS = r"""
const {app, sdk, target} = nativeContext(element, args);
const validate = () => {
  nativeContext(element, args);
  if (Date.now() > args.deadline || !document.querySelector('[data-qyt-native-video="' + args.token + '"]')) {
    throw new Error('native_video_cancelled');
  }
  const message = sdk.getConversationMessages({conversation: target}).find(m => String(m.serverId) === args.message);
  const content = message ? JSON.parse(message.content || '{}') : {};
  if (!message || message.type !== 27 || String(message.sender) !== args.sender || message.isFromMe ||
      message.conversationId !== args.conversation || (content.md5 || content.resource_url?.md5) !== args.md5 ||
      String(content.live_photo_video?.vid) !== args.live_vid || content.live_photo_video?.md5 !== args.live_md5) {
    throw new Error('native_video_source_changed');
  }
  return content.live_photo_video;
};
const resource = validate();
const runtime = Object.keys(window).filter(k => k.startsWith('@pc-im/im:') && Array.isArray(window[k]));
if (runtime.length !== 1) throw new Error('native_video_runtime_changed');
let load;
window[runtime[0]].push([['qyt-video-' + args.token], {}, r => {load = r;}]);
window[runtime[0]].pop();
if (!load?.u || !load.m?.[4623] || !String(load.m[4623]).includes('proxy.decryptVideo')) {
  throw new Error('native_video_decoder_changed');
}
// 与网页视频下载使用同一请求管理器、资源入口及客户端 worker；加密字段不离开页面。
const response = await app.requestManager.request({isCache: true, noStatusCode: true, method: 'POST',
  url: '/aweme/v1/web/maya/story/batch_play_info/v1/', params: {app_name: 'douyin_web'},
  data: {req_infos: [{item_id: 0, tos_key: resource.tkey, type: 2}], with_caption: true},
  headers: {'Content-Type': 'application/json; charset=UTF-8'}});
validate();
const info = response?.data?.play_infos;
if (!Array.isArray(info) || info.length !== 1) throw new Error('native_video_resource_missing');
// 播放版本可能已经转码，必须取原始资源，并以原消息视频摘要核对解码结果。
const original = info[0].original_encrypted_url;
const url = original?.main_url || original?.backup_url;
const address = new URL(url);
const hosts = ['douyinvod.com', 'douyin.com', 'byteimg.com', 'amemv.com', 'bytecdn.cn', 'zjcdn.com'];
if (address.protocol !== 'https:' || !hosts
    .some(host => address.hostname === host || address.hostname.endsWith('.' + host))) {
  throw new Error('native_video_resource_host_changed');
}
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), Math.max(1, args.deadline - Date.now()));
let worker, workerURL;
try {
  const fetched = await fetch(address.href, {credentials: 'omit', signal: controller.signal});
  if (!fetched.ok || Number(fetched.headers.get('content-length')) > args.max_bytes) {
    throw new Error('native_video_download_failed');
  }
  const reader = fetched.body.getReader(), chunks = [];
  let size = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > args.max_bytes) {await reader.cancel(); throw new Error('native_video_too_large');}
    chunks.push(value); validate();
  }
  const encrypted = await new Blob(chunks).arrayBuffer();
  const workerSource = new URL(load.p + load.u('1188'), load.b).href;
  workerURL = URL.createObjectURL(new Blob(['importScripts(' + JSON.stringify(workerSource) + ');'],
    {type: 'text/javascript'}));
  worker = new Worker(workerURL);
  const call = payload => new Promise((resolve, reject) => {
    const id = args.token + '-' + payload.type;
    const timeout = setTimeout(() => reject(new Error('native_video_worker_timeout')),
      Math.max(1, args.deadline - Date.now()));
    const listener = event => {
      const result = event.data;
      if (!result?.____CORS_WORKER____ || result.id !== id) return;
      worker.removeEventListener('message', listener); clearTimeout(timeout);
      if (result.status !== 'success') reject(new Error('native_video_decode_failed'));
      else resolve(result.body);
    };
    worker.addEventListener('message', listener);
    worker.postMessage({...payload, id, ____CORS_WORKER____: true});
  });
  await call({type: 'context'});
  validate();
  const decoded = await call({type: 'request', handler: 'decryptVideo',
    arguments: [original.skey || resource.skey, encrypted]});
  validate();
  const blob = decoded instanceof Blob ? decoded : new Blob([decoded]);
  if (!blob.size || blob.size > args.max_bytes) throw new Error('native_video_too_large');
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const encode = buffer => {
    const parts = [];
    for (let i = 0; i < buffer.length; i += 32768) {
      parts.push(String.fromCharCode(...buffer.subarray(i, i + 32768)));
    }
    return btoa(parts.join(''));
  };
  // 私有进程只接收两份有界媒体字节，用标准库核对加密文件摘要；skey / tkey 均不返回。
  return {data: encode(bytes), encrypted: encode(new Uint8Array(encrypted))};
} finally {
  clearTimeout(timer); controller.abort(); worker?.terminate();
  if (workerURL) URL.revokeObjectURL(workerURL);
}
"""


async def read_native_video(page, request: dict, helpers: str) -> bytes:
    token = uuid.uuid4().hex
    await page.evaluate(
        "token=>{const e=document.createElement('div');e.hidden=true;"
        "e.setAttribute('data-qyt-native-video',token);document.body.append(e);}",
        token,
    )
    try:
        request = request | {"token": token, "deadline": await page.evaluate("Date.now()+80000")}
        script = "async(element,args)=>{" + helpers + CLIENT_JS + DOWNLOAD_JS + "}"
        result = await asyncio.wait_for(
            page.locator('[data-e2e="msg-item-content"]').first.evaluate(script, request), timeout=90
        )
        if any(len(result[key]) > (request["max_bytes"] + 2) // 3 * 4 for key in ("data", "encrypted")):
            raise ValueError("实况视频超过大小限制")
        encrypted = base64.b64decode(result["encrypted"], validate=True)
        data = base64.b64decode(result["data"], validate=True)
        if (
            max(len(data), len(encrypted)) > request["max_bytes"]
            or data[4:8] != b"ftyp"
            or hashlib.md5(encrypted, usedforsecurity=False).hexdigest() != request["live_md5"]
        ):
            raise ValueError("实况视频内容与原消息摘要不符")
        return data
    finally:
        await page.locator(f'[data-qyt-native-video="{token}"]').evaluate_all("els=>els.forEach(e=>e.remove())")
