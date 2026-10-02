// 和后端打交道：接口请求、后台任务轮询、解析结果里的签名（转发 / 边缘取图都要用）

import { el, sleep } from './dom.js';

// 跨境链路偶尔断一下。GET 是幂等的，断了自动再试一次；POST 不重试，免得重复建任务
async function fetchOnce(path, opts) {
  const retry = (opts.method || 'GET') === 'GET' ? 1 : 0;
  for (let attempt = 0; ; attempt++) {
    try {
      return await fetch(path, opts);
    } catch (_) {
      if (attempt >= retry) throw new Error('网络连接中断了，检查一下网络再试');
      await sleep(1000);
    }
  }
}

export async function api(path, opts = {}) {
  const res = await fetchOnce(path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { /* 非 JSON 的错误页 */ }
  if (!res.ok) throw new Error((body && (body.detail || body.msg)) || '请求失败 ' + res.status);
  return body;
}

export const postJSON = (path, data) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

/** 轮询后台任务直到做完；每次拿到新状态都交给 onUpdate（进度条用）。失败时抛出任务的错误信息 */
export async function watchJob(job, onUpdate) {
  for (let delay = 500; ; delay = 700) {
    if (onUpdate) onUpdate(job);
    if (job.status === 'done') return job;
    if (job.status === 'error') throw new Error(job.error || '任务失败');
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
}

export const sigOf = (url) => sigs[url] || '';

/** 经服务器转发的地址；download 时带上文件名，浏览器直接存成文件 */
export function proxy(url, name, download) {
  return '/api/proxy?url=' + encodeURIComponent(url) + '&sig=' + sigOf(url)
    + (name ? '&filename=' + encodeURIComponent(name) : '') + (download ? '&download=1' : '');
}

/** 一个文件可以从哪儿拿：有国内边缘地址就先边缘（国内直达），再服务器转发（跨两次太平洋，兜底） */
export const sourcesOf = (url) => [edges[url], proxy(url)].filter(Boolean);

/** 视频 / 音频的下载链接。边缘地址是跨域的，<a download> 浏览器不认，靠边缘回的下载头 */
export function mediaDownload(url, name) {
  return edges[url] ? edges[url] + '&dl=1&name=' + encodeURIComponent(name) : proxy(url, name, true);
}

/** 走边缘的下载不经过服务器，单独报一声给使用统计（走服务器转发的那边自己会记） */
export function trackDownload(url) {
  if (!edges[url]) return;
  fetch('/api/download-hit', {
    method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, sig: sigOf(url) }),
  }).catch(() => {});
}

/** <video>：边缘地址播不了（节点出错、被平台拒）就退回服务器转发，只退一次 */
export function mediaVideo(url, attrs) {
  const [first, ...rest] = sourcesOf(url);
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
      img.title = '图片没加载出来，点一下重试';
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
