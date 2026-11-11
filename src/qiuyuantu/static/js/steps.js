// 视频结果的「下一步」：主按钮下面一排小标签，点一个只展开它自己的面板。
// 换标签会替换掉当前面板，再点同一个就收起，结果区不会越点越长。
// 标签是展开 / 收起按钮（aria-expanded），不是 tablist：允许全部收起，也不需要方向键切换。

import { t } from './i18n.js';

import { el } from './dom.js';

let panels = 0;

/**
 * items：[{ key, label, open(body) }]，open 往 body 里放内容，可返回清理函数（收起或换标签时调用）。
 * 带 controls 的项不用这里的面板（转换器在结果下方另开一块），open() 不带参数，controls 是那块的 id。
 */
export function stepTabs(items) {
  const id = 'step-panel-' + ++panels;
  const panel = el('section', { class: 'step-panel', id, hidden: true });
  const buttons = new Map();
  let active = null;
  let cleanup = null;

  function close() {
    if (cleanup) cleanup();
    cleanup = null;
    if (active) buttons.get(active.key).setAttribute('aria-expanded', 'false');
    active = null;
    panel.hidden = true;
    panel.replaceChildren();
  }

  function collapse() {
    const button = active && buttons.get(active.key);
    close();
    if (button) button.focus(); // 面板里的焦点随面板一起消失，交还给标签
  }

  function open(item) {
    close();
    active = item;
    buttons.get(item.key).setAttribute('aria-expanded', 'true');
    if (item.controls) {
      cleanup = item.open() || null;
      return;
    }
    const body = el('div', { class: 'step-body' });
    const hide = el('button', { class: 'step-close', type: 'button', 'aria-label': t('收起'), onclick: collapse }, '×');
    panel.setAttribute('aria-label', item.label);
    panel.replaceChildren(el('div', { class: 'step-head' }, el('b', {}, item.label), hide), body);
    panel.hidden = false;
    cleanup = item.open(body) || null;
  }

  for (const item of items) {
    buttons.set(item.key, el('button', {
      class: 'btn sm', type: 'button', 'aria-expanded': 'false', 'aria-controls': item.controls || id,
      onclick: () => (active === item ? close() : open(item)),
    }, item.label));
  }

  return {
    element: items.length ? el('div', { class: 'steps' }, el('div', { class: 'step-tabs' }, ...buttons.values()), panel) : null,
    /** 默认展开某一项（如没有主下载按钮时直接给出清晰度列表） */
    open(key) {
      const item = items.find((i) => i.key === key);
      if (item) open(item);
    },
    /** 数据变了（找到更高画质）：这一项正开着就按新数据重画 */
    refresh(key) {
      if (active?.key === key) open(active);
    },
  };
}
