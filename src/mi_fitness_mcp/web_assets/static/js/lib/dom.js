// html`` escapes every interpolated value unless it is already Safe markup,
// so untrusted strings (labels, user ids, API errors) can never become HTML.
export class Safe {
  constructor(s) {
    this.s = s;
  }
  toString() {
    return this.s;
  }
}

const MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => MAP[c]);
export const raw = (s) => (s instanceof Safe ? s : new Safe(s));

const part = (v) => (v instanceof Safe ? v.s : Array.isArray(v) ? v.map(part).join('') : esc(v));

export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += part(vals[i]) + strings[i + 1];
  return new Safe(out);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const when = (cond, content) => (cond ? content : '');

export function mount(el, content) {
  el.innerHTML = content instanceof Safe ? content.s : esc(content);
}
