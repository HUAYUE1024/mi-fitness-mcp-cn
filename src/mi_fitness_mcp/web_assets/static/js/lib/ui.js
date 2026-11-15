import { html, mount, $ } from './dom.js';
import { icon } from './icons.js';

let box;
export function toast(message, kind = 'info', title = '') {
  box ||= Object.assign(document.body.appendChild(document.createElement('div')), { className: 'toasts' });
  box.setAttribute('role', 'status');
  box.setAttribute('aria-live', 'polite');
  const el = document.createElement('div');
  el.className = 'toast';
  el.dataset.kind = kind;
  mount(el, html`${title ? html`<b>${title}</b>` : ''}${message}`);
  box.append(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 300); }, kind === 'error' ? 6000 : 3200);
}

export async function busy(btn, work) {
  if (!btn) return work();
  btn.classList.add('is-loading');
  btn.disabled = true;
  try { return await work(); } finally { btn.classList.remove('is-loading'); btn.disabled = false; }
}

export async function copy(text, label = '已复制') {
  try { await navigator.clipboard.writeText(text); toast(label, 'ok'); }
  catch { toast('浏览器不允许写入剪贴板，请手动复制', 'error'); }
}

export function modal(content, { onClose } = {}) {
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  scrim.innerHTML = '<div class="modal" role="dialog" aria-modal="true"></div>';
  const dlg = scrim.firstChild;
  mount(dlg, content);
  const prev = document.activeElement;
  const close = () => { scrim.remove(); document.removeEventListener('keydown', onKey); prev?.focus?.(); onClose?.(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  scrim.addEventListener('mousedown', (e) => { if (e.target === scrim) close(); });
  dlg.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(scrim);
  dlg.querySelector('input, button')?.focus();
  return { el: dlg, close };
}

export function confirmDialog(title, body, action = '确认') {
  return new Promise((resolve) => {
    let done = false;
    const m = modal(html`
      <h2 class="card-title" style="margin-bottom:8px">${title}</h2>
      <p class="muted" style="margin-bottom:22px">${body}</p>
      <div style="display:flex;gap:10px;justify-content:flex-end">
        <button class="btn" data-close>取消</button>
        <button class="btn btn-primary" data-ok>${action}</button>
      </div>`, { onClose: () => !done && resolve(false) });
    m.el.querySelector('[data-ok]').addEventListener('click', () => { done = true; m.close(); resolve(true); });
  });
}

export const empty = (title, text, ic = 'empty') =>
  html`<div class="empty">${icon(ic)}<b>${title}</b>${text ? html`<p>${text}</p>` : ''}</div>`;

export const skeleton = (h = 180) => html`<div class="skel" style="height:${h}px"></div>`;

export function seg(options, value, attr = 'data-seg') {
  return html`<div class="seg" role="group">${options.map(([v, label]) =>
    html`<button type="button" ${attr}="${v}" aria-pressed="${String(v === value)}">${label}</button>`)}</div>`;
}

export function delta(cur, prev, { suffix = '%', invert = false, digits = 1 } = {}) {
  if (cur == null || prev == null || prev === 0) return html`<span class="chip">—</span>`;
  const pct = ((cur - prev) / Math.abs(prev)) * 100;
  if (Math.abs(pct) < 0.05) return html`<span class="chip">持平</span>`;
  const good = invert ? pct < 0 : pct > 0;
  return html`<span class="chip ${good ? 'chip-up' : 'chip-down'}">${icon(pct > 0 ? 'arrowUp' : 'arrowDown')}${Math.abs(pct).toFixed(digits)}${suffix}</span>`;
}

export const on = (root, ev, sel, fn) =>
  root.addEventListener(ev, (e) => { const t = e.target.closest(sel); if (t && root.contains(t)) fn(e, t); });

export { $ };
