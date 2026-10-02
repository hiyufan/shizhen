// DOM 小工具和格式化：不依赖页面上的任何具体元素

export const $ = (sel, root = document) => root.querySelector(sel);

function setAttr(node, key, value) {
  if (key === 'class') node.className = value;
  else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
  else if (value === true) node.setAttribute(key, '');
  else if (value !== false && value != null) node.setAttribute(key, value);
}

/** el('a', { class: 'btn', href, onclick }, '文字', 子元素...)；值为 false / null 的属性不设，子元素里的 null 跳过 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) setAttr(node, key, value);
  node.append(...children.flat().filter((child) => child != null));
  return node;
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 秒 → 「1:05.3」/「5.3」，修剪条上用 */
export function fmtTime(s) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  const whole = m ? String(Math.floor(sec)).padStart(2, '0') : Math.floor(sec);
  return (m ? m + ':' : '') + whole + '.' + Math.floor((sec % 1) * 10);
}

/** 秒 → 「1:05」，视频时长用 */
export function fmtClock(s) {
  s = Math.round(s || 0);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

export function fmtSize(bytes) {
  if (!bytes) return '';
  return bytes < 1 << 20 ? (bytes / 1024).toFixed(0) + ' KB' : (bytes / (1 << 20)).toFixed(1) + ' MB';
}

/** 标题 → 能当文件名的字符串 */
export const safeName = (s) => (s || '').replace(/[\\/:*?"<>|\n\r]+/g, ' ').trim().slice(0, 60);

/** 「生成 GIF」「生成实况照片」：英文名前面空一格 */
export const spaced = (name) => (/^[A-Za-z]/.test(name) ? ' ' + name : name);

/** 和页面上的「提示条」同一个元素：#notice。msg 为空就收起 */
export function notice(msg, kind = '') {
  const box = $('#notice');
  if (!box) return;
  box.hidden = !msg;
  if (!msg) return;
  box.className = 'notice ' + kind;
  box.textContent = msg;
}
