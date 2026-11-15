import { html, raw } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { chart, heatStrip } from '../lib/charts.js';
import { get } from '../lib/api.js';
import { num, duration, mean, mdLabel, sportName, addDays, daysBetween, shortDate } from '../lib/format.js';
import { empty, delta, seg, on } from '../lib/ui.js';
import { state } from '../state.js';

const ZONES = [['rest', '静息', '低于 60', 'var(--c-oxygen)'], ['light', '轻度', '60 至 99', 'var(--c-body)'], ['moderate', '中等', '100 至 139', 'var(--c-stress)'], ['vigorous', '高强度', '140 以上', 'var(--c-heart)']];
const ROWS = [
  ['total_steps', '总步数', '', false], ['avg_daily_steps', '日均步数', '', false], ['total_distance_km', '总距离', ' km', false],
  ['total_active_kcal', '活动消耗', ' kcal', false], ['avg_sleep_minutes', '平均睡眠', ' 分钟', false], ['avg_heart_rate', '平均心率', ' bpm', true],
  ['avg_resting_heart_rate', '平均静息心率', ' bpm', true], ['avg_spo2', '平均血氧', '%', false], ['avg_stress', '平均压力', '', true],
  ['workout_count', '运动次数', ' 次', false], ['workout_minutes', '运动时长', ' 分钟', false],
];
const GAP_NAMES = { daily_activity: '每日活动', heart_rate: '心率', sleep: '睡眠', spo2: '血氧', stress: '压力' };
const ui = { goal: 8000 };

export default {
  id: 'insights',
  title: '洞察',
  subtitle: () => '与上一周期对比、个人纪录、心率区间、数据缺口',
  range: true,

  init(root) {
    root.addEventListener('change', (e) => { if (e.target.id === 'goal') { ui.goal = Math.max(1, Number(e.target.value) || 8000); this.load(root); } });
  },

  async load(root) {
    const { start, end } = state.range;
    const [rep, rec, gaps, hr] = await Promise.all([
      get('/api/report', { start_date: start, end_date: end }),
      get('/api/records', { step_goal: ui.goal }),
      get('/api/gaps', { start_date: start, end_date: end }),
      get('/api/heart-rate-summary', { start_date: start, end_date: end }),
    ]);
    const r = rep.data, k = rec.data, g = gaps.data || [], h = hr.data;
    const zoneTotal = h ? Object.values(h.zone_samples).reduce((a, b) => a + b, 0) : 0;

    root.innerHTML = String(html`
      <section class="card">
        <div class="card-head"><div><h2>区间报告</h2><p>${r ? `${r.period.start_date} 至 ${r.period.end_date}，对比 ${r.previous_period.start_date} 至 ${r.previous_period.end_date}` : ''}</p></div></div>
        ${r ? html`<div>
          <div class="compare"><span class="h">指标</span><span class="h">本期</span><span class="h">上期</span><span class="h">变化</span></div>
          ${ROWS.map(([key, label, unit, lowerIsBetter]) => html`<div class="compare"><span class="muted">${label}</span><b>${num(r.metrics[key])}${r.metrics[key] != null ? unit : ''}</b><span class="prev num">${num(r.previous_metrics[key])}${r.previous_metrics[key] != null ? unit : ''}</span>${delta(r.metrics[key], r.previous_metrics[key], { invert: lowerIsBetter })}</div>`)}
        </div>` : empty('无法生成报告', rep.error, 'alert')}
      </section>

      <div class="grid g-2">
        <section class="card">
          <div class="card-head"><div><h2>个人纪录</h2><p>全部历史数据</p></div>
            <div class="field" style="width:130px"><label for="goal">步数目标</label><input id="goal" class="input" type="number" min="1" step="500" value="${ui.goal}"></div></div>
          ${k ? html`<dl class="rows">
            ${[
              ['单日最多步数', k.most_steps_day && `${num(k.most_steps_day.steps)} 步`, k.most_steps_day?.date],
              ['最长运动', k.longest_workout && `${k.longest_workout.duration_minutes} 分钟 ${sportName(k.longest_workout.activity_type)}`, k.longest_workout?.start_at],
              ['最远运动', k.farthest_workout && `${num(k.farthest_workout.distance_m / 1000, 2)} 公里 ${sportName(k.farthest_workout.activity_type)}`, k.farthest_workout?.start_at],
              ['最高消耗', k.most_calories_workout && `${num(k.most_calories_workout.calories_kcal)} kcal`, k.most_calories_workout?.start_at],
              ['最佳睡眠评分', k.best_sleep_score && `${k.best_sleep_score.sleep_score} 分`, k.best_sleep_score?.start_at],
              ['最低静息心率', k.lowest_resting_heart_rate && `${k.lowest_resting_heart_rate.bpm} bpm`, k.lowest_resting_heart_rate?.timestamp],
              ['最低体重', k.lowest_weight && `${k.lowest_weight.weight_kg} kg`, k.lowest_weight?.timestamp],
            ].map(([label, value, when]) => html`<div><dt>${label}</dt><dd>${value || '—'}${when ? html`<br><small class="faint" style="font-weight:400">${when.slice(0, 10)}</small>` : ''}</dd></div>`)}
            <div><dt>连续达标（${num(k.step_goal)} 步）</dt><dd>最长 ${k.longest_goal_streak_days} 天<br><small class="faint" style="font-weight:400">当前 ${k.current_goal_streak_days} 天</small></dd></div>
          </dl>` : empty('无法读取纪录', rec.error, 'alert')}
        </section>

        <section class="card">
          <div class="card-head"><div><h2>心率区间</h2><p>被动测量样本在各强度的分布</p></div></div>
          ${zoneTotal ? html`<div class="rows" style="gap:0">${ZONES.map(([key, name, range, color]) => {
            const n = h.zone_samples[key] || 0, p = (n / zoneTotal) * 100;
            return html`<div style="display:grid;grid-template-columns:64px minmax(0,1fr) 58px;gap:12px;align-items:center;border:0;padding:10px 0"><span><b style="font-weight:600">${name}</b><br><small class="faint">${range}</small></span>
              <div style="height:8px;border-radius:4px;background:var(--surface-3);overflow:hidden"><i style="display:block;height:100%;width:${p}%;background:${raw(color)};border-radius:4px"></i></div><b class="num" style="text-align:right">${p.toFixed(1)}%</b></div>`;
          })}</div>
          ${h.overall?.count ? html`<p class="muted" style="margin-top:14px;font-size:12.5px">共 ${num(h.overall.count)} 个样本，区间最低 ${h.overall.min}，平均 ${h.overall.avg}，最高 ${h.overall.max} bpm。</p>` : ''}` : empty('没有心率样本', '', 'heart')}
        </section>
      </div>

      <section class="card">
        <div class="card-head"><div><h2>数据缺口</h2><p>没有任何记录的日期。缺口不等于数值为零，多半是没戴手环、没开监测，或还没同步。</p></div></div>
        ${g.length ? html`<div style="display:flex;flex-direction:column;gap:22px">${g.map((item) => {
          const total = item.days_in_range;
          const present = item.days_with_data;
          return html`<div>
            <div style="display:flex;justify-content:space-between;gap:12px;margin-bottom:10px"><b style="font-weight:600">${GAP_NAMES[item.data_type] || item.data_type}</b>
              <span class="${item.missing_days ? 'down' : 'up'}" style="font-size:12.5px;font-weight:550">${item.missing_days ? `缺 ${item.missing_days} 天` : '完整'}<span class="faint" style="font-weight:400"> / ${present} of ${total}</span></span></div>
            <div class="gap-bar">${item.gaps.map((x) => html`<i title="${x.start_date} 至 ${x.end_date}" style="left:${(daysBetween(start, x.start_date) / total) * 100}%;width:${(x.days / total) * 100}%"></i>`)}</div>
            ${item.gaps.length ? html`<p class="faint" style="margin-top:8px;font-size:12px">${item.gaps.slice(0, 4).map((x) => (x.days === 1 ? shortDate(x.start_date) : `${shortDate(x.start_date)} 至 ${shortDate(x.end_date)}`)).join('、')}${item.gaps.length > 4 ? `，另 ${item.gaps.length - 4} 段` : ''}</p>` : ''}
          </div>`;
        })}</div>` : empty('无法检测缺口', gaps.error, 'alert')}
      </section>
    `);
  },
};
