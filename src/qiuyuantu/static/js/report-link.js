// 解析结果下的「结果不对？告诉我们」→ 意见反馈页：刚才解析的链接只放在本标签页的会话存储里，
// 不进地址栏和 Referer；反馈页默认不附，访客勾选后才随反馈提交
const KEY = 'qyt_feedback_link';

export function rememberParsedLink(text) {
  const link = (text || '').match(/https?:\/\/\S+/)?.[0] || '';
  try {
    if (link) window.sessionStorage.setItem(KEY, link.slice(0, 2000));
    else window.sessionStorage.removeItem(KEY);
  } catch { /* 存储不可用时反馈页不显示附链接选项 */ }
}

export function parsedLink() {
  try {
    return window.sessionStorage.getItem(KEY) || '';
  } catch {
    return '';
  }
}

export function forgetParsedLink() {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch { /* 存储不可用时没有需要清理的链接 */ }
}
