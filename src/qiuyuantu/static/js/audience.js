// 只发送可见页面上的主动交互标记，不采集键值、剪贴板、鼠标轨迹或设备指纹。
import { postJSON } from './api.js';

let lastSent = -Infinity;
let pending = false;

async function recordInteraction(event) {
  if (!event.isTrusted || document.visibilityState !== 'visible' || pending) return;
  if (Date.now() - lastSent < 300000) return;
  pending = true;
  try {
    await postJSON('/api/visit', {
      visible: true, interaction: true, automated: navigator.webdriver === true,
    });
    lastSent = Date.now();
  } catch (_) {
    // 统计失败不能影响粘贴、提交和下载；下一次真实交互可以重试。
  } finally {
    pending = false;
  }
}

export function initAudience() {
  if (!document.querySelector('[data-audience]')) return;
  for (const name of ['pointerdown', 'keydown', 'paste']) {
    document.addEventListener(name, recordInteraction, { capture: true, passive: true });
  }
}
