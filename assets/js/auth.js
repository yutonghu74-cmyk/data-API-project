const BASE = 'http://localhost:8000';
const TOKEN_KEY = 'platform_token';
const USER_KEY  = 'platform_user';

export function getToken()    { return localStorage.getItem(TOKEN_KEY); }
export function getUser()     { const u = localStorage.getItem(USER_KEY); return u ? JSON.parse(u) : null; }
export function isLoggedIn()  { return !!getToken(); }

export function saveSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function requireLogin() {
  if (!isLoggedIn()) {
    window.location.href = '/pages/login.html';
    return false;
  }
  return true;
}

// 从服务器刷新用户信息（确保 role 最新），返回最新 user 对象
export async function refreshUser() {
  try {
    const res = await fetch(`${BASE}/auth/me`, {
      headers: { 'X-Token': getToken() || '' },
    });
    if (!res.ok) return getUser();
    const user = await res.json();
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    return user;
  } catch {
    return getUser();
  }
}

export function authHeaders() {
  return { 'Content-Type': 'application/json', 'X-Token': getToken() || '' };
}

export async function apiFetch(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: authHeaders() };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) { clearSession(); window.location.href = '/pages/login.html'; throw new Error('UNAUTHORIZED'); }
  if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
  return res.json();
}

export async function register(username, password) {
  const res = await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || '注册失败');
  return data;
}

export async function login(username, password) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || '登录失败');
  saveSession(data.token, data.user);
  return data;
}

export function logout() {
  clearSession();
  window.location.href = '/pages/login.html';
}

// 渲染面包屑右侧用户信息
export function renderUserBar() {
  const user = getUser();
  const el = document.getElementById('userBar');
  if (!el || !user) return;
  el.innerHTML = `
    <span class="username">${user.username}</span>
    <button class="logout" onclick="import('/assets/js/auth.js').then(m=>m.logout())">退出</button>`;
}
