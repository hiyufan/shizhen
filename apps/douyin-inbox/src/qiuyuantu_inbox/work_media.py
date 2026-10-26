"""按原始消息校验客户端缓存，读取公开媒体地址；不切换会话、不导出整个载荷。"""

import asyncio

from .native_live import CLIENT_JS

WORK_JS = r"""
const workConversation = args => {
  const actors = args.conversation.split(':');
  return actors.length === 4 && actors.every(v => /^\d+$/.test(v)) && args.sender !== args.account &&
    actors.slice(2).includes(args.account) && actors.slice(2).includes(args.sender);
};
const workContext = (element, args) => {
  if (!element) return null;
  const app = pageClient(element);
  if (String(app.store?.usersInfoStore?.curLoginUserInfo?.uid || '') !== args.account) {
    throw new Error('work_account_changed');
  }
  if (!workConversation(args)) return null;
  const sdk = app.imSdkService.imSdkManager.getImSdkInstance();
  const target = sdk.getConversation({conversationId: args.conversation});
  return target?.id === args.conversation && target.type === 1 ? {app, sdk, target} : null;
};
const workSource = (context, args) => {
  const source = context.sdk.getConversationMessages({conversation: context.target})
    .find(m => String(m.serverId) === args.message);
  if (!source || ![8, 77].includes(source.type) || String(source.sender) !== args.sender ||
      String(source.sender) === args.account || source.conversationId !== args.conversation) return null;
  return source;
};
const workCard = (context, args) => {
  const source = workSource(context, args);
  if (!source) return null;
  const card = JSON.parse(source.content);
  return String(card.itemId) === args.work_id ? card : null;
};
const workFirstURL = values => Array.isArray(values) ? values.find(v => typeof v === 'string') || '' : '';
const workVideoURL = video => workFirstURL(video?.play_addr_h264?.url_list) || workFirstURL(video?.play_addr?.url_list);
const workImage = image => {
  // download_url_list 带用户名水印；与解析器一样只从 url_list 选取图片。
  const urls = Array.isArray(image.url_list) ? image.url_list.filter(u => typeof u === 'string') : [];
  const url = urls.find(u => /\.jpe?g$/i.test(u.split('?')[0])) || workFirstURL(urls);
  const video = workVideoURL(image.video);
  const live = !!video || Number(image.live_photo_type) > 0 || Number(image.clip_type) === 5;
  return {url, video, live};
};
const workImages = (detail, card) => {
  if (card.image_count && detail.images.length !== Number(card.image_count)) return null;
  const media = detail.images.map(workImage);
  if (media.some(item => !item.url || item.live && !item.video) ||
      Number(card.is_live_photo) > 0 && !media.some(item => item.live)) return null;
  return media;
};
const workVideo = (detail, card) => {
  if (card.is_slides || Number(card.is_live_photo) > 0 || Number(detail.aweme_type) === 68) return null;
  const url = workVideoURL(detail.video);
  return url ? [{url, is_video: true, title: String(detail.desc || '').slice(0, 80)}] : null;
};
const cachedWorkMedia = (element, args) => {
  const context = workContext(element, args);
  if (!context) return null;
  const card = workCard(context, args);
  if (!card) return null;
  const cached = context.app.store.awemeStore?.awemeDetailMap?.get(args.work_id);
  const detail = cached?.detail;
  if (!detail || cached.filted || String(detail.aweme_id) !== args.work_id) return null;
  return Array.isArray(detail.images) && detail.images.length ? workImages(detail, card) : workVideo(detail, card);
};
"""

SOURCE_JS = WORK_JS + "return cachedWorkMedia(element, args);"

RESOLVE_JS = (
    WORK_JS
    + r"""
const context = workContext(element, args);
if (!context) return null;
const card = workCard(context, args), source = workSource(context, args);
if (!card || !source) return null;
const cached = cachedWorkMedia(element, args);
if (cached) return cached;
if (context.app.store.awemeStore?.awemeDetailMap?.get(args.work_id)?.filted) return null;
const shortId = String(context.target.shortId || '');
if (!/^\d{1,32}$/.test(shortId) || shortId !== String(source.conversationShortId)) return null;
// 与客户端后台补取作品详情相同，只查当前消息中的作品，不打开作品页或导出完整响应。
let raw;
try {
  raw = await context.app.requestManager.request({method: 'POST', url: '/aweme/v1/web/multi/aweme/detail/',
    noStatusCode: true, params: {aweme_ids: `[${args.work_id}]`, origin_type: 'chat', request_source: '3',
      conversation_short_id: shortId}});
} catch {return null;}
const current = workContext(element, args);
if (!current || !workCard(current, args) || String(current.target.shortId) !== shortId ||
    String(workSource(current, args)?.conversationShortId) !== shortId) return null;
const result = raw?.data?.status_code !== undefined ? raw.data : raw;
if (result?.status_code !== 0 || !Array.isArray(result.aweme_details) || result.aweme_details.length !== 1 ||
    result.filter_list?.some(row => String(row.aweme_id) === args.work_id)) return null;
const detail = result.aweme_details[0];
if (String(detail.aweme_id) !== args.work_id) return null;
const currentCard = workCard(current, args);
return Array.isArray(detail.images) && detail.images.length
  ? workImages(detail, currentCard) : workVideo(detail, currentCard);
"""
)


async def read_work_media(
    page, request: dict, helpers: str, timeout: float, *, resolve: bool = False
) -> list[dict] | None:
    script = (
        ("async args=>{" if resolve else "args=>{")
        + helpers
        + CLIENT_JS
        + 'const element = document.querySelector(\'[data-e2e="msg-item-content"], [data-e2e="conversation-item"]\');'
        + (RESOLVE_JS if resolve else SOURCE_JS)
        + "}"
    )
    try:
        return await asyncio.wait_for(page.evaluate(script, request), timeout=timeout)
    except asyncio.TimeoutError:
        if not resolve:
            raise
        return None
