import { raw } from './dom.js';

// Inline SVG icon set (stroke icons, 24px grid). No emoji anywhere in the UI.
const P = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  trend: '<path d="M3 17l5.5-6 4 4L21 6"/><path d="M15 6h6v6"/>',
  moon: '<path d="M20 14.2A8 8 0 1 1 9.8 4a6.5 6.5 0 0 0 10.2 10.2z"/>',
  insight: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3.2"/>',
  data: '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/><path d="M4 11.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  sync: '<path d="M20 11a8 8 0 0 0-14.3-4.5L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M4 13a8 8 0 0 0 14.3 4.5L20 15.5"/><path d="M20 20v-4.5h-4.5"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3"/><path d="m16 7 3 3"/><path d="m14 9 2 2"/>',
  steps: '<path d="M8 4.5c2 0 3 1.5 3 3.5s-1 3.5-3 3.5S5 10 5 8s1-3.5 3-3.5z"/><path d="M5.5 14.5h5l-.6 3.2a2 2 0 0 1-2 1.6h-.2a2 2 0 0 1-2-1.6z"/><path d="M16 9c2 0 3 1.5 3 3.5S18 16 16 16s-3-1.5-3-3.5S14 9 16 9z"/><path d="M13.5 19.5h5"/>',
  flame: '<path d="M12 3c.5 3.2 4.5 5 4.5 9.5a4.5 4.5 0 0 1-9 0c0-1.7.8-2.8 1.7-3.8.2 1.2.8 2 1.6 2.3C10.5 8.5 11 5.5 12 3z"/>',
  heart: '<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.2 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z"/>',
  pulse: '<path d="M3 12h4l2.2-6 3.6 12 2.2-6H21"/>',
  drop: '<path d="M12 3.5s6 6.2 6 10.5a6 6 0 0 1-12 0c0-4.3 6-10.5 6-10.5z"/>',
  mind: '<path d="M9.5 4.5A3.5 3.5 0 0 0 6 8a3 3 0 0 0-1.5 5.2A3.5 3.5 0 0 0 8 19a3 3 0 0 0 4-1.5V6a2.5 2.5 0 0 0-2.5-1.5z"/><path d="M14.5 4.5A3.5 3.5 0 0 1 18 8a3 3 0 0 1 1.5 5.2A3.5 3.5 0 0 1 16 19a3 3 0 0 1-4-1.5"/>',
  scale: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8.5 9.5a4 4 0 0 1 7 0"/><path d="m12 9.5 1.5-1.5"/>',
  run: '<circle cx="14.5" cy="4.5" r="1.8"/><path d="m9 21 2.5-5.5L9 13l1.5-4.5 3.5 1.5 2.5 3"/><path d="m6 12 3-3.5"/><path d="m13 16 3 5"/>',
  alert: '<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/>',
  download: '<path d="M12 4v11"/><path d="m7.5 11 4.5 4.5 4.5-4.5"/><path d="M5 20h14"/>',
  code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13.5 5-3 14"/>',
  qr: '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17.5 20H20v-3"/>',
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  empty: '<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M3.5 10h17"/><path d="M8 15h3"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5v-2a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.8 12.5h9.4L17.5 7"/>',
  arrowUp: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  arrowDown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  dash: '<path d="M6 12h12"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H5a2 2 0 0 0 0 4h3M16 6h3a2 2 0 0 1 0 4h-3"/><path d="M12 13v4M8.5 20h7M10 17h4"/>',
  bolt: '<path d="m13 3-8 11h6l-1 7 8-11h-6z"/>',
};

export const icon = (name, cls = '') =>
  raw(`<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || ''}</svg>`);

export const LOGO = raw(`<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="9" fill="none" stroke="currentColor" stroke-width="1.6" opacity=".4"/><path d="M5 17h5.5l3-8 5 15 3-9H27" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`);
