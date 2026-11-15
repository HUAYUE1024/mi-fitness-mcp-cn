import { html } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { chart, spark as sparkRaw, ring as ringRaw } from '../lib/charts.js';
import { raw } from '../lib/dom.js';

const spark = (v, c) => raw(sparkRaw(v, c));
const ring = (o) => raw(ringRaw(o));
import { get } from '../lib/api.js';
import { num, duration, mean, addDays, weekdayShort, mdLabel, todayStr } from '../lib/format.js';
import { empty, delta } from '../lib/ui.js';
import { state } from '../state.js';

const METRICS = {
  steps: { name: '步数', icon: 'steps', color: 'var(--c-steps)', unit: '步' },
  active_kcal: { name: '活动消耗', icon: 'flame', color: 'var(--c-workout)', unit: 'kcal' },
  resting_heart_rate: { name: '静息心率', icon: 'heart', color: 'var(--c-heart)', unit: 'bpm' },
  sleep_minutes: { name: '睡眠', icon: 'moon', color: 'var(--c-sleep)', unit: '小时' },
  spo2: { name: '血氧', icon: 'drop', color: 'var(--c-oxygen)', unit: '%' },
  stress: { name: '压力', icon: 'mind', color: 'var(--c-stress)', unit: '' },
};

const GOAL = 8000;

const card = (key, value, text, series, foot) => {
  const m = METRICS[key];
  return html`<article class="card stat" style="--c:${m.color}">
    <div class="stat-top"><span class="stat-name">${icon(m.icon)}${m.name}</span>${foot}</div>
    <div class="stat-value"><b class="num">${text}</b><span>${m.unit}</span></div>
    <div class="stat-foot"><small>${value}</small><span style="color:${m.color}">${series}</span></div>
  </article>`;
};

export default {
  id: 'overview',
  title: '总览',
  range: true,
  subtitle: (r) => `${r.start} 至 ${r.end}`,
  async load(root) {
    const { start, end } = state.range;
    const week = addDays(end, -6);
    const [ov, rep, steps, rhr, sleepS, spo2, stress, hr] = await Promise.all([
      get('/api/overview', { date: end }),
      get('/api/report', { start_date: start, end_date: end }),
      get('/api/metric-series', { metric: 'steps', start_date: start, end_date: end }),
      get('/api/metric-series', { metric: 'resting_heart_rate', start_date: start, end_date: end }),
      get('/api/metric-series', { metric: 'sleep_minutes', start_date: start, end_date: end }),
      get('/api/metric-series', { metric: 'spo2', start_date: start, end_date: end }),
      get('/api/metric-series', { metric: 'stress', start_date: start, end_date: end }),
      get('/api/heart-rate-summary', { start_date: week, end_date: end }),
    ]);
    if (!ov.ok) { root.innerHTML = String(empty('无法读取数据', ov.error, 'alert')); return; }
    const o = ov.data, r = rep.data, m = r?.metrics || {}, c = r?.change_pct || {};
    const vals = (res) => (res.data || []).map((d) => d.value);
    const t = o.today;
    const night = o.sleep_last_night;
    const asleep = night?.time_asleep_minutes;
    const stages = night?.stages || [];
    const total = stages.reduce((a, s) => a + s.minutes, 0) || 1;
    const share = (k) => Math.round(((stages.find((s) => s.stage === k)?.minutes || 0) / total) * 100);

    root.innerHTML = String(html`
      <section class="card hero">
        ${ring({ value: t?.steps || 0, max: GOAL, color: 'var(--c-steps)', big: num(t?.steps), small: `目标 ${num(GOAL)}`, size: 156 })}
        <div class="hero-meta">
          <span class="eyebrow">${mdLabel(o.date)}</span>
          <h2>${heroLine(t, GOAL, night)}</h2>
          <p>${heroSub(o, m, c)}</p>
          <div class="mini-stats">
            <div><b class="num">${t ? num(t.distance_m / 1000, 2) : '—'}</b><span>公里</span></div>
            <div><b class="num">${t ? num(t.active_kcal) : '—'}</b><span>活动千卡</span></div>
            <div><b class="num">${num(o.last_7_days.avg_steps)}</b><span>近 7 天日均步数</span></div>
          </div>
        </div>
      </section>

      <div class="grid g-4">
        ${card('steps', `${m.days_with_activity ?? 0} 天有记录`, num(m.avg_daily_steps), spark(vals(steps), METRICS.steps.color), delta(m.avg_daily_steps, prevOf(r, 'avg_daily_steps')))}
        ${card('resting_heart_rate', `最近 ${num(o.latest_heart_rate)} bpm`, num(o.resting_heart_rate), spark(vals(rhr), METRICS.resting_heart_rate.color), delta(m.avg_resting_heart_rate, prevOf(r, 'avg_resting_heart_rate'), { invert: true }))}
        ${card('sleep_minutes', asleep ? `昨晚 ${duration(asleep)}` : '昨晚无记录', asleep ? (asleep / 60).toFixed(1) : '—', spark(vals(sleepS), METRICS.sleep_minutes.color), delta(m.avg_sleep_minutes, prevOf(r, 'avg_sleep_minutes')))}
        ${card('spo2', `区间最低 ${num(m.min_spo2)}%`, num(o.latest_spo2), spark(vals(spo2), METRICS.spo2.color), delta(m.avg_spo2, prevOf(r, 'avg_spo2')))}
      </div>

      <div class="grid g-main">
        <section class="card">
          <div class="card-head"><div><h2>每日步数</h2><p>虚线为 ${num(GOAL)} 步目标</p></div>${delta(m.total_steps, prevOf(r, 'total_steps'))}</div>
          <div id="c-steps">${steps.data?.length ? '' : empty('这段时间没有步数记录', '检查日期范围，或先同步数据', 'steps')}</div>
        </section>
        <section class="card">
          <div class="card-head"><div><h2>昨晚睡眠</h2><p>${night ? `${night.start_at.slice(11, 16)} 入睡，${night.end_at.slice(11, 16)} 醒来` : '没有记录'}</p></div>${night?.sleep_score ? html`<span class="chip chip-accent">评分 ${night.sleep_score}</span>` : ''}</div>
          ${night ? html`<div class="stages">
            ${[['deep', '深睡', 'var(--c-sleep)'], ['rem', 'REM', 'var(--c-oxygen)'], ['light', '浅睡', 'var(--c-heart)'], ['awake', '清醒', 'var(--text-3)']].map(([k, label, col]) => html`
              <div class="stage-row"><span class="muted">${label}</span><div class="bar"><i style="width:${share(k)}%;background:${col}"></i></div><b>${share(k)}%</b></div>`)}
          </div>` : empty('昨晚没有睡眠数据', '', 'moon')}
        </section>
      </div>

      <div class="grid g-2">
        <section class="card"><div class="card-head"><div><h2>静息心率</h2><p>越低通常代表恢复越好</p></div></div><div id="c-rhr"></div></section>
        <section class="card"><div class="card-head"><div><h2>压力</h2><p>0 至 100，数值越高越紧张</p></div></div><div id="c-stress"></div></section>
      </div>
    `);

    const draw = (id, res, opt) => { const el = root.querySelector(id); if (el && res.data?.length) chart(el, res.data, opt); else if (el) el.innerHTML = String(empty('暂无数据', '', 'empty')); };
    const stepsEl = root.querySelector('#c-steps');
    if (steps.data?.length) chart(stepsEl, steps.data, { type: 'bar', color: 'var(--c-steps)', ref: GOAL, height: 250, label: '每日步数', fmt: (v) => `${num(v)} 步` });
    draw('#c-rhr', rhr, { color: 'var(--c-heart)', height: 210, label: '静息心率', fmt: (v) => `${num(v)} bpm` });
    draw('#c-stress', stress, { color: 'var(--c-stress)', height: 210, label: '压力', fmt: (v) => `${num(v)}` });
  },
};

function prevOf(r, key) { return r?.previous_metrics?.[key]; }

function heroLine(t, goal, night) {
  if (!t || !t.steps) return '这一天还没有活动记录';
  const pct = Math.round((t.steps / goal) * 100);
  if (pct >= 100) return `步数目标已完成，达成 ${pct}%`;
  return `距离 ${num(goal)} 步目标还差 ${num(goal - t.steps)} 步`;
}

function heroSub(o, m, c) {
  const bits = [];
  if (o.resting_heart_rate) bits.push(`静息心率 ${o.resting_heart_rate} bpm`);
  if (o.latest_stress != null) bits.push(`最近压力 ${o.latest_stress}`);
  if (o.latest_weight) bits.push(`体重 ${o.latest_weight.weight_kg} kg`);
  return bits.length ? bits.join('，') + '。' : '同步数据后，这里会显示当天的关键指标。';
}
