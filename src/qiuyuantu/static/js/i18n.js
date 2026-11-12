// 页面语言由服务端确定；原文作为键，格式化占位符与译文共用一份数据。
export const isEnglish = document.documentElement.lang === 'en';

// 英文译文是带版本号的独立模块，浏览器长期缓存；页面头部已预加载。取不到时退回原文，界面照常可用。
async function loadMessages() {
  if (!isEnglish) return {};
  try {
    return (await import('./messages-en.js')).default;
  } catch {
    return {};
  }
}

const messages = await loadMessages();

export function t(message, values = {}) {
  const text = messages[message] ?? message;
  return text.replace(/\{(\w+)\}/g, (match, key) => values[key] == null ? match : values[key]);
}

// 服务端带实际数值的提示：取出数值后按 locales/ui.en.json 里带占位符的键翻译，正则要和后端原文逐字对应
const SERVER_PATTERNS = [
  [/^配好 (?<done>\d+)\/(?<total>\d+) 张$/, '配好 {done}/{total} 张'],
  [/^(?<total>\d+) 张一起拉，稍等$/, '{total} 张一起拉，稍等'],
  [/^(?<size>[\d.]+) MB 有点胖，瘦个身重来：宽 (?<width>\d+) px、(?<fps>\d+) fps$/,
    '{size} MB 有点胖，瘦个身重来：宽 {width} px、{fps} fps'],
  [/^请求太频繁了，(?<wait>\d+) 秒后再试$/, '请求太频繁了，{wait} 秒后再试'],
  [/^现在解析的人太多了，(?<wait>\d+) 秒后再试$/, '现在解析的人太多了，{wait} 秒后再试'],
  [/^你有 (?<limit>\d+) 个下载还在进行，等一下再来$/, '你有 {limit} 个下载还在进行，等一下再来'],
  [/^你有 (?<limit>\d+) 个任务还在进行，等一下再来$/, '你有 {limit} 个任务还在进行，等一下再来'],
  [/^今天经本站转发的下载已经超过 (?<gb>[\d.]+) GB，明天再来$/, '今天经本站转发的下载已经超过 {gb} GB，明天再来'],
  [/^视频太长：最低清晰度也约 (?<size>\d+) MB，超过了服务器 (?<limit>\d+) MB 的上限$/,
    '视频太长：最低清晰度也约 {size} MB，超过了服务器 {limit} MB 的上限'],
  [/^这个清晰度的文件约 (?<size>\d+) MB，超过了服务器 (?<limit>\d+) MB 的上限，换低一档清晰度试试$/,
    '这个清晰度的文件约 {size} MB，超过了服务器 {limit} MB 的上限，换低一档清晰度试试'],
  [/^原视频超过 (?<limit>\d+) MB，不支持转换$/, '原视频超过 {limit} MB，不支持转换'],
  [/^视频没下完整，多半是超过了服务器 (?<limit>\d+) MB 的上限$/, '视频没下完整，多半是超过了服务器 {limit} MB 的上限'],
  [/^超过 (?<minutes>\d+) 分钟还没做完，已放弃。试试缩短时长或降低尺寸$/, '超过 {minutes} 分钟还没做完，已放弃。试试缩短时长或降低尺寸'],
  [/^超过 (?<seconds>\d+) 秒还没做完，已放弃。试试缩短时长或降低尺寸$/, '超过 {seconds} 秒还没做完，已放弃。试试缩短时长或降低尺寸'],
];

/** 服务端保留中文 API 契约；前端翻译已知提示和含实际数值的任务消息。 */
export function serverMessage(message, fallback = '操作失败，请稍后重试') {
  if (typeof message !== 'string') return t(fallback);
  if (!isEnglish || messages[message]) return t(message);
  const base = message.split('（')[0];
  if (messages[base]) return t(base);
  for (const [pattern, key] of SERVER_PATTERNS) {
    const match = message.match(pattern);
    if (match) return t(key, match.groups);
  }
  return /[\u3400-\u9fff]/.test(message) ? t(fallback) : message;
}
