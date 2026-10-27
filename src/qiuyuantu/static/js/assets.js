// 动态素材与壁纸：保留图片实际格式，动态图片按需进入现有转换器。
import { mediaDownload, mediaVideo, proxy, proxyImg, sigOf, sourcesOf, trackDownload } from './api.js';
import { openConverter } from './converter.js';
import { el } from './dom.js';
import { t } from './i18n.js';
import { imageButton, oneFile, PHOTOS, photosButton } from './save.js';

export function assetImages(data, title) {
  const tiles = data.images.filter(image => image.url).map((image, index) => {
    const name = title + '_' + (index + 1);
    const url = proxy(image.url, name, true);
    const save = imageButton(t('下载原图'), url, name, false);
    save.className = 'btn sm dark';
    const actions = [save];
    if (PHOTOS) actions.unshift(imageButton(t('存到相册'), url, name));
    if ((data.source === 'steam' && !data.video_url) || (data.source === 'miyoushe' && /\.gif(?:[?#]|$)/i.test(image.url))) {
      const request = { url: image.url, title: name, sig: sigOf(image.url), image: true };
      actions.push(el('button', { class: 'btn sm', type: 'button', onclick: () => openConverter(request, 'gif') }, t('转 GIF')));
      actions.push(el('button', { class: 'btn sm quiet', type: 'button', onclick: () => openConverter(request, 'livephoto') }, t('做成实况照片')));
    }
    return el('div', { class: 'card asset-tile' },
      proxyImg(image.url, { alt: title + ' ' + (index + 1), loading: 'lazy' }),
      el('div', { class: 'group' }, ...actions));
  });
  return el('div', { class: 'asset-gallery' }, ...tiles);
}

export function independentAssets(data, title) {
  const tiles = data.assets.filter(asset => asset.url).map((asset, index) => {
    const name = title + '_' + (index + 1);
    const isVideo = asset.kind === 'video';
    const preview = assetPreview(asset, title);
    const actions = assetDownloads(asset, name);
    if (isVideo || asset.kind === 'animation') {
      const request = { url: asset.url, title: name, sig: sigOf(asset.url), image: !isVideo };
      actions.push(el('button', { class: 'btn sm', type: 'button', onclick: () => openConverter(request, 'gif') }, t('转 GIF')));
      actions.push(el('button', { class: 'btn sm quiet', type: 'button', onclick: () => openConverter(request, 'livephoto') }, t('做成实况照片')));
    }
    const label = data.source === 'telegram' ? t('贴纸 {number}', { number: index + 1 })
      : (asset.label || title).split(' · ').map(part => t(part)).join(' · ');
    return el('div', { class: 'card asset-tile asset-item' },
      el('h3', {}, label), preview, el('div', { class: 'group' }, ...actions));
  });
  return el('div', { class: 'asset-gallery' }, ...tiles);
}

function assetPreview(asset, title) {
  if (asset.kind === 'video') {
    return mediaVideo(asset.url, { controls: true, playsinline: true, preload: 'none', poster: asset.cover_url ? proxy(asset.cover_url) : null });
  }
  if (asset.ext === 'json' && !asset.cover_url) {
    return el('div', { class: 'asset-placeholder' },
      el('strong', {}, 'LOTTIE'), el('p', {}, t('转换后预览动画')));
  }
  return proxyImg(asset.cover_url || asset.url, { alt: asset.label || title, loading: 'lazy' });
}

function assetDownloads(asset, name) {
  if (['tgs', 'json', 'zip'].includes(asset.ext)) {
    const filename = name + '.' + asset.ext;
    return [el('a', {
      class: 'btn sm dark', href: mediaDownload(asset.url, filename), download: filename,
      onclick: () => trackDownload(asset.url),
    }, t({ json: '下载原始 JSON', zip: '下载帧包 ZIP', tgs: '下载原始 TGS' }[asset.ext]))];
  }
  if (asset.kind !== 'video') {
    const url = proxy(asset.url, name, true);
    const save = imageButton(t('下载原图'), url, name, false);
    save.className = 'btn sm dark';
    return PHOTOS ? [imageButton(t('存到相册'), url, name), save] : [save];
  }
  const ext = /\.webm(?:[?#]|$)/i.test(asset.url) ? 'webm' : 'mp4';
  const filename = name + '.' + ext;
  const save = el('a', {
    class: 'btn sm dark', href: mediaDownload(asset.url, filename), download: filename,
    onclick: () => trackDownload(asset.url),
  }, t('下载视频'));
  return PHOTOS && ext === 'mp4'
    ? [photosButton(t('存到相册'), oneFile(() => sourcesOf(asset.url), name, 'video/mp4')), save]
    : [save];
}
