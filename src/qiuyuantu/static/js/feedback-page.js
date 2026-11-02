// 意见反馈页：提交表单；从解析结果点进来时默认附上刚才解析的链接，访客可以取消勾选

import { postJSON } from './api.js';
import { t } from './i18n.js';
import { forgetParsedLink, parsedLink } from './report-link.js';

const form = document.getElementById('feedback-form');
const error = document.getElementById('fb-error');
const submit = form.querySelector('button[type="submit"]');

// 只记来源页的站内路径；查询参数里可能有 ?url= 带着的分享链接
function sourcePage() {
  try {
    const ref = new URL(document.referrer);
    return ref.origin === location.origin ? ref.pathname : '';
  } catch {
    return '';
  }
}

function showAttachment() {
  const link = form.dataset.fromResult ? parsedLink() : '';
  if (!link) return;
  document.getElementById('fb-attach-link').textContent = link;
  document.getElementById('fb-attach').hidden = false;
}

function showError(message, field) {
  error.textContent = message;
  error.hidden = false;
  if (field) field.focus();
}

function payload() {
  const data = new FormData(form);
  const attach = document.getElementById('fb-attach-box').checked;
  return {
    kind: data.get('kind') || '',
    body: String(data.get('body') || '').trim(),
    email: String(data.get('email') || '').trim(),
    page: sourcePage(),
    link: attach ? parsedLink() : '',
    token: form.dataset.token,
  };
}

function showThanks(leftEmail) {
  const thanks = document.getElementById('fb-thanks');
  if (leftEmail) document.getElementById('fb-thanks-text').textContent = t('站长会尽快查看，需要时会回复到你的邮箱。');
  form.hidden = true;
  thanks.hidden = false;
  thanks.focus();
  forgetParsedLink();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  const data = payload();
  error.hidden = true;
  if (!data.body) {
    showError(t('写一下遇到了什么情况'), document.getElementById('fb-body'));
    return;
  }
  const email = document.getElementById('fb-email');
  if (data.email && !email.checkValidity()) {
    showError(t('邮箱格式不对'), email);
    return;
  }
  submit.disabled = true;
  try {
    const result = await postJSON('/api/feedback/message', data);
    showThanks(result.email);
  } catch (err) {
    showError(err.message);
    submit.disabled = false;
  }
});

showAttachment();
