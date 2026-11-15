import { html, raw, mount } from './lib/dom.js';
import { icon, LOGO } from './lib/icons.js';
import { get } from './lib/api.js';
import { seg, on, skeleton, toast } from './lib/ui.js';
import { state, setRange, subscribe, PRESETS } from './state.js';

import overview from './views/overview.js';
import trends from './views/trends.js';
import sleep from './views/sleep.js';
import insights from './views/insights.js';
import data from './views/data.js';
import sync from './views/sync.js';
import access from './views/access.js';

const VIEWS = [overview, trends, sleep, insights, data, sync, access];
const NAV = [
  ['数据', [['overview', 'overview'], ['trends', 'trend'], ['sleep', 'moon'], ['insights', 'insight']]],
  ['管理', [['data', 'data'], ['sync', 'sync'], ['access', 'key']]],
];
const BY_ID = Object.fromEntries(VIEWS.map((v) => [v.id, v]));
let current = null;
let token = 0;

const $ = (s) => document.querySelector(s);

function renderShell() {
  mount($('#app'), html`
    <aside class="rail" aria-label="主导航">
      <a class="brand" href="#overview" aria-label="Mi Fitness 首页">${raw(LOGO)}<span class="brand-text"><span class="brand-name">Mi Fitness</span><br><span class="brand-sub">本地健康数据</span></span></a>
      ${NAV.map(([label, items]) => html`<nav class="nav-group" aria-label="${label}"><div class="nav-label">${label}</div>
        ${items.map(([id, ic]) => html`<a class="nav-link" href="#${id}" data-nav="${id}">${raw(icon(ic))}<span>${BY_ID[id].title}</span></a>`)}</nav>`)}
      <div class="rail-foot">
        <div class="source" id="source"><i class="dot"></i><div class="source-text"><b>检测中</b><span>—</span></div></div>
        <button class="btn btn-ghost" id="theme" aria-label="切换明暗主题">${raw(icon('sun'))}<span class="brand-text">切换主题</span></button>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <div class="page-title"><h1 id="title"></h1><p id="subtitle"></p></div>
        <div class="topbar-tools" id="tools"></div>
      </header>
      <main class="page"><div class="view" id="view" tabindex="-1"></div></main>
    </div>
    <nav class="dock" aria-label="主导航（移动端）">${NAV.flatMap(([, items]) => items).map(([id, ic]) => html`<a href="#${id}" data-nav="${id}">${raw(icon(ic))}<span>${BY_ID[id].title}</span></a>`)}</nav>`);
}

function renderTools(view) {
  const el = $('#tools');
  if (!view.range) { el.innerHTML = ''; return; }
  const r = state.range;
  mount(el, html`
    ${seg([...PRESETS, ['custom', '自定义']], r.preset, 'data-preset')}
    <div class="range-inputs"><input class="input" type="date" id="r-start" value="${r.start}" aria-label="开始日期"><span class="faint">至</span><input class="input" type="date" id="r-end" value="${r.end}" aria-label="结束日期"></div>`);
}

async function paintStatus() {
  const res = await get('/api/status', { deep: false });
  state.status = res.data;
  const s = res.data || {};
  const box = $('#source');
  if (!box) return;
  const demo = s.mode === 'demo', ok = s.connected;
  box.querySelector('.dot').dataset.state = !res.ok ? 'off' : demo ? 'demo' : ok ? 'ok' : 'off';
  box.querySelector('b').textContent = !res.ok ? '后端离线' : demo ? '演示数据' : ok ? '已连接小米云' : s.mode === 'not_configured' ? '未配置账号' : '未连接';
  box.querySelector('span').textContent = !res.ok ? res.error : demo ? '合成数据，非真实记录' : s.user_id ? `账号 ${s.user_id}` : '运行 setup 或扫码登录';
}

async function show(id) {
  const view = BY_ID[id] || overview;
  const mine = ++token;
  current = view;
  state.view = view.id;
  document.title = `${view.title} · Mi Fitness`;
  $('#title').textContent = view.title;
  $('#subtitle').textContent = view.subtitle(state.range);
  document.querySelectorAll('[data-nav]').forEach((a) => (a.dataset.nav === view.id ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  renderTools(view);

  // 每次切换换一个全新的容器：旧页面的事件监听随旧节点一起丢弃，不会叠加
  const old = $('#view');
  const fresh = old.cloneNode(false);
  old.replaceWith(fresh);
  mount(fresh, html`<div class="grid g-4">${[1, 2, 3, 4].map(() => skeleton(132))}</div>${skeleton(300)}`);
  view.init?.(fresh);
  try {
    await view.load(fresh);
  } catch (err) {
    if (mine !== token) return;
    console.error(err);
    mount(fresh, html`<div class="card"><div class="empty"><b>页面出错了</b><p>${String(err.message || err)}</p></div></div>`);
  }
  if (mine === token) window.scrollTo({ top: 0 });
}

const route = () => show(location.hash.slice(1) || 'overview');

function bind() {
  window.addEventListener('hashchange', route);
  $('#theme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('mi_fitness_theme', next);
  });
  on($('#tools'), 'click', '[data-preset]', (e, b) => {
    const p = b.dataset.preset;
    setRange(p === 'custom' ? { ...state.range, preset: 'custom' } : { preset: p });
  });
  $('#tools').addEventListener('change', (e) => {
    if (e.target.id !== 'r-start' && e.target.id !== 'r-end') return;
    const s = $('#r-start').value, en = $('#r-end').value;
    if (!s || !en) return;
    if (s > en) return toast('开始日期不能晚于结束日期', 'error');
    setRange({ start: s, end: en, preset: 'custom' });
  });
  subscribe(route);
  document.addEventListener('auth-required', () => toast('需要有效的 API Key，请到「接入」页设置', 'error', '未授权'));
}

renderShell();
bind();
paintStatus();
route();
