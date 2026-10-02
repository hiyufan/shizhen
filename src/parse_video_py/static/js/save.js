// 保存到手机：设备判断、「存到相册」按钮（iPhone 走系统分享面板）、iPhone 实况的说明小字
//
// iPhone 上网页点下载存进的是「文件」App，要进相册还得自己去那儿再存一遍。Safari 支持带文件的
// 系统分享（iOS 15+），面板里点「存储图像 / 存储视频 / 存储 N 项」就直接进照片 App。
// 实况不走这条路：真机上分享面板会把配好对的 JPG + MOV 存成一张图加一段视频，合不成实况。
// 只在 iPhone / iPad 上换成这条路：电脑和安卓点下载本来就顺。

import { el, fmtSize, notice } from './dom.js';

export const IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
export const ANDROID = /Android/i.test(navigator.userAgent);
export const PHOTOS = IOS && (() => {
  try {
    const probe = new File([new Uint8Array(1)], 'a.jpg', { type: 'image/jpeg' });
    return !!navigator.canShare && navigator.canShare({ files: [probe] });
  } catch (_) {
    return false;
  }
})();

const MIME_EXT = {
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/heic': 'heic', 'image/gif': 'gif',
  'video/mp4': 'mp4', 'video/quicktime': 'mov',
};
const STALL_MS = 20000;
const ATTEMPTS = 3;

// 拉一个文件（同源地址），边下边报进度。跨境转发偶尔卡在半路：20 秒没收到数据就算卡住，
// 换个地址（&retry=n，和 proxyImg 一样避开缓存里那份残缺的）再试，最多三次
async function download(url, name, onBytes, type, guard) {
  const res = await fetch(url, { signal: guard.signal });
  if (!res.ok) throw new Error('下载失败（' + res.status + '）');
  const mime = type || (res.headers.get('content-type') || '').split(';')[0] || 'application/octet-stream';
  const total = +res.headers.get('content-length') || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    guard.touch();
    chunks.push(value);
    got += value.length;
    if (onBytes) onBytes(got, total);
  }
  return new File(chunks, name + (MIME_EXT[mime] ? '.' + MIME_EXT[mime] : ''), { type: mime });
}

/** 一段时间没收到数据就中断请求；每收到一块调 touch() 续命 */
function stallGuard() {
  const ctl = new AbortController();
  let timer = setTimeout(() => ctl.abort(), STALL_MS);
  return {
    signal: ctl.signal,
    touch() { clearTimeout(timer); timer = setTimeout(() => ctl.abort(), STALL_MS); },
    done() { clearTimeout(timer); },
  };
}

async function fetchWithRetry(url, name, onBytes, type, attempts) {
  for (let attempt = 0; ; attempt++) {
    const guard = stallGuard();
    const target = attempt ? url + (url.includes('?') ? '&' : '?') + 'retry=' + attempt : url;
    try {
      return await download(target, name, onBytes, type, guard);
    } catch (e) {
      if (attempt >= attempts - 1) throw new Error(e.name === 'AbortError' ? '网络太慢，下载卡住了' : e.message);
      if (onBytes) onBytes(0, 0);
    } finally {
      guard.done();
    }
  }
}

/** sources：一个地址，或者按顺序试的几个地址（边缘、服务器转发）。前面的只试一次，失败就换下一个 */
export async function fetchFile(sources, name, onBytes, type) {
  const list = [].concat(sources);
  for (let i = 0; i < list.length - 1; i++) {
    try {
      return await fetchWithRetry(list[i], name, onBytes, type, 1);
    } catch (_) {
      if (onBytes) onBytes(0, 0);
    }
  }
  return fetchWithRetry(list[list.length - 1], name, onBytes, type, ATTEMPTS);
}

/** 几个文件一起拉（同时最多 3 个），进度按字节合计 */
export async function fetchFiles(items, onBytes) {
  const got = new Array(items.length).fill(0);
  const out = new Array(items.length);
  let next = 0;
  const report = () => onBytes && onBytes(got.reduce((a, b) => a + b, 0), 0);
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await fetchFile(items[i].url, items[i].name, (g) => { got[i] = g; report(); }, items[i].type);
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  return out;
}

/** photosButton 的 load：只拉一个文件（sources 同 fetchFile） */
export const oneFile = (sources, name, type) => async (onBytes) => [await fetchFile(sources, name, onBytes, type)];

// 「存到相册」按钮：第一次点先把文件拉到手机上（显示进度），拉完立刻弹分享面板。
// Safari 要求弹面板离点击不能太久，大文件拉完它会拒绝（NotAllowedError）：这时按钮变成
// 「点这里存到相册」，再点一下，文件已经在手里，直接弹
export function photosButton(label, load, cls = 'btn dark') {
  let files = null;
  let busy = false;
  const btn = el('button', { class: cls, type: 'button' }, label);

  async function share() {
    try {
      await navigator.share({ files });
      btn.textContent = label;
    } catch (e) {
      if (e.name === 'NotAllowedError') btn.textContent = '准备好了，点这里存到相册';
      else if (e.name === 'AbortError') btn.textContent = label; // 用户自己关掉了面板
      else btn.textContent = '没打开分享面板，点了重试';
    }
  }

  async function prepare() {
    busy = true;
    btn.disabled = true;
    btn.textContent = '准备中…';
    try {
      files = await load((got, total) => {
        btn.textContent = '准备中 ' + (total ? Math.round((got / total) * 100) + '%' : fmtSize(got));
      });
      if (!navigator.canShare({ files })) throw new Error('Safari 不支持把这种文件存相册');
      return true;
    } catch (e) {
      files = null;
      btn.textContent = '没成功，点了重试';
      notice(e.message + '。也可以点旁边的下载。');
      return false;
    } finally {
      busy = false;
      btn.disabled = false;
    }
  }

  btn.addEventListener('click', async () => {
    if (busy) return;
    if (files || (await prepare())) await share();
  });
  return btn;
}

// ---------- iPhone 实况的说明
// iPhone 上实况的出路：视频存相册后用剪映（大多数人装着）或视频转实况的 App 转一下；有 Mac 的话 zip 拖进「照片」
const LIVE_APP = 'https://apps.apple.com/cn/app/id6738693561'; // 一键实况：免费，只做视频转实况

/** 三行小字：先说是 iOS 的限制（不然用户以为是网站做得不行），再给两条出路。btn / zip 是本处按钮上的叫法 */
export function iphoneLiveHint(btn, zip = '实况包') {
  return el('div', { class: 'fine' },
    el('p', {}, el('b', {}, '受 iOS 限制，网页无法直接把会动的实况存进相册'), '，只有 App 能写。'),
    el('p', {}, '做法：先点「' + btn + '」，再用剪映或',
      el('a', { href: LIVE_APP, target: '_blank', rel: 'noopener' }, '「一键实况」'), '把视频转成实况。'),
    el('p', {}, '有 Mac：下载' + zip + '，把 JPG 和 MOV 一起拖进「照片」App 就是实况，开了 iCloud 照片会同步到 iPhone。'));
}
