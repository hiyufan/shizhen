// 动态素材与壁纸：保留图片实际格式，动态图片按需进入现有转换器。
import { proxy, proxyImg, sigOf } from './api.js';
import { openConverter } from './converter.js';
import { el } from './dom.js';
import { t } from './i18n.js';
import { imageButton, PHOTOS } from './save.js';

export function assetImages(data, title) {
  const tiles = data.images.filter(image => image.url).map((image, index) => {
    const name = title + '_' + (index + 1);
    const url = proxy(image.url, name, true);
    const save = imageButton(t('下载原图'), url, name, false);
    save.className = 'btn sm dark';
    const actions = [save];
    if (PHOTOS) actions.unshift(imageButton(t('存到相册'), url, name));
    if (data.source === 'steam' && !data.video_url) {
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
