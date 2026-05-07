const BASE = 'http://localhost:8000';

function getPassword() {
  return sessionStorage.getItem('adminPwd') || '';
}

async function adminFetch(path, { method = 'GET', body } = {}) {
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Admin-Password': getPassword(),
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (res.status === 401) throw new Error('UNAUTHORIZED');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function login(password) {
  const res = await fetch(`${BASE}/admin/login`, {
    method: 'POST',
    headers: { 'X-Admin-Password': password },
  });
  const data = await res.json();
  if (data.ok) sessionStorage.setItem('adminPwd', password);
  return data.ok;
}

export const getConfigs    = ()         => adminFetch('/admin/configs');
export const createConfig  = (body)     => adminFetch('/admin/configs', { method: 'POST', body });
export const updateConfig  = (id, body) => adminFetch(`/admin/configs/${id}`, { method: 'PUT', body });
export const deleteConfig  = (id)       => adminFetch(`/admin/configs/${id}`, { method: 'DELETE' });
export const getStats      = ()         => adminFetch('/admin/stats');
export const getDailyStats = (id)       => adminFetch(`/admin/stats/${id}/daily`);
