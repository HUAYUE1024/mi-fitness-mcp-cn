export const num = (v, d = 0) =>
  v == null || Number.isNaN(Number(v))
    ? '—'
    : Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });

const pad = (n) => String(n).padStart(2, '0');
export const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const parseDay = (s) => {
  const [y, m, d] = s.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d);
};
export const todayStr = () => ymd(new Date());
export const addDays = (s, n) => {
  const d = parseDay(s);
  d.setDate(d.getDate() + n);
  return ymd(d);
};
export const daysBetween = (a, b) => Math.round((parseDay(b) - parseDay(a)) / 86400000);

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
export const weekday = (s) => WEEK[parseDay(s).getDay()];
export const weekdayShort = (s) => WEEK[parseDay(s).getDay()].slice(1);
export const mdLabel = (s) => `${Number(s.slice(5, 7))}月${Number(s.slice(8, 10))}日`;
export const shortDate = (s) => `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))}`;
export const hm = (iso) => (iso ? iso.slice(11, 16) : '—');

export function duration(min) {
  if (min == null) return '—';
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h ? `${h} 小时 ${pad(m)} 分` : `${m} 分`;
}

export const pace = (secPerKm) => {
  if (!secPerKm) return '—';
  return `${Math.floor(secPerKm / 60)}'${pad(Math.round(secPerKm % 60))}"`;
};

const SPORT = {
  running: '跑步',
  walking: '步行',
  cycling: '骑行',
  strength_training: '力量训练',
  swimming: '游泳',
  hiking: '徒步',
  yoga: '瑜伽',
  workout: '其他运动',
};
export const sportName = (k) => SPORT[k] || k;

export const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
