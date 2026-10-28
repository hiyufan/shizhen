import { $, el } from './dom.js';
import { t } from './i18n.js';

const LABELS = { human: t('高可信真人'), unknown: t('不确定'), machine: t('自动化') };
const REASONS = {
  insufficient: t('证据不足'), unclassified: t('尚未分类'), 'automation-agent': t('自动化客户端'),
  'browser-interaction': t('签名会话与主动操作'), 'rapid-actions': t('短时间大量独立解析'),
  'historical-browser-action': t('历史页面、资源与操作记录'), 'historical-automation': t('历史自动化请求'),
  'historical-missing': t('历史日志缺失'), 'historical-ambiguous': t('历史关联不明确'),
  'historical-insufficient': t('证据不足'),
};

export function renderAudience(data) {
  const counts = data.counts;
  $('#audience-counts').textContent = t('事件数：真人 {human} · 不确定 {unknown} · 自动化 {machine}；同一 IP 可能出现在多个分类中。', {
    human: counts.human.events.toLocaleString(), unknown: counts.unknown.events.toLocaleString(),
    machine: counts.machine.events.toLocaleString(),
  });
  $('#tbl-audience tbody').replaceChildren(...data.reasons.map((item) => el('tr', {},
    el('td', {}, LABELS[item.status]), el('td', {}, REASONS[item.reason] || t('证据不足')),
    el('td', {}, item.events.toLocaleString()))));
}
