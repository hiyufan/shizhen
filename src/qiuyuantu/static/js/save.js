// 保存到手机：设备判断、「存到相册」按钮（iPhone 走系统分享面板）、iPhone 实况的说明小字
//
// iPhone 上网页点下载存进的是「文件」App，要进相册还得自己去那儿再存一遍。Safari 支持带文件的
// 系统分享（iOS 15+），面板里点「存储图像 / 存储视频 / 存储 N 项」就直接进照片 App。
// 实况不走这条路：真机上分享面板会把配好对的 JPG + MOV 存成一张图加一段视频，合不成实况。
// 只在 iPhone / iPad 上换成这条路：电脑和安卓点下载本来就顺。

import { t } from './i18n.js';

import { canDirect } from './api.js';
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
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/heic': 'heic', 'image/gif': 'gif', 'image/avif': 'avif',
  'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm',
};
const STALL_MS = 20000;
// 能接着下的时候不用等那么久：断了只是多发一次请求，不会白下
const RESUME_STALL_MS = 10000;
const ATTEMPTS = 3;
// 一路卡一路续的总次数上限，免得每次只挪一点、没完没了
const RESUMES = 24;

/** 一次下载收到的部分：type 是调用方指定的类型（不信源站的 Content-Type 时用） */
const newPart = (type = '') => ({ type, chunks: [], got: 0, total: 0, mime: '', etag: '', resumable: false });

/** 续传请求的响应是不是正好从已收到的位置接上 */
function continues(res, part) {
  if (!part.got || res.status !== 206) return false;
  const m = /^bytes (\d+)-\d+\/(\d+)$/.exec(res.headers.get('content-range') || '');
  return !!m && +m[1] === part.got && +m[2] === part.total;
}

// 只对本站地址（任务结果、服务器转发）续传：跨域带 Range 在旧浏览器上要预检。
// 不知道总大小、或者正文压缩过（字节数对不上）的也不续
const canResume = (res, url, total) => /^\/(?!\/)/.test(url) && total > 0
  && !res.headers.get('content-encoding') && /bytes/i.test(res.headers.get('accept-ranges') || '');

/** 从头收一份：记下大小和版本，判断之后断了能不能接着下 */
function startOver(res, part, url) {
  if (res.status === 206) throw new Error(t('下载失败（{status}）', { status: res.status }));
  const etag = res.headers.get('etag') || '';
  const total = +res.headers.get('content-length') || 0;
  Object.assign(part, {
    chunks: [],
    got: 0,
    total,
    mime: part.type || (res.headers.get('content-type') || '').split(';')[0] || 'application/octet-stream',
    etag: etag.startsWith('"') ? etag : '',
    resumable: canResume(res, url, total),
  });
}

// 拉一个文件到 part 里，边下边报进度。跨境转发偶尔卡在半路：一段时间没收到数据就算卡住，由调用方
// 换个地址（&retry=n，和 proxyImg 一样避开缓存里那份残缺的）再试。part 记着已经收到的部分：
// 本站地址支持按范围取，重试时从断点接着下；源站不认范围（回 200）就从头收
async function download(url, part, guard, onBytes) {
  const res = await fetch(url, fetchOptions(url, guard, part));
  if (!res.ok) throw new Error(t('下载失败（{status}）', { status: res.status }));
  if (!continues(res, part)) startOver(res, part, url);
  checkLimit(part.total, guard);
  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    guard.touch(part.resumable);
    part.chunks.push(value);
    part.got += value.length;
    if (onBytes) onBytes(part.got, part.total);
  }
  if (part.resumable && part.got < part.total) throw new Error(t('网络太慢，下载卡住了'));
}

function downloadedFile(chunks, name, mime) {
  // 音乐 CDN 的 .jpg 地址甚至 image/jpg 响应也可能装着动态 WebP，按文件头识别并保留原字节。
  if (mime === 'image/*') return originalImage(chunks, name);
  return new File(chunks, name + (MIME_EXT[mime] ? '.' + MIME_EXT[mime] : ''), { type: mime });
}

const isAvif = head => head.slice(4, 8) === 'ftyp' && /avif|avis/.test(head.slice(8));

async function originalImage(chunks, name) {
  const blob = new File(chunks, name);
  const bytes = new Uint8Array(await blob.slice(0, 32).arrayBuffer());
  const head = String.fromCharCode(...bytes);
  let mime = '';
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) mime = 'image/jpeg';
  else if (head.startsWith('\x89PNG\r\n\x1a\n')) mime = 'image/png';
  else if (/^GIF8[79]a/.test(head)) mime = 'image/gif';
  else if (head.startsWith('RIFF') && head.slice(8, 12) === 'WEBP') mime = 'image/webp';
  else if (isAvif(head)) mime = 'image/avif';
  if (!mime) throw new Error(t('源站没有返回有效的图片'));
  return new File([blob], name + '.' + MIME_EXT[mime], { type: mime });
}

/** 封面按实际文件类型保存；iPhone 使用系统分享，电脑和安卓直接下载。 */
export function imageButton(label, url, name, share = PHOTOS) {
  const load = oneFile(url, name, 'image/*');
  if (share) return photosButton(label, load);
  const btn = el('button', { class: 'btn dark', type: 'button' }, label);
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    try {
      const files = await load((got, total) => {
        btn.textContent = t('下载中 ') + (total ? Math.round(got * 100 / total) + '%' : fmtSize(got));
      });
      saveFile(files[0]);
    } catch (error) {
      if (btn.isConnected && !btn.closest('[hidden]')) notice(error.message);
    } finally {
      btn.textContent = label;
      btn.disabled = false;
      btn.setAttribute('aria-busy', 'false');
    }
  });
  return btn;
}

/** 直连推特要不带 Referer，不然 403（防盗链）；已经收到一部分的本站文件只要剩下的字节 */
function fetchOptions(url, guard, part) {
  if (canDirect(url)) return { signal: guard.signal, referrerPolicy: 'no-referrer' };
  if (!part?.resumable || !part.got) return { signal: guard.signal };
  const headers = { Range: 'bytes=' + part.got + '-' };
  // 文件在两次请求之间换过（任务重做了）时服务器回整份，download 那边从头收
  if (part.etag) headers['If-Range'] = part.etag;
  return { signal: guard.signal, headers };
}

/** 设了上限的下载：不知道多大或者超了就不下 */
function checkLimit(total, guard) {
  if (!guard.limit || (total && total <= guard.limit)) return;
  guard.cancel();
  throw new Error(t('文件太大，换服务器转发'));
}

/** 一段时间没收到数据就中断请求；每收到一块调 touch() 续命，能续传时（quick）等得短一些 */
function stallGuard(limit = 0) {
  const ctl = new AbortController();
  let timer = setTimeout(() => ctl.abort(), STALL_MS);
  return {
    signal: ctl.signal,
    limit,
    cancel() { ctl.abort(); },
    touch(quick = false) {
      clearTimeout(timer);
      timer = setTimeout(() => ctl.abort(), quick ? RESUME_STALL_MS : STALL_MS);
    },
    done() { clearTimeout(timer); },
  };
}

const retryUrl = (url, attempt) => (attempt ? url + (url.includes('?') ? '&' : '?') + 'retry=' + attempt : url);

async function fetchWithRetry(url, name, onBytes, type, attempts) {
  const part = newPart(type);
  for (let attempt = 0, failed = 0; ; attempt++) {
    const guard = stallGuard();
    const before = part.got;
    try {
      await download(retryUrl(url, attempt), part, guard, onBytes);
      return downloadedFile(part.chunks, name, part.mime);
    } catch (e) {
      // 接着下并且又收到了数据的不算失败次数：线路时通时断也能一段段下完
      failed = part.resumable && part.got > before ? 0 : failed + 1;
      if (failed >= attempts || attempt >= RESUMES) {
        throw new Error(e.name === 'AbortError' ? t('网络太慢，下载卡住了') : e.message);
      }
      if (onBytes && !part.resumable) onBytes(0, 0);
    } finally {
      guard.done();
    }
  }
}

/** sources：一个地址，或者按顺序试的几个地址（边缘、服务器转发）。前面的只试一次，失败就换下一个 */
export async function fetchFile(sources, name, onBytes, type) {
  // 传函数的话点的时候才取：直连是结果显示出来之后才探通的
  const list = [].concat(typeof sources === 'function' ? sources() : sources);
  for (let i = 0; i < list.length - 1; i++) {
    try {
      return await fetchWithRetry(list[i], name, onBytes, type, 1);
    } catch (_) {
      if (onBytes) onBytes(0, 0);
    }
  }
  return fetchWithRetry(list[list.length - 1], name, onBytes, type, ATTEMPTS);
}

// 直连下载整个文件先放在浏览器内存里再存，太大的手机扛不住，交给服务器转发（浏览器自己的下载管理器）
const DIRECT_MAX = 200 * 1024 * 1024;

/** 跨域直连下载成文件；文件太大、连不上、中途断了都返回 null，由调用方退回服务器转发 */
export async function fetchDirect(url, name, onBytes, type = 'video/mp4') {
  const guard = stallGuard(DIRECT_MAX);
  const part = newPart(type);
  try {
    await download(url, part, guard, onBytes);
    return downloadedFile(part.chunks, name, part.mime);
  } catch (_) {
    return null;
  } finally {
    guard.done();
  }
}

/** 浏览器里已经有的文件存到本地 */
export function saveFile(file) {
  const href = URL.createObjectURL(file);
  el('a', { href, download: file.name }).click();
  setTimeout(() => URL.revokeObjectURL(href), 60000);
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
// 「点这里存到相册」，再点一下，文件已经在手里，直接弹。
// eager：按钮一出来就开始拉，不等用户点。服务器刚按用户的要求做完的文件用它：文件到手机这一段
// （跨境）往往比服务器处理还久，早点开始，点的时候多半已经在手里了
export function photosButton(label, load, cls = 'btn dark', eager = false) {
  let files = null;
  let busy = false;
  const btn = el('button', { class: cls, type: 'button' }, label);

  async function share() {
    try {
      await navigator.share({ files });
      btn.textContent = label;
    } catch (e) {
      if (e.name === 'NotAllowedError') btn.textContent = t('准备好了，点这里存到相册');
      else if (e.name === 'AbortError') btn.textContent = label; // 用户自己关掉了面板
      else btn.textContent = t('没打开分享面板，点了重试');
    }
  }

  async function prepare() {
    busy = true;
    btn.disabled = true;
    btn.textContent = t('准备中…');
    try {
      files = await load((got, total) => {
        btn.textContent = t('准备中 ') + (total ? Math.round((got / total) * 100) + '%' : fmtSize(got));
      });
      if (!navigator.canShare({ files })) throw new Error(t('Safari 不支持把这种文件存相册'));
      return true;
    } catch (e) {
      files = null;
      btn.textContent = t('没成功，点了重试');
      if (btn.isConnected) notice(e.message + t('。也可以点旁边的下载。'));
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
  if (eager) {
    prepare().then((ok) => {
      if (ok) btn.textContent = t('准备好了，点这里存到相册');
    });
  }
  return btn;
}

// ---------- iPhone 实况的说明
// iPhone 上实况的出路：视频存相册后用剪映（大多数人装着）或视频转实况的 App 转一下；有 Mac 的话 zip 拖进「照片」
const LIVE_APP = 'https://apps.apple.com/cn/app/id6738693561'; // 一键实况：免费，只做视频转实况

/** 三行小字：先说是 iOS 的限制（不然用户以为是网站做得不行），再给两条出路。btn / zip 是本处按钮上的叫法 */
export function iphoneLiveHint(btn, zip = t('实况包')) {
  return el('div', { class: 'fine' },
    el('p', {}, el('b', {}, t('受 iOS 限制，网页无法直接把会动的实况存进相册')), t('，只有 App 能写。')),
    el('p', {}, t('做法：先点「') + btn + t('」，再用剪映或'),
      el('a', { href: LIVE_APP, target: '_blank', rel: 'noopener' }, t('「一键实况」')), t('把视频转成实况。')),
    el('p', {}, t('有 Mac：下载') + zip + t('，把 JPG 和 MOV 一起拖进「照片」App 就是实况，开了 iCloud 照片会同步到 iPhone。')));
}
