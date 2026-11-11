// 视频结果的「图片」面板：上面是当前画面的截图，下面是封面，各自先给预览（缩略图、尺寸），确认后再保存。
//
// 截图在浏览器里画到 canvas 上完成，不经服务器转换。打开面板的那一刻先截正在显示的画面当缩略图，
// 同时在后台把最清楚、浏览器解得开的直链档位跳到同一时刻再截一次（预览常是兼容用的低一档，
// 抖音预览 576p、下载 1080p）；高清到手前保存按钮先等着，拿不到就保存预览画质并说明。
// 保存放在单独一次点击：iPhone 的分享面板要求紧跟一次点击，拉高清的几秒不会让它失效。
// <video> 都带 crossorigin：本站转发同源，国内边缘节点回 Access-Control-Allow-Origin，canvas 才不会被「污染」。

import { t } from './i18n.js';

import { proxyImg, sourcesOf, videoSources } from './api.js';
import { el, notice } from './dom.js';
import { PHOTOS, imageButton, saveFile } from './save.js';

// 后台取高清画面的总时限（边缘、服务器转发合计）；超时就保存预览画面，不让用户久等
const SHARP_TIMEOUT = 8000;

// 档位上标的编码 → canPlayType 用的 codecs 串；浏览器明说解不开的档位不去拉
const CODEC_PROBES = [
  [/^(avc|h\.?264)/i, 'avc1.640028'],
  [/^(hev|hvc|h\.?265|bytevc1)/i, 'hvc1.1.6.L120.90'],
  [/^av0?1/i, 'av01.0.08M.08'],
  [/^vp0?9/i, 'vp09.00.40.08'],
];

const shortSide = (w, h) => Math.min(w || h || 0, h || w || 0);
const hasFrame = (video) => video.readyState >= 2 && video.videoWidth > 0;

function decodable(format) {
  const probe = CODEC_PROBES.find(([pattern]) => pattern.test(format.codec || ''));
  if (!probe) return true; // 编码不明：试一下，解不开会超时或报错，再用预览画面
  const type = (format.ext === 'webm' ? 'video/webm' : 'video/mp4') + '; codecs="' + probe[1] + '"';
  return el('video').canPlayType(type) !== '';
}

/** 比预览清楚一截、有直链、浏览器解得开的档位里最高的一档；预览还没出画面时不比较 */
function sharperFormat(data, preview) {
  const minimum = shortSide(preview.videoWidth, preview.videoHeight) * 1.1;
  const candidates = [data.download_format, ...(data.formats || [])].filter((f) => f?.url
    && ['mp4', 'webm', 'mov', undefined].includes(f.ext) && shortSide(f.width, f.height) > minimum && decodable(f));
  candidates.sort((a, b) => shortSide(b.width, b.height) - shortSide(a.width, a.height));
  return candidates[0] || null;
}

function release(video) {
  video.removeAttribute('src');
  video.load();
}

/** 用一个看不见的 <video> 打开地址并跳到 time；跳到位、出了画面才算成功 */
function seekTo(src, time, deadline) {
  const video = el('video', { muted: true, playsinline: true, preload: 'auto', crossorigin: 'anonymous', src });
  return new Promise((resolve, reject) => {
    const fail = (reason) => {
      clearTimeout(timer);
      release(video);
      reject(new Error(reason));
    };
    const timer = setTimeout(() => fail('timeout'), Math.max(0, deadline - Date.now()));
    video.addEventListener('error', () => fail('error'), { once: true });
    video.addEventListener('loadedmetadata', () => {
      video.currentTime = Math.min(time, Math.max(0, video.duration - 0.05));
    }, { once: true });
    video.addEventListener('seeked', () => {
      clearTimeout(timer);
      if (hasFrame(video)) resolve(video);
      else fail('no-video');
    }, { once: true });
  });
}

function canvasBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error(t('没截到画面，请再试一次。')))), 'image/png');
  });
}

/** 当前帧 → { file, width, height }。跨域画面（理论上不会出现）会报错，不把空白图存下来 */
async function drawFrame(video, name) {
  const canvas = el('canvas', { width: video.videoWidth, height: video.videoHeight });
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  try {
    const file = new File([await canvasBlob(canvas)], name + '.png', { type: 'image/png' });
    return { file, width: canvas.width, height: canvas.height };
  } catch (error) {
    if (error.name === 'SecurityError') throw new Error(t('这个视频的画面不能在网页里截取，下载后用播放器截图吧。'));
    throw error;
  }
}

/** 高清档位同一时刻的画面：边缘不通再走服务器转发，两次合计不超过 SHARP_TIMEOUT；拿不到返回 null */
async function sharperShot(format, time, name) {
  const deadline = Date.now() + SHARP_TIMEOUT;
  for (const src of videoSources(format.url)) {
    if (Date.now() >= deadline) break;
    let video = null;
    try {
      video = await seekTo(src, time, deadline);
      return await drawFrame(video, name);
    } catch (_) {
      // 换下一个地址；都不行就用预览画面
    } finally {
      if (video) release(video);
    }
  }
  return null;
}

/** 时间点按 0.1 秒取整后拆成分、秒，免得 59.97 秒显示成「0:60.0」 */
function minutesSeconds(time) {
  const tenths = Math.round(time * 10);
  return [Math.floor(tenths / 600), ((tenths % 600) / 10).toFixed(1)];
}

/** 文件名带上时间点，同一视频截几张不会互相覆盖：标题_1m05.3s */
function frameName(baseName, time) {
  const [m, s] = minutesSeconds(time);
  return baseName + '_' + (m ? m + 'm' + s.padStart(4, '0') : s) + 's';
}

/** 卡片标题上的时间点，和播放器一样带分钟：0:02.1 */
function clockAt(time) {
  const [m, s] = minutesSeconds(time);
  return m + ':' + s.padStart(4, '0');
}

function flash(video) {
  video.classList.remove('shot-flash');
  void video.offsetWidth; // 重新触发动画；减少动态效果时全站已关掉动画
  video.classList.add('shot-flash');
}

// ---------- 面板

/** iPhone 走分享面板存相册，其他设备直接下载 */
async function keep(file) {
  if (!PHOTOS) {
    saveFile(file);
    return;
  }
  try {
    await navigator.share({ files: [file] });
  } catch (error) {
    if (error.name !== 'AbortError') notice(error.message);
  }
}

/** 预览卡片：缩略图 + 标题、尺寸 / 状态、操作按钮 */
function card(thumb) {
  const title = el('b', {});
  const meta = el('p', { class: 'hint', role: 'status' });
  const actions = el('div', { class: 'group shot-acts' });
  const element = el('div', { class: 'shot' }, el('div', { class: 'shot-thumb' }, thumb),
    el('div', { class: 'shot-body' }, title, meta), actions);
  return { element, title, meta, actions };
}

/** 截图卡片：缩略图先放预览画质，高清到手后换上；保存按钮在结果确定前不可点 */
function frameView(again) {
  const thumb = el('img', { alt: '' }); // 标题已说明是哪一刻的画面，缩略图不重复朗读
  const view = card(thumb);
  const save = el('button', { class: 'btn sm dark', type: 'button' }, t('保存截图'));
  let shot = null;
  save.addEventListener('click', () => shot && keep(shot.file));
  view.actions.append(save, el('button', { class: 'btn sm', type: 'button', onclick: again }, t('再截一张')));
  const show = (next) => {
    if (thumb.src) URL.revokeObjectURL(thumb.src);
    if (next) thumb.src = URL.createObjectURL(next.file);
    else thumb.removeAttribute('src'); // 空 src 会显示破图图标
  };
  return {
    element: view.element,
    start(time) {
      shot = null;
      show(null);
      view.title.textContent = t('{time} 的画面', { time: clockAt(time) });
      view.meta.textContent = t('正在取高清画面…');
      save.disabled = true;
    },
    preview: show,
    ready(next, note) {
      shot = next;
      show(next);
      save.disabled = false;
      view.meta.textContent = t('{width}×{height} PNG', { width: next.width, height: next.height }) + (note ? ' · ' + note : '');
    },
    fail(message) {
      show(null);
      view.meta.textContent = message;
    },
    release: () => show(null),
  };
}

/** 后台高清的结果写进卡片：高清优先，退回预览画质时说明；都没拿到就提示先播放 */
function settle(view, sharp, first, format) {
  if (sharp || first) view.ready(sharp || first, format && !sharp ? t('高清没取到，这是预览画质') : '');
  else view.fail(t('视频画面还没加载出来，先播放一下再截。'));
}

/**
 * 截图卡片：一出来就截当前画面，「再截一张」按播放器此刻的位置重截。
 * 先截正在显示的画面，再在后台换高清；又截了一次或面板已收起，旧结果不再写进去。
 */
function frameCard(data, baseName, video) {
  let current = 0;
  const shoot = async () => {
    const token = ++current;
    const time = video.currentTime;
    const name = frameName(baseName, time);
    const format = sharperFormat(data, video);
    view.start(time);
    let first = null;
    try {
      if (hasFrame(video)) first = await drawFrame(video, name);
    } catch (error) {
      view.fail(error.message);
      return;
    }
    if (token !== current) return;
    if (first) {
      flash(video);
      view.preview(first);
    }
    const sharp = format ? await sharperShot(format, time, name) : null;
    if (token === current) settle(view, sharp, first, format);
  };
  const view = frameView(shoot);
  shoot();
  return {
    element: view.element,
    cleanup() {
      current += 1;
      view.release();
    },
  };
}

/** 封面卡片：显示封面和实际尺寸，按原始格式保存 */
function coverCard(data, baseName) {
  const thumb = proxyImg(data.cover_url, { alt: '' });
  const view = card(thumb);
  view.title.textContent = t('封面');
  const save = imageButton(t('保存封面'), () => sourcesOf(data.cover_url), baseName + '_cover');
  save.className = 'btn sm dark';
  view.actions.append(save);
  const size = () => { view.meta.textContent = t('{width}×{height}', { width: thumb.naturalWidth, height: thumb.naturalHeight }); };
  if (thumb.complete && thumb.naturalWidth) size();
  else thumb.addEventListener('load', size, { once: true });
  return view.element;
}

/**
 * 「图片」标签：有可截的播放器给截图，有封面给封面；两样都没有返回 null。
 * 只有一样时标签直接叫「截图」或「封面」。
 */
export function imageStep(data, baseName, video) {
  if (!video && !data.cover_url) return null;
  const label = video && data.cover_url ? t('图片') : video ? t('截图') : t('封面');
  return {
    key: 'image', label,
    open(body) {
      const frame = video ? frameCard(data, baseName, video) : null;
      body.append(...[frame?.element, data.cover_url ? coverCard(data, baseName) : null].filter(Boolean));
      return frame ? frame.cleanup : null;
    },
  };
}
