// 本地动画复用后台准备任务；Spine ZIP 先选择真实存在的动画，再进入转换器。
import { api, postJSON, watchJob } from './api.js';
import { openConverter } from './converter.js';
import { $, el, notice } from './dom.js';
import { serverMessage, t } from './i18n.js';

let version = 0;
let pending = null;

export function cancelUpload() {
  version += 1;
  if (pending) pending.abort();
  pending = null;
}

async function showPrepared(response, current) {
  const source = response.ready ? response.source : (await watchJob(response.job, job => {
    if (current === version) notice(serverMessage(job.message), 'info');
  })).extra;
  if (current !== version) return;
  notice('');
  openConverter({ ready: source }, 'gif');
}

function chooseAnimation(response, current) {
  notice(t('选择一个动画，再开始转换。'), 'info');
  const select = el('select', { 'aria-label': t('选择动画') },
    ...response.animations.map(name => el('option', { value: name }, name)));
  const button = el('button', { type: 'button', class: 'btn sm dark' }, t('开始转换'));
  const controls = el('div', { class: 'group' }, el('div', { class: 'field animation-choice' }, select), button);
  button.addEventListener('click', async () => {
    if (current !== version || button.disabled) return;
    button.disabled = true;
    select.disabled = true;
    try {
      const ready = await postJSON('/api/animation/prepare', { upload_id: response.animation_upload, animation: select.value });
      await showPrepared(ready, current);
    } catch (error) {
      if (current === version) {
        notice(t('上传失败：') + error.message);
        $('#notice').append(controls);
        button.disabled = false;
        select.disabled = false;
      }
    }
  });
  $('#notice').append(controls);
}

export async function uploadFile(file) {
  cancelUpload();
  const current = version;
  pending = new AbortController();
  notice(t('正在上传 ') + file.name + '…', 'info');
  const body = new FormData();
  body.append('file', file);
  try {
    const response = await api('/api/upload', { method: 'POST', body, signal: pending.signal });
    if (current !== version) return;
    if (response.animations) chooseAnimation(response, current);
    else await showPrepared(response, current);
  } catch (error) {
    if (current === version) notice(t('上传失败：') + error.message);
  }
}
