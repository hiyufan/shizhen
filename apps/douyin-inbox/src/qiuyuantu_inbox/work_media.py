"""只读取当前会话中已收作品的公开媒体地址，不导出整个客户端载荷。"""

from .native_live import CLIENT_JS

SOURCE_JS = r"""
const {app, sdk, target} = nativeContext(element, args);
const source = sdk.getConversationMessages({conversation: target}).find(m => String(m.serverId) === args.message);
if (!source || ![8, 77].includes(source.type) || String(source.sender) !== args.sender ||
    String(source.sender) === args.account || source.conversationId !== args.conversation) return null;
const card = JSON.parse(source.content);
if (String(card.itemId) !== args.work_id) return null;
const cached = app.store.awemeStore?.awemeDetailMap?.get(args.work_id);
const detail = cached?.detail;
if (!detail || cached.filted || String(detail.aweme_id) !== args.work_id) return null;
if (!Array.isArray(detail.images) || !detail.images.length) {
  if (card.is_slides || Number(detail.aweme_type) === 68) return null;
  const urls = detail.video?.play_addr_h264?.url_list || detail.video?.play_addr?.url_list;
  const url = Array.isArray(urls) ? urls.find(v => typeof v === 'string') : '';
  return url ? [{url, is_video: true, title: String(detail.desc || '').slice(0, 80)}] : null;
}
if (card.image_count && detail.images.length !== Number(card.image_count)) return null;
const media = detail.images.map(image => {
  const first = values => Array.isArray(values) ? values.find(v => typeof v === 'string') : '';
  // download_url_list 带用户名水印；与解析器一样只从 url_list 选取图片。
  const urls = Array.isArray(image.url_list) ? image.url_list.filter(u => typeof u === 'string') : [];
  const url = urls.find(u => /\.jpe?g$/i.test(u.split('?')[0])) || first(urls);
  const video = first(image.video?.play_addr_h264?.url_list) || first(image.video?.play_addr?.url_list);
  const live = !!video || Number(image.live_photo_type) > 0 || Number(image.clip_type) === 5;
  return {url, video: video || '', live};
});
if (media.some(item => !item.url || item.live && !item.video) ||
    Number(card.is_live_photo) > 0 && !media.some(item => item.live)) return null;
return media;
"""


async def read_work_media(page, request: dict, helpers: str) -> list[dict] | None:
    script = "(element,args)=>{" + helpers + CLIENT_JS + SOURCE_JS + "}"
    return await page.locator('[data-e2e="msg-item-content"]').first.evaluate(script, request)
