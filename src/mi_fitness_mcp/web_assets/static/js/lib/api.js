const KEY = 'mi_fitness_api_key';
export const getKey = () => localStorage.getItem(KEY) || '';
export const setKey = (k) => (k ? localStorage.setItem(KEY, k) : localStorage.removeItem(KEY));

const listeners = new Set();
export const log = { last: null };
export const onResponse = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};

export function qs(params = {}) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '') continue;
    (Array.isArray(v) ? v : [v]).forEach((x) => u.append(k, x));
  }
  const s = u.toString();
  return s ? `?${s}` : '';
}

function describe(json, status) {
  const d = json && (json.detail ?? json.error);
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join('；');
  if (d && typeof d === 'object') return JSON.stringify(d);
  return d || `HTTP ${status}`;
}

export async function request(method, path, { body, params } = {}) {
  const url = path + qs(params);
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (getKey()) headers['X-API-Key'] = getKey();
  const t0 = performance.now();
  let status = 0;
  let text = '';
  let error = null;
  try {
    const res = await fetch('/proxy' + url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    status = res.status;
    text = await res.text();
  } catch {
    error = '无法连接到仪表盘服务，请确认它仍在运行';
  }
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    /* 非 JSON 响应 */
  }
  const ok = status >= 200 && status < 300;
  if (!ok && !error) error = describe(json, status);
  const out = {
    ok, status, error, method, path: url, body,
    ms: Math.round(performance.now() - t0),
    raw: json ?? text,
    data: json && json.data !== undefined ? json.data : json,
  };
  log.last = out;
  listeners.forEach((fn) => fn(out));
  if (status === 401) document.dispatchEvent(new CustomEvent('auth-required'));
  return out;
}

export const get = (path, params) => request('GET', path, { params });
export const post = (path, body, params) => request('POST', path, { body: body ?? {}, params });
export const del = (path) => request('DELETE', path);

export async function blob(path, params) {
  const headers = getKey() ? { 'X-API-Key': getKey() } : {};
  const res = await fetch('/proxy' + path + qs(params), { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.blob();
}
