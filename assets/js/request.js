// 通用 HTTP 请求封装
export async function request(url, { method = 'GET', headers = {}, body } = {}) {
  const options = {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
  };
  if (body) options.body = JSON.stringify(body);

  const res = await fetch(url, options);
  const data = await res.json().catch(() => res.text());

  if (!res.ok) throw { status: res.status, data };
  return data;
}
