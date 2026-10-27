// 作品目录与纯文本结果；所有正文以文本节点呈现。
import { proxyImg } from './api.js';
import { el, safeName } from './dom.js';
import { t } from './i18n.js';
import { saveFile } from './save.js';

function parseLink(url) {
  document.dispatchEvent(new window.CustomEvent('qyt:parse-link', { detail: url }));
}

function textDownload(title, content, ext, label) {
  return el('button', { class: 'btn sm dark', type: 'button', onclick: () => {
    saveFile(new File([content], (safeName(title) || 'Pixiv') + '.' + ext, { type: ext === 'json' ? 'application/json' : 'text/plain;charset=utf-8' }));
  } }, label);
}

function entryTile(item) {
  const action = item.kind === 'external'
    ? el('a', { class: 'btn sm quiet', href: item.url, target: '_blank', rel: 'noopener noreferrer' }, t('打开原页面'))
    : el('button', { class: 'btn sm', type: 'button', disabled: !item.available, onclick: () => parseLink(item.url) }, t(item.available ? '解析这篇作品' : '内容访问受限'));
  return el('div', { class: 'card asset-tile' },
    item.cover_url ? proxyImg(item.cover_url, { alt: '', loading: 'lazy' }) : null,
    el('h3', {}, item.title || 'Pixiv'), action);
}

export function contentSections(data, title) {
  const parts = (data.documents || []).map((item) => el('div', { class: 'card text-document' },
    el('h3', {}, item.title || title),
    textDownload(item.title || title, item.content, 'txt', t('下载 TXT')),
    el('details', {}, el('summary', {}, t('预览文字')), el('pre', {}, item.content))));
  if (data.animation_frames?.length) {
    parts.push(el('div', { class: 'group' },
      textDownload(title + '_frames', JSON.stringify({ frames: data.animation_frames }, null, 2), 'json', t('下载时序 JSON'))));
  }
  if (data.entries?.length) parts.push(el('div', { class: 'asset-gallery' }, ...data.entries.map(entryTile)));
  parts.push(...directoryStatus(data));
  if (data.next_page_url) {
    parts.push(el('div', { class: 'group' }, el('button', {
      class: 'btn', type: 'button', onclick: () => parseLink(data.next_page_url),
    }, t('查看下一页'))));
  }
  return parts;
}

function directoryStatus(data) {
  if (data.content_kind !== 'directory') return [];
  const parts = [];
  if (!data.entries?.length && !data.documents?.length) parts.push(el('p', { class: 'notice' }, t('这一页没有可匿名读取的作品。')));
  if (data.entry_total) parts.push(el('p', { class: 'fine' }, t('目录共 {count} 项，按页选择作品。', { count: data.entry_total })));
  return parts;
}
