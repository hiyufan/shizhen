// 直播间的各路流：只把地址交给用户自己的播放器，不经站内代理、下载或转换。
import { proxyImg } from './api.js';
import { el } from './dom.js';
import { t } from './i18n.js';

const SOURCE_HINT = '原始推流是主播端上传、平台没有转码的一路，多为 H.265；播放器打不开时换用上面的清晰度。';

function streamSpec(stream) {
  return [
    stream.width && stream.height ? stream.width + '×' + stream.height : '',
    stream.codec,
    stream.bitrate ? (stream.bitrate / 1e6).toFixed(1) + ' Mbps' : '',
    stream.fps ? Math.round(stream.fps) + ' fps' : '',
  ].filter(Boolean).join(' · ');
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 非安全上下文没有剪贴板接口，用户拒绝授权时会抛错：两种都改成手动复制
    return false;
  }
}

/** 复制失败时把地址放进只读输入框并选中，手机上长按也能复制 */
function copier(status, manual) {
  return async (url, name, format) => {
    if (await copyText(url)) {
      manual.hidden = true;
      status.textContent = t('已复制{name}的 {format} 地址', { name, format });
      return;
    }
    manual.value = url;
    manual.hidden = false;
    manual.focus();
    manual.select();
    status.textContent = t('浏览器没有允许自动复制，请手动复制下面的地址。');
  };
}

function copyButton(copy, url, name, format) {
  if (!url) return null;
  return el('button', {
    class: 'btn sm', type: 'button', 'aria-label': t('复制{name}的 {format} 地址', { name, format }),
    onclick: () => copy(url, name, format),
  }, t('复制 {format}', { format }));
}

function streamRow(stream, copy) {
  const name = t(stream.label);
  const spec = streamSpec(stream);
  return el('li', { class: 'stream-row' },
    el('div', { class: 'stream-name' }, el('b', {}, name), spec ? el('span', { class: 'mono' }, spec) : null),
    el('div', { class: 'group' },
      copyButton(copy, stream.flv_url, name, 'FLV'), copyButton(copy, stream.hls_url, name, 'HLS')));
}

export function streamSection(data) {
  const status = el('p', { class: 'hint found', role: 'status' });
  const manual = el('input', { class: 'stream-url mono', type: 'text', readonly: true, hidden: true, 'aria-label': t('直播流地址') });
  const copy = copier(status, manual);
  const side = el('div', { class: 'side' },
    el('p', { class: 'label' }, t('直播流地址')),
    el('ul', { class: 'stream-list' }, ...data.streams.map((stream) => streamRow(stream, copy))),
    status,
    manual,
    el('p', { class: 'hint' }, t('把地址粘贴到 VLC、PotPlayer、IINA 或 OBS 的「打开网络串流」里观看或录制。流里只有直播画面和声音，不带弹幕、礼物特效和平台水印。')),
    data.streams.some((stream) => stream.kind === 'source') ? el('p', { class: 'hint' }, t(SOURCE_HINT)) : null,
    el('p', { class: 'hint' }, t('地址只在这场直播期间有效，下播后需要重新解析。')));
  if (!data.cover_url) return side;
  return el('div', { class: 'res-body stream-result' },
    el('div', { class: 'media portrait' }, proxyImg(data.cover_url, { alt: '' })), side);
}
