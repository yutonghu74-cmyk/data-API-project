const BASE_URL = 'http://localhost:8000';

export async function streamChat({ messages, model, system = '', config_id = null, request_id = null, user_token = null, onToken, onDone, onError }) {
  let res;
  try {
    res = await fetch(`${BASE_URL}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages, model, system, config_id, request_id, user_token }),
    });
  } catch {
    onError('无法连接到代理服务，请先启动 server.py（python server.py）');
    return;
  }

  if (!res.ok) {
    onError(`服务器错误 ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const raw = line.slice(6).trim();
      if (raw === '[DONE]') { onDone(); return; }

      try {
        const msg = JSON.parse(raw);
        if (msg.error) { onError(msg.error); return; }
        if (msg.text)  { onToken(msg.text); }
      } catch { /* ignore malformed lines */ }
    }
  }

  onDone();
}

export async function fetchModels() {
  const res = await fetch(`${BASE_URL}/models`);
  const data = await res.json();
  return data.models;
}
