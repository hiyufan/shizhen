"""按原始消息复核评论图片；未加载的详情不能当作实况资源缺失。"""

import asyncio

from .native_live import CLIENT_JS

COMMENT_JS = r"""
const element = document.querySelector('[data-e2e="msg-item-content"], [data-e2e="conversation-item"]');
if (!element) return 'unknown';
const app = pageClient(element);
const sameImage = (url, expected) => {
  try {
    const left = new URL(url), right = new URL(expected);
    return left.protocol === 'https:' && left.origin === right.origin && left.pathname === right.pathname;
  } catch {return false;}
};
const context = () => {
  if (String(app.store?.usersInfoStore?.curLoginUserInfo?.uid || '') !== args.account) {
    throw new Error('comment_account_changed');
  }
  const actors = args.conversation.split(':');
  if (actors.length !== 4 || !actors.every(v => /^\d+$/.test(v)) || args.sender === args.account ||
      !actors.slice(2).includes(args.account) || !actors.slice(2).includes(args.sender)) return null;
  const sdk = app.imSdkService.imSdkManager.getImSdkInstance();
  const target = sdk.getConversation({conversationId: args.conversation});
  if (target?.id !== args.conversation || target.type !== 1) return null;
  const source = sdk.getConversationMessages({conversation: target}).find(m => String(m.serverId) === args.message);
  if (!source || source.type !== 105 || String(source.sender) !== args.sender ||
      source.conversationId !== args.conversation) return null;
  const card = JSON.parse(source.content);
  if (!/^\d+$/.test(String(card.itemId || '')) || !/^\d+$/.test(String(card.comment_id || '')) ||
      !card.comment_url?.url_list?.some(url => sameImage(url, args.url))) return null;
  return card;
};
const card = context();
if (!card) return 'unknown';
let comments = app.store.awemeStore?.commentMap?.get(String(card.itemId) + String(card.comment_id));
if (!Array.isArray(comments)) {
  // 与评论卡片的详情查询相同，仅读取这一条评论；签名、Cookie 和完整响应留在页面。
  const raw = await app.requestManager.request({method: 'GET', url: '/aweme/v1/web/comment/list/',
    noStatusCode: true, data: {aweme_id: String(card.itemId), cursor: 0, count: 1, item_type: 0,
      insert_ids: String(card.comment_id), whale_cut_token: '', cut_version: 1, rcFT: ''}});
  const result = raw?.data?.status_code !== undefined ? raw.data : raw;
  if (result?.status_code !== 0 || !Array.isArray(result.comments) || result.comments.length > 20) return 'unknown';
  comments = result.comments;
}
const current = context();
if (!current || String(current.itemId) !== String(card.itemId) ||
    String(current.comment_id) !== String(card.comment_id)) return 'unknown';
const comment = comments.find(row => String(row.cid) === String(card.comment_id));
if (!comment) return 'unavailable';
if (String(comment.aweme_id) !== String(card.itemId) || !Array.isArray(comment.image_list)) return 'unknown';
const images = comment.image_list;
const original = new URL(args.url);
const signing = new Set(['lk3s', 'x-expires', 'x-signature', 'from', 's', 'se', 'sc', 'biz_tag', 'l']);
const rawImage = original.pathname.replace(/^\/(?:obj\/)?/, '') === card.comment_url.uri &&
  Array.from(original.searchParams.keys()).every(key => signing.has(key));
// 同一 URI 的缩略图也可能是动态素材的封面，只接受已下载的原始对象，不能把转换预览当成原图。
const matched = rawImage && images.find(image => typeof image.origin_url?.uri === 'string' &&
  image.origin_url.uri === card.comment_url.uri);
if (!matched) return 'unknown';
const motion = image => !!(image.video || image.live_photo_video || Number(image.live_photo_type) > 0 ||
  Number(image.clip_type) === 5);
if (images.some(motion) || Array.isArray(comment.video_list) && comment.video_list.length) return 'motion';
if (comment.video_list != null && !Array.isArray(comment.video_list)) return 'unknown';
return 'static';
"""


async def read_comment_status(page, request: dict, timeout: float) -> str:
    script = "async args=>{" + CLIENT_JS + COMMENT_JS + "}"
    return await asyncio.wait_for(page.evaluate(script, request), timeout=timeout)
