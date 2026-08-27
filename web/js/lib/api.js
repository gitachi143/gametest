// API client. Anonymous player token lives in localStorage and rides along on
// every request; the server issues one on first contact.
const TOKEN_KEY = 'nexus.token';

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
  if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status, data });
  if (data.token) setToken(data.token);
  return data;
}

export const api = {
  games: () => fetch('/api/games', { headers: headers() }).then(handle),
  me: () => fetch('/api/me', { headers: headers() }).then(handle),
  setHandle: (name) => fetch('/api/me', { method: 'POST', headers: headers(), body: JSON.stringify({ handle: name }) }).then(handle),
  health: () => fetch('/api/health').then(handle),
  daily: () => fetch('/api/daily', { headers: headers() }).then(handle),
  leaderboard: (game, daily) => fetch(`/api/leaderboard?${new URLSearchParams({ ...(game ? { game } : {}), ...(daily ? { daily: 1 } : {}) })}`).then(handle),
  newSession: (body) => fetch('/api/session', { method: 'POST', headers: headers(), body: JSON.stringify(body) }).then(handle),
  session: (sid) => fetch(`/api/session/${sid}`, { headers: headers() }).then(handle),
};

/**
 * POST an action and consume the SSE response.
 * EventSource cannot POST, so the stream is parsed by hand - which also lets us
 * abort a turn cleanly when the view is torn down.
 */
export async function streamAction(sid, action, onEvent, signal) {
  const res = await fetch(`/api/session/${sid}/act`, {
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
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let name = 'message';
      const dataLines = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      let payload = {};
      try { payload = JSON.parse(dataLines.join('\n')); } catch { payload = { raw: dataLines.join('\n') }; }
      onEvent(name, payload);
    }
  }
}
