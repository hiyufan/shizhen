// 首页工具的入口：粘贴链接 → 解析 → 展示结果；本地视频上传后直接进转换器

import { api, useSignatures } from './api.js';
import { openConverter } from './converter.js';
import { $, el, notice } from './dom.js';
import { renderResult } from './result.js';
import { feedbackBox } from './ui.js';

const REASON_TIPS = {
  deleted: '确认一下原内容还在，或者重新复制一次分享链接（小红书的链接会过期）。',
  login: '站长可以在服务器上配置 cookies 解决；换个平台的链接不受影响。',
  blocked: '过几分钟再试。这是平台对服务器的限制，不是你的问题。',
  network: '稍后再试。',
  timeout: '稍后再试，或者换一个链接。',
  unsupported: '目前支持抖音、小红书、快手、YouTube、X、B站等，其它站点会交给 yt-dlp 试一试。',
  empty: '这条内容可能只有文字，或者需要登录才能看到媒体。',
  restricted: '这条是平台单独限制的，换一条链接通常就正常。不是你的网络问题。',
  parse: '平台可能刚改版，可以过两天再试。',
};

const form = $('#paste-form');
const urlBox = $('#url');
const parseBtn = $('#parse-btn');
const pasteBtn = $('#paste-btn');
const clearBtn = $('#clear-btn');
const hasLink = (text) => /https?:\/\//.test(text);

function hideOutputs() {
  $('#result').hidden = true;
  $('#converter').hidden = true;
}

// 解析超过两秒就轮播几句话：只有个转圈的话用户以为卡了会反复点或者直接走。
// 抖音图文要在服务端开浏览器渲染，3–5 秒，先说清楚为什么慢。
// 实况图的分享文案只写「# live实况」「#livephoto」，没有「图文作品」
const WAIT_LINES = [
  '原图在路上了，它腿短，再等等它',
  '正在翻箱底，找最清楚的那一版',
  '水印留在原地，只带走干净的',
  '顺着链接摸过去了，马上回来',
  '去平台那边排个队，前面就几个人',
  '正在跟平台那头握手，对方有点慢热',
  '快了快了，比泡面快',
];
const NOTE_LINE = '抖音图文得打开网页一张张捡图，三五秒，别走开';
const SLOW_LINES = [
  '平台今天有点磨蹭，我们再敲敲门',
  '比平时久不少，不过还在努力，没卡住',
];

function slowParseHints(text) {
  const isNote = /douyin\.com\/(note|share\/(note|slides))\//.test(text)
    || (/douyin\.com/.test(text) && /图文作品|实况|live/i.test(text));
  const lines = WAIT_LINES.slice().sort(() => Math.random() - 0.5);
  if (isNote) lines.unshift(NOTE_LINE);
  const start = Date.now();
  let i = 0;
  const show = () => {
    const slow = Date.now() - start > 15000;
    notice(slow ? SLOW_LINES[i++ % SLOW_LINES.length] : lines[i++ % lines.length], 'info');
  };
  let rotate;
  const first = setTimeout(() => { show(); rotate = setInterval(show, 4000); }, isNote ? 1000 : 2000);
  return () => { clearTimeout(first); clearInterval(rotate); };
}

// 从 GitHub issue 的「在线复现」点进来的，解析请求也带上来源，服务端不计入统计
const FROM = new URLSearchParams(location.search).get('src') === 'issue' ? '&src=issue' : '';

async function parse(text) {
  const r = await api('/api/parse?url=' + encodeURIComponent(text) + FROM);
  if (r.code !== 200) throw Object.assign(new Error(r.msg || '解析失败'), { reason: r.reason, feedback: r.feedback });
  return r.data;
}

function showParseError(err) {
  notice(err.message.replace(/[。.]$/, '') + '。' + (REASON_TIPS[err.reason] || ''));
  if (err.feedback) $('#notice').append(feedbackBox(err.feedback));
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = urlBox.value.trim();
  if (!text) {
    urlBox.focus();
    return;
  }
  parseBtn.disabled = true;
  parseBtn.replaceChildren(el('span', { class: 'spin' }));
  notice('');
  // 上一条的结果和转换器先收起来：新链接解析要几秒，期间旧结果的按钮还能点，容易点到上一条的「转 GIF」
  hideOutputs();
  const stopHints = slowParseHints(text);
  try {
    const data = await parse(text);
    useSignatures(data);
    notice('');
    renderResult(data);
  } catch (err) {
    showParseError(err);
  } finally {
    stopHints();
    parseBtn.disabled = false;
    parseBtn.textContent = '解析';
  }
});

// ---------- 输入框：有内容时显示 ×，为空时显示「粘贴」；粘进链接直接解析

function syncTools() {
  const has = urlBox.value.trim().length > 0;
  clearBtn.hidden = !has;
  pasteBtn.hidden = has;
}

urlBox.addEventListener('input', syncTools);
urlBox.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});
urlBox.addEventListener('paste', () => setTimeout(() => { if (hasLink(urlBox.value)) form.requestSubmit(); }, 30));

clearBtn.addEventListener('click', () => {
  urlBox.value = '';
  syncTools();
  notice('');
  hideOutputs();
  urlBox.focus();
});

pasteBtn.addEventListener('click', async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) {
      notice('剪贴板是空的。先去 App 里复制分享链接。', 'info');
      return;
    }
    urlBox.value = text;
    syncTools();
    if (hasLink(text)) form.requestSubmit();
    else urlBox.focus();
  } catch (_) {
    // 浏览器不给读剪贴板（非 HTTPS、或用户拒绝）：退回手动粘贴
    urlBox.focus();
    notice('浏览器没有允许读取剪贴板，请在输入框里长按或 Ctrl+V 粘贴。', 'info');
  }
});
if (!navigator.clipboard || !navigator.clipboard.readText) pasteBtn.hidden = true;
syncTools();

// ---------- 本地视频

$('#file-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  notice('正在上传 ' + file.name + '…', 'info');
  const body = new FormData();
  body.append('file', file);
  try {
    const r = await api('/api/upload', { method: 'POST', body });
    notice('');
    openConverter({ ready: r.source }, 'gif');
  } catch (err) {
    notice('上传失败：' + err.message);
  }
  e.target.value = '';
});

// ---------- 从别处带进来的：?url= 直接解析；其它页面导航栏的「本地视频」跳过来是 #upload

const initial = new URLSearchParams(location.search).get('url');
if (initial) {
  urlBox.value = initial;
  syncTools();
  form.requestSubmit();
} else if (location.hash === '#upload') {
  // 选文件的对话框只能由用户点击打开，这里给一个就在眼前的按钮
  notice('转 GIF 或实况照片：', 'info');
  $('#notice').append(el('label', { class: 'btn sm dark', for: 'file-input' }, '选择本地视频'));
}
