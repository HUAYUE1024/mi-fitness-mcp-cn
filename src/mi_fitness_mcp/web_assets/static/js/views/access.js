import { html, raw } from '../lib/dom.js';
import { icon } from '../lib/icons.js';
import { get, post, del, getKey, setKey } from '../lib/api.js';
import { empty, on, toast, busy, copy, confirmDialog, modal } from '../lib/ui.js';
import { state } from '../state.js';

const ENDPOINTS = [
  ['GET', '/api/overview', '某天的健康快照'], ['GET', '/api/report', '区间报告，对比上一周期'], ['GET', '/api/metric-series', '14 个指标的时间序列'],
  ['GET', '/api/sleep-summary', '睡眠规律与分期占比'], ['GET', '/api/heart-rate-summary', '逐日心率与区间分布'], ['GET', '/api/records', '个人纪录与连续达标'],
  ['GET', '/api/gaps', '缺数据的日期'], ['GET', '/api/export', '导出 CSV / JSON'], ['POST', '/api/sync', '同步（可后台运行）'],
];
const ui = { qr: null, timer: null, tick: null };
const when = (v, fallback = '从未') => (v ? v.slice(0, 16).replace('T', ' ') : fallback);

export default {
  id: 'access',
  title: '接入',
  subtitle: () => 'API Key、扫码登录与接口说明',

  init(root) {
    on(root, 'click', '[data-qr]', () => this.startQr(root));
    on(root, 'click', '[data-copy-curl]', () => copy(`curl "${location.origin}/proxy/api/overview" -H "X-API-Key: ${getKey() || 'mif_sk_...'}"`, 'cURL 命令已复制'));
    on(root, 'click', '[data-clear-key]', () => { setKey(''); toast('已清除浏览器中保存的 Key', 'ok'); this.load(root); });
    on(root, 'click', '[data-revoke]', async (e, b) => {
      if (!(await confirmDialog('吊销这个 Key？', `前缀 ${b.dataset.revoke}。使用它的程序会立刻失去访问权限，无法撤销。`, '吊销'))) return;
      const res = await del(`/api/auth/keys/${encodeURIComponent(b.dataset.revoke)}`);
      res.ok ? toast('Key 已吊销', 'ok') : toast(res.error, 'error', '吊销失败');
      this.load(root);
    });
    root.addEventListener('submit', (e) => {
      if (e.target.id !== 'key-form') return;
      e.preventDefault();
      const f = new FormData(e.target);
      busy(e.target.querySelector('button[type=submit]'), async () => {
        const res = await post('/api/auth/keys', { user_id: f.get('user_id').trim(), pass_token: f.get('pass_token').trim(), region: f.get('region'), label: f.get('label').trim() || null });
        if (!res.ok) return toast(res.error, 'error', '发放失败');
        e.target.reset();
        this.reveal(root, res.raw.api_key);
      });
    });
  },

  reveal(root, key) {
    const m = modal(html`<h2 class="card-title" style="margin-bottom:6px">API Key 已生成</h2>
      <p class="muted" style="margin-bottom:16px">完整的 Key 只显示这一次，请现在保存。</p>
      <input class="input mono" readonly value="${key}" aria-label="API Key">
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:20px">
        <button class="btn" data-copy>${raw(icon('copy'))}复制</button>
        <button class="btn btn-primary" data-use>在此浏览器中使用</button></div>`, { onClose: () => this.load(root) });
    m.el.querySelector('input').addEventListener('focus', (e) => e.target.select());
    m.el.querySelector('[data-copy]').addEventListener('click', () => copy(key, 'Key 已复制'));
    m.el.querySelector('[data-use]').addEventListener('click', () => { setKey(key); toast('已保存到此浏览器', 'ok'); m.close(); });
  },

  async startQr(root) {
    this.stopQr();
    const res = await post('/api/auth/qr/start', {}, { region: 'cn' });
    if (!res.ok) return toast(res.error, 'error', '无法生成二维码');
    const { qr_token: token, expires_in: ttl, login_url: url } = res.raw;
    let left = ttl;
    const m = modal(html`<h2 class="card-title" style="margin-bottom:4px">扫码登录</h2>
      <p class="muted" id="qr-msg" style="margin-bottom:16px">用小米运动健康、米家或小米账号 App 扫描，并在手机上确认。</p>
      <div style="display:grid;place-items:center;padding:14px;border-radius:12px;background:#fff;margin-bottom:14px"><img id="qr-img" alt="登录二维码" width="220" height="220" style="display:block"></div>
      <div style="display:flex;justify-content:space-between;color:var(--text-3);font-size:12.5px;margin-bottom:16px"><span id="qr-left" class="num"></span><button class="btn btn-ghost btn-sm" data-copy-url>复制登录链接</button></div>
      <div style="display:flex;justify-content:flex-end;gap:10px"><button class="btn" data-close>取消</button></div>`, { onClose: () => this.stopQr() });
    try {
      const { blob } = await import('../lib/api.js');
      m.el.querySelector('#qr-img').src = URL.createObjectURL(await blob(`/api/auth/qr/${token}.png`));
    } catch { toast('二维码图片加载失败', 'error'); }
    m.el.querySelector('[data-copy-url]').addEventListener('click', () => copy(url, '登录链接已复制'));
    const paint = () => { const el = m.el.querySelector('#qr-left'); if (el) el.textContent = `有效期剩余 ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`; };
    paint();
    ui.tick = setInterval(() => { left -= 1; paint(); if (left <= 0) { this.stopQr(); m.el.querySelector('#qr-msg').textContent = '二维码已过期，请关闭后重新生成。'; } }, 1000);
    ui.timer = setInterval(async () => {
      const r = await get('/api/auth/qr/poll', { token });
      if (r.raw?.status !== 'confirmed') return;
      this.stopQr(); m.close();
      if (r.raw.api_key) { this.reveal(root, r.raw.api_key); } else { toast(r.raw.note || '已确认，但没有拿到凭据', 'info', '扫码完成'); this.load(root); }
    }, 2500);
  },

  stopQr() { clearInterval(ui.timer); clearInterval(ui.tick); ui.timer = ui.tick = null; },

  async load(root) {
    const res = await get('/api/auth/keys');
    const keys = res.ok ? res.data : [];
    const demo = state.status?.mode === 'demo';
    root.innerHTML = String(html`
      <div class="grid g-main">
        <section class="card">
          <div class="card-head"><div><h2>API Key</h2><p>为程序或脚本发放独立的访问凭据，可随时吊销</p></div><button class="btn btn-primary btn-sm" data-qr>${raw(icon('qr'))}扫码登录</button></div>
          ${res.status === 403 || res.status === 401 ? empty('需要管理员权限', '设置环境变量 MI_FITNESS_ADMIN_KEY 后，由代理自动附带；未设置时仅本机可访问。', 'key')
            : keys.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>标签</th><th>Key</th><th>账号</th><th>最近使用</th><th></th></tr></thead>
              <tbody>${keys.map((k) => html`<tr><td>${k.label || '未命名'}${k.revoked ? html` <span class="chip" style="margin-left:6px">已吊销</span>` : ''}</td><td class="mono">${k.key_masked}</td><td class="mono">${k.user_id}</td><td class="faint">${when(k.last_used_at)}</td>
                <td class="r">${k.revoked ? '' : html`<button class="btn btn-ghost btn-sm btn-danger" data-revoke="${k.prefix}">${raw(icon('trash'))}吊销</button>`}</td></tr>`)}</tbody></table></div>`
            : empty('还没有发放过 Key', demo ? '演示模式下发放的 Key 不会连接真实账号。' : '用下面的表单，或点击右上角扫码登录。', 'key')}
        </section>
        <section class="card">
          <div class="card-head"><div><h2>此浏览器</h2><p>仪表盘请求使用的 Key</p></div></div>
          <dl class="rows"><div><dt>状态</dt><dd>${getKey() ? html`<span class="chip chip-up">已设置</span>` : html`<span class="chip">未设置</span>`}</dd></div>
            <div><dt>说明</dt><dd class="muted" style="font-weight:400;max-width:62%">未设置时使用本机已配置的账号。</dd></div></dl>
          <div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap">${getKey() ? html`<button class="btn btn-sm" data-clear-key>清除</button>` : ''}<button class="btn btn-sm" data-copy-curl>${raw(icon('copy'))}复制 cURL 示例</button></div>
        </section>
      </div>

      <section class="card">
        <div class="card-head"><div><h2>手动发放</h2><p>已有 userId 与 passToken 时，直接换取 Key。凭据会在发放时真实登录验证一次。</p></div></div>
        <form id="key-form" autocomplete="off"><div class="form-row" style="margin-bottom:16px">
          <div class="field"><label for="f-uid">userId</label><input id="f-uid" class="input mono" name="user_id" required></div>
          <div class="field"><label for="f-pt">passToken</label><input id="f-pt" class="input mono" name="pass_token" type="password" required autocomplete="new-password"></div>
          <div class="field"><label for="f-rg">区域</label><select id="f-rg" class="select" name="region">${['cn', 'de', 'i2', 'ru', 'sg', 'us'].map((r) => html`<option value="${r}">${r}</option>`)}</select></div>
          <div class="field"><label for="f-lb">标签（可选）</label><input id="f-lb" class="input" name="label" placeholder="例如：家里的树莓派"></div></div>
          <button class="btn btn-primary" type="submit">${raw(icon('key'))}发放 Key</button></form>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>接口一览</h2><p>完整的交互式文档在后端的 <span class="mono">/docs</span></p></div></div>
        <div class="table-wrap"><table class="table"><tbody>${ENDPOINTS.map(([m, p, d]) => html`<tr><td style="width:64px"><span class="chip ${m === 'POST' ? 'chip-accent' : ''}">${m}</span></td><td class="mono">${p}</td><td class="muted">${d}</td></tr>`)}</tbody></table></div>
      </section>`);
  },
};
