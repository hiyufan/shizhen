// 和后端打交道：接口请求、后台任务轮询、解析结果里的签名（转发 / 边缘取图都要用）

import { serverMessage, t } from './i18n.js';

import { el, sleep } from './dom.js';

// 跨境链路偶尔断一下。GET 是幂等的，断了自动再试一次；POST 不重试，免得重复建任务
async function fetchOnce(path, opts) {
  const { retry: allowedRetries, ...init } = opts;
  const retry = allowedRetries ?? ((opts.method || 'GET') === 'GET' ? 1 : 0);
  for (let attempt = 0; ; attempt++) {
    try {
      return await fetch(path, init);
    } catch (err) {
      if (opts.signal && opts.signal.aborted) throw err;
      if (attempt >= retry) throw new Error(t('网络连接中断了，检查一下网络再试'));
      await sleep(1000);
    }
  }
}

export async function api(path, opts = {}) {
  const res = await fetchOnce(path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { /* 非 JSON 的错误页 */ }
  if (!res.ok) throw new Error(serverMessage((body && (body.detail || body.msg)) || t('请求失败 {status}', { status: res.status })));
  return body;
}

async function* streamEvents(response) {
  const reader = response.body.getReader();
  const decoder = new window.TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (frame.startsWith('data: ')) yield JSON.parse(frame.slice(6));
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/** 基础结果之后继续读同一条连接；补充失败、中断或取消时页面保留已展示的结果。 */
async function followUpdates(events, onUpdate) {
  try {
    for (;;) {
      const { done, value: event } = await events.next();
      if (done || event.type === 'done') return;
      if (event.type === 'update') {
        onUpdate(event.result);
        return;
      }
    }
  } catch (_) {
    // 连接中断或被新的解析取消
  } finally {
    await events.return();
  }
}

async function openStream(path, signal, payload) {
  const opts = payload === undefined ? { signal } : {
    signal, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), retry: 1,
  };
  const response = await fetchOnce(path, opts);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(serverMessage(body.detail || body.msg || t('请求失败 {status}', { status: response.status })));
  }
  return streamEvents(response);
}

export async function parseWithProgress(path, update, signal, payload, onUpdate = null) {
  const events = await openStream(path, signal, payload);
  for (;;) {
    const { done, value: event } = await events.next();
    if (done) break;
    if (event.type === 'progress') update(event);
    if (event.type !== 'result') continue;
    if (event.pending && onUpdate) followUpdates(events, onUpdate);
    else await events.return();
    return event.result;
  }
  throw new Error(t('解析连接中断了，请重新试一次'));
}

export const postJSON = (path, data) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

/** 轮询后台任务直到做完；每次拿到新状态都交给 onUpdate（进度条用）。失败时抛出任务的错误信息 */
export async function watchJob(job, onUpdate) {
  for (let delay = 500; ; delay = 700) {
    if (onUpdate) onUpdate(job);
    if (job.status === 'done') return job;
    if (job.status === 'error') throw new Error(serverMessage(job.error || t('任务失败')));
    await sleep(delay);
    job = await api('/api/jobs/' + job.id);
  }
}

// ---------- 解析结果里的签名：服务器只转发 / 处理签过名的地址

let sigs = {};
let edges = {};

export function useSignatures(data) {
  sigs = data.sig || {};
  edges = data.edge || {};
  useDirect(sigs);
}

// ---------- 直连：推特视频 CDN 对任何来源都放行跨域，开着代理的用户直接从推特下，比绕一趟美国服务器快得多；
// 服务器 7 天转发的 3 GB 里 90% 是它，其中下载按钮占四分之三。
// - 防盗链：带了非推特的 Referer 一律 403（2026-10 实测），所以请求都不带 Referer。<video> 没法单独
//   不带 Referer，预览播放仍走服务器转发，只有下载走直连
// - 国内没开代理的连不上：先用 1 字节的请求探一下（不挡结果显示），通了才用，下载中途失败也退回服务器转发

const DIRECT_HOSTS = /^video\.twimg\.com$/;
const PROBE_MS = 3000;
let direct = {};
let reachable = null; // 这一页里探过的结果：null 没探过，true / false

export const canDirect = (url) => {
  try {
    return DIRECT_HOSTS.test(new URL(url).hostname);
  } catch (_) {
    return false;
  }
};

async function probe(url) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), PROBE_MS);
  try {
    const res = await fetch(url, { headers: { Range: 'bytes=0-0' }, referrerPolicy: 'no-referrer', signal: ctl.signal });
    return res.ok;
  } catch (_) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function useDirect(current) {
  direct = {};
  const urls = Object.keys(current).filter(canDirect);
  if (!urls.length || reachable === false) return;
  (reachable ? Promise.resolve(true) : probe(urls[0])).then((ok) => {
    reachable = ok;
    if (!ok || sigs !== current) return; // 探的时候用户已经换了一条链接
    for (const u of urls) direct[u] = u;
  });
}

export const directOf = (url) => direct[url] || '';

export const sigOf = (url) => sigs[url] || '';

/** 经服务器转发的地址；download 时带上文件名，浏览器直接存成文件 */
export function proxy(url, name, download) {
  return '/api/proxy?url=' + encodeURIComponent(url) + '&sig=' + sigOf(url)
    + (name ? '&filename=' + encodeURIComponent(name) : '') + (download ? '&download=1' : '');
}

// ---------- 独立下载域名：点下载交给浏览器的整文件走另一个域名，那是一条全新的连接。
// 同源下载复用页面这条 HTTP/2 连接，它加载页面时被接收窗口顶住过，高延迟线路上要几十秒才爬到满速
// （见后端 web/download_host.py）。只用于 <a> 链接：页面里 fetch 的（存相册、预览）仍走本站，不跨域。
// 那个域名收不到本站 Cookie、不记统计，所以下载都在本站另报一声。
const DOWNLOAD_ORIGIN = document.documentElement.dataset.downloadOrigin || '';

/** 站内路径 → 下载链接 */
export const downloadUrl = (path) => DOWNLOAD_ORIGIN + path;

/** 任务产物下载链接的属性；跨域时 download 属性不生效，文件名由服务器的下载头给 */
export function jobDownload(job) {
  const report = () => {
    if (!DOWNLOAD_ORIGIN) return; // 同源下载服务器自己会记
    fetch('/api/jobs/' + job.id + '/download-hit', { method: 'POST', keepalive: true }).catch(() => {});
  };
  return { href: downloadUrl('/api/jobs/' + job.id + '/file'), download: job.filename || '', onclick: report };
}

/** 一个文件可以从哪儿拿：有国内边缘地址就先边缘（国内直达），再服务器转发（跨两次太平洋，兜底） */
export const sourcesOf = (url) => [edges[url], direct[url], proxy(url)].filter(Boolean);

/** 视频 / 音频的下载链接。边缘地址是跨域的，<a download> 浏览器不认，靠边缘回的下载头 */
export function mediaDownload(url, name) {
  return edges[url] ? edges[url] + '&dl=1&name=' + encodeURIComponent(name) : downloadUrl(proxy(url, name, true));
}

/** 走边缘 / 直连 / 下载域名的下载服务器记不到，单独报一声给使用统计（同源转发的那边自己会记） */
export function trackDownload(url) {
  if (!edges[url] && !direct[url] && !DOWNLOAD_ORIGIN) return;
  fetch('/api/download-hit', {
    method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, sig: sigOf(url) }),
  }).catch(() => {});
}

/** <video> 能用的地址：先边缘再服务器转发。不用直连：<video> 没法不带 Referer（见上面「直连」） */
export const videoSources = (url) => [edges[url], proxy(url)].filter(Boolean);

/** <video>：边缘地址播不了（节点出错、被平台拒）就退回服务器转发，只退一次 */
export function mediaVideo(url, attrs) {
  const [first, ...rest] = videoSources(url);
  const video = el('video', { ...attrs, src: first });
  video.addEventListener('error', () => {
    if (rest.length) video.src = rest.shift();
  });
  return video;
}

// 国内平台的图先从国内边缘节点直接拿（edges 里有地址的话），不绕海外服务器；
// 边缘拿不到就走服务器转发。转发是跨境的，偶尔断在半路：再自动重试两次，还不行就让用户点一下再试。
// 加 &retry=n 换个地址，浏览器不会拿缓存里那份残缺的
export function proxyImg(url, attrs = {}) {
  const src = proxy(url);
  const sources = [edges[url], src, src + '&retry=1', src + '&retry=2'].filter(Boolean);
  let i = 0;
  const img = el('img', { ...attrs, src: sources[0] });
  img.addEventListener('error', () => {
    const fromEdge = sources[i] === edges[url];
    if (++i < sources.length) {
      setTimeout(() => { img.src = sources[i]; }, fromEdge ? 0 : 800 * i);
    } else {
      img.classList.add('failed');
      img.title = t('图片没加载出来，点一下重试');
    }
  });
  img.addEventListener('click', () => {
    if (!img.classList.contains('failed')) return;
    img.classList.remove('failed');
    img.title = '';
    i = sources.length - 1;
    img.src = src + '&retry=' + Date.now();
  });
  return img;
}
