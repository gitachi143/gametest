/**
 * Boot + router. Hash routes:
 *   #/            arcade home
 *   #/g/<game>[/<mode>]   start a game
 *   #/s/<sessionId>       resume a session
 *   #/daily               today's challenge
 */
import { $, h } from './lib/dom.js';
import { api, getToken } from './lib/api.js';
import { renderHome } from './ui/home.js';
import { GameView } from './game.js';
import { toast } from './ui/toast.js';
import { leaderboardSheet, profileSheet } from './ui/panels.js';
import { closeSheet } from './ui/sheet.js';
import { sfx, isOn, toggle, unlock } from './lib/sound.js';

const view = $('#view');
const state = { catalogue: null, profile: null, current: null };

/* ---------------------------------------------------------------- chrome */
function paintProfile(profile) {
  if (!profile) return;
  state.profile = profile;
  const lvl = profile.level;
  $('#level-num').textContent = lvl.level;
  $('#level-title').textContent = lvl.title;
  $('#xp-fill').style.width = `${lvl.progress * 100}%`;
  $('#level-chip').style.setProperty('--p', `${lvl.progress}turn`);
  $('#streak-num').textContent = profile.streak;
  $('#streak-chip').classList.toggle('hot', profile.streak > 0);
}

function applyProfileDelta(data) {
  if (!state.profile) return;
  state.profile.level = data.level;
  state.profile.streak = data.streak;
  paintProfile(state.profile);
  if (data.streak_extended && data.streak > 1) {
    toast(`${data.streak}-day streak. Multiplier is climbing.`, 'good', '🔥');
  }
  if (data.xp_gained) {
    const chip = $('#level-chip');
    chip.animate?.([{ transform: 'scale(1)' }, { transform: 'scale(1.09)' }, { transform: 'scale(1)' }],
      { duration: 420, easing: 'ease-out' });
  }
}

/* ---------------------------------------------------------------- routes */
function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function ensureCatalogue(force = false) {
  if (!state.catalogue || force) {
    const data = await api.games();
    state.catalogue = data;
    paintProfile(data.profile);
  }
  return state.catalogue;
}

function teardown() {
  // A result sheet is deliberately non-dismissable; route changes (browser back,
  // the brand button) must still clear it or its backdrop swallows every click.
  closeSheet();
  if (state.current) { state.current.destroy(); state.current = null; }
}

function loading(label = 'Loading') {
  view.className = 'view enter';
  view.replaceChildren(h('div', { class: 'panel', style: { textAlign: 'center', padding: '46px' } },
    h('div', { class: 'thinking', style: { margin: '0 auto' } },
      h('span', {}, label), h('span', { class: 'dots' }, h('i'), h('i'), h('i')))));
}

const hooks = {
  onHome: () => go('#/'),
  onProfile: applyProfileDelta,
  onReplay: async (mode, opts) => {
    const meta = state.current?.meta;
    if (!meta) return go('#/');
    await startGame(meta.id, mode || undefined, opts);
  },
};

async function startGame(gameId, mode, opts) {
  const cat = await ensureCatalogue();
  const meta = cat.games.find((g) => g.id === gameId);
  if (!meta) { toast(`No game called ${gameId}.`, 'bad'); return go('#/'); }
  teardown();
  loading(`Spinning up ${meta.codename}`);
  try {
    const data = await api.newSession({ game: gameId, mode, opts });
    view.className = 'view enter';
    state.current = new GameView(view, {
      meta: data.game, sessionId: data.session_id, pub: data.public, daily: data.daily,
    }, hooks);
    history.replaceState(null, '', `#/s/${data.session_id}`);
  } catch (err) {
    toast(err.message || 'Could not start that game.', 'bad', '⚠️');
    go('#/');
  }
}

async function startDaily() {
  teardown();
  loading('Loading today\'s challenge');
  try {
    const data = await api.newSession({ daily: true });
    view.className = 'view enter';
    state.current = new GameView(view, {
      meta: data.game, sessionId: data.session_id, pub: data.public, daily: true,
    }, hooks);
    history.replaceState(null, '', `#/s/${data.session_id}`);
  } catch (err) {
    if (err.status === 409) {
      toast('Today\'s challenge is already played. Back tomorrow.', 'warn', '📅');
    } else {
      toast(err.message || 'Could not start the daily.', 'bad', '⚠️');
    }
    go('#/');
  }
}

async function resume(sid) {
  teardown();
  loading('Restoring session');
  try {
    const data = await api.session(sid);
    if (data.status !== 'active') {
      toast('That run is finished.', 'warn');
      return go('#/');
    }
    view.className = 'view enter';
    state.current = new GameView(view, {
      meta: data.game, sessionId: data.session_id, pub: data.public, daily: data.daily,
    }, hooks);
  } catch {
    toast('Could not restore that session.', 'warn');
    go('#/');
  }
}

async function home() {
  teardown();
  loading('Opening the arcade');
  const data = await ensureCatalogue(true);
  view.className = 'view enter';
  renderHome(view, data, {
    play: (game) => go(`#/g/${game.id}`),
    playDaily: () => go('#/daily'),
    openLeaderboard: (gameId) => leaderboardSheet(data.games, gameId),
  });
}

function route() {
  closeSheet();
  const parts = (location.hash || '#/').replace(/^#\/?/, '').split('/').filter(Boolean);
  if (!parts.length) return home();
  if (parts[0] === 'daily') return startDaily();
  if (parts[0] === 'g') return startGame(parts[1], parts[2]);
  if (parts[0] === 's') return resume(parts[1]);
  return home();
}

/* ------------------------------------------------------------------ boot */
window.addEventListener('hashchange', route);
document.addEventListener('pointerdown', () => unlock(), { once: true });

$('#brand').onclick = () => go('#/');
$('#btn-sound').onclick = (e) => {
  const on = toggle();
  e.currentTarget.textContent = on ? '🔊' : '🔇';
  e.currentTarget.setAttribute('aria-pressed', String(on));
};
$('#btn-sound').textContent = isOn() ? '🔊' : '🔇';
$('#btn-sound').setAttribute('aria-pressed', String(isOn()));
$('#btn-leaderboard').onclick = async () => {
  const cat = await ensureCatalogue();
  leaderboardSheet(cat.games);
};
const openProfile = async () => {
  const fresh = await api.me();
  paintProfile(fresh.profile);
  const cat = await ensureCatalogue();
  profileSheet(fresh.profile, cat.games);
};
$('#level-chip').onclick = openProfile;
$('#level-chip').onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') openProfile(); };
$('#streak-chip').onclick = openProfile;

route();
