const BASE = 'http://localhost:8000';

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Token': localStorage.getItem('platform_token') || '',
  };
}

// 旧端点兼容:过渡期同时携带 admin password header(后端两个都接受)
function legacyAdminHeaders() {
  const h = authHeaders();
  h['X-Admin-Password'] = sessionStorage.getItem('adminPwd') || '';
  return h;
}

async function apiFetch(path, { method = 'GET', body, headers } = {}) {
  const opts = { method, headers: headers || authHeaders() };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) throw new Error('UNAUTHORIZED');
  if (res.status === 204) return null;
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).detail || ''; } catch {}
    throw new Error(`HTTP ${res.status}${detail ? ': ' + detail : ''}`);
  }
  return res.json();
}

// 旧 admin 密码登录(过渡期保留)
export async function login(password) {
  const res = await fetch(`${BASE}/admin/login`, {
    method: 'POST',
    headers: { 'X-Admin-Password': password },
  });
  const data = await res.json();
  if (data.ok) sessionStorage.setItem('adminPwd', password);
  return data.ok;
}

// 新:三层 CRUD
export const listAccounts    = ()          => apiFetch('/admin/accounts');
export const createAccount   = (body)      => apiFetch('/admin/accounts', { method: 'POST', body });
export const updateAccount   = (id, body)  => apiFetch(`/admin/accounts/${id}`, { method: 'PUT', body });
export const deleteAccount   = (id)        => apiFetch(`/admin/accounts/${id}`, { method: 'DELETE' });

export const listSubAccounts   = (accId)        => apiFetch(`/admin/accounts/${accId}/sub-accounts`);
export const createSubAccount  = (accId, body)  => apiFetch(`/admin/accounts/${accId}/sub-accounts`, { method: 'POST', body });
export const updateSubAccount  = (id, body)     => apiFetch(`/admin/sub-accounts/${id}`, { method: 'PUT', body });
export const deleteSubAccount  = (id)           => apiFetch(`/admin/sub-accounts/${id}`, { method: 'DELETE' });

export const listApiKeys    = (subId)        => apiFetch(`/admin/sub-accounts/${subId}/api-keys`);
export const createApiKey   = (subId, body)  => apiFetch(`/admin/sub-accounts/${subId}/api-keys`, { method: 'POST', body });
export const updateApiKey   = (id, body)     => apiFetch(`/admin/api-keys/${id}`, { method: 'PUT', body });
export const deleteApiKey   = (id)           => apiFetch(`/admin/api-keys/${id}`, { method: 'DELETE' });

export const listProviders     = ()    => apiFetch('/admin/providers');
export const listTeams         = ()    => apiFetch('/admin/teams');
export const fetchModelsForAcc = (id)  => apiFetch(`/admin/accounts/${id}/fetch-models`);

// 老端点保留供 user 视图(GET only)
export const getConfigs    = ()  => apiFetch('/admin/configs', { headers: legacyAdminHeaders() });
export const getStats      = ()  => apiFetch('/admin/stats', { headers: legacyAdminHeaders() });
export const getDailyStats = (id) => apiFetch(`/admin/stats/${id}/daily`, { headers: legacyAdminHeaders() });
