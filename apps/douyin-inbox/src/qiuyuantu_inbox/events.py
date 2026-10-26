"""已登录客户端的有界事件缓冲：数据库提交后才确认，不操作聊天界面。"""

import asyncio
import contextlib
import logging
import uuid

from .native_live import CLIENT_JS

log = logging.getLogger(__name__)

EVENT_JS = r"""
const symbol = Symbol.for('qiuyuantu.inbox.events.v1');
const identity = app => String(app.store?.usersInfoStore?.curLoginUserInfo?.uid || '');
let state = window[symbol];
const scope = args.scope.slice().sort().join('|');
if (state && identity(state.app) !== args.account) throw new Error('event_account_changed');
if (args.operation === 'install') {
  if (state && state.version === 4 && state.account === args.account && state.scope === scope) {
    state.owner = args.owner;
  } else {
    const previous = state && state.account === args.account && state.scope === scope ? state : null;
    if (state) {
      state.detach.forEach(fn => fn());
      clearTimeout(state.wakeTimer);
    }
    const element = document.querySelector('[data-e2e="msg-item-content"], [data-e2e="conversation-item"]');
    const app = pageClient(element);
    if (identity(app) !== args.account) throw new Error('event_account_changed');
    const manager = app.imSdkService.imSdkManager;
    const sdk = manager.getImSdkInstance();
    if (!manager.registerEvent || !manager.unRegisterEvent ||
        !Array.isArray(manager.listenerObj?.ReceiveNewMessage)) throw new Error('event_adapter_changed');
    state = {app, version: 4, account: args.account, scope, owner: args.owner,
      generation: previous?.generation || crypto.randomUUID(), rows: previous?.rows || [],
      sequence: previous?.sequence || 0, gaps: (previous?.gaps || 0) + 1, detach: [], limit: args.limit};
    const notify = () => {
      if (state.wakeTimer) return;
      state.wakeTimer = setTimeout(() => {
        state.wakeTimer = null;
        if (window[symbol] !== state || identity(app) !== state.account) return;
        const binding = window[state.binding];
        if (typeof binding !== 'function') return;
        // 只发送唤醒提示；消息内容仍经有界缓冲读取、数据库提交后再确认。
        Promise.resolve(binding({owner: state.owner, generation: state.generation,
          sequence: state.sequence, gaps: state.gaps})).catch(() => {});
      }, 20);
    };
    const receive = raw => {
      try {
        if (identity(app) !== state.account) {state.identityChanged = true; return;}
        const cid = String(raw?.conversationId || '');
        if (args.scope.length && !args.scope.includes(cid)) return;
        const conv = sdk.getConversation({conversationId: cid});
        const actors = cid.split(':');
        if (!conv || conv.type !== 1 || conv.id !== cid || actors.length !== 4 ||
            !actors.every(v => /^\d+$/.test(v)) || !actors.slice(2).includes(args.account) ||
            !actors.slice(2).includes(String(raw.sender))) return;
        const model = {serverId: raw.serverId, conversationId: raw.conversationId, sender: raw.sender,
          createdAt: raw.createdAt, type: raw.type, flightStatus: raw.flightStatus, serverStatus: raw.serverStatus,
          parsedContent: JSON.parse(raw.content || '{}'), isFromMe: String(raw.sender) === args.account};
        const element = {__reactFiber$event: {memoizedProps: {message: model, value: app}},
          querySelector: () => null, setAttribute: () => {}};
        const row = message(element);
        if (!row?.id || !row.created) {state.gaps++; return;}
        if (row.from_me) return;
        if (!row.kind) {state.gaps++; return;}
        if (state.rows.length >= state.limit) {state.gaps++; return;}
        state.rows.push({sequence: ++state.sequence, row});
      } catch {state.gaps++;}
      finally {if (state.rows.length || state.gaps) notify();}
    };
    for (const type of ['ReceiveNewMessage', 'ReceiveSelfMessage']) {
      manager.registerEvent(type, receive);
      state.detach.push(() => manager.unRegisterEvent(type, receive));
    }
    window[symbol] = state;
  }
  if (state.binding && state.binding !== args.binding) delete window[state.binding];
  state.binding = args.binding || '';
}
if (!state || state.owner !== args.owner || state.identityChanged) throw new Error('event_owner_changed');
if (args.operation === 'close') {
  clearTimeout(state.wakeTimer);
  state.wakeTimer = null;
  delete window[state.binding];
  state.binding = '';
}
if (args.operation === 'ack') {
  if (state.generation !== args.generation) throw new Error('event_generation_changed');
  state.rows = state.rows.filter(item => item.sequence > args.sequence);
  if (args.clear_gaps === state.gaps) state.gaps = 0;
}
return {generation: state.generation, gaps: state.gaps, rows: state.rows.slice(0, args.batch || 100),
  more: state.rows.length > (args.batch || 100)};
"""


class EventReader:
    def __init__(self, browser, wake=None):
        self.browser = browser
        self.owner = uuid.uuid4().hex
        self.scope: set[str] = set()
        self.wake = wake
        self.binding = ""
        self.generation = ""

    def notified(self, source: dict, hint: dict) -> bool:
        if source.get("page") is not self.browser.page or not isinstance(hint, dict):
            return False
        if hint.get("owner") == self.owner and hint.get("generation") == self.generation and self.wake:
            self.wake()
            return True
        return False

    async def call(self, operation: str, **kwargs) -> dict:
        from .browser import JS

        args = {
            "operation": operation,
            "account": self.browser.account,
            "scope": sorted(self.scope),
            "owner": self.owner,
            "limit": self.browser.settings.event_buffer,
        } | kwargs
        script = "args=>{" + JS + CLIENT_JS + EVENT_JS + "}"
        return await self.browser.page.evaluate(script, args)

    async def install(self, scope: set[str]) -> dict:
        self.scope = scope
        if self.wake and not self.binding:
            name = "__qiuyuantuInboxWake_" + self.owner
            try:
                await self.browser.page.expose_binding(name, self.notified)
                self.binding = name
            except Exception as exc:
                log.warning("inbox wake unavailable, using event polling: %s", type(exc).__name__)
        batch = await self.call("install", binding=self.binding)
        self.generation = batch["generation"]
        return batch

    async def read(self) -> dict:
        return await self.call("read")

    async def ack(self, batch: dict) -> None:
        sequence = max((item["sequence"] for item in batch["rows"]), default=0)
        await self.call("ack", generation=batch["generation"], sequence=sequence)

    async def scanned(self, batch: dict) -> None:
        await self.call("ack", generation=batch["generation"], sequence=0, clear_gaps=batch["gaps"])

    async def close(self) -> None:
        self.wake = None
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.call("close"), timeout=self.browser.settings.connect_timeout)
