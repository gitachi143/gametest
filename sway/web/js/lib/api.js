// HTTP + SSE. The anonymous player token lives in localStorage and rides along
// on every request; the server mints one on first contact.
const TOKEN_KEY = 'sway.token';

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}
export function setToken(token) {
  if (!token) return;
  try { localStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
}

function headers(extra = {}) {
  const h = { 'Content-Type': 'application/json', ...extra };
  const t = getToken();
  if (t) h['X-Player-Token'] = t;
  return h;
}

async function handle(res) {
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { error: text }; }
  if (!res.ok) {
    throw Object.assign(new Error(data.error || `HTTP ${res.status}`),
                        { status: res.status, data });
  }
  if (data.token) setToken(data.token);
  return data;
}

const get = (url) => fetch(url, { headers: headers() }).then(handle);
const post = (url, body) =>
  fetch(url, { method: 'POST', headers: headers(), body: JSON.stringify(body || {}) }).then(handle);

export const api = {
  boot: () => get('/api/boot'),
  me: () => get('/api/me'),
  health: () => fetch('/api/health').then(handle),
  save: (body) => post('/api/me', body),
  leaderboard: (mode, daily) => get(
    `/api/leaderboard?${new URLSearchParams({
      ...(mode ? { mode } : {}), ...(daily ? { daily: 1 } : {}),
    })}`),
  newRun: (body) => post('/api/run', body),
  run: (id) => get(`/api/run/${id}`),
  abandon: (id) => post(`/api/run/${id}/abandon`),
};

/**
 * POST an action and consume the SSE response.
 * EventSource cannot POST, so frames are parsed by hand - which also lets a
 * turn be aborted cleanly when the view is torn down.
 */
export async function streamAction(runId, action, onEvent, signal) {
  const res = await fetch(`/api/run/${runId}/act`, {
    method: 'POST',
    headers: headers({ Accept: 'text/event-stream' }),
    body: JSON.stringify(action),
    signal,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let name = 'message';
      const data = [];
      for (const raw of frame.split('\n')) {
        if (raw.startsWith('event:')) name = raw.slice(6).trim();
        else if (raw.startsWith('data:')) data.push(raw.slice(5).trim());
      }
      if (!data.length) continue;
      let payload = {};
      try { payload = JSON.parse(data.join('\n')); } catch { payload = { raw: data.join('\n') }; }
      await onEvent(name, payload);
    }
  }
}
