import { html, raw } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { get, post } from '../lib/api.js';
import { num, todayStr, addDays } from '../lib/format.js';
import { empty, on, toast, busy } from '../lib/ui.js';
import { state } from '../state.js';

const TYPES = [['daily_activity', '每日活动'], ['heart_rate', '心率'], ['sleep', '睡眠'], ['workouts', '运动'], ['body_measurements', '体成分'], ['spo2', '血氧'], ['stress', '压力'], ['abnormal_heart_beat', '异常心跳']];
const NAMES = Object.fromEntries(TYPES);
const ui = { picked: new Set(TYPES.map((t) => t[0])), full: false, start: '', end: '', job: null, timer: null };

export default {
  id: 'sync',
  title: '同步',
  subtitle: () => '从小米云把数据拉到本地',

  init(root) {
    on(root, 'click', '[data-pick]', (e, b) => {
      const k = b.dataset.pick;
      ui.picked.has(k) ? ui.picked.delete(k) : ui.picked.add(k);
      b.setAttribute('aria-pressed', String(ui.picked.has(k)));
    });
    on(root, 'click', '[data-all]', () => {
      const all = ui.picked.size < TYPES.length;
      ui.picked = new Set(all ? TYPES.map((t) => t[0]) : []);
      root.querySelectorAll('[data-pick]').forEach((b) => b.setAttribute('aria-pressed', String(all)));
    });
    root.addEventListener('input', (e) => {
      if (e.target.id === 'sync-start') ui.start = e.target.value;
      if (e.target.id === 'sync-end') ui.end = e.target.value;
      if (e.target.id === 'sync-full') ui.full = e.target.checked;
    });
    on(root, 'click', '[data-run]', (e, b) => busy(b, async () => {
      if (!ui.picked.size) return toast('至少选择一类数据', 'error');
      const body = { data_types: [...ui.picked], start_date: ui.start || null, end_date: ui.end || null, force_full_sync: ui.full, background: true };
      const res = await post('/api/sync', body);
      if (!res.ok) return toast(res.error, 'error', '无法开始同步');
      ui.job = { id: res.raw.sync_id, status: 'queued' };
      toast('任务已在后台运行', 'ok', '同步开始');
      this.poll(root);
    }));
  },

  poll(root) {
    clearTimeout(ui.timer);
    const tick = async () => {
      if (!ui.job) return;
      const res = await get(`/api/sync/${ui.job.id}`);
      if (res.ok) ui.job = { id: ui.job.id, ...res.raw };
      this.paintJob(root);
      if (['queued', 'running'].includes(ui.job.status) && root.isConnected && state.view === 'sync') ui.timer = setTimeout(tick, 1500);
      else if (ui.job.status === 'ok' || ui.job.status === 'partial') { toast(`新增 ${num(ui.job.records_added)}，更新 ${num(ui.job.records_updated)}`, ui.job.status === 'ok' ? 'ok' : 'info', '同步完成'); }
    };
    tick();
  },

  paintJob(root) {
    const el = root.querySelector('#job');
    if (!el) return;
    const j = ui.job;
    if (!j) { el.innerHTML = ''; return; }
    const running = ['queued', 'running'].includes(j.status);
    el.innerHTML = String(html`<div class="card" style="background:var(--surface-2);box-shadow:none">
      <div class="card-head" style="margin-bottom:${j.results ? 14 : 0}px"><div><h2>${running ? '同步进行中' : j.status === 'ok' ? '同步完成' : j.status === 'partial' ? '部分完成' : '同步失败'}</h2>
        <p>${j.error ? j.error : running ? '正在向小米云请求数据，大范围同步可能需要几分钟。' : `新增 ${num(j.records_added)} 条，更新 ${num(j.records_updated)} 条，未变化 ${num(j.records_skipped)} 条`}</p></div>
        <span class="chip ${running ? 'chip-warn' : j.status === 'ok' ? 'chip-up' : 'chip-down'}">${running ? '运行中' : j.status === 'ok' ? '成功' : j.status === 'partial' ? '部分成功' : '失败'}</span></div>
      ${j.results ? html`<div class="rows">${j.results.map((r) => html`<div><span class="k">${NAMES[r.data_type] || r.data_type}</span><span class="v">${r.status === 'ok' ? html`新增 ${num(r.added)}，更新 ${num(r.updated)}，未变 ${num(r.skipped)}` : html`<span class="down">${r.error || r.status}</span>`}</span></div>`)}</div>` : ''}
    </div>`);
  },

  async load(root) {
    const [st, cov] = await Promise.all([get('/api/status', { deep: false }), get('/api/coverage')]);
    const s = st.data || {}, c = cov.ok ? cov.data : [];
    const demo = s.mode === 'demo';
    const cmap = Object.fromEntries(c.map((x) => [x.data_type, x]));
    ui.start ||= ''; ui.end ||= '';

    root.innerHTML = String(html`
      <div class="grid g-main">
        <section class="card">
          <div class="card-head"><div><h2>开始同步</h2><p>不填日期时，会从上次同步的位置继续；第一次同步默认拉取最近 30 天。</p></div></div>
          ${demo ? html`<div class="empty" style="padding:20px 0 8px">${raw(icon('sync'))}<b>演示模式不连接小米云</b><p>当前看到的是合成数据，所以无需同步。用自己的账号启动后，这里就可以拉取真实数据。</p></div>` : html`
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><span class="label muted" style="font-size:12px;font-weight:550">数据类型</span><button class="btn btn-ghost btn-sm" data-all>全选 / 清空</button></div>
            <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:22px">${TYPES.map(([k, n]) => html`<button type="button" class="toggle-chip" data-pick="${k}" aria-pressed="${String(ui.picked.has(k))}">${n}</button>`)}</div>
            <div class="form-row" style="margin-bottom:18px">
              <div class="field"><label for="sync-start">开始日期（可选）</label><input id="sync-start" class="input" type="date" value="${ui.start}"></div>
              <div class="field"><label for="sync-end">结束日期（可选）</label><input id="sync-end" class="input" type="date" value="${ui.end}"></div>
            </div>
            <label class="check" style="margin-bottom:22px"><input id="sync-full" type="checkbox" ${ui.full ? raw('checked') : ''}> 忽略断点，重新拉取（数据量大时较慢）</label>
            <div><button class="btn btn-primary" data-run>${raw(icon('sync'))}开始同步</button></div>`}
          <div id="job" style="margin-top:22px"></div>
        </section>
        <section class="card">
          <div class="card-head"><div><h2>连接状态</h2><p>${demo ? '演示模式' : '小米云'}</p></div><span class="chip ${s.connected ? 'chip-up' : demo ? 'chip-warn' : 'chip-down'}">${demo ? '演示' : s.connected ? '已连接' : '未连接'}</span></div>
          <dl class="rows">
            <div><dt>账号</dt><dd class="mono">${s.user_id || '—'}</dd></div>
            <div><dt>区域</dt><dd>${s.region || '—'}</dd></div>
            <div><dt>正在同步</dt><dd>${s.sync_in_progress ? '是' : '否'}</dd></div>
            ${s.error ? html`<div><dt>最近错误</dt><dd class="down" style="max-width:60%">${s.error}</dd></div>` : ''}
          </dl>
        </section>
      </div>
      <section class="card">
        <div class="card-head"><div><h2>本地数据覆盖</h2><p>每类数据在本地库中的范围</p></div></div>
        ${c.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>类型</th><th>起</th><th>止</th><th class="r">有数据天数</th><th class="r">记录数</th></tr></thead>
          <tbody>${TYPES.map(([k, n]) => cmap[k] ? html`<tr><td>${n}</td><td class="num">${cmap[k].first_date}</td><td class="num">${cmap[k].last_date}</td><td class="r num">${num(cmap[k].days_with_data)}</td><td class="r num">${num(cmap[k].record_count)}</td></tr>` : html`<tr><td>${n}</td><td colspan="4" class="faint">没有数据</td></tr>`)}</tbody></table></div>`
          : empty('本地库还是空的', '完成一次同步后，这里会显示每类数据的覆盖范围。', 'data')}
      </section>`);
    this.paintJob(root);
  },
};
