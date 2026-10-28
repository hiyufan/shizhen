// 广告商的同步脚本使用 document.write；此配置必须作为外部经典脚本在它之前执行。
// 本文件只在没有 allow-same-origin 的广告沙箱中加载，不属于工具页的模块 import 图。
window.atOptions = {
  key: document.body.dataset.key, format: 'iframe',
  height: Number(document.body.dataset.height), width: Number(document.body.dataset.width), params: {},
};

let finished = false;
const observer = new window.MutationObserver(findFrame);
const timeout = setTimeout(() => finish('unavailable'), 12000);
observer.observe(document.body, { childList: true, subtree: true });

function finish(state) {
  if (finished) return;
  finished = true;
  observer.disconnect();
  clearTimeout(timeout);
  window.parent.postMessage({ type: 'qyt-banner', state }, '*');
}

function findFrame() {
  const frame = document.querySelector('iframe');
  if (!frame || frame.dataset.watched) return;
  frame.dataset.watched = 'true';
  frame.addEventListener('load', () => finish('loaded'), { once: true });
}
