// 解析结果：视频（预览、下载、其他清晰度、转换入口）、图集（逐张保存、实况打包）

import { postJSON, proxy, proxyImg, sigOf, watchJob } from './api.js';
import { openConverter } from './converter.js';
import { $, el, fmtClock, fmtSize, safeName, sleep } from './dom.js';
import { ANDROID, IOS, PHOTOS, fetchFiles, iphoneLiveHint, oneFile, photosButton } from './save.js';
import { jobCard } from './ui.js';

const PLATFORMS = {
  douyin: '抖音', redbook: '小红书', kuaishou: '快手', youtube: 'YouTube', twitter: 'X', bilibili: 'B站', weibo: '微博',
  tiktok: 'TikTok', instagram: 'Instagram', xigua: '西瓜视频', pipixia: '皮皮虾', acfun: 'AcFun', weishi: '微视',
  lvzhou: '绿洲', zuiyou: '最右', quanmin: '度小视', lishipin: '梨视频', pipigaoxiao: '皮皮搞笑', huya: '虎牙',
  doupai: '逗拍', meipai: '美拍', quanminkge: '全民K歌', sixroom: '六间房', xinpianchang: '新片场', haokan: '好看视频',
  qqvideo: '腾讯视频', sohu: '搜狐视频', cctv: '央视网', vimeo: 'Vimeo', facebook: 'Facebook', twitch: 'Twitch',
  reddit: 'Reddit', pinterest: 'Pinterest', ytdlp: 'yt-dlp',
};

const download = (url, filename, label, cls) =>
  el('a', { class: cls, href: proxy(url, filename, true), download: filename }, label);

export function renderResult(d) {
  const title = d.title || '未命名';
  // notice：平台不给的那部分是什么（比如快手实况只有照片），说在前面免得用户以为是网站漏了
  const notice = d.notice ? el('p', { class: 'fine' }, d.notice) : null;
  const res = el('div', { class: 'res' }, resultHead(d), el('h2', {}, title), notice, ...resultBody(d, safeName(title) || 'video'));
  const box = $('#result');
  box.replaceChildren(res);
  box.hidden = false;
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function resultHead(d) {
  return el('div', { class: 'res-head' },
    el('span', { class: 'tag' }, PLATFORMS[d.source] || d.source || '未知来源'),
    d.author && d.author.name ? el('span', {}, d.author.name) : null,
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
    const audio = download(d.music_url, baseName + '.mp3', '下载音频', 'btn');
    parts.push(el('div', { class: 'side' }, el('div', { class: 'group' }, audio), el('div', { class: 'jobs' })));
  }
  if (images.length) parts.push(...imageSection(d, images, baseName, hasVideo));
  if (!parts.length) {
    parts.push(el('div', { class: 'notice' }, '解析成功但没有拿到任何视频或图片，这条内容可能已被删除或需要登录。'));
  }
  return parts;
}

// ---------- 视频

function videoSection(d, baseName, jobsBox) {
  const preview = d.video_url
    ? el('div', { class: 'media' + (isPortrait(d) ? ' portrait' : '') },
      el('video', {
        controls: true, playsinline: true, preload: 'metadata',
        src: proxy(d.video_url), poster: d.cover_url ? proxy(d.cover_url) : null,
      }))
    : el('div', { class: 'media portrait' }, d.cover_url ? proxyImg(d.cover_url, { alt: '' }) : null);
  const convert = (format) => () => openConverter(sourceRequest(d, baseName), format);
  const side = el('div', { class: 'side' },
    saveActions(d, baseName, jobsBox),
    el('div', { class: 'rule' }),
    el('p', { class: 'label' }, '拿这段视频继续做'),
    el('div', { class: 'group' },
      el('button', { class: 'btn', type: 'button', onclick: convert('gif') }, '转 GIF'),
      el('button', { class: 'btn', type: 'button', onclick: convert('livephoto') }, '做成实况照片')),
    jobsBox);
  return el('div', { class: 'res-body' }, preview, side);
}

const isPortrait = (d) => (d.width && d.height ? d.height > d.width : true);

/** 下载视频 / 其他清晰度 / 音频。iPhone 上「存到相册」是主按钮，其余收成下面一行小字，免得三个大按钮竖着排 */
function saveActions(d, baseName, jobsBox) {
  if (PHOTOS && d.video_url) {
    const more = el('div', { class: 'more-acts' }, ...secondaryDownloads(d, baseName, jobsBox, true));
    return el('div', { class: 'group' }, photosButton('存到相册', oneFile(proxy(d.video_url), baseName, 'video/mp4')), more);
  }
  return el('div', { class: 'group' }, ...secondaryDownloads(d, baseName, jobsBox, false));
}

function secondaryDownloads(d, baseName, jobsBox, compact) {
  const links = [];
  if (d.video_url) {
    const label = '下载视频' + (d.height ? ' ' + d.height + 'p' : '');
    links.push(download(d.video_url, baseName + '.mp4', label, compact ? 'btn sm quiet' : 'btn dark'));
  }
  if (d.formats && d.formats.length) links.push(formatMenu(d, baseName, jobsBox, compact));
  // 音频用得少，不和下载视频抢位置
  if (d.music_url) links.push(download(d.music_url, baseName + '.mp3', '下载音频', compact ? 'btn sm quiet' : 'btn quiet'));
  return links;
}

/** 「其他清晰度 ▾」：有直链的直接下载，要服务端合并的起一个下载任务 */
function formatMenu(d, baseName, jobsBox, compact) {
  const list = el('div', { class: 'menu-list', hidden: true });
  const close = () => { list.hidden = true; };
  for (const f of d.formats) {
    const meta = [el('span', {}, f.label), el('small', {}, f.filesize ? fmtSize(f.filesize) : f.ext)];
    const filename = baseName + '_' + f.label.replace(/\s+/g, '') + '.' + (f.ext || 'mp4');
    list.append(f.url
      ? el('a', { href: proxy(f.url, filename, true), download: '', onclick: close }, ...meta)
      : el('button', { type: 'button', onclick: () => { close(); startDownload(f, d, baseName, jobsBox); } }, ...meta));
  }
  if (d.formats.some((f) => f.codec)) {
    list.append(el('p', { class: 'hint', style: 'padding:6px 10px 4px' },
      'H.265 体积更小、更清晰，手机都能播；老电脑可能需要装解码器。'));
  }
  const toggleClass = compact ? 'btn sm quiet' : 'btn' + (d.video_url ? '' : ' dark');
  const toggle = el('button', { class: toggleClass, type: 'button', onclick: () => { list.hidden = !list.hidden; } },
    d.video_url ? '其他清晰度 ▾' : '下载视频 ▾');
  return el('div', { class: 'menu' }, toggle, list);
}

// 点菜单外面任何地方都收起。整页挂一个就够，别每渲染一次结果都往 document 上加
document.addEventListener('click', (e) => {
  for (const menu of document.querySelectorAll('.menu')) {
    if (!menu.contains(e.target)) menu.querySelector('.menu-list').hidden = true;
  }
});

/** 转换要用的原视频：直链优先；没有直链（YouTube 之类）就让服务端用 yt-dlp 拉 */
function sourceRequest(d, title) {
  const needsServer = d.formats.some((f) => !f.url);
  if (d.video_url && (!needsServer || d.height >= 720 || !d.page_url)) {
    return { url: d.video_url, headers: d.video_headers || {}, title, sig: sigOf(d.video_url) };
  }
  const page = d.page_url || d.share_url;
  return { page_url: page, title, sig: sigOf(page) };
}

async function startDownload(f, d, baseName, jobsBox) {
  const card = jobCard(jobsBox, '下载 ' + f.label);
  const page = d.page_url || d.share_url;
  try {
    const job = await postJSON('/api/download', {
      page_url: page, format_spec: f.format_spec, title: baseName, ext: f.ext, sig: sigOf(page),
    });
    const done = await watchJob(job, card.update);
    const file = '/api/jobs/' + done.id + '/file';
    // 照片 App 只认 mp4 / mov；webm、音频还是走下载
    const photos = PHOTOS && /\.mp4$/i.test(done.filename || '')
      ? photosButton('存到相册', oneFile(file + '?inline=1', baseName + '_' + f.label.replace(/\s+/g, ''), 'video/mp4'), 'btn sm dark')
      : null;
    card.done((photos ? '下载 ' : '保存 ') + (done.filesize ? fmtSize(done.filesize) : ''), file, done.filename, photos);
  } catch (e) {
    card.fail(e.message);
  }
}

// ---------- 图集

function imageSection(d, images, baseName, hasVideo) {
  const lives = images.filter((i) => i.live_photo_url);
  const head = el('div', { class: 'sheet-title' },
    el('span', {}, images.length + ' 张图片' + (lives.length ? '，其中 ' + lives.length + ' 张是实况' : '')));
  const acts = headActions(d, images, baseName, hasVideo, lives.length > 0);
  if (acts.length) head.append(el('div', { class: 'head-acts' }, ...acts));
  const grid = el('div', { class: 'grid' }, ...images.map((img, i) => imageTile(img, i + 1, baseName)));
  return [head, ...(lives.length ? liveBlock(images, lives, baseName) : []), grid];
}

/** 图集标题行右边的小按钮：背景音乐（用得少，不占一整行）；没有实况时的「全部保存」 */
function headActions(d, images, baseName, hasVideo, hasLives) {
  const acts = [];
  if (d.music_url && !hasVideo) acts.push(download(d.music_url, baseName + '.mp3', '下载背景音乐', 'btn sm quiet'));
  if (hasLives) return acts;
  const label = !PHOTOS ? '逐张下载全部' : images.length > 1 ? '全部存到相册（' + images.length + ' 张）' : '存到相册';
  const all = stillsButton(images, baseName, label, PHOTOS ? 'btn sm dark' : 'btn sm');
  return all ? [...acts, all] : acts;
}

/** 只存静态图：iPhone 走分享面板一次存进相册，其它设备逐张下载（只有一张就不用这个按钮） */
function stillsButton(images, baseName, label, cls) {
  if (PHOTOS) {
    const items = images.map((im, i) => ({ url: proxy(im.url), name: baseName + '_' + (i + 1) }));
    return photosButton(label, (onBytes) => fetchFiles(items, onBytes), cls);
  }
  if (images.length < 2) return null;
  return el('button', { class: cls, type: 'button', onclick: (e) => downloadAll(images, baseName, e.currentTarget) }, label);
}

async function downloadAll(images, baseName, btn) {
  btn.disabled = true;
  for (let i = 0; i < images.length; i++) {
    const a = el('a', { href: proxy(images[i].url, baseName + '_' + (i + 1) + '.jpg', true), download: '' });
    document.body.append(a);
    a.click();
    a.remove();
    btn.textContent = '下载中 ' + (i + 1) + '/' + images.length;
    await sleep(900);
  }
  btn.textContent = '已全部触发下载';
  btn.disabled = false;
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
      url: proxy(im.live_photo_url), name: baseName + '_' + (images.indexOf(im) + 1) + '_live', type: 'video/mp4',
    }));
    return photosButton('存实况视频到相册', (onBytes) => fetchFiles(items, onBytes), 'btn sm dark');
  };

  let buttons;
  if (PHOTOS) buttons = [videos(), stills('只存静态图'), iphone('btn sm quiet', 'Mac 用实况包')];
  else if (IOS) buttons = [iphone('btn sm dark', '下载实况包'), stills('只下载静态图')];
  else if (ANDROID) buttons = [android('btn sm dark', '下载动态照片'), stills('只下载静态图'), iphone('btn sm quiet', 'iPhone 实况包')];
  else buttons = [iphone('btn sm dark', '下载 iPhone 实况包'), android('btn sm quiet', '下载安卓动态照片'), stills('只下载静态图')];
  const [main, ...more] = buttons;

  return [
    el('div', { class: 'live-actions' }, main, el('div', { class: 'more-acts' }, ...more.filter(Boolean))),
    PHOTOS ? iphoneLiveHint('存实况视频到相册', '「Mac 用实况包」') : liveHint(),
    jobsBox,
  ];
}

function liveHint() {
  if (ANDROID) {
    return el('div', { class: 'fine' }, el('p', {}, '动态照片存到相册就会动，Google 相册、三星、小米、OPPO、vivo 都认。静态图不会动。'));
  }
  return el('div', { class: 'fine' },
    el('p', {}, el('b', {}, '受 iOS 限制，iPhone 上存不成实况'),
      '：实况包要在 Mac 上用，解压后把 JPG 和 MOV 一起拖进「照片」App，开了 iCloud 照片会同步到 iPhone。'),
    el('p', {}, '安卓动态照片是一张内嵌视频的 JPG，存到相册就会动。静态图不会动。'));
}

async function startLive(lives, fmt, baseName, jobsBox, btn) {
  const label = fmt === 'livephoto' ? 'iPhone 实况' : '安卓动态照片';
  const card = jobCard(jobsBox, label + (lives.length > 1 ? ' × ' + lives.length : ''));
  btn.disabled = true;
  try {
    const items = lives.map((i) => ({
      image_url: i.url, video_url: i.live_photo_url, image_sig: sigOf(i.url), video_sig: sigOf(i.live_photo_url),
    }));
    const done = await watchJob(await postJSON('/api/live', { items, format: fmt, title: baseName }), card.update);
    const ext = (done.filename || '').split('.').pop().toUpperCase();
    card.done('保存 ' + ext + (done.filesize ? '，' + fmtSize(done.filesize) : ''), '/api/jobs/' + done.id + '/file', done.filename);
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
  const save = (url, file, ext, label, type) => (PHOTOS
    ? photosButton(label, oneFile(proxy(url), file, type), 'btn sm quiet')
    : el('a', { class: 'btn sm quiet', href: proxy(url, file + '.' + ext, true), download: file + '.' + ext }, label));
  const actions = [save(img.url, name, 'jpg', live ? '原图' : PHOTOS ? '存相册' : '下载')];
  if (live) {
    actions.push(
      save(live, name + '_live', 'mp4', '视频', 'video/mp4'),
      el('button', {
        class: 'btn sm quiet', type: 'button', title: '用这段实况视频做 GIF',
        onclick: () => openConverter({ url: live, title: name, sig: sigOf(live) }, 'gif'),
      }, 'GIF'));
  }
  return el('div', { class: 'tile' },
    proxyImg(img.url, { alt: '', loading: 'lazy' }),
    live ? el('span', { class: 'live' }, '实况') : null,
    el('div', { class: 'bar' }, el('span', {}, n), el('div', {}, ...actions)));
}
