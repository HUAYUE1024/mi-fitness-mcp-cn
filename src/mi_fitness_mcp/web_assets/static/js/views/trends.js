import { html, raw } from '../lib/dom.js';
import { chart } from '../lib/charts.js';
import { get } from '../lib/api.js';
import { num, mean, weekdayShort } from '../lib/format.js';
import { empty, seg, on } from '../lib/ui.js';
import { state } from '../state.js';

const GROUPS = [
  ['活动', [['steps', '步数', '步', 'var(--c-steps)', 'bar'], ['distance_m', '距离', '米', 'var(--c-steps)', 'bar'], ['active_kcal', '活动消耗', 'kcal', 'var(--c-workout)', 'bar']]],
  ['睡眠与运动', [['sleep_minutes', '睡眠时长', '分钟', 'var(--c-sleep)', 'bar'], ['workout_minutes', '运动时长', '分钟', 'var(--c-workout)', 'bar'], ['workout_kcal', '运动消耗', 'kcal', 'var(--c-workout)', 'bar']]],
  ['心率、血氧、压力', [['resting_heart_rate', '静息心率', 'bpm', 'var(--c-heart)', 'area'], ['avg_heart_rate', '平均心率', 'bpm', 'var(--c-heart)', 'area'], ['max_heart_rate', '最高心率', 'bpm', 'var(--c-heart)', 'area'], ['spo2', '血氧', '%', 'var(--c-oxygen)', 'area'], ['stress', '压力', '', 'var(--c-stress)', 'area']]],
  ['体成分', [['weight_kg', '体重', 'kg', 'var(--c-body)', 'area'], ['bmi', 'BMI', '', 'var(--c-body)', 'area'], ['body_fat_pct', '体脂率', '%', 'var(--c-body)', 'area']]],
];
const FLAT = Object.fromEntries(GROUPS.flatMap(([, items]) => items.map((m) => [m[0], m])));
const GRAN = [['day', '按日'], ['week', '按周'], ['month', '按月']];
const ui = { metric: 'steps', gran: 'day' };

export default {
  id: 'trends',
  title: '趋势',
  subtitle: () => '任意指标，按日、周、月查看',
  range: true,

  init(root) {
    root.addEventListener('change', (e) => {
      if (e.target.id === 'metric') { ui.metric = e.target.value; this.load(root); }
    });
    on(root, 'click', '[data-seg]', (e, b) => { ui.gran = b.dataset.seg; this.load(root); });
  },

  async load(root) {
    const [, label, unit, color, kind] = FLAT[ui.metric];
    const res = await get('/api/metric-series', { metric: ui.metric, granularity: ui.gran, start_date: state.range.start, end_date: state.range.end });
    const data = res.ok ? res.data : [];
    const vals = data.map((d) => d.value);
    const digits = ['weight_kg', 'bmi', 'body_fat_pct'].includes(ui.metric) ? 1 : 0;
    const fmt = (v) => `${num(v, digits)}${unit ? ` ${unit}` : ''}`;

    root.innerHTML = String(html`
      <section class="card">
        <div class="form-row" style="grid-template-columns:minmax(200px,280px) auto;align-items:end;margin-bottom:22px">
          <div class="field"><label for="metric">指标</label>
            <select id="metric" class="select">${GROUPS.map(([g, items]) => html`<optgroup label="${g}">${items.map((m) => html`<option value="${m[0]}" ${m[0] === ui.metric ? raw('selected') : ''}>${m[1]}</option>`)}</optgroup>`)}</select></div>
          <div class="field"><span class="label">粒度</span>${seg(GRAN, ui.gran)}</div>
        </div>
        ${data.length ? html`
          <div class="grid g-4" style="margin-bottom:24px">
            ${[['平均', fmt(mean(vals))], ['最高', fmt(Math.max(...vals))], ['最低', fmt(Math.min(...vals))], ['数据点', String(data.length)]].map(([k, v]) => html`
              <div><div class="eyebrow">${k}</div><div class="num" style="font-size:24px;font-weight:650;margin-top:4px">${v}</div></div>`)}
          </div>
          <div id="chart"></div>`
          : empty(res.ok ? '这段时间没有数据' : '读取失败', res.ok ? '换一个指标或日期范围试试，也可以先同步数据。' : res.error, res.ok ? 'trend' : 'alert')}
      </section>
      ${data.length ? html`<section class="card">
        <div class="card-head"><div><h2>明细</h2><p>${label}，最近 ${Math.min(60, data.length)} 条</p></div></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>日期</th><th class="r">${label}</th></tr></thead>
          <tbody>${[...data].reverse().slice(0, 60).map((d) => html`<tr><td>${d.date}${ui.gran === 'day' ? html` <span class="faint">周${weekdayShort(d.date)}</span>` : ''}</td><td class="r num">${fmt(d.value)}</td></tr>`)}</tbody></table></div>
      </section>` : ''}
    `);
    const el = root.querySelector('#chart');
    if (el) chart(el, data, { type: kind, color, height: 300, label, fmt });
  },
};
