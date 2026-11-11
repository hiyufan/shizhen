// 解析结果：视频（预览、下载、其他清晰度、转换入口）、图集（逐张保存、实况打包）

import { serverMessage, t } from './i18n.js';

import {
  directOf, mediaDownload, mediaVideo, postJSON, proxy, proxyImg, sigOf, sourcesOf, trackDownload, watchJob,
} from './api.js';
import { closeConverter, openConverter } from './converter.js';
import { assetImages, independentAssets } from './assets.js';
import { contentSections } from './content.js';
import { imageStep } from './snapshot.js';
import { stepTabs } from './steps.js';
import { streamSection } from './stream.js';
import { $, el, fmtClock, fmtSize, notice, safeName, sleep } from './dom.js';
import { ANDROID, IOS, PHOTOS, fetchDirect, fetchFiles, imageButton, iphoneLiveHint, oneFile, photosButton, saveFile } from './save.js';
import { jobCard } from './ui.js';
import { rememberParsedLink } from './report-link.js';

const PLATFORMS = {
  douyin: t('抖音'), redbook: t('小红书'), kuaishou: t('快手'), youtube: 'YouTube', twitter: 'X', bilibili: t('B站'), weibo: t('微博'),
  tiktok: 'TikTok', instagram: 'Instagram', threads: 'Threads', xigua: t('西瓜视频'), pipixia: t('皮皮虾'), acfun: 'AcFun', weishi: t('微视'),
  lvzhou: t('绿洲'), zuiyou: t('最右'), quanmin: t('度小视'), lishipin: t('梨视频'), pipigaoxiao: t('皮皮搞笑'), huya: t('虎牙'),
  doupai: t('逗拍'), meipai: t('美拍'), quanminkge: t('全民K歌'), sixroom: t('六间房'), xinpianchang: t('新片场'), haokan: t('好看视频'),
  qqvideo: t('腾讯视频'), sohu: t('搜狐视频'), cctv: t('央视网'), vimeo: 'Vimeo', facebook: 'Facebook', twitch: 'Twitch',
  reddit: 'Reddit', pinterest: 'Pinterest', ytdlp: 'yt-dlp',
  qqmusic: t('QQ 音乐'), netease: t('网易云音乐'), goofish: t('闲鱼'),
  steam: 'Steam', hoyoplay: 'HoYoPlay', applemusic: 'Apple Music', telegram: 'Telegram', discord: 'Discord',
  wutheringwaves: t('鸣潮'), miyoushe: t('米游社'), 'bilibili-assets': t('B站装扮与收藏集'),
  'wechat-channels': t('视频号'), 'wechat-mp': t('公众号'),
  '7tv': '7TV', bttv: 'BetterTTV', ffz: 'FrankerFaceZ', line: 'LINE', signal: 'Signal',
  'noto-emoji': 'Noto Emoji', animation: t('特殊动画'),
  pixiv: 'Pixiv', 'pixiv-sketch': 'Pixiv Sketch',
};

/** 视频 / 音频的下载按钮：有国内边缘地址就从边缘下（国内直达），探通了直连就直连，否则服务器转发 */
const download = (url, filename, label, cls) =>
  el('a', { class: cls, href: mediaDownload(url, filename), download: filename, onclick: (e) => saveDirect(e, url, filename) }, label);

/** 直连可用时在页面里拉（按钮上显示进度）；拉不下来就照原来的链接走服务器转发 */
async function saveDirect(e, url, filename) {
  trackDownload(url);
  const src = directOf(url);
  const a = e.currentTarget;
  if (!src) return; // 链接本身就是边缘 / 服务器转发
  e.preventDefault();
  if (a.dataset.busy) return;
  a.dataset.busy = '1';
  const label = a.textContent;
  const file = await fetchDirect(src, filename.replace(/\.(mp4|webm)$/i, ''), (got, total) => {
    a.textContent = t('下载中 ') + (total ? Math.round(got * 100 / total) + '%' : fmtSize(got));
  }, /\.webm$/i.test(filename) ? 'video/webm' : 'video/mp4');
  a.textContent = label;
  delete a.dataset.busy;
  if (file) saveFile(file);
  else location.href = a.href;
}

// 当前结果的下载区刷新函数；只有视频结果会登记，换结果时清空。
let refreshDownloads = null;

/** 后台核对到更高画质后，就地更新同一份结果数据与下载按钮；转换按钮在点击时读取最新档位。 */
export function updateResult(next) {
  if (refreshDownloads) refreshDownloads(next);
}

export function renderResult(d) {
  refreshDownloads = null;
  const title = d.title || t('未命名');
  // notice：平台不给的那部分是什么（比如快手实况只有照片），说在前面免得用户以为是网站漏了
  const sourceNotice = d.notice ? el('p', { class: 'fine' }, serverMessage(d.notice, '平台仅提供部分媒体，请检查结果后下载。')) : null;
  const box = $('#result');
  const res = el('div', { class: 'res' }, resultHead(d), el('h2', {}, title), sourceNotice, ...resultContent(d, safeName(title) || 'video'),
    reportLink(box.dataset.feedback));
  box.replaceChildren(res);
  box.hidden = false;
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// 意见反馈开着时（#result 带 data-feedback）在结果末尾放一行小字入口；点击时才记下刚才解析的链接
function reportLink(href) {
  if (!href) return null;
  const link = el('a', { href: href + '?from=result' }, t('结果不对？告诉我们'));
  link.addEventListener('click', () => rememberParsedLink($('#url')?.value));
  return el('p', { class: 'res-report' }, link);
}

function resultContent(data, baseName) {
  if (data.streams?.length) return [streamSection(data)];
  if (['qqmusic', 'netease', 'applemusic'].includes(data.source) && data.images?.[0]?.url) return [coverSection(data.images[0], baseName, data)];
  return resultBody(data, baseName);
}

function resultHead(d) {
  return el('div', { class: 'res-head' },
    el('span', { class: 'tag' }, PLATFORMS[d.source] || d.source || t('未知来源')),
    d.author && d.author.name ? el('span', {}, d.author.name) : null,
    d.content_kind === 'live' ? el('span', {}, t('直播中')) : null,
    d.duration ? el('span', {}, fmtClock(d.duration)) : null);
}

/** 有视频放视频区；只有音频（少见）放一个下载按钮；有图放图集。什么都没有就说一声 */
function resultBody(d, baseName) {
  const hasVideo = !!d.video_url || !!(d.formats && d.formats.length);
  const images = (d.images || []).filter((i) => i.url);
  const parts = [];
  if (hasVideo) {
    parts.push(videoSection(d, baseName, el('div', { class: 'jobs' })));
  } else if (d.music_url && !images.length) {
    const audio = download(d.music_url, baseName + '.mp3', t('下载音频'), 'btn');
    parts.push(el('div', { class: 'side' }, el('div', { class: 'group' }, audio), el('div', { class: 'jobs' })));
  }
  parts.push(...resultImages(d, images, baseName, hasVideo));
  if (d.assets?.length) parts.push(independentAssets(d, baseName));
  parts.push(...contentSections(d, baseName));
  if (!parts.length) {
    parts.push(el('div', { class: 'notice' }, t('解析成功但没有拿到任何视频或图片，这条内容可能已被删除或需要登录。')));
  }
  return parts;
}

function resultImages(data, images, baseName, hasVideo) {
  if (!images.length) return [];
  return ['steam', 'hoyoplay', 'wutheringwaves', 'miyoushe', 'wechat-channels', 'pixiv', 'pixiv-sketch'].includes(data.source)
    ? [assetImages(data, baseName)] : imageSection(data, images, baseName, hasVideo);
}

function coverSection(image, baseName, data) {
  const name = baseName + '_cover';
  const jobsBox = el('div', { class: 'jobs' });
  return el('div', { class: 'res-body cover-result' },
    el('div', { class: 'cover-media' }, proxyImg(image.url, { alt: t('音乐封面') })),
    el('div', { class: 'side' },
      el('p', { class: 'label' }, t('音乐封面')),
      el('div', { class: 'group' }, ...coverActions(proxy(image.url, name, true), name),
        data.formats.length ? formatMenu(data, baseName, jobsBox) : null),
      el('p', { class: 'hint' }, t(coverHint(data))),
      data.source === 'applemusic' && !data.formats.length ? null : coverConversions(image.url, baseName, data), jobsBox));
}

// 网易云的动态封面是另一段视频（有才给档位）；歌单封面本身可能就是动图，按原格式保存就保留动效
function coverHint(data) {
  if (data.source === 'applemusic') return '静态封面与动态视频分开下载；动态封面以当前公开版本为准。';
  return data.formats.length ? '静态封面直接下载原图；动态封面是一段无声视频，可单独下载或继续转换。'
    : '按图片原始格式保存，动态封面保留动效。';
}

function coverConversions(url, title, data) {
  const request = data.formats.length ? sourceRequest(data, title) : { url, title, sig: sigOf(url), image: true };
  return el('div', {}, el('div', { class: 'rule' }),
    el('p', { class: 'label' }, t('动态封面继续做')),
    el('div', { class: 'group' },
      el('button', { class: 'btn', type: 'button', onclick: () => openConverter(request, 'gif') }, t('转 GIF')),
      el('button', { class: 'btn', type: 'button', onclick: () => openConverter(request, 'livephoto') }, t('做成实况照片'))),
    el('p', { class: 'hint' }, t('仅动态封面支持转换；静态封面可直接下载原图。')));
}

function coverActions(url, name) {
  const downloadCover = imageButton(t('下载封面'), url, name, false);
  if (!PHOTOS) return [downloadCover];
  downloadCover.className = 'btn quiet';
  return [imageButton(t('存到相册'), url, name), downloadCover];
}

// ---------- 视频
// 结果区按步骤走：主按钮（下载 / iPhone 存相册）直接给出，大多数人到这里就够了；
// 清晰度、GIF、实况、图片（截图与封面）收成一排标签，一次只展开一个（steps.js），点得再多结果区也不会越来越长。
// GIF / 实况的编辑区要整宽，仍在结果下方另开（#converter），同样由标签开关。

function videoSection(d, baseName, jobsBox) {
  // crossorigin：「截图」要把这一帧画到 canvas 上，跨域的边缘地址需带 CORS 请求才不污染画布
  const player = d.video_url ? mediaVideo(d.video_url, {
    controls: true, playsinline: true, preload: 'metadata', crossorigin: 'anonymous', poster: d.cover_url ? proxy(d.cover_url) : null,
  }) : null;
  const media = player
    ? el('div', { class: 'media' + (isPortrait(d) ? ' portrait' : '') }, player)
    : el('div', { class: 'media portrait' }, d.cover_url ? proxyImg(d.cover_url, { alt: '' }) : null);
  let primary = primaryAction(d, baseName, jobsBox);
  const status = el('p', { class: 'hint found', role: 'status' });
  const steps = stepTabs(stepItems(d, baseName, jobsBox, player));
  refreshDownloads = (next) => {
    const before = defaultDownload(d)?.url;
    Object.assign(d, next);
    const fresh = primaryAction(d, baseName, jobsBox);
    primary.replaceWith(fresh);
    primary = fresh;
    steps.refresh('formats');
    const best = defaultDownload(d);
    if (best?.url !== before && best?.label) status.textContent = t('已找到更高画质：{label}', { label: t(best.label) });
  };
  // 没有能一键下载的档位（都要服务端合并、又没有直链预览）时，直接摊开清晰度让人选
  if (!defaultDownload(d)) steps.open('formats');
  return el('div', { class: 'res-body' }, media, el('div', { class: 'side' }, primary, status, steps.element, jobsBox));
}

/** 「下一步」标签：有什么给什么，没有的不显示 */
function stepItems(d, baseName, jobsBox, player) {
  const items = [];
  const downloads = downloadStep(d, baseName, jobsBox);
  if (downloads) items.push(downloads);
  items.push(converterStep(d, baseName, 'gif', 'GIF', []));
  // 安卓相册认的是动态照片，其他设备默认实况照片；两种可在编辑区里互换
  const live = ANDROID ? ['motionphoto', t('动态照片')] : ['livephoto', t('实况照片')];
  items.push(converterStep(d, baseName, live[0], live[1], ['livephoto', 'motionphoto']));
  const image = imageStep(d, baseName, player && videoExtension(d) !== 'm3u8' ? player : null);
  if (image) items.push(image);
  return items;
}

const converterStep = (d, baseName, format, label, switches) => ({
  key: format, label, controls: 'converter',
  open: () => {
    openConverter(sourceRequest(d, baseName), format, { switches });
    return closeConverter;
  },
});

/** 清晰度 / 音频：列出全部档位和单独的音频；面板每次打开都按当前数据重画 */
function downloadStep(d, baseName, jobsBox) {
  const hasFormats = !!d.formats?.length;
  if (!hasFormats && !d.music_url) return null;
  return {
    key: 'formats', label: hasFormats ? formatsLabel(d) : t('音频'),
    open: (body) => body.append(formatList(d, baseName, jobsBox)),
  };
}

function formatsLabel(d) {
  if (d.video_url) return t('清晰度');
  return t(['applemusic', 'netease'].includes(d.source) ? '动态封面' : '下载视频');
}

const isPortrait = (d) => (d.width && d.height ? d.height > d.width : !['steam', 'hoyoplay', 'wutheringwaves'].includes(d.source));

const videoExtension = (data) => /\.webm(?:[?#]|$)/.test(data.video_url) ? 'webm' : (data.video_ext || 'mp4');

const defaultDownload = (data) => (PHOTOS && albumDownload(data)) || data.download_format || previewDownload(data);

// 照片 App 稳妥能收的是 H.264 / HEVC。YouTube 的 2K、4K 只有 AV1 / VP9，多数 iPhone 存不进相册；
// 编码不明的档位照旧当作能存，避免把其他平台的正常档位误挡掉
const ALBUM_MENU_HINT = '标了 AV1 / VP9 的档位（多是 2K、4K）多数 iPhone 存不进相册，下载后在「文件」App 里看；要存相册选没有标记的档位。';
const ALBUM_JOB_HINT = '这一档是 {codec} 编码，多数 iPhone 存不进相册，点「保存」会存到「文件」App；要存相册请换没有标记的档位。';
const ALBUM_UNSAFE = /^(av01|av1|vp0?8|vp0?9)/i;
const albumSafe = (codec) => !ALBUM_UNSAFE.test(codec || '');
const codecMark = (codec) => (/^av0?1/i.test(codec) ? 'AV1' : /^vp0?8/i.test(codec) ? 'VP8' : 'VP9');
const tierRank = (f) => [Math.min(f.width || f.height, f.height || f.width), f.fps || 0, f.bitrate || 0];
const byRank = (a, b) => tierRank(b).reduce((diff, value, i) => diff || value - tierRank(a)[i], 0);

/** iPhone 的主下载：最高档存不进相册时，换成存得进的最高一档；更高的仍在「其他清晰度」里 */
function albumDownload(data) {
  const best = data.download_format;
  if (!best || albumSafe(best.codec)) return null;
  const safe = data.formats.filter((f) => f.height && ['mp4', 'mov'].includes(f.ext) && albumSafe(f.codec));
  if (safe.length) return safe.sort(byRank)[0];
  return albumSafe(data.video_codec) ? previewDownload(data) : null;
}

function previewDownload(data) {
  if (!data.video_url || videoExtension(data) === 'm3u8') return null;
  const resolution = data.width && data.height ? Math.min(data.width, data.height) : data.height;
  return { url: data.video_url, ext: videoExtension(data), label: resolution ? resolution + 'p' : '' };
}

/** 主按钮只有一个：iPhone 上能存相册时是「存到相册」，其他情况是最高档的「下载视频」 */
function primaryAction(d, baseName, jobsBox) {
  const best = defaultDownload(d);
  if (!best) return el('div', { class: 'group', hidden: true });
  if (PHOTOS && best.url && best.ext === 'mp4') {
    return el('div', { class: 'group' }, photosButton(t('存到相册'), oneFile(() => sourcesOf(best.url), baseName, 'video/mp4')));
  }
  return el('div', { class: 'group' }, primaryDownload(best, d, baseName, jobsBox));
}

function primaryDownload(format, data, baseName, jobsBox) {
  const label = t('下载视频') + (format.label ? ' ' + t(format.label) : '');
  if (format.url) return download(format.url, baseName + '.' + (format.ext || 'mp4'), label, 'btn dark');
  return el('button', { class: 'btn dark', type: 'button', onclick: async (event) => {
    const button = event.currentTarget;
    if (button.disabled) return;
    button.disabled = true;
    try { await startDownload(format, data, baseName, jobsBox); }
    finally { button.disabled = false; }
  } }, label);
}

function downloadFormats(data) {
  const formats = [...data.formats];
  const preview = previewDownload(data);
  if (preview && data.download_format?.url !== preview.url && !formats.some((f) => f.url === preview.url)) {
    formats.push(preview);
  }
  return formats;
}

/** 菜单里的一档：有直链的直接下载，要服务端合并的起一个下载任务。iPhone 上给存不进相册的档位标出编码 */
function formatItem(f, d, baseName, jobsBox, close) {
  const size = f.filesize ? fmtSize(f.filesize) : f.ext;
  const meta = [el('span', {}, t(f.label)), el('small', {}, PHOTOS && !albumSafe(f.codec) ? size + ' · ' + codecMark(f.codec) : size)];
  const filename = baseName + '_' + f.label.replace(/\s+/g, '') + '.' + (f.ext || 'mp4');
  return f.url
    ? el('a', { href: mediaDownload(f.url, filename), download: '', onclick: (e) => { close(); saveDirect(e, f.url, filename); } }, ...meta)
    : el('button', { type: 'button', onclick: () => { close(); startDownload(f, d, baseName, jobsBox); } }, ...meta);
}

function formatHints(formats) {
  const hints = [];
  if (PHOTOS && formats.some((f) => f.height && !albumSafe(f.codec))) hints.push(t(ALBUM_MENU_HINT));
  if (formats.some((f) => /H\.265|hevc|hvc1|hev1/i.test(f.codec || ''))) {
    hints.push(t('H.265 通常体积更小，播放兼容性因设备而异；画质取决于来源。'));
  }
  return hints.map((text) => el('p', { class: 'hint', style: 'padding:6px 10px 4px' }, text));
}

/** 清晰度面板里的列表：各档位、编码提示，最后是单独的音频 */
function formatList(d, baseName, jobsBox) {
  const list = el('div', { class: 'format-list' });
  if (d.formats?.length) {
    list.append(...downloadFormats(d).map((f) => formatItem(f, d, baseName, jobsBox, () => {})), ...formatHints(d.formats));
  }
  if (d.music_url) {
    const name = baseName + '.mp3';
    list.append(el('a', { href: mediaDownload(d.music_url, name), download: name, onclick: (e) => saveDirect(e, d.music_url, name) },
      el('span', {}, t('仅音频')), el('small', {}, 'MP3')));
  }
  return list;
}

/** 音乐动态封面的「下载 ▾」下拉菜单 */
function formatMenu(d, baseName, jobsBox) {
  const list = el('div', { class: 'menu-list', hidden: true });
  const close = () => { list.hidden = true; };
  for (const f of downloadFormats(d)) list.append(formatItem(f, d, baseName, jobsBox, close));
  list.append(...formatHints(d.formats));
  const toggle = el('button', { class: 'btn' + (d.video_url ? '' : ' dark'), type: 'button', onclick: () => { list.hidden = !list.hidden; } },
    d.video_url ? t('其他清晰度 ▾') : t(['applemusic', 'netease'].includes(d.source) ? '下载动态封面 ▾' : '下载视频 ▾'));
  return el('div', { class: 'menu' }, toggle, list);
}

// 点菜单外面任何地方都收起。整页挂一个就够，别每渲染一次结果都往 document 上加
document.addEventListener('click', (e) => {
  for (const menu of document.querySelectorAll('.menu')) {
    if (!menu.contains(e.target)) menu.querySelector('.menu-list').hidden = true;
  }
});

const directConversionSource = (d, title, url) => ({ url, headers: d.video_headers || {}, title, sig: sigOf(url) });

function pageConversionSource(d, title, formatSpec = '') {
  const page = d.page_url || d.share_url;
  return { page_url: page, format_spec: formatSpec, title, sig: sigOf(page) };
}

function legacyConversionSource(d, title) {
  // 旧响应没有 download_format；保留原有兼容选择，动态封面则指定其独立构图。
  const needsServer = d.formats.some((f) => !f.url);
  if (d.video_url && (!needsServer || d.height >= 720 || !d.page_url)) {
    return directConversionSource(d, title, d.video_url);
  }
  const format = d.formats.find((f) => f.format_spec);
  return pageConversionSource(d, title, format?.format_spec || '');
}

function conversionSource(d, title) {
  if (d.source === 'hoyoplay') return pageConversionSource(d, title);
  const best = d.download_format;
  if (best?.url) return directConversionSource(d, title, best.url);
  if (best?.format_spec) return pageConversionSource(d, title, best.format_spec);
  return legacyConversionSource(d, title);
}

/** 转换使用与主下载相同的高清源；兼容预览只在浏览器裁剪时播放，不作为转换输入。 */
function sourceRequest(d, title) {
  const request = conversionSource(d, title);
  if (d.video_url && videoExtension(d) !== 'm3u8') request.preview_url = proxy(d.video_url);
  return request;
}

// 做完就先往手机上拉的文件上限：整份放在浏览器内存里，再大的等用户自己点
const EAGER_MAX = 100 * 1024 * 1024;

// 照片 App 只认 mp4 / mov 里的 H.264 / HEVC；webm、音频、AV1 / VP9 还是走下载
function jobPhotosButton(f, done, file, baseName) {
  if (!PHOTOS || !/\.mp4$/i.test(done.filename || '') || !albumSafe(f.codec)) return null;
  const load = oneFile(file + '?inline=1', baseName + '_' + f.label.replace(/\s+/g, ''), 'video/mp4');
  return photosButton(t('存到相册'), load, 'btn sm dark', done.filesize > 0 && done.filesize <= EAGER_MAX);
}

async function startDownload(f, d, baseName, jobsBox) {
  const card = jobCard(jobsBox, t('下载 ') + t(f.label));
  const page = d.page_url || d.share_url;
  try {
    const job = await postJSON('/api/download', {
      page_url: page, format_spec: f.format_spec, title: baseName, ext: f.ext, sig: sigOf(page),
    });
    const done = await watchJob(job, card.update);
    const file = '/api/jobs/' + done.id + '/file';
    const photos = jobPhotosButton(f, done, file, baseName);
    card.done((photos ? t('下载 ') : t('保存 ')) + (done.filesize ? fmtSize(done.filesize) : ''), file, done.filename, photos);
    if (PHOTOS && !albumSafe(f.codec)) card.card.append(el('p', { class: 'hint' }, t(ALBUM_JOB_HINT, { codec: codecMark(f.codec) })));
  } catch (e) {
    card.fail(e.message);
  }
}

// ---------- 图集

function imageSection(d, images, baseName, hasVideo) {
  const lives = images.filter((i) => i.live_photo_url);
  const head = el('div', { class: 'sheet-title' },
    el('span', {}, t('{count} 张图片', { count: images.length }) + (lives.length ? t('，其中 {count} 张是实况', { count: lives.length }) : '')));
  const acts = headActions(d, images, baseName, hasVideo, lives.length > 0);
  if (acts.length) head.append(el('div', { class: 'head-acts' }, ...acts));
  const grid = el('div', { class: 'grid' }, ...images.map((img, i) => imageTile(img, i + 1, baseName)));
  return [head, ...(lives.length ? liveBlock(images, lives, baseName) : []), grid];
}

/** 图集标题行右边的小按钮：背景音乐（用得少，不占一整行）；没有实况时的「全部保存」 */
function headActions(d, images, baseName, hasVideo, hasLives) {
  const acts = [];
  if (d.music_url && !hasVideo) acts.push(download(d.music_url, baseName + '.mp3', t('下载背景音乐'), 'btn sm quiet'));
  if (hasLives) return acts;
  const label = !PHOTOS ? t('逐张下载全部') : images.length > 1 ? t('全部存到相册（{count} 张）', { count: images.length }) : t('存到相册');
  const all = stillsButton(images, baseName, label, PHOTOS ? 'btn sm dark' : 'btn sm');
  return all ? [...acts, all] : acts;
}

/** 只存静态图：iPhone 走分享面板一次存进相册，其它设备逐张下载（只有一张就不用这个按钮） */
function stillsButton(images, baseName, label, cls) {
  if (PHOTOS) {
    const items = images.map((im, i) => ({ url: sourcesOf(im.url), name: baseName + '_' + (i + 1), type: 'image/*' }));
    return photosButton(label, (onBytes) => fetchFiles(items, onBytes), cls);
  }
  if (images.length < 2) return null;
  return el('button', { class: cls, type: 'button', onclick: (e) => downloadAll(images, baseName, e.currentTarget) }, label);
}

async function downloadAll(images, baseName, btn) {
  const label = btn.textContent;
  const active = () => btn.isConnected && !$('#result').hidden;
  btn.disabled = true;
  try {
    for (let i = 0; i < images.length; i++) {
      if (!active()) return;
      const name = baseName + '_' + (i + 1);
      const [file] = await oneFile(proxy(images[i].url, name, true), name, 'image/*')();
      if (!active()) return;
      saveFile(file);
      btn.textContent = t('下载中 ') + (i + 1) + '/' + images.length;
      await sleep(900);
    }
    btn.textContent = t('已全部触发下载');
  } catch (error) {
    if (active()) {
      btn.textContent = label;
      notice(error.message);
    }
  } finally {
    btn.disabled = false;
  }
}

// 平台自带的实况：原图 + 短视频，直接配对打包，不用裁剪。
// 只有一个实心主按钮（本机用得上的那种），另外的是同一行的小字按钮：以前三个大按钮在手机上竖着排，
// 太占地方；并排的「iPhone 实况 / 安卓动态照片」还被当成过平台切换。张数标题行已经写了，按钮上不重复
function liveBlock(images, lives, baseName) {
  const jobsBox = el('div', { class: 'jobs', style: 'margin-bottom:12px' });
  const pack = (fmt, cls, label) =>
    el('button', { class: cls, type: 'button', onclick: (e) => startLive(lives, fmt, baseName, jobsBox, e.currentTarget) }, label);
  const iphone = (cls, label) => pack('livephoto', cls, label);
  const android = (cls, label) => pack('motionphoto', cls, label);
  const stills = (label) => stillsButton(images, baseName, label, 'btn sm quiet');
  // iPhone 上网页怎么都存不成实况（分享面板、zip、小程序都试过 / 查过，只有原生 App 能写）：
  // 先把实况的视频存进相册，再用视频转实况的 App 转。zip 只有拖进 Mac 的「照片」才会合成
  const videos = () => {
    const items = lives.map((im) => ({
      url: sourcesOf(im.live_photo_url), name: baseName + '_' + (images.indexOf(im) + 1) + '_live', type: 'video/mp4',
    }));
    return photosButton(t('存实况视频到相册'), (onBytes) => fetchFiles(items, onBytes), 'btn sm dark');
  };

  let buttons;
  if (PHOTOS) buttons = [videos(), stills(t('只存静态图')), iphone('btn sm quiet', t('Mac 用实况包'))];
  else if (IOS) buttons = [iphone('btn sm dark', t('下载实况包')), stills(t('只下载静态图'))];
  else if (ANDROID) buttons = [android('btn sm dark', t('下载动态照片')), stills(t('只下载静态图')), iphone('btn sm quiet', t('iPhone 实况包'))];
  else buttons = [iphone('btn sm dark', t('下载 iPhone 实况包')), android('btn sm quiet', t('下载安卓动态照片')), stills(t('只下载静态图'))];
  const [main, ...more] = buttons;

  return [
    el('div', { class: 'live-actions' }, main, el('div', { class: 'more-acts' }, ...more.filter(Boolean))),
    PHOTOS ? iphoneLiveHint(t('存实况视频到相册'), t('「Mac 用实况包」')) : liveHint(),
    jobsBox,
  ];
}

function liveHint() {
  if (ANDROID) {
    return el('div', { class: 'fine' }, el('p', {}, t('动态照片存到相册就会动，Google 相册、三星、小米、OPPO、vivo 都认。静态图不会动。')));
  }
  return el('div', { class: 'fine' },
    el('p', {}, el('b', {}, t('受 iOS 限制，iPhone 上存不成实况')),
      t('：实况包要在 Mac 上用，解压后把 JPG 和 MOV 一起拖进「照片」App，开了 iCloud 照片会同步到 iPhone。')),
    el('p', {}, t('安卓动态照片是一张内嵌视频的 JPG，存到相册就会动。静态图不会动。')));
}

async function startLive(lives, fmt, baseName, jobsBox, btn) {
  const label = fmt === 'livephoto' ? t('iPhone 实况') : t('安卓动态照片');
  const card = jobCard(jobsBox, label + (lives.length > 1 ? ' × ' + lives.length : ''));
  btn.disabled = true;
  try {
    const items = lives.map((i) => ({
      image_url: i.url, video_url: i.live_photo_url, image_sig: sigOf(i.url), video_sig: sigOf(i.live_photo_url),
    }));
    const done = await watchJob(await postJSON('/api/live', { items, format: fmt, title: baseName }), card.update);
    const ext = (done.filename || '').split('.').pop().toUpperCase();
    card.done(t('保存 ') + ext + (done.filesize ? t('，') + fmtSize(done.filesize) : ''), '/api/jobs/' + done.id + '/file', done.filename);
  } catch (e) {
    card.fail(e.message);
  } finally {
    btn.disabled = false;
  }
}

/** 图集里的第 n 张：原图（实况的话再加短视频和「做 GIF」） */
function imageTile(img, n, baseName) {
  const name = baseName + '_' + n;
  const live = img.live_photo_url;
  // 原始文件可能是 WebP / GIF，甚至响应头也不可靠；复用封面下载的文件头识别。
  const still = imageButton(live ? t('原图') : PHOTOS ? t('存相册') : t('下载'), proxy(img.url, name, true), name);
  still.className = 'btn sm quiet';
  const actions = [still];
  if (live) {
    actions.push(
      liveVideoButton(live, name + '_live'),
      el('button', {
        class: 'btn sm quiet', type: 'button', title: t('用这段实况视频做 GIF'),
        onclick: () => openConverter({ url: live, title: name, sig: sigOf(live) }, 'gif'),
      }, 'GIF'));
  }
  return el('div', { class: 'tile' },
    proxyImg(img.url, { alt: '', loading: 'lazy' }),
    live ? el('span', { class: 'live' }, t('实况')) : null,
    el('div', { class: 'bar' }, el('span', {}, n), el('div', {}, ...actions)));
}

function liveVideoButton(url, name) {
  return PHOTOS
    ? photosButton(t('视频'), oneFile(() => sourcesOf(url), name, 'video/mp4'), 'btn sm quiet')
    : download(url, name + '.mp4', t('视频'), 'btn sm quiet');
}
