import { addDays, todayStr, ymd } from './lib/format.js';

const RANGE_KEY = 'mi_fitness_range';
export const PRESETS = [
  ['7', '7 天'],
  ['30', '30 天'],
  ['90', '90 天'],
  ['month', '本月'],
  ['year', '今年'],
];

function compute(preset) {
  const end = todayStr();
  if (preset === 'month') return { start: end.slice(0, 8) + '01', end, preset };
  if (preset === 'year') return { start: end.slice(0, 5) + '01-01', end, preset };
  return { start: addDays(end, -(Number(preset) - 1)), end, preset };
}

const saved = (() => {
  try { return JSON.parse(localStorage.getItem(RANGE_KEY)); } catch { return null; }
})();

export const state = {
  range: saved?.preset && saved.preset !== 'custom' ? compute(saved.preset) : saved?.start ? saved : compute('30'),
  status: null,
  coverage: [],
};

const subs = new Set();
export const subscribe = (fn) => { subs.add(fn); return () => subs.delete(fn); };

export function setRange(next) {
  state.range = next.preset && next.preset !== 'custom' ? compute(next.preset) : { ...next, preset: 'custom' };
  localStorage.setItem(RANGE_KEY, JSON.stringify(state.range));
  subs.forEach((fn) => fn(state.range));
}

export const clampDay = (d) => (d > todayStr() ? todayStr() : d);
export { ymd };
