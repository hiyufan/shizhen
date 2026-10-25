// 后端报阶段，前端只计时；不拿等待时长猜下载进度。
import { t } from './i18n.js';

import { $, el, notice } from './dom.js';

const LINES = {
  connecting: t('收到链接啦，正在敲平台家的门'),
  extracting: t('正在顺着链接翻箱底，找找视频和原图'),
  checking: t('找到地址了，先试试这扇门能不能打开'),
  fallback: t('正门有点挤，已启用备用方式，换条路继续找'),
  queued: t('备用小分队正在排队，前面的同学还没搬完'),
  browser: t('正在打开网页翻箱底，视频和原图都要找齐'),
  downloading: t('找到啦，正在把视频搬回来，片子越长搬得越久'),
  merging: t('画面和声音正在会合，少一个都不行'),
  verifying: t('视频已搬到，画面和声音正在接受体检'),
  cached: t('这条刚找过，正在从小仓库拿出来'),
};

function waited(seconds) {
  if (seconds < 60) return t('{seconds} 秒', { seconds });
  return t('{minutes} 分 {seconds} 秒', { minutes: Math.floor(seconds / 60), seconds: seconds % 60 });
}

export function parseHints() {
  const start = performance.now();
  const box = $('#notice');
  const line = el('span', { class: 'parse-stage', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }, LINES.connecting);
  const elapsed = el('span', { class: 'parse-elapsed', 'aria-live': 'off' });
  const fallback = el('span', { class: 'parse-fallback', hidden: true }, t('备用方式已启用'));
  let stopped = false;
  const tick = () => { elapsed.textContent = t('已等待 {time}', { time: waited(Math.floor((performance.now() - start) / 1000)) }); };
  tick();
  const first = setTimeout(() => {
    notice(t('解析中'), 'info parse-wait');
    box.setAttribute('aria-live', 'off');
    box.replaceChildren(line, el('span', { class: 'parse-meta' }, fallback, elapsed));
  }, 350);
  const timer = setInterval(tick, 1000);
  return {
    update(event) {
      if (stopped || !LINES[event.stage]) return;
      line.textContent = LINES[event.stage];
      if (event.fallback) fallback.hidden = false;
    },
    stop() {
      stopped = true;
      clearTimeout(first);
      clearInterval(timer);
      if (box.contains(line)) box.setAttribute('aria-live', 'polite');
    },
  };
}
