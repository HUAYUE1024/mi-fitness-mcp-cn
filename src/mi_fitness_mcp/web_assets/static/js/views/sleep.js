import { html } from '../lib/dom.js';
import { chart, hypnogram, stack } from '../lib/charts.js';
import { raw } from '../lib/dom.js';
import { get } from '../lib/api.js';
import { num, duration, hm, shortDate, weekday } from '../lib/format.js';
import { empty, on } from '../lib/ui.js';
import { state } from '../state.js';

const COLORS = {
  deep: 'var(--c-sleep)',
  rem: 'var(--c-oxygen)',
  light: 'color-mix(in srgb, var(--c-sleep) 42%, var(--surface-3))',
  awake: 'var(--c-workout)',
};
const NAMES = { deep: '深睡', rem: 'REM', light: '浅睡', awake: '清醒' };
const ui = { selected: null };

const nightDate = (s) => s.end_at.slice(0, 10);

export default {
  id: 'sleep',
  title: '睡眠',
  subtitle: () => '时长、分期与作息规律',
  range: true,

  init(root) {
    on(root, 'click', '[data-night]', (e, row) => { ui.selected = row.dataset.night; this.load(root); });
  },

  async load(root) {
    const { start, end } = state.range;
    const [list, sum] = await Promise.all([
      get('/api/sleep', { start_date: start, end_date: end, include_naps: false }),
      get('/api/sleep-summary', { start_date: start, end_date: end }),
    ]);
    const nights = list.ok ? list.data : [];
    if (!nights.length) {
      root.innerHTML = String(html`<section class="card">${empty(list.ok ? '这段时间没有睡眠记录' : '读取失败', list.ok ? '手环需要在睡眠时佩戴，并在小米运动健康里开启睡眠监测。' : list.error, 'moon')}</section>`);
      return;
    }
    const s = sum.data || {};
    const sel = nights.find((n) => n.sleep_id === ui.selected) || nights.at(-1);
    ui.selected = sel.sleep_id;
    const share = s.stage_share_pct || {};
    const shareOrder = ['deep', 'rem', 'light', 'awake'].filter((k) => share[k] != null);

    root.innerHTML = String(html`
      <div class="grid g-4">
        ${[
          ['平均睡眠', duration(s.avg_asleep_minutes), `平均评分 ${num(s.avg_score)}`],
          ['平均入睡', s.avg_bedtime || '—', `波动 ±${num(s.bedtime_variability_minutes)} 分钟`],
          ['平均起床', s.avg_wake_time || '—', `${s.nights} 晚有记录`],
          ['睡眠不足', `${s.short_nights_under_6h ?? 0} 晚`, '少于 6 小时'],
        ].map(([k, v, f]) => html`<article class="card stat" style="--c:var(--c-sleep)"><span class="stat-name">${k}</span><div class="stat-value"><b class="num" style="font-size:28px">${v}</b></div><div class="stat-foot"><small>${f}</small></div></article>`)}
      </div>

      <section class="card">
        <div class="card-head"><div><h2>每晚睡眠时长</h2><p>虚线为 8 小时</p></div></div>
        <div id="c-dur"></div>
      </section>

      <div class="grid g-main">
        <section class="card">
          <div class="card-head"><div><h2>${weekday(sel.start_at.slice(0, 10))}夜晚的分期</h2><p>${sel.start_at.slice(5, 10).replace('-', '/')} ${hm(sel.start_at)} 至 ${hm(sel.end_at)}，共 ${duration(sel.duration_minutes)}</p></div>${sel.sleep_score ? html`<span class="chip chip-accent">评分 ${sel.sleep_score}</span>` : ''}</div>
          <div id="c-hyp" class="chart">${sel.stages?.length ? '' : empty('这一晚没有分期数据', '', 'moon')}</div>
          <ul class="legend" style="margin-top:16px">${(sel.stages ? ['deep', 'rem', 'light', 'awake'] : []).map((k) => {
            const m = sel.stages.filter((x) => x.stage === k).reduce((a, x) => a + x.minutes, 0);
            return html`<li><i style="background:${raw(COLORS[k])}"></i>${NAMES[k]} <b>${duration(m)}</b></li>`;
          })}</ul>
        </section>
        <section class="card">
          <div class="card-head"><div><h2>平均分期占比</h2><p>整个区间</p></div></div>
          ${shareOrder.length ? html`
            ${raw(stack(shareOrder.map((k) => ({ label: NAMES[k], value: share[k], color: COLORS[k] }))))}
            <div class="rows" style="margin-top:18px">${shareOrder.map((k) => html`<div><span class="k" style="display:flex;align-items:center;gap:8px"><i style="width:9px;height:9px;border-radius:3px;background:${raw(COLORS[k])}"></i>${NAMES[k]}</span><span class="v">${share[k]}%</span></div>`)}</div>`
            : empty('暂无分期数据', '', 'moon')}
        </section>
      </div>

      <section class="card">
        <div class="card-head"><div><h2>逐晚记录</h2><p>点击一行查看当晚分期</p></div></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>醒来日期</th><th>入睡</th><th>起床</th><th class="r">睡着时长</th><th class="r">评分</th><th style="width:28%">分期</th></tr></thead>
          <tbody>${[...nights].reverse().map((n) => html`<tr data-night="${n.sleep_id}" style="cursor:pointer" ${n.sleep_id === sel.sleep_id ? raw('aria-selected="true"') : ''}>
            <td>${nightDate(n)} <span class="faint">${weekday(nightDate(n))}</span></td><td class="num">${hm(n.start_at)}</td><td class="num">${hm(n.end_at)}</td>
            <td class="r num">${duration(n.time_asleep_minutes)}</td><td class="r num">${num(n.sleep_score)}</td>
            <td>${n.stages?.length ? raw(stack(['deep', 'rem', 'light', 'awake'].map((k) => ({ label: NAMES[k], value: n.stages.filter((x) => x.stage === k).reduce((a, x) => a + x.minutes, 0), color: COLORS[k] })))) : ''}</td></tr>`)}</tbody></table></div>
      </section>
    `);

    chart(root.querySelector('#c-dur'), nights.map((n) => ({ date: nightDate(n), value: n.time_asleep_minutes })), {
      type: 'bar', color: 'var(--c-sleep)', ref: 480, height: 240, label: '每晚睡眠时长', fmt: (v) => duration(v),
    });
    if (sel.stages?.length) hypnogram(root.querySelector('#c-hyp'), sel.stages, COLORS);
  },
};
