import { t } from './i18n.js';

const root = document.documentElement;
const panel = document.getElementById('preferences');
const systemTheme = matchMedia('(prefers-color-scheme: dark)');
const DRAFT_KEY = 'qyt_language_draft';
const applyButton = panel.querySelector('.preferences-apply');

function cookie(name) {
  return document.cookie.split('; ').find((item) => item.startsWith(name + '='))?.split('=')[1];
}

function save(name, value) {
  try {
    document.cookie = name + '=' + value + '; Path=/; Max-Age=31536000; SameSite=Lax'
      + (location.protocol === 'https:' ? '; Secure' : '');
    return cookie(name) === value;
  } catch {
    return false;
  }
}

function updateBrowserColor() {
  const dark = root.dataset.theme === 'dark' || (root.dataset.theme === 'auto' && systemTheme.matches);
  document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => {
    meta.content = dark ? '#161b22' : '#fafaf9';
  });
}

function report(message) {
  const status = panel.querySelector('.preferences-status');
  status.textContent = t(message);
  status.hidden = false;
}

function changeLanguage(value) {
  if (!save('qyt_language', value)) {
    report('浏览器未允许保存设置，请允许本站使用 Cookie 后重试。');
    return;
  }
  // 切换语言不清除已输入的分享文字，草稿只在当前标签页保留一次。
  const input = document.getElementById('url');
  try {
    if (input) window.sessionStorage.setItem(DRAFT_KEY, input.value);
  } catch { /* 隐私模式可能禁用存储，语言仍可切换 */ }
  location.reload();
}

export function restoreLanguageDraft(input) {
  try {
    const draft = window.sessionStorage.getItem(DRAFT_KEY);
    window.sessionStorage.removeItem(DRAFT_KEY);
    if (draft !== null) input.value = draft;
  } catch { /* 不依赖会话存储才能使用解析功能 */ }
}

panel.addEventListener('change', (event) => {
  const input = event.target;
  if (input.name === 'language') {
    const hasResult = [...document.querySelectorAll('#result, #converter')].some((node) => !node.hidden);
    const parsing = document.getElementById('parse-btn')?.getAttribute('aria-busy') === 'true';
    if (hasResult || parsing) {
      report('切换语言会刷新页面，当前结果将清空。');
      applyButton.hidden = false;
    } else changeLanguage(input.value);
  }
  if (input.name !== 'theme') return;
  root.dataset.theme = input.value;
  updateBrowserColor();
  if (!save('qyt_theme', input.value)) report('浏览器未允许保存设置，本次外观已生效。');
});
applyButton.addEventListener('click', () => changeLanguage(panel.querySelector('input[name="language"]:checked').value));

document.addEventListener('click', (event) => {
  if (!panel.contains(event.target)) panel.open = false;
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape' || !panel.open) return;
  panel.open = false;
  panel.querySelector('summary').focus();
});
document.addEventListener('focusin', (event) => {
  if (!panel.contains(event.target)) panel.open = false;
});
// 前进 / 后退缓存恢复的是离开时的页面；期间在别的页面改过偏好，就跟上 Cookie。
window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return;
  const saved = (name, values) => (values.includes(cookie(name)) ? cookie(name) : 'auto');
  if (saved('qyt_language', ['zh', 'en']) !== root.dataset.language) {
    location.reload();
    return;
  }
  const theme = saved('qyt_theme', ['light', 'dark']);
  const option = panel.querySelector(`input[name="theme"][value="${theme}"]`);
  if (root.dataset.theme === theme) return;
  option.checked = true;
  root.dataset.theme = theme;
  updateBrowserColor();
});
systemTheme.addEventListener('change', updateBrowserColor);
updateBrowserColor();
