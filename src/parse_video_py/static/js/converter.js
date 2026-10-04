// 转换器：准备原视频 → 选一段、调参数 → 生成 GIF / 实况照片 / 动态照片 → 预览和保存
//
// 视频不超过这个格式的时长上限就直接按默认参数生成，剪片段和调参数收起来，要改再展开；
// 超过上限必须先选一段，就默认展开。进度和结果放在编辑区下面，收起时紧跟着标题

import { postJSON, watchJob } from './api.js';
import { $, el, fmtSize, fmtTime, spaced } from './dom.js';
import { PHOTOS, iphoneLiveHint, oneFile, photosButton } from './save.js';
import { LIMITS, Trimmer } from './trimmer.js';
import { jobCard } from './ui.js';

const GIF_TARGET = 1000000; // 微信：GIF 不超过 1MB 才自动播放，超过 5MB 发不出去
const FORMAT_NAMES = { gif: 'GIF', livephoto: '实况照片', motionphoto: '动态照片' };
const SWITCH_LABELS = { gif: '改做 GIF', livephoto: '改做实况照片（iPhone）', motionphoto: '改做动态照片（安卓）' };

let teardown = () => {};

/** 打开转换器。req 是 /api/prepare 的参数；本地上传的视频已经在服务器上了，给 { ready: source } */
export async function openConverter(req, format) {
  teardown();
  teardown = () => {};
  const box = $('#converter');
  const jobsBox = el('div', { class: 'jobs', style: 'margin-top:10px;max-width:420px' });
  const status = el('div', {},
    el('p', { class: 'hint' }, el('span', { class: 'spin' }), ' 正在把原视频搬上服务器，片子越长搬得越久…'), jobsBox);
  const conv = el('div', { class: 'conv' }, el('div', { class: 'conv-head' }, el('h2', {}, FORMAT_NAMES[format])), status);
  box.replaceChildren(conv);
  box.hidden = false;
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });

  let source;
  try {
    source = req.ready || (await prepareSource(req, jobCard(jobsBox, '准备原视频')));
  } catch (e) {
    status.replaceChildren(el('div', { class: 'notice' }, '准备失败：' + e.message));
    return;
  }
  conv.replaceChildren();
  teardown = buildConverter(conv, source, format);
}

async function prepareSource(req, card) {
  const r = await postJSON('/api/prepare', req);
  if (r.ready) return r.source;
  const done = await watchJob(r.job, card.update);
  return done.extra && done.extra.id ? done.extra : { id: r.source_id, ...(done.extra || {}) };
}

// ---------- 参数面板

function slider(label, attrs, show, onInput) {
  const out = el('output', {}, show(attrs.value));
  const input = el('input', {
    type: 'range', ...attrs,
    oninput: (e) => { const v = +e.target.value; onInput(v); out.textContent = show(v); },
  });
  return el('div', { class: 'field' }, el('label', {}, label, out), input);
}

function gifOptions(state) {
  const dither = el('select', { onchange: (e) => { state.dither = e.target.value; } },
    el('option', { value: 'bayer' }, '有序抖动，文件小'),
    el('option', { value: 'sierra2_4a' }, '误差扩散，更细腻'),
    el('option', { value: 'none' }, '不抖动，色块风'));
  const fit = el('input', { type: 'checkbox', checked: true, onchange: (e) => { state.fit = e.target.checked; } });
  return el('div', {},
    slider('帧率', { min: 5, max: 30, step: 1, value: state.fps }, (v) => v + ' fps', (v) => { state.fps = v; }),
    slider('宽度', { min: 160, max: 960, step: 40, value: state.width }, (v) => v + ' px', (v) => { state.width = v; }),
    slider('速度', { min: 0.5, max: 3, step: 0.25, value: state.speed }, (v) => v + '×', (v) => { state.speed = v; }),
    el('div', { class: 'field' }, el('label', {}, '抖动'), dither),
    el('label', { class: 'check' }, fit,
      el('span', {}, '压到 1 MB 以内',
        el('small', {}, '微信里超过 1 MB 的 GIF 不会自动播放。勾着时帧率和宽度是上限，超了会自动往下降。'))),
    el('p', { class: 'hint' }, 'GIF 最长 30 秒。想要文件小：降帧率、缩宽度、缩短时长。'));
}

/** 实况 / 动态照片的封面帧：默认片段正中间；滑块按选区的千分比取，或者直接用视频当前画面 */
function keyFrameOptions(state, video) {
  const out = el('output', {}, '中间');
  const range = el('input', {
    type: 'range', min: 0, max: 1000, step: 1, value: 500,
    oninput: (e) => {
      const t = state.start + (state.end - state.start) * (+e.target.value / 1000);
      state.keyTime = t;
      out.textContent = fmtTime(t);
      video.currentTime = t;
    },
  });
  const useCurrent = () => {
    state.keyTime = video.currentTime;
    out.textContent = fmtTime(video.currentTime);
    range.value = Math.round(((video.currentTime - state.start) / Math.max(state.end - state.start, 0.01)) * 1000);
  };
  const hint = el('p', { class: 'hint' });
  return {
    element: el('div', {},
      el('div', { class: 'field' }, el('label', {}, '封面帧', out), range),
      el('div', { class: 'field' },
        el('button', { class: 'btn sm', type: 'button', style: 'justify-self:start', onclick: useCurrent }, '用当前画面做封面')),
      hint),
    hint,
    /** 选区挪走了、封面帧不在里面了，就退回「中间」 */
    keepInRange() {
      if (state.keyTime == null || (state.keyTime >= state.start && state.keyTime <= state.end)) return;
      state.keyTime = null;
      out.textContent = '中间';
      range.value = 500;
    },
  };
}

function liveHintText(format) {
  if (format === 'motionphoto') return '会得到一张内嵌视频的 JPG（最长 10 秒）。保存到安卓相册就会显示为动态照片。';
  if (PHOTOS) {
    return '最长 10 秒。iPhone 上网页存不成实况：生成后把这段视频存到相册，再用剪映或视频转实况 App 转一下；'
      + '或者下载 zip 拖进 Mac 的「照片」。';
  }
  return '会得到一个 zip，里面是配对好的 JPG + MOV（最长 10 秒）。'
    + '把两个文件一起拖进 Mac 的「照片」App 就是实况，开了 iCloud 照片会同步到 iPhone。';
}

/** 竖屏视频按宽 480 出图，高度就有 850 多，GIF 动辄七八兆：默认宽度按长边 480 算 */
function defaultWidth(src) {
  if (src.height <= src.width) return 480;
  return Math.max(160, Math.round((480 * src.width) / src.height / 40) * 40);
}

// ---------- 组装

/** 返回清理函数：再开一个转换器时调 */
function buildConverter(conv, src, format) {
  // fit：压到 GIF_TARGET 以内，帧率和宽度变成上限，超了服务端自动往下降
  const state = {
    format, start: 0, end: Math.min(src.duration, LIMITS[format]),
    fps: 12, width: defaultWidth(src), speed: 1, keyTime: null, dither: 'bayer', fit: true,
  };

  // 格式在结果页点「转 GIF / 做成实况照片」时已经选过了，这里不再摆一排标签让人再选一遍，
  // 只留个小链接给偶尔想换的人
  const heading = el('h2', {}, '');
  const switcher = el('div', { class: 'conv-switch' });

  const video = el('video', { src: '/api/source/' + src.id, playsinline: true, muted: true, preload: 'auto', controls: true });
  const keyFrame = keyFrameOptions(state, video);
  const trimmer = new Trimmer(src, video, state, keyFrame.keepInRange);
  const gifPanel = gifOptions(state);
  const goBtn = el('button', { class: 'btn dark block', type: 'button', onclick: () => run() }, '开始转换');

  const left = el('div', {}, el('div', { class: 'media' + (src.height > src.width ? ' portrait' : '') }, video), trimmer.element);
  const right = el('div', {}, gifPanel, keyFrame.element, el('div', { style: 'margin-top:16px' }, goBtn));
  const summary = el('summary', {});
  const adjust = el('details', { class: 'adjust' }, summary, el('div', { class: 'conv-grid' }, left, right));
  const jobsBox = el('div', { class: 'jobs', style: 'margin-top:14px' });
  const resultBox = el('div', {});
  conv.append(el('div', { class: 'conv-head' }, heading, switcher), adjust, jobsBox, resultBox);
  adjust.addEventListener('toggle', trimmer.layout); // 收起时修剪条宽度是 0，展开后重新排

  // 换格式时上一个格式的任务可能还在跑：只认最后一次，旧的做完了也不往结果区放
  let seq = 0;
  async function run() {
    const mine = ++seq;
    const fmt = state.format;
    goBtn.disabled = true;
    const card = jobCard(jobsBox, FORMAT_NAMES[fmt]);
    try {
      const job = await postJSON('/api/convert', {
        source_id: src.id, format: fmt, start: state.start, end: state.end, fps: state.fps, width: state.width,
        dither: state.dither, speed: state.speed, key_time: state.keyTime,
        max_bytes: fmt === 'gif' && state.fit ? GIF_TARGET : null,
      });
      const done = await watchJob(job, card.update);
      card.card.remove();
      if (mine === seq) showResult(resultBox, done, fmt);
    } catch (e) {
      if (mine === seq) card.fail(e.message);
      else card.card.remove();
    } finally {
      if (mine === seq) goBtn.disabled = false;
    }
  }

  function setFormat(key) {
    const name = FORMAT_NAMES[key];
    state.format = key;
    switcher.replaceChildren(...Object.keys(FORMAT_NAMES).filter((k) => k !== key).map((k) =>
      el('button', { class: 'btn sm quiet', type: 'button', onclick: () => setFormat(k) }, SWITCH_LABELS[k])));
    gifPanel.hidden = key !== 'gif';
    keyFrame.element.hidden = key === 'gif';
    keyFrame.hint.textContent = liveHintText(key);
    trimmer.setRange(state.start, Math.min(state.end, state.start + LIMITS[key]), 'start');
    heading.textContent = name + (src.title ? ' · ' + src.title.slice(0, 24) : ''); // 窄屏会截断，格式放前面
    resultBox.replaceChildren();
    const fits = src.duration > 0 && src.duration <= LIMITS[key] + 0.05;
    adjust.open = !fits;
    if (fits) {
      summary.textContent = '剪片段、调参数';
      goBtn.textContent = '重新生成' + spaced(name);
      run();
    } else {
      summary.textContent = '先选一段：' + name + (/^[A-Za-z]/.test(name) ? ' ' : '') + '最长 ' + LIMITS[key] + ' 秒';
      goBtn.textContent = '生成' + spaced(name);
    }
  }

  setFormat(format);
  trimmer.layout();
  return trimmer.destroy;
}

// ---------- 结果

function livePreview(job) {
  const mov = el('video', { src: '/api/jobs/' + job.id + '/video', playsinline: true, muted: true, loop: true, preload: 'auto' });
  const prev = el('div', { class: 'prev' },
    el('img', { src: '/api/jobs/' + job.id + '/preview', alt: '实况封面' }), mov, el('span', { class: 'tip' }, '按住看动态'));
  const play = () => { prev.classList.add('playing'); mov.play().catch(() => {}); };
  const stop = () => { prev.classList.remove('playing'); mov.pause(); mov.currentTime = 0; };
  prev.addEventListener('pointerdown', play);
  prev.addEventListener('pointerup', stop);
  prev.addEventListener('pointerleave', stop);
  return prev;
}

function imagePreview(job, format) {
  const prev = el('div', { class: 'prev' }, el('img', { src: '/api/jobs/' + job.id + '/file?inline=1', alt: '转换结果' }));
  // iPhone 上点按钮下载会进「文件」App，长按图片才能直接存进相册
  if (format === 'gif' && matchMedia('(pointer: coarse)').matches) prev.append(el('span', { class: 'tip' }, '长按图片存到相册'));
  return prev;
}

function resultNote(job, format) {
  if (format === 'livephoto') {
    return '解压后把 JPG 和 MOV 一起拖进 Mac 的「照片」App 就是实况，开了 iCloud 照片会同步到 iPhone。直接传到 iPhone 上存不成实况。';
  }
  if (format !== 'gif') return '保存到安卓相册后会显示为动态照片；在电脑上它就是一张普通 JPG。';
  const base = '手机上长按图片就能存进相册；电脑上右键另存或点按钮。';
  const x = job.extra || {};
  if (x.fits == null) return base;
  if (!x.fits) return '压到最小还有 ' + fmtSize(job.filesize) + '，微信里要点一下才播放。把片段剪短一点就能压进 1 MB。' + base;
  const colors = x.colors < 256 ? '、' + x.colors + ' 色' : '';
  return '已压到 1 MB 以内（宽 ' + x.width + ' px、' + x.fps + ' fps' + colors + '），微信里能自动播放。' + base;
}

/** iPhone：GIF 的主按钮换成「存到相册」，下载留着当备用。实况：剪好的那段视频先存相册，再用 App 转（zip 只在 Mac 上有用） */
function photosAction(job, format, fileUrl) {
  if (!PHOTOS) return null;
  if (format === 'gif') {
    const name = (job.filename || 'clip').replace(/\.gif$/i, '');
    return photosButton('存到相册', oneFile(fileUrl + '?inline=1', name, 'image/gif'), 'btn dark block');
  }
  if (format === 'livephoto') {
    const name = (job.filename || 'live').replace(/\.zip$/i, '');
    return photosButton('存视频到相册', oneFile('/api/jobs/' + job.id + '/video', name, 'video/quicktime'), 'btn dark block');
  }
  return null;
}

/** 下载按钮：没有「存到相册」时它就是主按钮 */
function saveLink(job, fileUrl, secondary) {
  const ext = (job.filename || '').split('.').pop().toUpperCase();
  const label = (secondary ? '下载 ' : '保存 ') + ext + (job.filesize ? '，' + fmtSize(job.filesize) : '');
  return secondary
    ? el('a', { class: 'btn block', href: fileUrl, download: job.filename || '', style: 'margin-top:10px' }, label)
    : el('a', { class: 'btn block dark', href: fileUrl, download: job.filename || '' }, label);
}

function showResult(box, job, format) {
  const fileUrl = '/api/jobs/' + job.id + '/file';
  const photos = photosAction(job, format, fileUrl);
  const save = saveLink(job, fileUrl, !!photos);
  const note = PHOTOS && format === 'livephoto'
    ? iphoneLiveHint('存视频到相册', ' ZIP')
    : el('p', { class: 'hint' }, resultNote(job, format));
  box.replaceChildren(el('div', { class: 'result' },
    format === 'livephoto' ? livePreview(job) : imagePreview(job, format),
    el('div', { class: 'side' }, photos, save, el('div', { class: 'size' }, job.filename || ''), note)));
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
