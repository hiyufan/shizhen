"""关注关系只在登录页面内读写；仅导出稳定 ID、关系状态与分页游标。"""

import asyncio
import time
import uuid

from .native_live import CLIENT_JS

FOLLOW_JS = r"""
const app = pageClient(document.querySelector('[data-e2e="msg-item-content"], [data-e2e="conversation-item"]'));
const account = () => {
  const user = app.store.usersInfoStore?.curLoginUserInfo;
  if (String(user?.uid || '') !== args.account) throw new Error('follow_account_changed');
  return user;
};
const body = raw => raw?.data?.status_code !== undefined ? raw.data : raw;
const checked = raw => {
  account();
  const result = body(raw);
  if (result?.status_code !== 0) throw new Error('follow_read_rejected');
  return result;
};
const userRow = user => {
  const uid = String(user?.uid || ''), sec = String(user?.sec_uid || '');
  if (!/^\d{1,32}$/.test(uid) || uid === args.account || !/^[A-Za-z0-9_-]{1,256}$/.test(sec) ||
      !Number.isInteger(user.follow_status) || ![0, 1].includes(user.follower_status)) {
    throw new Error('follow_user_adapter_changed');
  }
  return {uid, sec_uid: sec, following: user.follow_status, follower: user.follower_status,
    blocked: !!(user.is_block || user.is_blocked || user.is_ban || user.user_canceled)};
};
const request = async options => checked(await app.requestManager.request({...options, noStatusCode: true}));
const relation = async () => {
  account();
  const result = await request({method: 'GET', url: '/aweme/v1/web/user/profile/other/',
    data: {sec_user_id: args.user.sec_uid}});
  const row = userRow(result.user);
  if (row.uid !== args.user.uid || row.sec_uid !== args.user.sec_uid) throw new Error('follow_target_changed');
  return row;
};
const terminal = row => {
  if (row.blocked || row.follower !== 1) return 'skipped';
  if (row.following === 2) return 'mutual';
  if (row.following === 1) return 'followed';
  return row.following > 0 ? 'requested' : '';
};
const symbol = Symbol.for('qiuyuantu.inbox.follow.v1');
const owner = () => {
  account();
  const state = window[symbol];
  if (!state || state.owner !== args.owner || state.closed) throw new Error('follow_owner_changed');
  return state;
};
account();
if (args.operation === 'install') {
  let state = window[symbol];
  if (state?.active) throw new Error('follow_previous_request_active');
  state = state || {};
  Object.assign(state, {owner: args.owner, closed: false});
  window[symbol] = state;
  return true;
}
if (args.operation === 'page') {
  const user = account();
  const result = await request({method: 'GET', url: '/aweme/v1/web/user/follower/list/', data: {
    user_id: args.account, sec_user_id: String(user.secUid || user.sec_uid || ''),
    count: 20, max_time: args.cursor, min_time: 0, offset: 0, source_type: 1}});
  if (String(result.myself_user_id) !== args.account || !Array.isArray(result.followers) ||
      result.followers.length > 20 || ![false, true, 0, 1].includes(result.has_more)) {
    throw new Error('follow_page_adapter_changed');
  }
  // min_time 是本页最早的关系时间，下一页用作 max_time；max_time 是页的上界，不能拿来续页。
  const cursor = Number(result.min_time), more = !!result.has_more;
  if (!Number.isSafeInteger(cursor) || cursor < 0 || more && cursor >= args.cursor) {
    throw new Error('follow_cursor_did_not_advance');
  }
  const rows = result.followers.map(userRow);
  return {users: [...new Map(rows.map(row => [row.uid, row])).values()], cursor, more};
}
if (args.operation === 'relation') return await relation();
const state = owner();
if (args.operation === 'close') {state.closed = true; return true;}
if (args.operation === 'idle') return !state.active;
if (args.operation !== 'follow' || !args.token || state.active) throw new Error('follow_operation_invalid');
state.active = args.token;
const guard = () => {
  if (owner() !== state || Date.now() > args.deadline || state.active !== args.token) {
    throw new Error('follow_submission_expired');
  }
};
try {
  const before = await relation();
  guard();
  if (terminal(before)) return {state: terminal(before)};
  // 与当前网页关注按钮相同的请求管理器及参数；只设为关注，绝不切换或取关。
  const response = body(await app.requestManager.request({method: 'POST',
    url: '/aweme/v1/web/commit/follow/user/', data: {user_id: before.uid, type: '1'}, noStatusCode: true,
    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
      'x-secsdk-csrf-token': 'DOWNGRADE'}}));
  guard();
  const after = await relation();
  guard();
  const status = terminal(after);
  if (status) return {state: status};
  if (Number.isInteger(response?.status_code) && response.status_code !== 0) {
    return {state: 'rejected', code: response.status_code};
  }
  return {state: 'uncertain'};
} finally {
  if (state.active === args.token) state.active = '';
}
"""


class FollowBrowser:
    def __init__(self, browser):
        self.browser = browser
        self.owner = uuid.uuid4().hex

    async def call(self, operation: str, **kwargs):
        args = {
            "operation": operation,
            "account": self.browser.account,
            "owner": self.owner,
        } | kwargs
        script = "async args=>{" + CLIENT_JS + FOLLOW_JS + "}"
        return await asyncio.wait_for(
            self.browser.page.evaluate(script, args), timeout=self.browser.settings.follow_timeout
        )

    async def follow(self, job: dict) -> dict:
        deadline = time.time() * 1000 + self.browser.settings.follow_timeout * 1000
        return await self.call("follow", user=job, token=job["token"], deadline=deadline)
