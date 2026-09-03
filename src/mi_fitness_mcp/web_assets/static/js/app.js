/**
 * Mi Fitness Dashboard & Developer Console
 * Core Client-side Application Logic
 */

// State Store
const state = {
  activeTab: 'overview',
  theme: localStorage.getItem('mi_fitness_theme') || 'dark',
  apiKey: localStorage.getItem('mi_fitness_api_key') || '',
  qrToken: '',
  qrPollInterval: null,
  qrTimerInterval: null,
  qrExpiresIn: 300,
  syncTypes: ['daily_activity', 'heart_rate', 'sleep', 'workouts', 'body_measurements', 'spo2', 'stress', 'abnormal_heart_beat'],
  lastResponse: null,
  lastRequestInfo: null,
};

// DOM Utilities
const $ = (id) => document.getElementById(id);
const $$ = (selector) => document.querySelectorAll(selector);

// Date Helpers
const formatDate = (d) => {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

function initDates() {
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  
  $('startDate').value = formatDate(yesterday);
  $('endDate').value = formatDate(today);
}

function setDatePreset(type) {
  const today = new Date();
  let start = new Date();
  
  $$('.preset-pill').forEach(p => p.classList.remove('active'));
  
  if (type === 'today') {
    start = today;
  } else if (type === 'yesterday') {
    start = new Date(Date.now() - 86400000);
    today.setDate(today.getDate() - 1);
  } else if (type === '7d') {
    start = new Date(Date.now() - 7 * 86400000);
  } else if (type === '30d') {
    start = new Date(Date.now() - 30 * 86400000);
  } else if (type === 'month') {
    start = new Date(today.getFullYear(), today.getMonth(), 1);
  }
  
  $('startDate').value = formatDate(start);
  $('endDate').value = formatDate(type === 'yesterday' ? start : new Date());
  
  const el = $(`preset-${type}`);
  if (el) el.classList.add('active');
}

const getSd = () => $('startDate').value;
const getEd = () => $('endDate').value;
const getRangeQuery = (path) => `${path}?start_date=${getSd()}&end_date=${getEd()}`;
const getSelQuery = (id, param) => {
  const v = $(id).value;
  return v ? `&${param}=${encodeURIComponent(v)}` : '';
};
const getLimQuery = (id) => {
  const v = $(id).value.trim();
  return v ? `&limit=${encodeURIComponent(v)}` : '';
};

// UI Theme Management
function initTheme() {
  document.documentElement.setAttribute('data-theme', state.theme);
  updateThemeIcon();
}

function toggleTheme() {
  state.theme = state.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('mi_fitness_theme', state.theme);
  document.documentElement.setAttribute('data-theme', state.theme);
  updateThemeIcon();
  showToast('主题已切换', `已切换至 ${state.theme === 'dark' ? '深色模式 🌙' : '浅色模式 ☀️'}`, 'info');
}

function updateThemeIcon() {
  const btn = $('themeToggleBtn');
  if (btn) {
    btn.innerHTML = state.theme === 'dark' ? '🌙' : '☀️';
  }
}

// Toast Notifications
function showToast(title, desc = '', type = 'info', duration = 3500) {
  const container = $('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast-message ${type}`;
  
  const icons = {
    success: '✅',
    error: '❌',
    warning: '⚠️',
    info: '💡',
  };

  toast.innerHTML = `
    <div class="toast-icon">${icons[type] || 'ℹ️'}</div>
    <div class="toast-content">
      <div class="toast-title">${title}</div>
      ${desc ? `<div class="toast-desc">${desc}</div>` : ''}
    </div>
  `;

  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add('toast-leaving');
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// Tab Switching
function switchTab(tabId) {
  state.activeTab = tabId;
  
  $$('.nav-item').forEach(item => {
    item.classList.toggle('active', item.getAttribute('data-tab') === tabId);
  });
  
  $$('.tab-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === `tab-${tabId}`);
  });
}

// API Communication & Proxy Handler
function getHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const key = state.apiKey || $('apiKeyInput').value.trim();
  if (key) {
    headers['X-API-Key'] = key;
  }
  return headers;
}

async function api(method, path, body = null) {
  const startTime = performance.now();
  const url = '/proxy' + path;
  
  updateInspectorHeader(method, path, 'pending', '请求中…');
  $('inspectorBody').innerHTML = `<div class="inspector-empty-state"><div class="spinner"></div><div>正在通过 Flask 代理请求后端...</div></div>`;

  try {
    const options = {
      method,
      headers: getHeaders(),
    };
    if (body) {
      options.body = JSON.stringify(body);
    }

    const resp = await fetch(url, options);
    const latency = Math.round(performance.now() - startTime);
    const text = await resp.text();

    let jsonObj = null;
    try {
      jsonObj = JSON.parse(text);
    } catch (e) {
      // plain text
    }

    state.lastResponse = jsonObj || text;
    state.lastRequestInfo = {
      method,
      path,
      url,
      status: resp.status,
      latency,
      headers: options.headers,
      body,
    };

    updateInspectorHeader(method, path, resp.ok ? 'ok' : 'err', `HTTP ${resp.status}`, latency);
    renderJsonViewer(state.lastResponse);

    if (!resp.ok) {
      showToast(`请求失败: HTTP ${resp.status}`, jsonObj?.error || jsonObj?.detail || '请查看控制台输出', 'error');
    }

    return jsonObj;
  } catch (err) {
    const latency = Math.round(performance.now() - startTime);
    updateInspectorHeader(method, path, 'err', '网络错误', latency);
    $('inspectorBody').innerHTML = `<div style="color:var(--status-error);padding:12px;">❌ 代理网络连接错误: ${err.message}<br><br>💡 请检查:<br>1. Python FastAPI 是否在 127.0.0.1:8321 运行<br>2. Flask 代理是否正常监听</div>`;
    showToast('代理连接失败', '无法连接到后端服务，请确认端口与服务状态', 'error');
    return null;
  }
}

// Inspector View Rendering
function updateInspectorHeader(method, path, statusType, statusText, latency = 0) {
  const methodClass = method.toLowerCase();
  $('metaMethod').className = `http-method-pill ${methodClass}`;
  $('metaMethod').textContent = method;
  
  $('metaStatus').className = `status-pill ${statusType}`;
  $('metaStatus').textContent = statusText;
  
  $('metaLatency').textContent = latency > 0 ? `${latency} ms` : '...';
  $('metaUrl').textContent = path;
}

function renderJsonViewer(data) {
  if (!data) {
    $('inspectorBody').innerHTML = `<div class="inspector-empty-state"><div class="inspector-empty-icon">📂</div><div>暂无返回数据</div></div>`;
    return;
  }

  const jsonStr = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  $('inspectorBody').innerHTML = highlightJson(jsonStr);
}

function highlightJson(json) {
  if (typeof json !== 'string') {
    json = JSON.stringify(json, null, 2);
  }
  json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
    let cls = 'json-number';
    if (/^"/.test(match)) {
      if (/:$/.test(match)) {
        cls = 'json-key';
      } else {
        cls = 'json-string';
      }
    } else if (/true|false/.test(match)) {
      cls = 'json-boolean';
    } else if (/null/.test(match)) {
      cls = 'json-null';
    }
    return `<span class="${cls}">${match}</span>`;
  });
}

function filterInspectorJson(query) {
  if (!state.lastResponse) return;
  const q = query.trim().toLowerCase();
  if (!q) {
    renderJsonViewer(state.lastResponse);
    return;
  }
  const str = JSON.stringify(state.lastResponse, null, 2);
  const lines = str.split('\n');
  const filtered = lines.filter(line => line.toLowerCase().includes(q)).join('\n');
  $('inspectorBody').innerHTML = `<div style="color:var(--accent-cyan);font-size:11px;margin-bottom:8px;">🔍 匹配到 ${lines.filter(l => l.toLowerCase().includes(q)).length} 行:</div>` + highlightJson(filtered || '未找到匹配项');
}

// Copy & Export Tools
function copyResponse() {
  if (!state.lastResponse) {
    showToast('无可复制数据', '', 'warning');
    return;
  }
  const text = typeof state.lastResponse === 'string' ? state.lastResponse : JSON.stringify(state.lastResponse, null, 2);
  navigator.clipboard.writeText(text).then(() => {
    showToast('复制成功', '响应 JSON 已存入剪贴板', 'success');
  });
}

function copyCurl() {
  if (!state.lastRequestInfo) {
    showToast('无可用请求', '请先发起一次 API 请求', 'warning');
    return;
  }
  const { method, path, headers, body } = state.lastRequestInfo;
  let curl = `curl -X ${method} "http://127.0.0.1:8321${path}"`;
  for (const [k, v] of Object.entries(headers)) {
    curl += ` \\\n  -H "${k}: ${v}"`;
  }
  if (body) {
    curl += ` \\\n  -d '${JSON.stringify(body)}'`;
  }
  navigator.clipboard.writeText(curl).then(() => {
    showToast('cURL 命令已复制', '可在终端直接执行该请求', 'success');
  });
}

function downloadJson() {
  if (!state.lastResponse) {
    showToast('无可下载数据', '', 'warning');
    return;
  }
  const text = typeof state.lastResponse === 'string' ? state.lastResponse : JSON.stringify(state.lastResponse, null, 2);
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `mi-fitness-response-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(url);
  showToast('下载完成', 'JSON 文件已保存', 'success');
}

// Overview Dashboard Loader
async function loadOverview() {
  showToast('正在加载仪表盘概览…', '', 'info', 1500);
  const summaryPromise = api('GET', getRangeQuery('/api/summary'));
  const hrPromise = api('GET', getRangeQuery('/api/heart-rate') + '&sample_type=resting&limit=1');
  const sleepPromise = api('GET', getRangeQuery('/api/sleep') + '&include_naps=true');
  const spo2Promise = api('GET', getRangeQuery('/api/spo2') + '&limit=1');
  const stressPromise = api('GET', getRangeQuery('/api/stress') + '&limit=1');

  const [summary, hr, sleep, spo2, stress] = await Promise.all([
    summaryPromise, hrPromise, sleepPromise, spo2Promise, stressPromise
  ]);

  if (summary && Array.isArray(summary) && summary.length > 0) {
    const latest = summary[summary.length - 1];
    $('statSteps').textContent = latest.steps ? latest.steps.toLocaleString() : '0';
    $('statCalories').textContent = latest.active_calories ? Math.round(latest.active_calories) : '0';
    $('statDistance').textContent = latest.distance ? (latest.distance / 1000).toFixed(2) : '0';
  }

  if (hr && Array.isArray(hr) && hr.length > 0) {
    $('statHr').textContent = hr[0].bpm || '--';
  }

  if (sleep && Array.isArray(sleep) && sleep.length > 0) {
    const lastSleep = sleep[sleep.length - 1];
    const totalMinutes = lastSleep.duration_minutes || (lastSleep.end_time - lastSleep.start_time) / 60;
    const hours = (totalMinutes / 60).toFixed(1);
    $('statSleep').textContent = isNaN(hours) ? '--' : hours;
  }

  if (spo2 && Array.isArray(spo2) && spo2.length > 0) {
    $('statSpo2').textContent = spo2[0].spo2_value ? `${spo2[0].spo2_value}%` : '--';
  }

  if (stress && Array.isArray(stress) && stress.length > 0) {
    $('statStress').textContent = stress[0].stress_value || '--';
  }

  showToast('仪表盘已刷新', '最新数据已加载', 'success');
}

// Health Data Queries
async function querySummary() {
  const data = await api('GET', getRangeQuery('/api/summary'));
  renderSummaryVisual(data);
}

function renderSummaryVisual(data) {
  const container = $('summaryVisualContainer');
  if (!container) return;
  if (!data || !Array.isArray(data) || data.length === 0) {
    container.innerHTML = `<div class="inspector-empty-state"><p>所选时间段内无活动数据</p></div>`;
    return;
  }

  let html = `
    <div class="data-table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>步数</th>
            <th>活动卡路里 (kcal)</th>
            <th>距离 (km)</th>
          </tr>
        </thead>
        <tbody>
  `;

  data.forEach(item => {
    const distKm = item.distance ? (item.distance / 1000).toFixed(2) : '0.00';
    html += `
      <tr>
        <td><strong>${item.date || '--'}</strong></td>
        <td><span class="badge badge-success">👟 ${(item.steps || 0).toLocaleString()} 步</span></td>
        <td>🔥 ${Math.round(item.active_calories || 0)} kcal</td>
        <td>📍 ${distKm} km</td>
      </tr>
    `;
  });

  html += `</tbody></table></div>`;
  container.innerHTML = html;
}

async function queryHeartRate() {
  const q = getRangeQuery('/api/heart-rate') + getSelQuery('hrType', 'sample_type') + getLimQuery('hrLimit');
  await api('GET', q);
}

async function querySleep() {
  const q = getRangeQuery('/api/sleep') + '&include_naps=' + $('napsSelect').value;
  const data = await api('GET', q);
  renderSleepTimeline(data);
}

function renderSleepTimeline(data) {
  const container = $('sleepTimelineWrapper');
  if (!container) return;
  if (!data || !Array.isArray(data) || data.length === 0) {
    container.innerHTML = '<p class="panel-subtitle">无睡眠分段数据</p>';
    return;
  }

  const latest = data[data.length - 1];
  const deep = latest.deep_sleep_minutes || 0;
  const light = latest.light_sleep_minutes || 0;
  const rem = latest.rem_sleep_minutes || 0;
  const awake = latest.awake_minutes || 0;
  const total = deep + light + rem + awake || 1;

  container.innerHTML = `
    <div class="sleep-timeline-container">
      <div class="sleep-timeline-bar">
        <div class="sleep-segment deep" style="width:${(deep / total) * 100}%" data-tooltip="深睡: ${deep} 分钟"></div>
        <div class="sleep-segment light" style="width:${(light / total) * 100}%" data-tooltip="浅睡: ${light} 分钟"></div>
        <div class="sleep-segment rem" style="width:${(rem / total) * 100}%" data-tooltip="快速眼动 (REM): ${rem} 分钟"></div>
        <div class="sleep-segment awake" style="width:${(awake / total) * 100}%" data-tooltip="清醒: ${awake} 分钟"></div>
      </div>
      <div class="sleep-legend">
        <div class="legend-item"><span class="legend-dot deep"></span>深睡 ${deep}m (${Math.round((deep/total)*100)}%)</div>
        <div class="legend-item"><span class="legend-dot light"></span>浅睡 ${light}m (${Math.round((light/total)*100)}%)</div>
        <div class="legend-item"><span class="legend-dot rem"></span>REM ${rem}m (${Math.round((rem/total)*100)}%)</div>
        <div class="legend-item"><span class="legend-dot awake"></span>清醒 ${awake}m</div>
      </div>
    </div>
  `;
}

async function queryWorkouts() {
  await api('GET', getRangeQuery('/api/workouts'));
}

async function queryBody() {
  const isLatest = $('latestOnlyCheck').checked;
  await api('GET', getRangeQuery('/api/body-measurements') + '&latest_only=' + isLatest);
}

async function querySpo2() {
  await api('GET', getRangeQuery('/api/spo2') + getLimQuery('spo2Limit'));
}

async function queryStress() {
  await api('GET', getRangeQuery('/api/stress') + getSelQuery('stressLevel', 'level') + getLimQuery('stressLimit'));
}

async function queryAbnormalHr() {
  await api('GET', getRangeQuery('/api/abnormal-heart-beat'));
}

// Metric Series Analytics
async function queryMetricSeries() {
  const metric = $('metricSelect').value;
  const gran = $('granularitySelect').value;
  const agg = $('aggregationSelect').value;
  const q = `${getRangeQuery('/api/metric-series')}&metric=${metric}&granularity=${gran}&aggregation=${agg}`;
  const data = await api('GET', q);
  renderMetricChart(data, metric);
}

function renderMetricChart(data, metricName) {
  const container = $('metricChartContainer');
  if (!container) return;
  if (!data || !Array.isArray(data) || data.length === 0) {
    container.innerHTML = '<div class="inspector-empty-state"><p>所选范围内无序列数据</p></div>';
    return;
  }

  const maxVal = Math.max(...data.map(d => d.value || 0), 1);
  let barsHtml = '';

  data.forEach(item => {
    const val = item.value || 0;
    const heightPercent = Math.max(Math.round((val / maxVal) * 100), 5);
    const dateLabel = item.timestamp ? item.timestamp.split('T')[0].slice(5) : (item.date || '');

    barsHtml += `
      <div class="chart-bar-group">
        <div class="chart-bar" style="height:${heightPercent}%">
          <div class="chart-bar-tooltip">${item.date || item.timestamp || ''}: ${val.toLocaleString()}</div>
        </div>
        <div class="chart-bar-label">${dateLabel}</div>
      </div>
    `;
  });

  container.innerHTML = barsHtml;
}

// Sync Center Control
function toggleSyncTag(tag) {
  const idx = state.syncTypes.indexOf(tag);
  if (idx > -1) {
    if (state.syncTypes.length > 1) {
      state.syncTypes.splice(idx, 1);
    } else {
      showToast('至少保留一种同步类型', '', 'warning');
      return;
    }
  } else {
    state.syncTypes.push(tag);
  }
  renderSyncPills();
}

function selectAllSyncTags(all = true) {
  if (all) {
    state.syncTypes = ['daily_activity', 'heart_rate', 'sleep', 'workouts', 'body_measurements', 'spo2', 'stress', 'abnormal_heart_beat'];
  } else {
    state.syncTypes = ['daily_activity'];
  }
  renderSyncPills();
}

function renderSyncPills() {
  const container = $('syncTagPills');
  if (!container) return;
  const allTypes = [
    { id: 'daily_activity', name: '每日活动' },
    { id: 'heart_rate', name: '心率' },
    { id: 'sleep', name: '睡眠' },
    { id: 'workouts', name: '运动' },
    { id: 'body_measurements', name: '体成分' },
    { id: 'spo2', name: '血氧' },
    { id: 'stress', name: '压力' },
    { id: 'abnormal_heart_beat', name: '异常心跳' },
  ];

  container.innerHTML = allTypes.map(t => {
    const isSelected = state.syncTypes.includes(t.id);
    return `
      <div class="tag-pill ${isSelected ? 'selected' : ''}" onclick="toggleSyncTag('${t.id}')">
        <span class="tag-check">✓</span>
        <span>${t.name}</span>
      </div>
    `;
  }).join('');
}

async function triggerSync(background = false) {
  const body = {
    start_date: getSd(),
    end_date: getEd(),
    background,
    data_types: state.syncTypes,
  };

  showToast(background ? '已触发后台异步同步' : '正在执行前台同步…', '请耐心等待云端响应', 'info');
  const res = await api('POST', '/api/sync', body);

  if (background && res && res.sync_id) {
    $('syncIdInput').value = res.sync_id;
    showToast('已获取同步任务 ID', res.sync_id, 'success');
  } else if (!background && res) {
    showToast('前台同步完成', '健康数据已更新至本地 SQLite', 'success');
  }
}

async function pollSyncStatus() {
  const syncId = $('syncIdInput').value.trim();
  if (!syncId) {
    showToast('请输入同步任务 ID', '', 'warning');
    return;
  }
  await api('GET', `/api/sync/${encodeURIComponent(syncId)}`);
}

// QR Code Authentication Modal & Auto Polling
function openQrModal() {
  $('qrModal').classList.add('active');
  startQrFlow();
}

function closeQrModal() {
  $('qrModal').classList.remove('active');
  stopQrFlow();
}

async function startQrFlow() {
  stopQrFlow();
  $('qrStatusText').textContent = '正在向小米账号服务器生成登录二维码...';
  $('qrScanBeam').style.display = 'none';

  const res = await api('POST', '/api/auth/qr/start?region=cn');
  if (res && res.qr_token) {
    state.qrToken = res.qr_token;
    state.qrExpiresIn = res.expires_in || 300;
    
    $('qrImage').src = `/proxy/api/auth/qr/${res.qr_token}.png`;
    $('qrLoginUrl').value = res.login_url || '';
    $('qrScanBeam').style.display = 'block';
    $('qrStatusText').textContent = '请打开小米运动健康/米家/小米账号 App 扫码并在手机上点击确认';

    startQrTimer();
    startQrPolling();
  } else {
    $('qrStatusText').textContent = '二维码生成失败，请确认后端连接正常';
  }
}

function startQrTimer() {
  const total = state.qrExpiresIn;
  let remaining = total;
  
  state.qrTimerInterval = setInterval(() => {
    remaining--;
    const mins = Math.floor(remaining / 60);
    const secs = String(remaining % 60).padStart(2, '0');
    $('qrTimerText').textContent = `有效期剩余: ${mins}:${secs}`;
    $('qrProgressBar').style.width = `${(remaining / total) * 100}%`;

    if (remaining <= 0) {
      stopQrFlow();
      $('qrStatusText').textContent = '二维码已过期，请点击「刷新二维码」重试';
    }
  }, 1000);
}

function startQrPolling() {
  state.qrPollInterval = setInterval(async () => {
    if (!state.qrToken) return;
    try {
      const res = await fetch(`/proxy/api/auth/qr/poll?token=${state.qrToken}`, {
        headers: getHeaders(),
      });
      const data = await res.json();

      if (data.status === 'scanned') {
        $('qrStatusText').innerHTML = '📱 <strong>已扫码！</strong> 请在手机上点击「确认登录」';
      } else if (data.status === 'confirmed' && data.api_key) {
        stopQrFlow();
        $('qrStatusText').innerHTML = `✅ <strong>登录成功！</strong> API Key 已自动保存`;
        saveApiKey(data.api_key);
        showToast('扫码授权成功', `API Key: ${data.api_key.slice(0, 16)}…`, 'success');
        setTimeout(() => closeQrModal(), 1800);
      }
    } catch (e) {
      // ignore transient poll error
    }
  }, 2000);
}

function stopQrFlow() {
  if (state.qrPollInterval) clearInterval(state.qrPollInterval);
  if (state.qrTimerInterval) clearInterval(state.qrTimerInterval);
  state.qrPollInterval = null;
  state.qrTimerInterval = null;
}

function copyLoginUrl() {
  const url = $('qrLoginUrl').value;
  if (!url) return;
  navigator.clipboard.writeText(url).then(() => {
    showToast('登录链接已复制', '也可在手机浏览器打开该链接登录确认', 'success');
  });
}

// API Key Management
function saveApiKey(key) {
  state.apiKey = key.trim();
  localStorage.setItem('mi_fitness_api_key', state.apiKey);
  $('apiKeyInput').value = state.apiKey;
}

function initApiKeyInput() {
  const input = $('apiKeyInput');
  input.value = state.apiKey;
  input.addEventListener('change', (e) => {
    saveApiKey(e.target.value);
    showToast('API Key 已更新', '', 'info');
  });
}

async function createKeyWithCredentials() {
  const userId = $('keyUserId').value.trim();
  const passToken = $('keyPassToken').value.trim();
  const label = $('keyLabel').value.trim() || 'web-console';

  if (!userId || !passToken) {
    showToast('请填写 user_id 和 passToken', '', 'warning');
    return;
  }

  const res = await api('POST', '/api/auth/keys', {
    user_id: userId,
    pass_token: passToken,
    region: 'cn',
    label,
  });

  if (res && res.api_key) {
    saveApiKey(res.api_key);
    showToast('API Key 发放成功', '已自动填入全局请求头', 'success');
    listKeys();
  }
}

async function listKeys() {
  const data = await api('GET', '/api/auth/keys');
  renderKeysTable(data);
}

function renderKeysTable(data) {
  const container = $('keysTableWrapper');
  if (!container) return;
  if (!data || !Array.isArray(data) || data.length === 0) {
    container.innerHTML = '<p class="panel-subtitle">暂无已发放的 API Key 记录</p>';
    return;
  }

  let html = `
    <div class="data-table-container">
      <table class="data-table">
        <thead>
          <tr>
            <th>标签</th>
            <th>Key 前缀</th>
            <th>User ID</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
  `;

  data.forEach(k => {
    html += `
      <tr>
        <td><strong>${k.label || '默认'}</strong></td>
        <td><code>${k.prefix || k.api_key_prefix || 'mif_sk_...'}</code></td>
        <td>${k.user_id || '--'}</td>
        <td>${k.created_at || '--'}</td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="revokeKeyByPrefix('${k.prefix || k.api_key_prefix}')">吊销</button>
        </td>
      </tr>
    `;
  });

  html += '</tbody></table></div>';
  container.innerHTML = html;
}

async function revokeKeyByPrefix(prefix) {
  if (!prefix) {
    prefix = $('revokePrefixInput').value.trim();
  }
  if (!prefix) {
    showToast('请提供要吊销的 Key 前缀', '', 'warning');
    return;
  }
  if (!confirm(`确定要吊销前缀为 ${prefix} 的 API Key 吗？`)) return;

  await api('DELETE', `/api/auth/keys/${encodeURIComponent(prefix)}`);
  showToast('Key 吊销指令已发送', '', 'info');
  listKeys();
}

// System Connection & Health Check
async function checkSystemHealth() {
  const statusEl = $('backendStatusText');
  const dotEl = $('backendStatusDot');
  try {
    const res = await fetch('/api/proxy_status');
    const data = await res.json();
    if (data.backend_reachable) {
      dotEl.className = 'status-dot pulsing';
      statusEl.textContent = `FastAPI: 在线 (${data.backend_latency_ms}ms)`;
    } else {
      dotEl.className = 'status-dot offline pulsing';
      statusEl.textContent = 'FastAPI: 离线';
    }
  } catch (e) {
    dotEl.className = 'status-dot offline pulsing';
    statusEl.textContent = '代理服务异常';
  }
}

// Initialization on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  initDates();
  initApiKeyInput();
  renderSyncPills();
  checkSystemHealth();
  setInterval(checkSystemHealth, 15000);

  // Bind preset pills
  $$('.preset-pill').forEach(pill => {
    pill.addEventListener('click', (e) => {
      const type = e.target.getAttribute('data-preset');
      if (type) setDatePreset(type);
    });
  });

  // Tab Navigation
  $$('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      const tab = item.getAttribute('data-tab');
      if (tab) switchTab(tab);
    });
  });

  // Auto load overview stats
  loadOverview();
});
