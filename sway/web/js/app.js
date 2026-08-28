/**
 * Boot and router.
 *
 *   #/               home
 *   #/m/<mode>       start a run in that mode
 *   #/r/<runId>      resume a run
 */
import { $, h, mount, setHue } from './lib/dom.js';
import { api } from './lib/api.js';
import { renderHome } from './screens/home.js';
import { RunView } from './screens/encounter.js';
import { collectionSheet, leaderboardSheet, profileSheet, howSheet } from './screens/sheets.js';
import { closeSheet } from './ui/sheet.js';
import { toast } from './ui/toast.js';
import { sfx, isOn, toggle, unlock } from './lib/sound.js';

const view = $('#view');
const state = { boot: null, current: null };

/* ------------------------------------------------------------------ chrome */
function paintProfile(profile) {
  if (!profile) return;
  if (state.boot) state.boot.profile = profile;
  const lvl = profile.level || {};
  $('#lvl-num').textContent = lvl.level ?? 1;
  $('#lvl-name').textContent = lvl.title || '';
  $('#lvl-ring').style.setProperty('--p', `${lvl.progress ?? 0}turn`);
  $('#streak-num').textContent = profile.streak ?? 0;
  $('#streak').classList.toggle('hot', (profile.streak ?? 0) > 0);
}

function applyProfileDelta(data) {
  const profile = state.boot?.profile;
  if (!profile) return;
  profile.level = data.level;
  profile.streak = data.streak;
  paintProfile(profile);
  if (data.levelled) {
    sfx.levelUp();
    toast(`Level ${data.level.level} — ${data.level.title}`, 'good', '✦');
  }
  if (data.streak_extended && data.streak > 1) {
    toast(`${data.streak}-day streak.`, 'good', '⌗');
  }
  for (const u of data.unlocked || []) {
    toast(`${u.name} unlocked`, 'good', u.glyph || '✦');
  }
}

function crumb(text = '', highlight = '') {
  const el = $('#crumb');
  el.replaceChildren();
  if (!text && !highlight) { el.style.display = 'none'; return; }
  el.style.display = '';
  if (text) el.appendChild(document.createTextNode(text));
  if (highlight) el.appendChild(h('b', {}, highlight));
}

function loading(label) {
  view.className = 'enter';
  mount(view, h('div', { class: 'loading' },
    h('div', { class: 'loading-mark' }, '◈'),
    h('p', {}, label)));
}

/* ------------------------------------------------------------------ routes */
function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function ensureBoot(force = false) {
  if (!state.boot || force) {
    state.boot = await api.boot();
    paintProfile(state.boot.profile);
  }
  return state.boot;
}

function teardown() {
  closeSheet();
  view._stopDemo?.();
  view._stopDemo = null;
  if (state.current) { state.current.destroy(); state.current = null; }
}

const hooks = {
  onHome: () => go('#/'),
  onProfile: applyProfileDelta,
  onHelp: () => howSheet(state.boot?.collection?.cards?.filter((c) => !c.locked) || []),
  onAgain: (mode) => go(`#/m/${mode}`),
};

async function startRun(mode) {
  await ensureBoot();
  teardown();
  loading(`Setting the room…`);
  try {
    const data = await api.newRun({ mode });
    view.className = 'enter';
    state.current = new RunView(view, { runId: data.run_id, run: data.run }, hooks);
    crumb(`${data.run.mode_name} · floor `, String(data.run.floor));
    history.replaceState(null, '', `#/r/${data.run_id}`);
  } catch (err) {
    if (err.status === 409) toast(err.message, 'warn', '◉');
    else toast(err.message || 'Could not start that.', 'bad', '⚠');
    go('#/');
  }
}

async function resumeRun(runId) {
  await ensureBoot();
  teardown();
  loading('Back into the room…');
  try {
    const data = await api.run(runId);
    if (data.status !== 'active' || !data.run.encounter) {
      toast('That run is finished.', 'warn');
      return go('#/');
    }
    view.className = 'enter';
    state.current = new RunView(view, { runId: data.run_id, run: data.run }, hooks);
    crumb(`${data.run.mode_name} · floor `, String(data.run.floor));
  } catch {
    toast('Could not restore that run.', 'warn');
    go('#/');
  }
}

async function home() {
  teardown();
  crumb();
  setHue(40);
  loading('Opening the door…');
  const data = await ensureBoot(true);
  view.className = 'enter';
  renderHome(view, data, {
    play: (mode) => go(`#/m/${mode}`),
    resume: (id) => go(`#/r/${id}`),
    drop: async (id) => {
      await api.abandon(id).catch(() => {});
      home();
    },
    collection: () => collectionSheet(data.collection, data.profile),
    leaderboard: (mode) => leaderboardSheet(mode || null, data.profile?.player_id),
    how: () => hooks.onHelp(),
  });
  if (!data.profile?.seen_intro) {
    hooks.onHelp();
    api.save({ seen_intro: true }).catch(() => {});
  }
}

function route() {
  closeSheet();
  const parts = (location.hash || '#/').replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length) return home();
  if (parts[0] === 'm') return startRun(parts[1]);
  if (parts[0] === 'r') return resumeRun(parts[1]);
  return home();
}

/* -------------------------------------------------------------------- boot */
window.addEventListener('hashchange', route);
document.addEventListener('pointerdown', () => unlock(), { once: true });
document.addEventListener('keydown', () => unlock(), { once: true });

$('#brand').onclick = () => { sfx.back(); go('#/'); };
$('#sound').onclick = (e) => {
  const on = toggle();
  e.currentTarget.textContent = on ? '♪' : '♪̸';
  e.currentTarget.setAttribute('aria-pressed', String(on));
};
$('#sound').textContent = isOn() ? '♪' : '♪̸';
$('#sound').setAttribute('aria-pressed', String(isOn()));
$('#board').onclick = async () => {
  const data = await ensureBoot();
  leaderboardSheet(null, data.profile?.player_id);
};
const openProfile = async () => {
  const fresh = await api.me();
  paintProfile(fresh.profile);
  if (state.boot) state.boot.collection = fresh.collection;
  profileSheet(fresh.profile, (p) => paintProfile(p));
};
$('#lvl').onclick = openProfile;
$('#streak').onclick = openProfile;

// A stale service worker or a cached module can leave the page half-alive; a
// visible failure beats a blank screen.
window.addEventListener('unhandledrejection', (e) => {
  if (e.reason?.name === 'AbortError') return;
  console.error(e.reason);
});

route();
