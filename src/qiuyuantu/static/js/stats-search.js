import { $, el } from './dom.js';
import { api } from './api.js';
import { t } from './i18n.js';

const STATES = {
  unconfigured: '未接入', pending: '等待同步', syncing: '同步中', ready: '已连接', stale: '保留上次报表', error: '同步失败',
};
const ERRORS = {
  authorization: '报表授权不可用，请检查服务端配置和站点权限。',
  rate_limit: '报表请求达到平台限制，稍后自动重试。',
  timeout: '报表同步超时，稍后自动重试。',
  pending: '平台正在准备报表，稍后自动重试。',
  wrong_site: '返回的站点与绑定站点不一致，请检查配置。',
  too_large: '报表超过读取上限，本次未更新。',
};
const format = (n) => n == null ? '–' : n.toLocaleString(document.documentElement.lang, { maximumFractionDigits: 1 });
const percent = (n) => n == null ? '–' : (n * 100).toFixed(2) + '%';
const metricValue = (key, value) => key === 'ctr' ? percent(value) : format(value);

function table(card, kind, rows, google) {
  const target = card.querySelector('[data-search-table="' + kind + '"] tbody');
  if (!target) return;
  target.replaceChildren();
  for (const row of rows) {
    const values = [row.label ?? row.date, format(row.impressions), format(row.clicks), percent(row.ctr)];
    if (google) values.push(format(row.position));
    target.append(el('tr', {}, ...values.map((value) => el('td', {}, value))));
  }
  if (!rows.length) target.append(el('tr', {}, el('td', { colspan: google ? 5 : 4, class: 'muted' }, t('暂无报表记录'))));
}

function comparison(report) {
  if (!report.previous) return t('前一期间没有可比较的报表记录。');
  return t('前一期间 {start} 至 {end}：展现 {impressions}，点击 {clicks}。', {
    start: report.previous_from, end: report.previous_to,
    impressions: format(report.previous.impressions), clicks: format(report.previous.clicks),
  });
}

function statusMessage(source) {
  if (source.state === 'unconfigured') return t('尚未配置此平台的报表连接。');
  const error = source.error ? t(ERRORS[source.error] || '报表暂时无法同步，稍后自动重试。') : '';
  if (error && !source.report) return error;
  if (!source.report) return t('等待首次同步完成。');
  const date = new Date(source.report.synced * 1000).toLocaleString(document.documentElement.lang);
  const synced = source.report.latest ? t('上次同步：{date}', { date }) : t('上次同步：{date}，平台尚未返回报表记录。', { date });
  return [error, synced].filter(Boolean).join(' ');
}

function renderProvider(source) {
  const card = $('#search-' + source.provider);
  if (!card) return;
  card.querySelector('[data-search-state]').textContent = t(STATES[source.state] || '等待报表');
  card.querySelector('[data-search-account]').textContent = source.account || '';
  card.querySelector('[data-search-message]').textContent = statusMessage(source);
  const report = source.report;
  card.querySelector('[data-search-content]').hidden = !report?.totals;
  if (!report?.totals) return;
  for (const node of card.querySelectorAll('[data-search-metric]')) {
    const key = node.dataset.searchMetric;
    node.textContent = metricValue(key, report.totals[key]);
  }
  card.querySelector('[data-search-period]').textContent = t('报表日期：{start} 至 {end}；最新记录：{latest}。', {
    start: report.date_from, end: report.date_to, latest: report.latest,
  });
  card.querySelector('[data-search-comparison]').textContent = comparison(report);
  card.querySelector('[data-search-limited]').hidden = !report.limited;
  for (const kind of ['queries', 'pages', 'daily']) table(card, kind, report[kind], source.provider === 'google');
}

const token = $('[data-token]').dataset.token;
const selector = $('#search-days');
let controller = null;
let generation = 0;

async function load() {
  const current = ++generation;
  controller?.abort();
  controller = new AbortController();
  $('#search-performance').setAttribute('aria-busy', 'true');
  $('#search-status').textContent = t('正在加载搜索报表…');
  try {
    const data = await api('/api/stats/search?days=' + selector.value, {
      signal: controller.signal, headers: { Authorization: 'Bearer ' + token },
    });
    if (current !== generation) return;
    for (const source of data.providers) renderProvider(source);
    $('#search-status').textContent = t('搜索报表已更新。');
  } catch (error) {
    if (current === generation && error.name !== 'AbortError') {
      $('#search-status').textContent = t('搜索报表加载失败，请稍后重试。');
    }
  } finally {
    if (current === generation) $('#search-performance').setAttribute('aria-busy', 'false');
  }
}

export function initSearchReports() {
  selector.addEventListener('change', () => {
    for (const content of document.querySelectorAll('[data-search-content]')) content.hidden = true;
    load();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
  setInterval(() => { if (!document.hidden) load(); }, 60000);
  load();
}
