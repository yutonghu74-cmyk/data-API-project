import { initSidebar } from './sidebar.js';
import { requireLogin, refreshUser, authHeaders, logout } from './auth.js';

if (!requireLogin()) throw new Error('redirect');

initSidebar();

const user = await refreshUser();
if (!user) { window.location.href = '/pages/login.html'; throw new Error('not logged in'); }

const isAdmin = user.role === 'admin';
const ADMIN_HEADERS = { 'Content-Type': 'application/json', 'X-Admin-Password': 'admin123' };
const API = 'http://localhost:8000';

document.getElementById('userBar').innerHTML = `
  <span class="username">${user.username}${isAdmin ? '（管理员）' : ''}</span>
  <button class="logout" id="logoutBtn">退出</button>`;
document.getElementById('logoutBtn').addEventListener('click', () => logout());

// ── Tab 切换 ─────────────────────────────────────────────
const platformTabBtn = document.getElementById('platformTab');
let platformLoaded = false;

if (isAdmin) {
  platformTabBtn.style.display = '';
  // admin 默认进入平台看板
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  platformTabBtn.classList.add('active');
  document.getElementById('tab-platform').classList.add('active');
}

document.querySelectorAll('#tabBar .tab').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.style.display === 'none') return;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    btn.classList.add('active');
    const panel = document.getElementById('tab-' + btn.dataset.tab);
    panel.classList.add('active');
    if (btn.dataset.tab === 'platform' && !platformLoaded) {
      platformLoaded = true;
      loadPlatform();
    }
  });
});

// ── 个人看板：今日概览 ───────────────────────────────────
async function loadMeToday() {
  try {
    const r = await fetch(`${API}/me/stats/today`, { headers: authHeaders() });
    const d = await r.json();
    document.getElementById('meTotal').textContent  = (d.total_calls || 0).toLocaleString();
    document.getElementById('meTokens').textContent = (d.total_tokens || 0).toLocaleString();
    document.getElementById('meCost').textContent   = '¥' + (d.total_cost || 0).toFixed(4);
    document.getElementById('meRate').textContent   = (d.success_rate || 0).toFixed(1) + '%';
  } catch (e) {
    ['meTotal','meTokens','meCost','meRate'].forEach(id => document.getElementById(id).textContent = '—');
  }
}

// ── 个人看板：按模型 ─────────────────────────────────────
async function loadMeByModel() {
  const tbody = document.getElementById('meModelTbody');
  const tfoot = document.getElementById('meModelFoot');
  try {
    const r = await fetch(`${API}/me/stats/by-model`, { headers: authHeaders() });
    const list = await r.json();
    if (!Array.isArray(list) || !list.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px">暂无数据</td></tr>';
      tfoot.innerHTML = '';
      return;
    }
    tbody.innerHTML = list.map(r => `
      <tr>
        <td>${r.model || '—'}</td>
        <td>${(r.calls || 0).toLocaleString()}</td>
        <td>${(r.in_tokens || 0).toLocaleString()}</td>
        <td>${(r.out_tokens || 0).toLocaleString()}</td>
        <td>${(r.tokens || 0).toLocaleString()}</td>
        <td>¥${(r.cost || 0).toFixed(4)}</td>
        <td>
          <span class="pct-bar"><span class="pct-bar-fill" style="width:${r.percent || 0}%"></span></span>
          ${(r.percent || 0).toFixed(1)}%
        </td>
      </tr>`).join('');
    const totCalls  = list.reduce((s,r) => s + (r.calls || 0), 0);
    const totIn     = list.reduce((s,r) => s + (r.in_tokens || 0), 0);
    const totOut    = list.reduce((s,r) => s + (r.out_tokens || 0), 0);
    const totTok    = list.reduce((s,r) => s + (r.tokens || 0), 0);
    const totCost   = list.reduce((s,r) => s + (r.cost || 0), 0);
    tfoot.innerHTML = `<tr>
      <td>合计 (${list.length})</td>
      <td>${totCalls.toLocaleString()}</td>
      <td>${totIn.toLocaleString()}</td>
      <td>${totOut.toLocaleString()}</td>
      <td>${totTok.toLocaleString()}</td>
      <td>¥${totCost.toFixed(4)}</td>
      <td>100%</td>
    </tr>`;
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#dc2626;padding:24px">加载失败</td></tr>';
  }
}

// ── 个人看板：按 API key 余额 ───────────────────────────
async function loadMeKeysBalance() {
  const tbody = document.getElementById('meKeysTbody');
  try {
    const r = await fetch(`${API}/me/stats/keys-balance`, { headers: authHeaders() });
    const list = await r.json();
    if (!Array.isArray(list) || !list.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:24px">暂无已审核 API</td></tr>';
      return;
    }
    const fmt = v => (v == null) ? '—' : (typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : v);
    tbody.innerHTML = list.map(r => {
      const status = r.exhausted
        ? '<span style="color:#dc2626">已用尽</span>'
        : (r.balance != null ? '<span style="color:var(--green)">正常</span>' : '<span style="color:var(--text-muted)">未知</span>');
      return `<tr>
        <td>${r.api_name || '—'}</td>
        <td>${r.provider || '—'}</td>
        <td>${r.sub_account_name || '—'}</td>
        <td>${r.project_name || '—'}</td>
        <td>${fmt(r.total)}</td>
        <td>${fmt(r.used)}</td>
        <td>${fmt(r.balance)}</td>
        <td>${status}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#dc2626;padding:24px">加载失败</td></tr>';
  }
}

// 个人看板首次进入即加载（默认激活）
loadMeToday();
loadMeByModel();
loadMeKeysBalance();

// ─────────────────────────────────────────────────────────
// 平台看板（仅 admin）
// ─────────────────────────────────────────────────────────

let currentDays = 7;
const MODEL_COLORS = [
  '#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6',  // 原 5 色(Top5 用)
  '#14b8a6', '#ec4899', '#06b6d4', '#84cc16', '#f97316',  // 扩展到 10 色(展开 Top10 用)
];

if (isAdmin) {
  document.querySelectorAll('#timeBtns .time-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#timeBtns .time-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      // 注意:data-days="0"(总量)是合法值,不能用 || 兜底(会被当成 falsy)
      const v = Number(btn.dataset.days);
      currentDays = Number.isFinite(v) ? v : 7;
      loadPlatform();
    });
  });
}

async function loadPlatform() {
  await Promise.all([
    loadOverview(),
    loadTrends(),
    loadRanking(),
    loadModels(),
    loadAnomalies(),
  ]);
}

function periodLabel(days) {
  if (days === 0) return '总量';
  if (days === 1) return '今日';
  return `近 ${days} 天`;
}
async function loadOverview() {
  // 同步刷新分区标题
  const titleEl = document.getElementById('ovSectionTitle');
  if (titleEl) titleEl.textContent = `核心指标（${periodLabel(currentDays)}）`;
  try {
    const r = await fetch(`${API}/admin/stats/platform/overview?days=${currentDays}`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    document.getElementById('ovTotal').textContent  = (d.total_calls || 0).toLocaleString();
    document.getElementById('ovTokens').textContent = (d.total_tokens || 0).toLocaleString();
    document.getElementById('ovCost').textContent   = '¥' + (d.total_cost || 0).toFixed(2);
    const projects = d.active_projects || [];
    const projCount = d.active_projects_count ?? projects.length;
    document.getElementById('ovProjects').textContent = projCount;
    const listEl = document.getElementById('ovProjectsList');
    if (listEl) {
      const shown = projects.slice(0, 3).join('、');
      const more  = projects.length > 3 ? ` 等${projects.length}个` : '';
      listEl.textContent = projects.length ? (shown + more) : '—';
      listEl.title = projects.join('\n');   // 鼠标悬浮看完整列表
    }
    document.getElementById('ovUsers').textContent  = d.active_users || 0;
    document.getElementById('ovRate').textContent   = (d.success_rate || 0).toFixed(1) + '%';
    document.getElementById('ovMs').textContent     = (d.avg_duration_ms || 0) + ' ms';
  } catch(e) {
    ['ovTotal','ovTokens','ovCost','ovProjects','ovUsers','ovRate','ovMs']
      .forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '—'; });
    const listEl = document.getElementById('ovProjectsList');
    if (listEl) { listEl.textContent = ''; listEl.title = ''; }
  }
}

async function loadRanking() {
  try {
    const r = await fetch(`${API}/admin/stats/platform/ranking?days=${currentDays}`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    renderRank('rankProjectTbody', d.projects || [], 'project');
    renderRank('rankUserTbody',    d.users    || [], 'username');
  } catch(e) {
    ['rankProjectTbody','rankUserTbody'].forEach(id =>
      document.getElementById(id).innerHTML =
        '<tr><td colspan="4" style="text-align:center;color:#dc2626;padding:18px">加载失败</td></tr>');
  }
}

function renderRank(tbodyId, list, nameKey) {
  const tbody = document.getElementById(tbodyId);
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:18px">暂无数据</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(r => `
    <tr>
      <td>${escapeHtml(r[nameKey] || '—')}</td>
      <td>${(r.calls || 0).toLocaleString()}</td>
      <td>${(r.tokens || 0).toLocaleString()}</td>
      <td>¥${(r.cost || 0).toFixed(4)}</td>
    </tr>`).join('');
}

// ── 调用排行展开弹窗(查看全部项目/用户) ────────────────────
async function openRankExpand(kind) {
  const bg = document.getElementById('rankExpandBg');
  const head = document.getElementById('rankExpandHead');
  const tbody = document.getElementById('rankExpandTbody');
  const title = document.getElementById('rankExpandTitle');
  const hint = document.getElementById('rankExpandHint');
  const isProject = kind === 'project';
  title.textContent = isProject ? '项目调用排行 — 全部' : '用户调用排行 — 全部';
  hint.textContent = `统计区间:${periodLabel(currentDays)}`;
  head.innerHTML = `<th>排名</th><th>${isProject ? '项目' : '用户'}</th><th>请求量</th><th>Token</th><th>成本(元)</th>`;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:24px">加载中…</td></tr>';
  bg.classList.add('open');
  try {
    const r = await fetch(`${API}/admin/stats/platform/ranking?days=${currentDays}&limit=1000`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    const list = isProject ? (d.projects || []) : (d.users || []);
    const nameKey = isProject ? 'project' : 'username';
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:24px">暂无数据</td></tr>';
      return;
    }
    tbody.innerHTML = list.map((r, i) => `
      <tr>
        <td style="color:var(--text-muted);font-family:monospace">${i + 1}</td>
        <td>${escapeHtml(r[nameKey] || '—')}</td>
        <td>${(r.calls || 0).toLocaleString()}</td>
        <td>${(r.tokens || 0).toLocaleString()}</td>
        <td>¥${(r.cost || 0).toFixed(4)}</td>
      </tr>`).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#dc2626;padding:24px">加载失败</td></tr>';
  }
}
document.getElementById('rankProjectExpand')?.addEventListener('click', () => openRankExpand('project'));
document.getElementById('rankUserExpand')?.addEventListener('click',    () => openRankExpand('user'));
document.getElementById('rankExpandClose')?.addEventListener('click',   () => document.getElementById('rankExpandBg').classList.remove('open'));
document.getElementById('rankExpandBg')?.addEventListener('click', e => {
  if (e.target.id === 'rankExpandBg') document.getElementById('rankExpandBg').classList.remove('open');
});

// 模型治理展开:复用同一个弹窗,列头/行结构不同
async function openModelExpand() {
  const bg = document.getElementById('rankExpandBg');
  const head = document.getElementById('rankExpandHead');
  const tbody = document.getElementById('rankExpandTbody');
  const title = document.getElementById('rankExpandTitle');
  const hint = document.getElementById('rankExpandHint');
  title.textContent = '模型治理 — 全部';
  hint.textContent = `统计区间:${periodLabel(currentDays)}`;
  head.innerHTML = '<th>排名</th><th>模型</th><th>请求量</th><th>Token</th><th>成本(元)</th><th>成功率</th><th>平均延迟</th>';
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px">加载中…</td></tr>';
  bg.classList.add('open');
  try {
    const r = await fetch(`${API}/admin/stats/platform/models?days=${currentDays}`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    const list = d.models || [];
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:24px">暂无数据</td></tr>';
      return;
    }
    tbody.innerHTML = list.map((m, i) => `
      <tr>
        <td style="color:var(--text-muted);font-family:monospace">${i + 1}</td>
        <td>${escapeHtml(m.model || '—')}</td>
        <td>${(m.calls || 0).toLocaleString()}</td>
        <td>${(m.tokens || 0).toLocaleString()}</td>
        <td>¥${(m.cost || 0).toFixed(4)}</td>
        <td>${(m.success_rate || 0).toFixed(1)}%</td>
        <td>${m.avg_ms || 0} ms</td>
      </tr>`).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#dc2626;padding:24px">加载失败</td></tr>';
  }
}
document.getElementById('modelGovExpand')?.addEventListener('click', openModelExpand);

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadModels() {
  const tbody = document.getElementById('modelGovTbody');
  try {
    const r = await fetch(`${API}/admin/stats/platform/models?days=${currentDays}`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    const list = d.models || [];
    if (!list.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:18px">暂无数据</td></tr>';
      return;
    }
    tbody.innerHTML = list.map(m => `
      <tr>
        <td>${escapeHtml(m.model || '—')}</td>
        <td>${(m.calls || 0).toLocaleString()}</td>
        <td>${(m.tokens || 0).toLocaleString()}</td>
        <td>¥${(m.cost || 0).toFixed(4)}</td>
        <td>${(m.success_rate || 0).toFixed(1)}%</td>
        <td>${m.avg_ms || 0} ms</td>
      </tr>`).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#dc2626;padding:18px">加载失败</td></tr>';
  }
}

async function loadTrends() {
  try {
    const r = await fetch(`${API}/admin/stats/platform/trends?days=${currentDays}`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    const days = fillDaysGrid(d.daily || [], currentDays);
    drawLineChart('chartCalls', days, days.map(p => p.calls), v => v.toLocaleString());
    drawLineChart('chartCost',  days, days.map(p => p.cost),  v => '¥' + v.toFixed(2));
    drawModelChart(d.model_daily || [], d.top_models || [], currentDays);
  } catch(e) {
    ['chartCalls','chartCost','chartModel'].forEach(id =>
      document.getElementById(id).innerHTML =
        '<text x="50%" y="50%" text-anchor="middle" fill="#dc2626" font-size="12">加载失败</text>');
  }
}

function fillDaysGrid(rows, n) {
  const map = {};
  rows.forEach(r => { map[r.day] = r; });
  const out = [];
  // days=0(总量):从数据中的最早一天填到今天;数据为空时退化为最近 1 天
  if (n === 0) {
    if (!rows.length) {
      const today = new Date().toISOString().slice(0,10);
      return [{ day: today, calls: 0, cost: 0 }];
    }
    const dates = rows.map(r => r.day).sort();
    const start = new Date(dates[0]);
    const end   = new Date();
    for (let dt = new Date(start); dt <= end; dt.setDate(dt.getDate() + 1)) {
      const key = dt.toISOString().slice(0, 10);
      const r = map[key] || { day: key, calls: 0, cost: 0 };
      out.push({ day: key, calls: r.calls || 0, cost: r.cost || 0 });
    }
    return out;
  }
  for (let i = n - 1; i >= 0; i--) {
    const dt = new Date(); dt.setDate(dt.getDate() - i);
    const key = dt.toISOString().slice(0, 10);
    const r = map[key] || { day: key, calls: 0, cost: 0 };
    out.push({ day: key, calls: r.calls || 0, cost: r.cost || 0 });
  }
  return out;
}

function drawLineChart(svgId, points, values, fmt) {
  const svg = document.getElementById(svgId);
  if (!points.length) {
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#9ca3af" font-size="12">暂无数据</text>';
    return;
  }
  const W = 560, H = 160, padL = 40, padR = 12, padT = 14, padB = 28;
  const max = Math.max(...values, 1);
  const xs = points.map((_, i) =>
    padL + (points.length === 1 ? (W - padL - padR) / 2 : i * ((W - padL - padR) / (points.length - 1))));
  const ys = values.map(v => H - padB - (v / max) * (H - padT - padB));
  const polyPts = xs.map((x, i) => `${x},${ys[i]}`).join(' ');
  const areaPts = `${xs[0]},${H - padB} ${polyPts} ${xs[xs.length - 1]},${H - padB}`;

  // y 轴 3 格
  const gridLines = [0, 0.5, 1].map(t => {
    const y = H - padB - t * (H - padT - padB);
    const v = max * t;
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#e5e7eb" stroke-width="1"/>
            <text x="${padL - 6}" y="${y + 3}" text-anchor="end" fill="#9ca3af" font-size="10">${fmt(v)}</text>`;
  }).join('');

  // x 轴标签（最多 7 个）
  const step = Math.max(1, Math.ceil(points.length / 7));
  const xLabels = points.map((p, i) =>
    (i % step === 0 || i === points.length - 1)
      ? `<text x="${xs[i]}" y="${H - 8}" text-anchor="middle" fill="#9ca3af" font-size="10">${p.day.slice(5)}</text>`
      : '').join('');

  svg.innerHTML = `
    ${gridLines}
    <polygon points="${areaPts}" fill="rgba(16,185,129,.10)"/>
    <polyline points="${polyPts}" fill="none" stroke="#10b981" stroke-width="2" stroke-linejoin="round"/>
    ${xs.map((x, i) => values[i] > 0 ? `<circle cx="${x}" cy="${ys[i]}" r="3" fill="#10b981"/>` : '').join('')}
    ${xLabels}`;
}

function drawModelChart(modelDaily, topModels, days, opts = {}) {
  const svgId    = opts.svgId    || 'chartModel';
  const legendId = opts.legendId || 'chartModelLegend';
  const W = opts.W ?? 560, H = opts.H ?? 160;
  const padL = 40, padR = 12, padT = 14, padB = 28;
  const svg = document.getElementById(svgId);
  const legend = document.getElementById(legendId);
  if (!topModels.length) {
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#9ca3af" font-size="12">暂无数据</text>';
    legend.innerHTML = '';
    return;
  }
  // 把 modelDaily 转成 { model: { day: calls } }
  const map = {};
  topModels.forEach(m => { map[m] = {}; });
  modelDaily.forEach(r => {
    if (map[r.model]) map[r.model][r.day] = r.calls;
  });
  // 时间轴:days>0 时按固定 N 天;days=0(总量)时从 modelDaily 最早一天到今天
  let grid = [];
  if (days === 0) {
    const allDates = modelDaily.map(r => r.day).sort();
    if (allDates.length) {
      const start = new Date(allDates[0]);
      const end   = new Date();
      for (let dt = new Date(start); dt <= end; dt.setDate(dt.getDate() + 1)) {
        grid.push(dt.toISOString().slice(0, 10));
      }
    } else {
      grid.push(new Date().toISOString().slice(0,10));
    }
  } else {
    for (let i = days - 1; i >= 0; i--) {
      const dt = new Date(); dt.setDate(dt.getDate() - i);
      grid.push(dt.toISOString().slice(0, 10));
    }
  }
  // 求 max
  let max = 1;
  topModels.forEach(m => grid.forEach(d => max = Math.max(max, map[m][d] || 0)));

  const xs = grid.map((_, i) =>
    padL + (grid.length === 1 ? (W - padL - padR) / 2 : i * ((W - padL - padR) / (grid.length - 1))));

  const gridLines = [0, 0.5, 1].map(t => {
    const y = H - padB - t * (H - padT - padB);
    return `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="#e5e7eb" stroke-width="1"/>
            <text x="${padL - 6}" y="${y + 3}" text-anchor="end" fill="#9ca3af" font-size="10">${Math.round(max * t)}</text>`;
  }).join('');

  const lines = topModels.map((m, idx) => {
    const color = MODEL_COLORS[idx % MODEL_COLORS.length];
    const ys = grid.map(d => H - padB - ((map[m][d] || 0) / max) * (H - padT - padB));
    const pts = xs.map((x, i) => `${x},${ys[i]}`).join(' ');
    return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
  }).join('');

  const step = Math.max(1, Math.ceil(grid.length / 7));
  const xLabels = grid.map((d, i) =>
    (i % step === 0 || i === grid.length - 1)
      ? `<text x="${xs[i]}" y="${H - 8}" text-anchor="middle" fill="#9ca3af" font-size="10">${d.slice(5)}</text>`
      : '').join('');

  svg.innerHTML = gridLines + lines + xLabels;
  legend.innerHTML = topModels.map((m, idx) =>
    `<span><span class="dot" style="background:${MODEL_COLORS[idx % MODEL_COLORS.length]}"></span>${escapeHtml(m)}</span>`
  ).join('');
}

// 模型使用趋势 — 展开 Top10
async function openModelTrendExpand() {
  const bg = document.getElementById('modelTrendExpandBg');
  document.getElementById('modelTrendExpandHint').textContent = `统计区间:${periodLabel(currentDays)} · Top 10`;
  bg.classList.add('open');
  try {
    const r = await fetch(`${API}/admin/stats/platform/trends?days=${currentDays}&top=10`, { headers: ADMIN_HEADERS });
    const d = await r.json();
    drawModelChart(
      d.model_daily || [], d.top_models || [], currentDays,
      { svgId: 'chartModelExpand', legendId: 'chartModelExpandLegend', W: 880, H: 320 }
    );
  } catch (e) {
    document.getElementById('chartModelExpand').innerHTML =
      '<text x="50%" y="50%" text-anchor="middle" fill="#dc2626" font-size="12">加载失败</text>';
  }
}
document.getElementById('modelTrendExpand')?.addEventListener('click', openModelTrendExpand);
document.getElementById('modelTrendExpandClose')?.addEventListener('click', () =>
  document.getElementById('modelTrendExpandBg').classList.remove('open'));
document.getElementById('modelTrendExpandBg')?.addEventListener('click', e => {
  if (e.target.id === 'modelTrendExpandBg') document.getElementById('modelTrendExpandBg').classList.remove('open');
});

// admin 默认进入平台 tab，立刻加载
if (isAdmin) {
  platformLoaded = true;
  loadPlatform();
}


