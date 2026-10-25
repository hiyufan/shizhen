"""已登录客户端的有界事件缓冲：数据库提交后才确认，不操作聊天界面。"""

import uuid

from .native_live import CLIENT_JS

EVENT_JS = r"""
const symbol = Symbol.for('qiuyuantu.inbox.events.v1');
const identity = app => String(app.store?.usersInfoStore?.curLoginUserInfo?.uid || '');
let state = window[symbol];
const scope = args.scope.slice().sort().join('|');
if (state && identity(state.app) !== args.account) throw new Error('event_account_changed');
if (args.operation === 'install') {
  if (state && state.version === 3 && state.account === args.account && state.scope === scope) {
    state.owner = args.owner;
  } else {
    const previous = state && state.account === args.account && state.scope === scope ? state : null;
    if (state) state.detach.forEach(fn => fn());
    const element = document.querySelector('[data-e2e="msg-item-content"], [data-e2e="conversation-item"]');
    const app = pageClient(element);
    if (identity(app) !== args.account) throw new Error('event_account_changed');
    const manager = app.imSdkService.imSdkManager;
    const sdk = manager.getImSdkInstance();
    if (!manager.registerEvent || !manager.unRegisterEvent ||
        !Array.isArray(manager.listenerObj?.ReceiveNewMessage)) throw new Error('event_adapter_changed');
    state = {app, version: 3, account: args.account, scope, owner: args.owner,
      generation: previous?.generation || crypto.randomUUID(), rows: previous?.rows || [],
      sequence: previous?.sequence || 0, gaps: (previous?.gaps || 0) + 1, detach: [], limit: args.limit};
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
    };
    for (const type of ['ReceiveNewMessage', 'ReceiveSelfMessage']) {
      manager.registerEvent(type, receive);
      state.detach.push(() => manager.unRegisterEvent(type, receive));
    }
    window[symbol] = state;
  }
}
if (!state || state.owner !== args.owner || state.identityChanged) throw new Error('event_owner_changed');
if (args.operation === 'ack') {
  if (state.generation !== args.generation) throw new Error('event_generation_changed');
  state.rows = state.rows.filter(item => item.sequence > args.sequence);
  if (args.clear_gaps === state.gaps) state.gaps = 0;
}
return {generation: state.generation, gaps: state.gaps, rows: state.rows.slice(0, args.batch || 100)};
"""


class EventReader:
    def __init__(self, browser):
        self.browser = browser
        self.owner = uuid.uuid4().hex
        self.scope: set[str] = set()

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
        return await self.call("install")

    async def read(self) -> dict:
        return await self.call("read")

    async def ack(self, batch: dict) -> None:
        sequence = max((item["sequence"] for item in batch["rows"]), default=0)
        await self.call("ack", generation=batch["generation"], sequence=sequence)

    async def scanned(self, batch: dict) -> None:
        await self.call("ack", generation=batch["generation"], sequence=0, clear_gaps=batch["gaps"])
