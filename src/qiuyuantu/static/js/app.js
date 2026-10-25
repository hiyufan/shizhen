// 首页工具的入口：粘贴链接 → 解析 → 展示结果；本地视频上传后直接进转换器

import { isEnglish, serverMessage, t } from './i18n.js';

import { api, parseWithProgress, useSignatures } from './api.js';
import { openConverter } from './converter.js';
import { $, el, notice } from './dom.js';
import { parseHints } from './parse-progress.js';
import { renderResult } from './result.js';
import { askForStarAfterDownload, feedbackBox } from './ui.js';

const REASON_TIPS = {
  deleted: t('确认一下原内容还在，或者重新复制一次分享链接（小红书的链接会过期）。'),
  login: t('站长可以在服务器上配置 cookies 解决；换个平台的链接不受影响。'),
  blocked: t('过几分钟再试。这是平台对服务器的限制，不是你的问题。'),
  network: t('稍后再试。'),
  timeout: t('稍后再试，或者换一个链接。'),
  unsupported: t('目前支持抖音、小红书、快手、YouTube、X、B站等，其它站点会交给 yt-dlp 试一试。'),
  empty: t('这条内容可能只有文字，或者需要登录才能看到媒体。'),
  copyright: t('这类内容整集体积大，没登录能拿到的画质也低。UP 主自己投稿的视频不受影响。'),
  restricted: t('这条是平台单独限制的，换一条链接通常就正常。不是你的网络问题。'),
  parse: t('平台可能刚改版，可以过两天再试。'),
  too_large: t('YouTube 把服务器拦下时只能走备用线路，太长的视频暂时搬不了，换一条短些的试试。'),
};

const form = $('#paste-form');
const urlBox = $('#url');
const parseBtn = $('#parse-btn');
const pasteBtn = $('#paste-btn');
const clearBtn = $('#clear-btn');
const hasLink = (text) => /https?:\/\//.test(text);
let activeParse = null;

askForStarAfterDownload($('#result'), $('#converter'));

function hideOutputs() {
  $('#result').hidden = true;
  $('#converter').hidden = true;
}

// 从 GitHub issue 的「在线复现」点进来的，解析请求也带上来源，服务端不计入统计
const FROM = new URLSearchParams(location.search).get('src') === 'issue' ? '&src=issue' : '';

async function parse(text, hints, signal) {
  const r = await parseWithProgress('/api/parse/stream?url=' + encodeURIComponent(text) + FROM, hints.update, signal);
  if (r.code !== 200) throw Object.assign(new Error(serverMessage(r.msg || t('解析失败'))), { reason: r.reason, feedback: r.feedback });
  return r.data;
}

function parseBusy(busy) {
  parseBtn.disabled = busy;
  parseBtn.setAttribute('aria-busy', String(busy));
  parseBtn.setAttribute('aria-label', busy ? t('正在解析') : t('解析'));
  parseBtn.replaceChildren(busy ? el('span', { class: 'spin' }) : t('解析'));
}

function cancelParse() {
  if (!activeParse) return;
  activeParse.controller.abort();
  activeParse.hints.stop();
  activeParse = null;
}

function showParseError(err) {
  notice(err.message.replace(/[。.]$/, '') + (isEnglish ? '. ' : '。') + (REASON_TIPS[err.reason] || ''));
  // 是不是整个平台出问题了：状态页上有每小时的自检结果
  $('#notice').append(' ', el('a', { href: '/status', target: '_blank' }, t('看各平台现在的解析状态')));
  if (err.feedback) $('#notice').append(feedbackBox(err.feedback));
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = urlBox.value.trim();
  if (!text) {
    urlBox.focus();
    return;
  }
  cancelParse();
  const controller = new AbortController();
  parseBusy(true);
  notice('');
  // 上一条的结果和转换器先收起来：新链接解析要几秒，期间旧结果的按钮还能点，容易点到上一条的「转 GIF」
  hideOutputs();
  const hints = parseHints();
  const request = { controller, hints };
  activeParse = request;
  try {
    const data = await parse(text, hints, controller.signal);
    if (activeParse !== request) return;
    hints.stop();
    useSignatures(data);
    notice('');
    renderResult(data);
  } catch (err) {
    if (!controller.signal.aborted && activeParse === request) {
      hints.stop();
      showParseError(err);
    }
  } finally {
    hints.stop();
    if (activeParse === request) {
      activeParse = null;
      parseBusy(false);
    }
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
  cancelParse();
  parseBusy(false);
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
      notice(t('剪贴板是空的。先去 App 里复制分享链接。'), 'info');
      return;
    }
    urlBox.value = text;
    syncTools();
    if (hasLink(text)) form.requestSubmit();
    else urlBox.focus();
  } catch (_) {
    // 浏览器不给读剪贴板（非 HTTPS、或用户拒绝）：退回手动粘贴
    urlBox.focus();
    notice(t('浏览器没有允许读取剪贴板，请在输入框里长按或 Ctrl+V 粘贴。'), 'info');
  }
});
if (!navigator.clipboard || !navigator.clipboard.readText) pasteBtn.hidden = true;
syncTools();

// ---------- 本地视频

$('#file-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  notice(t('正在上传 ') + file.name + '…', 'info');
  const body = new FormData();
  body.append('file', file);
  try {
    const r = await api('/api/upload', { method: 'POST', body });
    notice('');
    openConverter({ ready: r.source }, 'gif');
  } catch (err) {
    notice(t('上传失败：') + err.message);
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
  notice(t('转 GIF 或实况照片：'), 'info');
  $('#notice').append(el('label', { class: 'btn sm dark', for: 'file-input' }, t('选择本地视频')));
}
