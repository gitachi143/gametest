import { h, mount, tpl, fmt, timeAgo, $ } from '../lib/dom.js';
import { sfx } from '../lib/sound.js';

function pips(n) {
  return h('span', { class: 'pips', title: `difficulty ${n}/5` },
    [1, 2, 3, 4, 5].map((i) => h('i', { class: `pip ${i <= n ? 'on' : ''}` })));
}

function countdownTo(iso) {
  const el = h('span', { class: 'countdown' }, '');
  const tick = () => {
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) { el.textContent = 'new challenge ready'; return; }
    const s = Math.floor(ms / 1000);
    el.textContent = `resets in ${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    setTimeout(tick, 1000);
  };
  tick();
  return el;
}

function gameCard(game, best, onPlay) {
  const card = h('button', {
    class: `card ${game.id === 'gauntlet' ? 'wide' : ''}`,
    style: { '--accent': game.accent, '--accent-2': game.accent2 },
    onmouseenter: () => sfx.hover(),
    onclick: () => onPlay(game),
  },
    h('div', { class: 'card-top' },
      h('div', {},
        h('div', { class: 'card-code' }, game.codename),
        h('h3', {}, game.tagline),
      ),
      h('span', { class: 'card-icon' }, game.icon),
    ),
    h('p', { class: 'card-tag' }, game.blurb),
    h('div', { class: 'card-modes' }, (game.modes || []).map((m) => h('span', { class: 'mode-pill' }, m.name))),
    h('div', { class: 'card-foot' },
      pips(game.difficulty),
      h('span', { class: 'card-best' }, best ? `best ${fmt(best)}` : `${game.minutes} min`),
    ),
  );
  return card;
}

export function renderHome(view, data, handlers) {
  const node = tpl('tpl-home');
  mount(view, node);
  document.documentElement.style.setProperty('--accent', '#7c8cff');
  document.documentElement.style.setProperty('--accent-2', '#4cc9f0');

  const { games, daily, profile, demo_mode: demo, provider } = data;
  const gameById = Object.fromEntries(games.map((g) => [g.id, g]));
  const dailyGame = gameById[daily.game];

  mount($('#hero-meta', node),
    h('span', { class: `tag ${demo ? 'demo' : 'live'}` }, demo ? 'demo mode · no api key' : `live · ${provider}`),
    h('span', { class: 'tag' }, `${games.length} games`),
    h('span', { class: 'tag' }, `${profile.unlocked_count}/${profile.achievement_total} badges`),
    profile.streak > 0 ? h('span', { class: 'tag' }, `🔥 ${profile.streak}-day streak`) : null,
  );

  mount($('#daily-card', node), h('div', { class: 'daily', style: { '--accent': dailyGame?.accent || '#7c8cff', '--accent-2': dailyGame?.accent2 || '#4cc9f0' } },
    h('div', { class: 'daily-kicker' }, 'daily challenge'),
    h('h3', {}, `${dailyGame?.icon || '🎲'} ${dailyGame?.codename || daily.game} · ${daily.mode}`),
    h('p', {}, 'Same puzzle for everybody, one attempt, seeded by the date. Post a score and compare cards.'),
    h('div', { class: 'daily-row' },
      daily.played
        ? h('span', { class: 'daily-done' }, '✓ played today')
        : h('button', { class: 'btn btn-primary btn-sm', onclick: () => handlers.playDaily() }, 'Enter'),
      h('button', { class: 'btn btn-ghost btn-sm', onclick: () => handlers.openLeaderboard(daily.game) }, 'Today\'s board'),
    ),
    h('div', { style: { marginTop: '10px' } }, countdownTo(daily.expires)),
  ));

  mount($('#game-grid', node), games.map((g) => gameCard(g, profile.summary[g.id]?.best, handlers.play)));

  const achStrip = $('#ach-strip', node);
  mount(achStrip, profile.achievements.map((a) => h('div', {
    class: `ach ${a.unlocked ? 'on' : ''}`, title: `${a.name} — ${a.desc}`,
  }, a.icon)));
  $('#ach-count', node).textContent = `${profile.unlocked_count} / ${profile.achievement_total}`;

  mount($('#recent-list', node), profile.recent.length
    ? profile.recent.slice(0, 6).map((r) => h('div', { class: `recent ${r.outcome}` },
        h('span', { class: 'r-icon' }, gameById[r.game]?.icon || '🎮'),
        h('span', { class: 'r-game' }, gameById[r.game]?.codename || r.game),
        h('span', { class: 'hint' }, timeAgo(r.created)),
        h('b', { class: 'r-score' }, fmt(r.score)),
      ))
    : h('div', { class: 'empty' }, 'Play something and it shows up here.'));

  $('#hero-daily', node).onclick = () => (daily.played ? handlers.play(dailyGame) : handlers.playDaily());
  $('#hero-random', node).onclick = () => handlers.play(games[Math.floor(Math.random() * games.length)]);
  return node;
}
