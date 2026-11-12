// 本地解包的文件直接使用签名引用，不把服务器路径或平台包路径交给浏览器。
import { postJSON, watchJob } from './api.js';
import { closeConverter, openConverter } from './converter.js';
import { $, el, fmtSize, notice } from './dom.js';
import { serverMessage, t } from './i18n.js';
import { stepTabs } from './steps.js';

let selection = 0;

function assetUrl(data, item, download = false) {
  return '/api/wallpaper/' + data.wallpaper_upload + '/' + item.id + '?sig=' + encodeURIComponent(item.sig)
    + (download ? '&download=1' : '');
}

async function convertAsset(data, item, format, isCurrent) {
  notice(t('正在准备壁纸素材'), 'info');
  try {
    const response = await postJSON('/api/wallpaper/prepare', {
      upload_id: data.wallpaper_upload, asset_id: String(item.id), sig: item.sig,
    });
    const source = response.ready ? response.source : (await watchJob(response.job, job => {
      if (isCurrent()) notice(serverMessage(job.message), 'info');
    })).extra;
    if (isCurrent()) {
      notice('');
      openConverter({ ready: source }, format);
    }
  } catch (error) {
    if (isCurrent()) notice(error.message);
  }
}

function conversionTabs(data, item, isCurrent) {
  return stepTabs([['gif', 'GIF'], ['livephoto', t('实况照片')]].map(([format, label]) => ({
    key: format, label, controls: 'converter',
    open() {
      const current = ++selection;
      convertAsset(data, item, format, () => isCurrent() && current === selection);
      return () => {
        if (current !== selection) return;
        selection += 1;
        closeConverter();
        notice('');
      };
    },
  }))).element;
}

function assetTile(data, item, isCurrent) {
  const url = assetUrl(data, item);
  const video = item.kind === 'video';
  const preview = video
    ? el('video', { src: url, controls: true, playsinline: true, preload: 'none' })
    : el('img', { src: url, alt: item.name, loading: 'lazy' });
  const actions = el('div', { class: 'group' }, el('a', {
    class: 'btn sm dark', href: assetUrl(data, item, true), download: item.name,
  }, t(video ? '下载视频' : '下载原图')));
  const conversions = video || /\.(gif|webp)$/i.test(item.name) ? conversionTabs(data, item, isCurrent) : null;
  return el('div', { class: 'card asset-tile asset-item' },
    el('h3', {}, item.name), preview, el('p', { class: 'fine' }, fmtSize(item.size)), actions,
    conversions ? el('div', { class: 'group' }, conversions) : null);
}

export async function showWallpaper(response, isCurrent) {
  const data = (await watchJob(response.job, job => {
    if (isCurrent()) notice(serverMessage(job.message), 'info');
  })).extra;
  if (!isCurrent()) return;
  notice('');
  const all = assetUrl(data, { id: 'all', sig: data.sig }, true);
  const content = el('div', { class: 'res' }, el('div', { class: 'res-head' },
    el('span', { class: 'tag' }, 'Wallpaper Engine')), el('h2', {}, data.title),
  el('p', { class: 'hint' }, t('提取包内图片和视频，不包含场景渲染效果。')),
  el('div', { class: 'group' }, el('a', { class: 'btn dark', href: all, download: data.title + '.zip' }, t('下载全部素材 ZIP'))),
  el('div', { class: 'asset-gallery' }, ...data.assets.map(item => assetTile(data, item, isCurrent))));
  if (data.skipped.length) {
    content.append(el('details', {}, el('summary', {}, t('有 {count} 个素材暂不支持', { count: data.skipped.length })),
      ...data.skipped.map(item => el('p', { class: 'fine' }, item.name + ' · ' + serverMessage(item.reason)))));
  }
  $('#result').replaceChildren(content);
  $('#result').hidden = false;
  $('#result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
