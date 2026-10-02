// 页面上的小部件：后台任务的进度卡片、解析失败后的「反馈这个问题」

import { postJSON } from './api.js';
import { el } from './dom.js';

/** 任务卡片：进度条 + 状态文字，做完换成保存按钮（iPhone 上前面多一个「存到相册」） */
export function jobCard(container, title) {
  const bar = el('div', { class: 'bar-fill' });
  const status = el('small', {}, '排队中');
  const card = el('div', { class: 'job' },
    el('div', { class: 'row' }, el('b', {}, title), status),
    el('div', { class: 'bar-track' }, bar));
  container.prepend(card);
  return {
    card,
    update(job) {
      const pct = Math.round((job.progress || 0) * 100);
      bar.style.width = pct + '%';
      status.textContent = (job.message || (job.status === 'queued' ? '排队中' : '处理中')) + ' ' + pct + '%';
    },
    done(text, href, filename, photos) {
      bar.style.width = '100%';
      const link = el('a', { class: 'btn sm' + (photos ? '' : ' dark'), href, download: filename || '' }, text);
      status.replaceWith(photos ? el('div', { class: 'group' }, photos, link) : link);
    },
    fail(msg) {
      card.classList.add('err');
      status.textContent = msg;
    },
  };
}

// 服务器只给「修得好」的失败发凭证（解析出错 / 拿到空的 / 不支持 / 超时）；删了、平台限制这些没有反馈按钮。
// 提交后在 GitHub 建 issue，修好了服务器先复测再发邮件，邮箱加密存储、发完即删
export function feedbackBox(ticket) {
  const box = el('div', { class: 'fb' });
  const email = el('input', {
    type: 'email', placeholder: '邮箱（选填）', autocomplete: 'email', maxlength: '254', 'aria-label': '邮箱（选填）',
  });
  const send = el('button', { class: 'btn sm dark', type: 'submit' }, '提交反馈');
  const err = el('p', { class: 'fb-err', hidden: true });
  const form = el('form', { class: 'fb-form', hidden: true },
    el('div', { class: 'fb-row' }, email, send),
    err,
    el('details', { class: 'fb-more' },
      el('summary', {}, '🔒 加密存储 · 仅用于本次修复通知 · 发送即销毁'),
      el('p', {}, '邮箱经 HTTPS 传输，服务器以 AES-256 加密保存，只用来告诉你这个问题修好了；'
        + '邮件发出后立即销毁，最长保留 90 天，不会出现在任何公开位置。')));
  const open = el('button', {
    class: 'btn sm',
    type: 'button',
    onclick: () => { open.hidden = true; form.hidden = false; email.focus(); },
  }, '反馈这个问题');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    send.disabled = true;
    err.hidden = true;
    try {
      const r = await postJSON('/api/feedback', { ticket, email: email.value.trim() });
      const head = r.duplicate ? '这个问题已经有人反馈过，正在修' : '收到了，谢谢';
      box.replaceChildren(el('p', { class: 'fb-done' }, head + (r.email ? '，修好后会发邮件告诉你。' : '。')));
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
      send.disabled = false;
    }
  });
  box.append(open, form);
  return box;
}
