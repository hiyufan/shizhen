// 页面语言由服务端确定；原文作为键，格式化占位符与译文共用一份数据。
const messages = JSON.parse(document.getElementById('translations')?.textContent || '{}');
export const isEnglish = document.documentElement.lang === 'en';

export function t(message, values = {}) {
  const text = messages[message] ?? message;
  return text.replace(/\{(\w+)\}/g, (match, key) => values[key] == null ? match : values[key]);
}

/** 服务端保留中文 API 契约；前端翻译已知提示和含实际数值的任务消息。 */
export function serverMessage(message, fallback = '操作失败，请稍后重试') {
  if (typeof message !== 'string') return t(fallback);
  if (!isEnglish || messages[message]) return t(message);
  const base = message.split('（')[0];
  if (messages[base]) return t(base);
  const patterns = [
    [/^配好 (?<done>\d+)\/(?<total>\d+) 张$/, '配好 {done}/{total} 张'],
    [/^(?<total>\d+) 张一起拉，稍等$/, '{total} 张一起拉，稍等'],
    [/^(?<size>[\d.]+) MB 有点胖，瘦个身重来：宽 (?<width>\d+) px、(?<fps>\d+) fps$/,
      '{size} MB 有点胖，瘦个身重来：宽 {width} px、{fps} fps'],
  ];
  for (const [pattern, key] of patterns) {
    const match = message.match(pattern);
    if (match) return t(key, match.groups);
  }
  return /[\u3400-\u9fff]/.test(message) ? t(fallback) : message;
}
