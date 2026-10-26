"""页面内共用文件交接预算；排队时尚未把文件交给页面，取消后等实际上传结束才释放。"""

BUDGET_JS = r"""
const uploadUsage = state => {
  const tickets = [...state.uploadBudget.active];
  return {count: tickets.length, bytes: tickets.reduce((sum, ticket) => sum + ticket.bytes, 0)};
};
const fitsUploadBudget = (state, ticket) => {
  const used = uploadUsage(state);
  return used.count < state.uploadLimit && used.bytes + ticket.bytes <= state.uploadBytes;
};
const pumpUploadBudget = state => {
  const budget = state.uploadBudget;
  budget.waiters = budget.waiters.filter(waiter => {
    try {bridgeTicket(state, waiter.ticket); return true;}
    catch (error) {waiter.reject(error); return false;}
  });
  budget.waiters.sort((left, right) => Number(left.ticket.warm) - Number(right.ticket.warm));
  while (budget.waiters.length && fitsUploadBudget(state, budget.waiters[0].ticket)) {
    const waiter = budget.waiters.shift();
    budget.active.add(waiter.ticket);
    waiter.resolve(true);
  }
};
const releaseUploadBudget = (state, ticket) => {
  state.uploadBudget.active.delete(ticket);
  pumpUploadBudget(state);
};
const reserveUpload = async (state, args) => {
  if (state.uploads.has(args.ticket)) throw new Error('outbound_upload_already_attached');
  if (!Number.isSafeInteger(args.bytes) || args.bytes <= 0 || args.bytes > state.uploadBytes) {
    throw new Error('outbound_upload_budget_exceeded');
  }
  const ticket = {owner: args.owner, deadline: Date.now() + args.timeout, bytes: args.bytes,
    part: args.part, warm: !!args.warm, running: false, cacheHits: 0, uploadSeconds: 0};
  // 预热不排队：正式任务等待或预算已满时，直接留给用户选择后的正式流程。
  if (ticket.warm && (state.uploadBudget.waiters.length || !fitsUploadBudget(state, ticket))) return false;
  state.uploads.set(args.ticket, ticket);
  const admitted = new Promise((resolve, reject) => {
    state.uploadBudget.waiters.push({ticket, resolve, reject});
  });
  pumpUploadBudget(state);
  await admitted;
  bridgeTicket(state, ticket);
  return true;
};
const cancelUpload = (state, ticket) => {
  ticket.cancelled = true;
  // 不能因 Python 超时就释放预算：页面上传器可能仍持有 File，另一张实况文件也可能仍在传。
  if (!ticket.running) releaseUploadBudget(state, ticket);
};
"""
