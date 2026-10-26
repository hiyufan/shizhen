"""网页适配层：稳定会话 ID 校验，复用页面 UI 与已登录客户端的媒体收发封装。"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from pathlib import Path

from .native_live import CLIENT_JS, send_live_files
from .native_video import read_native_video
from .work_media import read_work_media

JS = r"""
const property = (element, names) => {
  const key = Object.keys(element || {}).find(k => k.startsWith('__reactFiber$'));
  for (let fiber = element?.[key], depth = 0; fiber && depth < 18; fiber = fiber.return, depth++) {
    for (const name of names) if (fiber.memoizedProps?.[name]) return fiber.memoizedProps[name];
  }
  return null;
};
const conversation = (c, element) => {
  if (!c || c.type !== 1) return null;
  const id = String(c.id || ''), recipient = String(c.toParticipantUserId || '');
  const parts = id.split(':');
  const actors = parts.slice(2);
  if (parts.length !== 4 || !parts.every(v => /^\d+$/.test(v)) || !actors.includes(recipient)) return null;
  const account = actors.find(v => v !== recipient);
  if (!account) return null;
  element?.setAttribute('data-qyt-conversation', id);
  return {id, recipient, account, unread: Number(c.unreadCount || 0),
    last_id: String(c.lastMessage?.serverId || ''),
    last_at: Number(c.lastMessage?.createdAt || 0) / 1000};
};
const firstURL = values => Array.isArray(values) ? values.find(v => typeof v === 'string') : null;
const commentVerified = (element, content) => {
  const key = Object.keys(element || {}).find(k => k.startsWith('__reactFiber$'));
  for (let fiber = element?.[key], depth = 0; fiber && depth < 90; fiber = fiber.return, depth++) {
    const store = fiber.memoizedProps?.value?.store?.awemeStore;
    if (!store) continue;
    const comments = store.commentMap?.get(String(content.itemId) + String(content.comment_id));
    return Array.isArray(comments) && comments.some(c => String(c.cid) === String(content.comment_id));
  }
  return false;
};
const message = element => {
  const m = property(element, ['message']);
  if (!m) return null;
  const c = m.parsedContent || {};
  let kind = null, data = {};
  if ([8, 77].includes(m.type) && /^\d+$/.test(String(c.itemId || ''))) {
    kind = 'work'; data = {work_id: String(c.itemId),
      title: String(c.content_title || c.content_name || '').slice(0, 80),
      live: Number(c.is_live_photo || 0) > 0,
      photos: c.is_slides === true || c.is_slides === 1 || Number(c.awemeType) === 68 ||
        !!element.querySelector('.MessageItemShareAwemephotosTag')};
  } else if (m.type === 105) {
    const url = firstURL(c.comment_url?.url_list) ||
      element.querySelector('img.MessageItemCommentSharecommentCover')?.src;
    if (url) {kind = 'media'; data = {url, comment_verified: commentVerified(element, c)};}
  } else if (m.type === 27) {
    const url = firstURL(c.resource_url?.origin_url_list) ||
      element.querySelector('img.MessageItemImageImage')?.src;
    if (url) {
      kind = c.live_photo_video?.vid ? 'live' : 'photo';
      data = {url, md5: String(c.md5 || c.resource_url?.md5 || '')};
      if (kind === 'live') {
        data.live_vid = String(c.live_photo_video.vid);
        data.live_md5 = String(c.live_photo_video.md5 || '');
      }
    }
  } else if (m.type === 7) {
    kind = 'text'; data = {text: String(c.text || '').slice(0, 4096)};
  }
  if (m.serverId) element.setAttribute('data-qyt-message', String(m.serverId));
  return {id: String(m.serverId || ''), type: m.type, conversation: String(m.conversationId || ''),
    sender: String(m.sender || ''), created: Number(m.createdAt || 0) / 1000,
    from_me: Boolean(m.isFromMe), flight: m.flightStatus, server_status: m.serverStatus,
    md5: String(c.md5 || c.resource_url?.md5 || c.video?.md5 || ''),
    live_vid: String(c.live_photo_video?.vid || ''), live_md5: String(c.live_photo_video?.md5 || ''),
    awe_type: c.aweType, kind, data};
};
"""


class BrowserUnavailable(RuntimeError):
    pass


def acknowledged(rows: list[dict], part: dict, conversation: str, before: set[str]) -> list[str]:
    delivered = [
        row
        for row in rows
        if row
        and row["id"]
        and row["id"] not in before
        and row["conversation"] == conversation
        and row["from_me"]
        and row.get("flight") == 4
        and row.get("server_status") == 0
        and (not part.get("live_vid") or row.get("live_vid") == part["live_vid"])
        and (not part.get("native_video_md5") or row.get("live_md5") == part["native_video_md5"])
        and (
            not part.get("live_pair")
            or (row.get("awe_type") == 2704 and row.get("live_md5") == part["md5"][1] and row.get("type") == 27)
        )
        and (not part.get("video") or row.get("type") == 30)
        and (not part.get("forward_source") or row.get("type") == 27)
    ]
    if "text" in part:
        return [row["id"] for row in delivered if row["data"].get("text") == part["text"]][:1]
    expected = Counter(part["md5"][:1] if part.get("live_pair") else part["md5"])
    found = []
    for row in delivered:
        digest = row.get("md5")
        if expected[digest] > 0:
            expected[digest] -= 1
            found.append(row["id"])
    return found if not any(expected.values()) else []


class Browser:
    def __init__(self, page, settings, account: str = ""):
        self.page, self.settings, self.account = page, settings, account
        self.send_timings: dict[str, float] = {}

    async def active(self) -> dict | None:
        heading = self.page.locator(".StackLayoutStackChatHeadertitle").filter(visible=True)
        if await heading.count() != 1:
            return None
        return await heading.evaluate("e => {" + JS + "return conversation(property(e,['curConversation']),e);}")

    def check_account(self, info: dict | None) -> None:
        if not info or (self.account and info["account"] != self.account):
            raise BrowserUnavailable("account_or_adapter_changed")

    async def guard(self, conversation: str) -> None:
        info = await self.active()
        self.check_account(info)
        if info["id"] != conversation:
            raise BrowserUnavailable("conversation_changed")
        editor = self.page.locator('[data-e2e="msg-input"] [contenteditable="true"]')
        if (await editor.inner_text()).replace("\u200b", "").strip():
            raise BrowserUnavailable("manual_draft")

    async def work_media(self, message) -> list[dict] | None:
        await self.open(message.conversation)
        await self.guard(message.conversation)
        return await self.cached_work_media(message)

    async def cached_work_media(self, message) -> list[dict] | None:
        """只读 SDK 中对应会话的消息和已加载详情，不依赖当前聊天或草稿状态。"""
        return await read_work_media(
            self.page,
            {
                "account": self.account,
                "conversation": message.conversation,
                "message": message.id,
                "sender": message.sender,
                "work_id": message.data["work_id"],
            },
            JS,
            self.settings.connect_timeout,
        )

    async def back(self) -> None:
        current = await self.active()
        if current:
            await self.guard(current["id"])
            button = self.page.locator(".StackLayoutStackTitleBarbackBtn").filter(visible=True)
            if await button.count():
                await button.first.click(timeout=5000)
                await self.page.locator(".StackLayoutStackChatHeadertitle").wait_for(state="hidden", timeout=5000)

    async def _list(self) -> list[dict]:
        return (
            await self.page.locator('[data-e2e="conversation-item"]')
            .filter(visible=True)
            .evaluate_all(
                "els => {" + JS + "return els.map(e=>conversation(property(e,['conversation']),e)).filter(Boolean);}"
            )
        )

    async def conversations(self) -> list[dict]:
        await self.back()
        items = self.page.locator('[data-e2e="conversation-item"]').filter(visible=True)
        if not await items.count():
            login = self.page.get_by_text("扫码登录", exact=True)
            if await login.is_visible():
                raise BrowserUnavailable("login_required")
            await self.page.locator('[data-e2e="im-entry"] [data-e2e="something-button"]').click(timeout=5000)
            await items.first.wait_for(timeout=10000)
        scroll = self.page.locator(".conversationConversationListwrapper").filter(visible=True)
        await scroll.evaluate("e=>e.scrollTop=0")
        found = {}
        self.conversation_scan_complete = False
        for _ in range(self.settings.scan_pages):
            rows = await self._list()
            for row in rows:
                self.check_account(row)
                found[row["id"]] = row
            old = set(found)
            end = await scroll.evaluate(
                "e=>{const end=e.scrollTop+e.clientHeight>=e.scrollHeight-2;e.scrollTop+=e.clientHeight;return end;}"
            )
            if end:
                self.conversation_scan_complete = True
                break
            await asyncio.sleep(0.2)
            extra = await self._list()
            if {row["id"] for row in extra}.issubset(old):
                break
        if not found:
            raise BrowserUnavailable("missing_stable_conversation_ids")
        return list(found.values())

    async def open(self, conversation: str) -> None:
        current = await self.active()
        if current and current["id"] == conversation:
            await self.guard(conversation)
            return
        await self.back()
        scroll = self.page.locator(".conversationConversationListwrapper").filter(visible=True)
        await scroll.evaluate("e=>e.scrollTop=0")
        for _ in range(self.settings.scan_pages):
            await self._list()
            item = self.page.locator(f'[data-e2e="conversation-item"][data-qyt-conversation="{conversation}"]').filter(
                visible=True
            )
            if await item.count() == 1:
                await item.click(timeout=5000)
                await self.page.locator('[data-e2e="msg-input"]').wait_for(timeout=10000)
                await self.guard(conversation)
                return
            await scroll.evaluate("e=>e.scrollTop+=e.clientHeight")
            await asyncio.sleep(0.2)
        raise BrowserUnavailable("conversation_not_loaded")

    async def messages(self, *, allow_empty: bool = False) -> list[dict]:
        rows = await self.page.locator('[data-e2e="msg-item-content"]').evaluate_all(
            "els=>{" + JS + "return els.map(message).filter(Boolean);}"
        )
        if not rows and allow_empty:
            return []
        invalid = [row for row in rows if not row["id"] or not row["created"]]
        if not rows or any(not row["from_me"] or row.get("flight") == 4 for row in invalid):
            raise BrowserUnavailable("missing_stable_message_ids")
        # 上传的乐观预览可能尚无服务端 ID；它不是已送达回执，也不能阻断等待后续确认。
        return [row for row in rows if row["id"] and row["created"]]

    async def empty_conversation(self, conversation: str) -> bool:
        # DOM 空白也可能是尚未加载；只有客户端确认没有历史消息才推进补扫游标。
        script = (
            "async args=>{"
            + JS
            + CLIENT_JS
            + """
const element = document.querySelector('.StackLayoutStackChatHeadertitle');
const {sdk, target} = nativeContext(element, args);
const result = await sdk.getMessagesByConversation({conversation: target, limit: 1});
const current = nativeContext(element, args);
return Array.isArray(result?.messages) && result.messages.length === 0 && result.hasMore === false &&
  !current.target.lastMessage && document.querySelectorAll('[data-e2e="msg-item-content"]').length === 0;
}"""
        )
        return await asyncio.wait_for(
            self.page.evaluate(script, {"account": self.account, "conversation": conversation}), timeout=10
        )

    async def collect(self, conversation: str, since: float) -> tuple[list[dict], bool]:
        await self.open(conversation)
        scroll = self.page.locator(".messageMessageListlist")
        await scroll.evaluate("e=>e.scrollTop=0")
        found = {}
        for _ in range(self.settings.scan_pages):
            rows = await self.messages(allow_empty=True)
            if not rows:
                return list(found.values()), not found and await self.empty_conversation(conversation)
            found.update((row["id"], row) for row in rows)
            if min(row["created"] for row in rows) <= since:
                return list(found.values()), True
            previous = set(found)
            await scroll.evaluate("e=>e.scrollTop=e.scrollHeight")
            await asyncio.sleep(0.4)
            more = await self.messages(allow_empty=True)
            if {row["id"] for row in more}.issubset(previous):
                return list(found.values()), False
        return list(found.values()), False

    async def _upload(self, conversation: str, files: list[Path]) -> None:
        started = time.monotonic()
        await self.page.locator("input.semi-upload-hidden-input").set_input_files(files)
        self.send_timings["upload_select"] = time.monotonic() - started
        started = time.monotonic()
        dialog = self.page.get_by_role("dialog").filter(has_text=files[0].name)
        await dialog.wait_for(timeout=10000)
        await self.guard(conversation)
        self.send_timings["upload_preview"] = time.monotonic() - started
        started = time.monotonic()
        await dialog.get_by_role("button", name="发送", exact=True).click(timeout=5000)
        self.send_timings["upload_submit"] = time.monotonic() - started
        started = time.monotonic()
        await dialog.wait_for(state="hidden", timeout=20000)
        self.send_timings["upload_close"] = time.monotonic() - started

    async def native_source(self, part: dict):
        scroll = self.page.locator(".messageMessageListlist")
        await scroll.evaluate("e=>e.scrollTop=0")
        await asyncio.sleep(0.2)
        for _ in range(self.settings.scan_pages):
            rows = await self.messages()
            matching = [row for row in rows if row["id"] == part["forward_source"]]
            if matching:
                row = matching[0]
                if (
                    row["from_me"]
                    or row.get("type") != 27
                    or row.get("live_vid", "") != part.get("live_vid", "")
                    or row.get("md5") != part["md5"][0]
                    or part.get("native_video_md5")
                    and row.get("live_md5") != part["native_video_md5"]
                ):
                    raise BrowserUnavailable("native_source_changed")
                return self.page.locator(f'[data-qyt-message="{row["id"]}"]')
            await scroll.evaluate("e=>e.scrollTop=e.scrollHeight")
            await asyncio.sleep(0.4)
        raise BrowserUnavailable("native_source_not_loaded")

    async def native_motion(self, message) -> bytes:
        await self.open(message.conversation)
        await self.guard(message.conversation)
        part = {"forward_source": message.id, "md5": [message.data["md5"]], "live_vid": message.data["live_vid"]}
        await self.native_source(part)
        request = message.data | {
            "account": self.account,
            "conversation": message.conversation,
            "sender": message.sender,
            "message": message.id,
            "max_bytes": self.settings.video_bytes,
        }
        return await read_native_video(self.page, request, JS)

    async def native_target(self, conversation: str):
        dialog = self.page.get_by_role("dialog").filter(
            has=self.page.locator("input.ShareMessageFriendPickerModalsearchInput")
        )
        await dialog.wait_for(timeout=10000)
        buttons = dialog.get_by_text("发送", exact=True).filter(visible=True)
        infos = await buttons.evaluate_all(
            "els=>{" + JS + "return els.map(e=>conversation(property(e,['conversation']),e)).filter(Boolean);}"
        )
        matching = [info for info in infos if info["id"] == conversation]
        if len(matching) != 1:
            raise BrowserUnavailable("native_recipient_not_unique")
        self.check_account(matching[0])
        target = dialog.locator(f'[data-qyt-conversation="{conversation}"]')
        if await target.count() != 1:
            raise BrowserUnavailable("native_recipient_changed")
        return dialog, target

    async def _share_native(self, conversation: str, part: dict) -> None:
        source = await self.native_source(part)
        await source.click(button="right", timeout=5000)
        await self.page.get_by_text("分享", exact=True).filter(visible=True).click(timeout=5000)
        dialog, target = await self.native_target(conversation)
        await self.guard(conversation)
        await target.click(timeout=5000)
        # 关闭分享窗口不会取消已提交的发送；仍在原会话等待包含实况视频的服务端回执。
        await self.page.keyboard.press("Escape")
        await dialog.wait_for(state="hidden", timeout=5000)

    async def send(self, conversation: str, part: dict, files: list[Path]) -> list[str]:
        self.send_timings = {}
        await self.guard(conversation)
        before = {row["id"] for row in await self.messages()}
        if part.get("live_pair"):
            request = {"part": part, "conversation": conversation, "account": self.account}
            row = await send_live_files(self.page, request, JS, files)
            self.send_timings.update(row.pop("timings", {}))
            result = acknowledged([row], part, conversation, before)
            if result:
                return result
            # SDK 可能替换缓存中的消息对象；创建时的对象尚未反映服务端回执，继续观察页面。
        elif "forward_source" in part:
            await self._share_native(conversation, part)
        elif "text" in part:
            await self.page.locator('[contenteditable="true"]').fill(part["text"])
            await self.page.locator(".e2e-send-msg-btn").click(timeout=5000)
        else:
            await self._upload(conversation, files)
        started = time.monotonic()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = acknowledged(await self.messages(), part, conversation, before)
            if result:
                self.send_timings["upload_delivery"] = time.monotonic() - started
                return result
            await asyncio.sleep(0.5)
        raise TimeoutError("delivery_ack_missing")
