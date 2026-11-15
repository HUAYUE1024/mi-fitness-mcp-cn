import { esc } from './dom.js';
import { shortDate } from './format.js';

const NS = 'http://www.w3.org/2000/svg';
let uid = 0;
const id = (p) => `${p}${++uid}`;

export function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { lo: 0, hi: 1, ticks: [0, 1] };
  if (min === max) { const pad = Math.abs(min) * 0.1 || 1; min -= pad; max += pad; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return { lo, hi, ticks };
}

const compact = (v) => {
  const a = Math.abs(v);
  if (a >= 1e4) return `${+(v / 1e4).toFixed(1)}万`;
  if (a >= 1e3) return `${+(v / 1e3).toFixed(1)}k`;
  return `${+v.toFixed(a < 10 ? 1 : 0)}`;
};

/** Smooth monotone path through points (Fritsch-Carlson), so curves never overshoot the data. */
function monotone(pts) {
  const n = pts.length;
  if (n < 3) return pts.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join('');
  const dx = [], m = [], t = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0]; m[i] = (pts[i + 1][1] - pts[i][1]) / (dx[i] || 1); }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += `C${pts[i][0] + h},${pts[i][1] + t[i] * h} ${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h} ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}

function frame(el, build) {
  let last = 0;
  const draw = () => {
    const w = Math.max(160, Math.round(el.clientWidth));
    if (w === last && el.firstChild) return;
    last = w;
    el.innerHTML = build(w);
    wire(el);
  };
  draw();
  const ro = new ResizeObserver(() => requestAnimationFrame(draw));
  ro.observe(el);
  el._ro?.disconnect();
  el._ro = ro;
}

function wire(el) {
  const svg = el.querySelector('svg');
  const tip = el.querySelector('.tip');
  const pts = el._hits;
  if (!svg || !tip || !pts) return;
  const guide = svg.querySelector('.guide');
  const dot = svg.querySelector('.hover-dot');
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].x - x) < Math.abs(pts[best].x - x)) best = i;
    const p = pts[best];
    guide?.setAttribute('x1', p.x); guide?.setAttribute('x2', p.x); guide?.removeAttribute('hidden');
    if (dot && p.y != null) { dot.setAttribute('cx', p.x); dot.setAttribute('cy', p.y); dot.removeAttribute('hidden'); }
    tip.innerHTML = p.html;
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = `${Math.min(Math.max(p.x - tw / 2, 0), r.width - tw)}px`;
    tip.style.top = '0px';
  };
  const leave = () => { tip.hidden = true; guide?.setAttribute('hidden', ''); dot?.setAttribute('hidden', ''); };
  svg.addEventListener('pointermove', move);
  svg.addEventListener('pointerleave', leave);
  svg.addEventListener('touchmove', move, { passive: true });
  svg.addEventListener('touchend', leave);
}

const tipHtml = (label, value, color) =>
  `<span class="tip-l">${esc(label)}</span><b class="tip-v"><i style="background:${color}"></i>${esc(value)}</b>`;

/**
 * Line / area / bar chart.
 * series: [{ date|label, value }]  options: color, height, type: 'area'|'line'|'bar', unit, fmt, ref
 */
export function chart(el, series, opt = {}) {
  const { color = 'var(--accent)', height = 240, type = 'area', unit = '', ref, fmt = (v) => `${compact(v)}${unit}` } = opt;
  const data = series.filter((d) => d.value != null);
  if (!data.length) { el.innerHTML = ''; return; }
  frame(el, (w) => {
    const m = { t: 14, r: 12, b: 28, l: 44 };
    const iw = w - m.l - m.r, ih = height - m.t - m.b;
    const vals = data.map((d) => d.value);
    let lo = Math.min(...vals, ref ?? Infinity), hi = Math.max(...vals, ref ?? -Infinity);
    if (type === 'bar') lo = Math.min(0, lo);
    const { lo: y0, hi: y1, ticks } = niceTicks(lo, hi, height > 200 ? 4 : 3);
    const X = type === 'bar'
      ? (i) => m.l + ((i + 0.5) / data.length) * iw
      : (i) => m.l + (data.length === 1 ? iw / 2 : (i / (data.length - 1)) * iw);
    const Y = (v) => m.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
    const gid = id('g');
    const pts = data.map((d, i) => [X(i), Y(d.value)]);

    const grid = ticks.map((t) => `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${Y(t)}" y2="${Y(t)}"/><text class="ax" x="${m.l - 10}" y="${Y(t) + 4}" text-anchor="end">${compact(t)}</text>`).join('');
    const every = Math.max(1, Math.ceil(data.length / Math.max(2, Math.floor(iw / 64))));
    const shown = new Set();
    for (let i = 0; i < data.length; i += every) shown.add(i);
    const last = data.length - 1;
    if (!shown.has(last)) {
      const prev = Math.max(...shown);
      if (last - prev < every * 0.75) shown.delete(prev);
      shown.add(last);
    }
    const anchor = (i) => (i === 0 ? 'start' : i === last ? 'end' : 'middle');
    const xl = [...shown].map((i) => `<text class="ax" x="${X(i)}" y="${height - 8}" text-anchor="${anchor(i)}">${esc(data[i].label ?? shortDate(data[i].date))}</text>`).join('');

    let body = '';
    if (type === 'bar') {
      const bw = Math.max(2, Math.min(34, (iw / data.length) * 0.62));
      body = data.map((d, i) => `<rect x="${X(i) - bw / 2}" y="${Y(Math.max(d.value, 0))}" width="${bw}" height="${Math.max(1.5, Math.abs(Y(0) - Y(d.value)))}" rx="${Math.min(4, bw / 2)}" fill="${color}" opacity="${0.92}"/>`).join('');
    } else {
      const line = monotone(pts);
      const area = `${line}L${pts.at(-1)[0]},${m.t + ih}L${pts[0][0]},${m.t + ih}Z`;
      body = `${type === 'area' ? `<path d="${area}" fill="url(#${gid})"/>` : ''}<path d="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`;
      if (data.length <= 40) body += pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="2.6" fill="var(--surface)" stroke="${color}" stroke-width="1.6"/>`).join('');
    }
    const refLine = ref != null ? `<line class="ref" x1="${m.l}" x2="${w - m.r}" y1="${Y(ref)}" y2="${Y(ref)}"/><text class="ax ref-l" x="${w - m.r}" y="${Y(ref) - 6}" text-anchor="end">${opt.refLabel ?? ''}</text>` : '';
    const hit = el._hits = data.map((d, i) => ({ x: X(i), y: Y(d.value), html: tipHtml(d.label ?? d.date, fmt(d.value), color) }));
    return `<div class="chart" style="--h:${height}px"><svg width="${w}" height="${height}" viewBox="0 0 ${w} ${height}" role="img" aria-label="${esc(opt.label || '图表')}">
      <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".32"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
      ${grid}${refLine}${body}${xl}
      <line class="guide" y1="${m.t}" y2="${m.t + ih}" hidden/><circle class="hover-dot" r="5" fill="${color}" stroke="var(--surface)" stroke-width="2" hidden/>
    </svg><div class="tip" hidden></div></div>`;
  });
}

/** Tiny inline trend line for stat cards. */
export function spark(values, color = 'var(--accent)', w = 112, h = 34) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return '';
  const lo = Math.min(...v), hi = Math.max(...v);
  const pts = v.map((x, i) => [(i / (v.length - 1)) * (w - 4) + 2, h - 3 - ((x - lo) / (hi - lo || 1)) * (h - 8)]);
  const gid = id('s');
  const line = monotone(pts);
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="${line}L${w - 2},${h}L2,${h}Z" fill="url(#${gid})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="${pts.at(-1)[0]}" cy="${pts.at(-1)[1]}" r="2.6" fill="${color}"/></svg>`;
}

/** Circular progress. */
export function ring({ value, max, color = 'var(--accent)', size = 148, stroke = 12, big, small }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, max ? value / max : 0));
  return `<div class="ring" style="width:${size}px;height:${size}px"><svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(big)}，${esc(small)}"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="${stroke}"/><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round" stroke-dasharray="${c * p} ${c}" transform="rotate(-90 ${size / 2} ${size / 2})"/></svg><div class="ring-in"><b class="num">${esc(big)}</b><span>${esc(small)}</span></div></div>`;
}

/** Horizontal stacked proportion bar with legend data returned by caller. */
export function stack(parts, total) {
  const sum = (total ?? parts.reduce((a, p) => a + p.value, 0)) || 1;
  return `<div class="stack" role="img" aria-label="${esc(parts.map((p) => `${p.label} ${Math.round((p.value / sum) * 100)}%`).join('，'))}">${parts.filter((p) => p.value > 0).map((p) => `<i style="flex:${p.value};background:${p.color}" title="${esc(p.label)}"></i>`).join('')}</div>`;
}

/** Sleep hypnogram: awake / rem / light / deep on four lanes. stages = [{stage, minutes}] in order. */
export function hypnogram(el, stages, colors) {
  const lanes = ['awake', 'rem', 'light', 'deep'];
  const labels = { awake: '清醒', rem: 'REM', light: '浅睡', deep: '深睡' };
  const total = stages.reduce((a, s) => a + s.minutes, 0) || 1;
  frame(el, (w) => {
    const m = { l: 44, r: 8, t: 6, b: 6 }, lh = 26, h = lanes.length * lh + m.t + m.b;
    const iw = w - m.l - m.r;
    let x = m.l;
    const blocks = stages.map((s) => {
      const bw = Math.max(1.5, (s.minutes / total) * iw);
      const y = m.t + lanes.indexOf(s.stage) * lh + 4;
      const out = `<rect x="${x}" y="${y}" width="${bw - 1}" height="${lh - 8}" rx="3" fill="${colors[s.stage]}"/>`;
      x += bw;
      return out;
    }).join('');
    const names = lanes.map((l, i) => `<text class="ax" x="0" y="${m.t + i * lh + lh / 2 + 4}">${labels[l]}</text><line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${m.t + (i + 1) * lh - 0.5}" y2="${m.t + (i + 1) * lh - 0.5}"/>`).join('');
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="睡眠分期">${names}${blocks}</svg>`;
  });
}

/** Calendar heat strip: one cell per day, intensity by value / max. */
export function heatStrip(el, days, color) {
  const max = Math.max(...days.map((d) => d.value || 0), 1);
  el.innerHTML = `<div class="heat">${days.map((d) => {
    const a = d.value == null ? 0 : 0.14 + 0.86 * Math.min(1, d.value / max);
    return `<i class="${d.value == null ? 'gap' : ''}" style="--a:${a};--c:${color}" title="${esc(d.title)}"></i>`;
  }).join('')}</div>`;
}
