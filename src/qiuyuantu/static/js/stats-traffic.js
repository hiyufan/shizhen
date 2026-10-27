import { $, el } from './dom.js';
import { t } from './i18n.js';

const CHANNELS = {
  search: '搜索', campaign: '推广', paid: '付费推广', social: '社交', referral: '外部链接', direct: '直接 / 未知',
};
const SOURCES = { google: 'Google', baidu: '百度', bing: 'Bing', duckduckgo: 'DuckDuckGo', telegram: 'Telegram' };
const fmt = (n) => (n || 0).toLocaleString(document.documentElement.lang);

function originLabel(row) {
  const source = SOURCES[row.source] || row.source;
  const label = [t(CHANNELS[row.channel] || row.channel), t(source)].filter(Boolean).join(' · ');
  const node = el('span', {}, label);
  const detail = [row.medium, row.campaign].filter(Boolean).join(' / ');
  if (detail) node.append(el('small', { class: 'traffic-detail' }, detail));
  return node;
}

function metrics(row) {
  return [fmt(row.visits), fmt(row.parsed), fmt(row.downloaded), fmt(row.converted)];
}

export function renderTraffic(data, fillTable) {
  if (!data) return;
  const coverage = data.started
    ? t('来源记录始于 {date}；此前 {historical} 条事件未记录来源，另有 {unassigned} 条事件没有访问会话。', {
      date: new Date(data.started * 1000).toLocaleString(document.documentElement.lang),
      historical: fmt(data.historical_events), unassigned: fmt(data.unassigned_events),
    })
    : t('来源统计等待首次访问；历史来源无法还原。');
  $('#traffic-coverage').textContent = coverage;
  fillTable($('#tbl-channels tbody'), data.channels.map((row) => [
    originLabel(row), ...metrics(row), row.visits ? (100 * row.downloaded / row.visits).toFixed(1) + '%' : '–',
  ]));
  fillTable($('#tbl-landings tbody'), data.landing_pages.map((row) => [row.landing, ...metrics(row)]));
  fillTable($('#tbl-search tbody'), data.search_pages.map((row) => [
    t(SOURCES[row.source] || row.source) + ' · ' + row.landing, ...metrics(row),
  ]));
}

const form = $('#campaign-form');
form.addEventListener('submit', (event) => {
  event.preventDefault();
  const result = $('#campaign-result');
  const url = new URL($('#campaign-path').value, result.dataset.base);
  url.searchParams.set('utm_source', $('#campaign-source').value);
  url.searchParams.set('utm_medium', $('#campaign-medium').value);
  url.searchParams.set('utm_campaign', $('#campaign-name').value);
  result.value = url.href;
  result.focus();
  result.select();
  $('#campaign-status').textContent = t('链接已生成并选中，可复制后分享。');
});
