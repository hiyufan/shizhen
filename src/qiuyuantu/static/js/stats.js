// 使用统计页（/stats）：拉 /api/stats，画两张柱状图和几张表，每分钟刷新一次（页面在后台时暂停）

import { $, el } from './dom.js';

const TOKEN = $('[data-token]').dataset.token;
const REFRESH_MS = 60000;
const SVG_NS = 'http://www.w3.org/2000/svg';

const REASONS = {
  deleted: '内容已删除 / 链接过期', login: '平台要求登录', blocked: '被平台风控', unsupported: '不支持的链接',
  network: '网络错误', timeout: '超时', empty: '没有媒体', restricted: '平台不给这条的数据', copyright: '版权内容', parse: '页面结构变了',
  cache: '缓存', '': '未知',
};
const JOBS = { prepare: '准备原视频', gif: 'GIF', livephoto: '实况照片', motionphoto: '动态照片', download: '高清下载', live: '实况打包' };
const PLATFORMS = {
  douyin: '抖音', redbook: '小红书', kuaishou: '快手', youtube: 'YouTube', twitter: 'X', bilibili: 'B站', weibo: '微博',
  tiktok: 'TikTok', instagram: 'Instagram', xigua: '西瓜视频', pipixia: '皮皮虾', acfun: 'AcFun', ytdlp: '其它（yt-dlp）',
  '': '未识别',
};
const BLUE = '#4A94CC';
const ORANGE = '#E8702A';

const fmt = (n) => (n || 0).toLocaleString('zh-CN');
const pct = (ok, n) => (n ? Math.round((ok / n) * 100) + '%' : '–');
const ms = (v) => (!v ? '–' : v < 1000 ? v + ' ms' : (v / 1000).toFixed(1) + ' s');
const pad2 = (n) => String(n).padStart(2, '0');

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

/** 一个桶的时间标签：短的放 x 轴（「14时」「3/5」），长的放提示框（「3月5日 14:00–15:00」） */
function bucketLabel(t, step, long) {
  const d = new Date(t * 1000);
  const day = d.getMonth() + 1 + (long ? '月' + d.getDate() + '日' : '/' + d.getDate());
  if (step >= 86400) return day;
  const h = d.getHours();
  if (long) return day + ' ' + pad2(h) + ':00–' + pad2((h + 1) % 24) + ':00';
  return h === 0 ? day : h + '时';
}

function niceMax(v) {
  if (v <= 4) return 4;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

// ---------- 柱状图：单序列或两段堆叠；每根柱子的整个槽位都是命中区，hover / 焦点出提示

const tip = $('#tip');

function showTip(target, s, step, rows) {
  tip.replaceChildren(el('b', {}, bucketLabel(s.t, step, true) + (s.partial ? '（进行中）' : '')));
  for (const [label, value, color] of rows) {
    tip.append(el('div', { class: 'row' },
      color ? el('i', { style: 'background:' + color }) : null, el('span', {}, value), el('small', {}, label)));
  }
  tip.hidden = false;
  const r = target.getBoundingClientRect();
  const wrap = tip.offsetParent.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left + r.width / 2 - wrap.left - tip.offsetWidth / 2), wrap.width - tip.offsetWidth - 8);
  tip.style.left = left + 'px';
  tip.style.top = r.top - wrap.top - tip.offsetHeight - 8 + 'px';
}

/** 圆角柱子的一段（只有顶上两个角是圆的） */
function barPath(x0, top, bottom, bw) {
  const r = Math.min(4, bottom - top, bw / 2);
  const x1 = x0 + bw;
  return 'M' + x0 + ',' + bottom + 'V' + (top + r) + 'Q' + x0 + ',' + top + ' ' + (x0 + r) + ',' + top
    + 'H' + (x1 - r) + 'Q' + x1 + ',' + top + ' ' + x1 + ',' + (top + r) + 'V' + bottom + 'Z';
}

function chart(svg, series, step, { keys, colors, rows }) {
  const W = Math.max(320, svg.clientWidth || 720);
  const H = 220;
  const pad = { l: 40, r: 12, t: 18, b: 28 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;
  const slot = plotW / series.length;
  const bw = Math.max(2, Math.min(24, slot - 2));
  const totals = series.map((s) => keys.reduce((a, k) => a + (s[k] || 0), 0));
  const max = niceMax(Math.max(...totals, 0));
  const y = (v) => pad.t + plotH - (v / max) * plotH;

  svg.replaceChildren();
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  for (const t of max <= 4 ? [0, 1, 2, 3, 4] : [0, max / 4, max / 2, (max * 3) / 4, max]) {
    svg.append(svgEl('line', { x1: pad.l, x2: W - pad.r, y1: y(t), y2: y(t), class: 'grid' }));
    svg.append(svgEl('text', { x: pad.l - 8, y: y(t) + 4, class: 'tick', 'text-anchor': 'end' }, fmt(t)));
  }
  // x 轴：标签隔几个显示一个，别挤成一团
  const every = Math.max(1, Math.ceil(series.length / Math.floor(plotW / 56)));
  series.forEach((s, i) => {
    if (i % every) return;
    svg.append(svgEl('text', { x: pad.l + slot * i + slot / 2, y: H - 8, class: 'tick', 'text-anchor': 'middle' }, bucketLabel(s.t, step)));
  });

  const peak = totals.indexOf(Math.max(...totals));
  series.forEach((s, i) => {
    const x0 = pad.l + slot * i + (slot - bw) / 2;
    const g = svgEl('g', {
      class: 'bar' + (s.partial ? ' partial' : ''), tabindex: '0', role: 'img',
      'aria-label': bucketLabel(s.t, step, true) + '：' + rows(s).map((r) => r[0] + ' ' + r[1]).join('，'),
    });
    let base = 0;
    keys.forEach((k, ki) => {
      const v = s[k] || 0;
      if (!v) return;
      const top = y(base + v);
      const bottom = y(base) - (base ? 2 : 0); // 段与段之间留 2px 底色
      if (bottom > top) g.append(svgEl('path', { d: barPath(x0, top, bottom, bw), fill: colors[ki] }));
      base += v;
    });
    if (i === peak && totals[i] > 0) {
      g.append(svgEl('text', { x: x0 + bw / 2, y: y(totals[i]) - 6, class: 'cap-label', 'text-anchor': 'middle' }, fmt(totals[i])));
    }
    g.append(svgEl('rect', { x: pad.l + slot * i, y: pad.t, width: slot, height: plotH, fill: 'transparent' }));
    const show = () => showTip(g, s, step, rows(s));
    const hide = () => { tip.hidden = true; };
    g.addEventListener('pointerenter', show);
    g.addEventListener('focus', show);
    g.addEventListener('pointerleave', hide);
    g.addEventListener('blur', hide);
    svg.append(g);
  });
}

// ---------- 数字和表格

function shortTime(ts) {
  const d = new Date(ts * 1000);
  return d.getMonth() + 1 + '/' + d.getDate() + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}

/** 失败链接：点了在新标签页里用它重新解析（?url= 会自动提交） */
function failureRow(f) {
  const link = el('a', { href: '/?url=' + encodeURIComponent(f.link), target: '_blank', rel: 'noopener', class: 'mono' },
    f.link.length > 70 ? f.link.slice(0, 70) + '…' : f.link);
  const reason = el('span', { title: f.msg }, REASONS[f.reason] || f.reason);
  return [shortTime(f.ts), PLATFORMS[f.platform] || f.platform, reason, fmt(f.n), link];
}

function fillTable(tbody, rows) {
  if (!rows.length) {
    tbody.replaceChildren(el('tr', {}, el('td', { colspan: 8, class: 'muted' }, '暂无')));
    return;
  }
  tbody.replaceChildren(...rows.map((r) => el('tr', {}, ...r.map((c) => el('td', {}, c)))));
}

function fillTotals(t) {
  const set = (key, value) => { $('[data-k="' + key + '"]').textContent = value; };
  set('users', fmt(t.users));
  set('parse', fmt(t.parse));
  set('parse_rate', pct(t.parse_ok, t.parse));
  set('job', fmt(t.job));
  set('job_rate', pct(t.job_ok, t.job));
  set('download', fmt(t.download));
}

let last = null;

function render(d) {
  last = d;
  const series = d.series.map((s) => ({ ...s, fail: s.parse - s.parse_ok, partial: s.t + d.step > d.until }));
  $('#empty').hidden = d.first != null;
  fillTotals(d.totals);

  const unit = d.step < 86400 ? '每小时' : '每天';
  $('#c1-title').textContent = unit + '有多少人';
  $('#c2-title').textContent = unit + '解析次数';
  chart($('#c1'), series, d.step, {
    keys: ['users'], colors: [BLUE],
    rows: (s) => [['人', fmt(s.users), BLUE], ['次解析', fmt(s.parse)], ['个任务', fmt(s.job)], ['次保存', fmt(s.download)], ['次浏览', fmt(s.view)]],
  });
  chart($('#c2'), series, d.step, {
    keys: ['parse_ok', 'fail'], colors: [BLUE, ORANGE],
    rows: (s) => [['成功', fmt(s.parse_ok), BLUE], ['失败', fmt(s.fail), ORANGE], ['人', fmt(s.users)]],
  });

  fillTable($('#tbl-series tbody'), series.slice().reverse().map((s) => [
    bucketLabel(s.t, d.step, true) + (s.partial ? ' ·' : ''),
    fmt(s.users), fmt(s.parse), fmt(s.parse_ok), fmt(s.fail), fmt(s.job), fmt(s.download), fmt(s.view),
  ]));
  fillTable($('#tbl-sources tbody'), d.sources.map((s) => [PLATFORMS[s.source] || s.source, fmt(s.n), pct(s.ok, s.n), fmt(s.users), ms(s.ms)]));
  fillTable($('#tbl-reasons tbody'), d.reasons.map((r) => [REASONS[r.reason] || r.reason, fmt(r.n)]));
  fillTable($('#tbl-jobs tbody'), d.jobs.map((j) => [JOBS[j.type] || j.type, fmt(j.n), pct(j.ok, j.n), ms(j.ms)]));
  fillTable($('#tbl-failures tbody'), (d.failures || []).map(failureRow));

  const now = new Date();
  const since = d.first ? ' · 记录始于 ' + new Date(d.first * 1000).toLocaleDateString('zh-CN') : '';
  $('#updated').textContent = '更新于 ' + pad2(now.getHours()) + ':' + pad2(now.getMinutes()) + since;
}

// ---------- 时间范围切换、定时刷新

const tabs = $('#ranges');
let range = new URLSearchParams(location.search).get('range') || '24h';
if (!tabs.querySelector('button[data-range="' + range + '"]')) range = '24h';

function selectTab(active) {
  for (const b of tabs.children) b.setAttribute('aria-selected', String(b.dataset.range === active));
}

async function load() {
  const query = '?range=' + range + '&tz=' + new Date().getTimezoneOffset() + '&token=' + encodeURIComponent(TOKEN);
  const res = await fetch('/api/stats' + query);
  if (!res.ok) {
    $('#updated').textContent = '加载失败 ' + res.status;
    return;
  }
  render(await res.json());
}

tabs.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  range = b.dataset.range;
  selectTab(range);
  const charts = document.querySelectorAll('.chart');
  charts.forEach((c) => c.classList.add('loading'));
  load().finally(() => charts.forEach((c) => c.classList.remove('loading')));
});

let resizeTimer;
addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => last && render(last), 150);
});

let timer = null;
function startRefresh() {
  load();
  timer = setInterval(load, REFRESH_MS);
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearInterval(timer);
    timer = null;
  } else if (!timer) {
    startRefresh();
  }
});

selectTab(range);
startRefresh();
