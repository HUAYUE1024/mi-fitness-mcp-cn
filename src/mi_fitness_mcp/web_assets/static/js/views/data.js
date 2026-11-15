import { html, raw } from '../lib/dom.js';
import { get, blob } from '../lib/api.js';
import { num, duration, hm, sportName, pace } from '../lib/format.js';
import { empty, seg, on, toast, busy } from '../lib/ui.js';
import { icon } from '../lib/icons.js';
import { state } from '../state.js';

const lvl = { low: '低', medium: '中', high: '高' };
const T = (s) => (s ? String(s).slice(0, 16).replace('T', ' ') : '—');

const SETS = {
  daily_activity: { label: '每日活动', path: '/api/summary', cols: [['date', '日期'], ['steps', '步数', (v) => num(v), 'r'], ['distance_m', '距离', (v) => `${num(v / 1000, 2)} 公里`, 'r'], ['active_kcal', '活动消耗', (v) => `${num(v)} kcal`, 'r']] },
  heart_rate: { label: '心率', path: '/api/heart-rate', limit: 400, cols: [['timestamp', '时间', T], ['bpm', '心率', (v) => `${v} bpm`, 'r'], ['sample_type', '类型', (v) => ({ resting: '静息', passive: '被动', active: '主动', workout: '运动' }[v] || v)]] },
  sleep: { label: '睡眠', path: '/api/sleep', cols: [['start_at', '入睡', T], ['end_at', '起床', (v) => hm(v)], ['time_asleep_minutes', '睡着时长', duration, 'r'], ['time_awake_minutes', '清醒', (v) => `${v} 分`, 'r'], ['sleep_score', '评分', (v) => num(v), 'r']] },
  workouts: { label: '运动', path: '/api/workouts', cols: [['start_at', '开始', T], ['activity_type', '类型', sportName], ['duration_minutes', '时长', (v) => `${v} 分`, 'r'], ['distance_m', '距离', (v) => (v ? `${num(v / 1000, 2)} 公里` : '—'), 'r'], ['calories_kcal', '消耗', (v) => (v ? `${num(v)} kcal` : '—'), 'r'], ['avg_heart_rate_bpm', '平均心率', (v) => num(v), 'r'], ['avg_pace_sec_per_km', '配速', pace, 'r']] },
  body_measurements: { label: '体成分', path: '/api/body-measurements', cols: [['timestamp', '时间', T], ['weight_kg', '体重', (v) => `${v} kg`, 'r'], ['bmi', 'BMI', (v) => num(v, 1), 'r'], ['body_fat_pct', '体脂率', (v) => (v == null ? '—' : `${v}%`), 'r'], ['muscle_mass_kg', '肌肉量', (v) => (v == null ? '—' : `${v} kg`), 'r'], ['water_pct', '水分', (v) => (v == null ? '—' : `${v}%`), 'r']] },
  spo2: { label: '血氧', path: '/api/spo2', limit: 400, cols: [['timestamp', '时间', T], ['spo2_pct', '血氧', (v) => `${v}%`, 'r']] },
  stress: { label: '压力', path: '/api/stress', limit: 400, cols: [['timestamp', '时间', T], ['stress_score', '压力分', (v) => v, 'r'], ['level', '等级', (v) => lvl[v] || v]] },
  abnormal_heart_beat: { label: '异常心跳', path: '/api/abnormal-heart-beat', cols: [['start_at', '开始', T], ['end_at', '结束', (v) => hm(v)], ['duration_seconds', '持续', (v) => `${v} 秒`, 'r']] },
};
const ui = { type: 'daily_activity' };

function download(blobData, name) {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blobData), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export default {
  id: 'data',
  title: '数据',
  subtitle: () => '浏览本地库中的原始记录并导出',
  range: true,

  init(root) {
    on(root, 'click', '[data-seg]', (e, b) => { ui.type = b.dataset.seg; this.load(root); });
    on(root, 'click', '[data-export]', (e, b) => busy(b, async () => {
      const { start, end } = state.range, fmt = b.dataset.export;
      try {
        download(await blob('/api/export', { data_type: ui.type, start_date: start, end_date: end, format: fmt }), `mi_fitness_${ui.type}_${start}_${end}.${fmt}`);
        toast('文件已生成', 'ok', '导出完成');
      } catch (err) { toast(String(err.message || err), 'error', '导出失败'); }
    }));
  },

  async load(root) {
    const set = SETS[ui.type];
    const { start, end } = state.range;
    const res = await get(set.path, { start_date: start, end_date: end, limit: set.limit });
    const rows = res.ok ? res.data : [];
    const shown = rows.slice(ui.type === 'heart_rate' || ui.type === 'spo2' || ui.type === 'stress' ? -set.limit : 0);

    root.innerHTML = String(html`
      <section class="card">
        <div class="card-head">
          <div class="seg-scroll">${seg(Object.entries(SETS).map(([k, v]) => [k, v.label]), ui.type)}</div>
          <div style="display:flex;gap:8px"><button class="btn btn-sm" data-export="csv">${raw(icon('download'))}导出 CSV</button><button class="btn btn-sm" data-export="json">${raw(icon('code'))}JSON</button></div>
        </div>
        ${rows.length ? html`
          <p class="muted" style="font-size:12.5px;margin-bottom:6px">共 ${num(rows.length)} 条${set.limit && rows.length >= set.limit ? html`，原始样本很多，这里只显示最早的 ${set.limit} 条，导出文件包含全部` : ''}</p>
          <div class="table-wrap"><table class="table"><thead><tr>${set.cols.map((c) => html`<th class="${c[3] || ''}">${c[1]}</th>`)}</tr></thead>
            <tbody>${shown.slice(0, 500).map((row) => html`<tr>${set.cols.map((c) => html`<td class="${c[3] || ''} ${c[3] === 'r' ? 'num' : ''}">${c[2] ? c[2](row[c[0]]) : row[c[0]] ?? '—'}</td>`)}</tr>`)}</tbody></table></div>`
          : empty(res.ok ? '这段时间没有记录' : '读取失败', res.ok ? '换一个日期范围，或先同步数据。' : res.error, res.ok ? 'empty' : 'alert')}
      </section>`);
  },
};
