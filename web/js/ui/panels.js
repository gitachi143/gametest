import { h, mount, fmt, timeAgo } from '../lib/dom.js';
import { openSheet, sheetHead } from './sheet.js';
import { api, getToken } from '../lib/api.js';
import { toast } from './toast.js';

const myId = () => (getToken() || '').split('.')[0];

export async function leaderboardSheet(games, initial = '') {
  const list = h('div', { class: 'lb-list' }, h('div', { class: 'empty' }, 'Loading…'));
  const tabs = h('div', { class: 'tabs' });
  let current = initial;
  let daily = false;

  async function load() {
    mount(list, h('div', { class: 'empty' }, 'Loading…'));
    try {
      const data = await api.leaderboard(current || undefined, daily);
      if (!data.entries.length) {
        mount(list, h('div', { class: 'empty' }, daily
          ? 'Nobody has posted a score for today yet. Be first.'
          : 'No scores yet. The board is yours to take.'));
        return;
      }
      mount(list, data.entries.map((e) => h('div', { class: `lb-row ${e.player_id === myId() ? 'me' : ''}` },
        h('span', { class: 'lb-rank' }, `#${e.rank}`),
        h('span', {}, e.player_id === myId() ? `${e.handle} (you)` : e.handle),
        h('span', { class: 'hint', style: { marginLeft: '6px' } }, e.game),
        h('b', { class: 'lb-score' }, fmt(e.score)),
      )));
    } catch (err) {
      mount(list, h('div', { class: 'empty' }, `Could not load: ${err.message}`));
    }
  }

  const buttons = [
    { id: '', label: 'All games' },
    ...games.map((g) => ({ id: g.id, label: g.codename })),
  ];
  mount(tabs, [
    ...buttons.map((b) => h('button', {
      class: b.id === current ? 'on' : '',
      onclick: (e) => {
        current = b.id;
        [...tabs.children].forEach((c) => c.classList.remove('on'));
        e.currentTarget.classList.add('on');
        load();
      },
    }, b.label)),
  ]);

  openSheet([
    sheetHead('Leaderboards', h('button', {
      class: 'btn btn-sm',
      onclick: (e) => {
        daily = !daily;
        e.currentTarget.textContent = daily ? 'Today only ✓' : 'Today only';
        load();
      },
    }, 'Today only')),
    tabs,
    list,
  ], { wide: true });
  load();
}

export function profileSheet(profile, games) {
  const gameById = Object.fromEntries(games.map((g) => [g.id, g]));
  const lvl = profile.level;
  const handleInput = h('input', {
    type: 'text', value: profile.handle || '', maxlength: 18, placeholder: 'anon',
    style: { height: '38px', padding: '0 12px', borderRadius: 'var(--r-sm)',
             border: '1px solid var(--line)', background: 'rgba(0,0,0,0.35)', flex: '1' },
  });

  openSheet([
    sheetHead('Your record'),
    h('div', { style: { display: 'flex', gap: '14px', alignItems: 'center', marginBottom: '18px' } },
      h('div', { class: 'level-ring', style: { width: '56px', height: '56px', fontSize: 'var(--fs-lg)',
                 '--p': `${lvl.progress}turn` } }, h('span', {}, lvl.level)),
      h('div', {},
        h('div', { style: { fontSize: 'var(--fs-lg)', fontWeight: '600' } }, lvl.title),
        h('div', { class: 'hint' }, `${fmt(lvl.xp)} xp · ${fmt(lvl.to_next)} to level ${lvl.level + 1}`),
      ),
      h('div', { style: { marginLeft: 'auto', textAlign: 'right' } },
        h('div', { style: { fontSize: 'var(--fs-xl)' } }, `🔥 ${profile.streak}`),
        h('div', { class: 'hint' }, `best ${profile.best_streak}`),
      ),
    ),
    h('div', { class: 'meter', style: { marginBottom: '18px' } }, h('i', { style: { width: `${lvl.progress * 100}%` } })),

    h('div', { style: { display: 'flex', gap: '8px', marginBottom: '18px' } },
      handleInput,
      h('button', {
        class: 'btn btn-sm',
        onclick: async () => {
          try {
            await api.setHandle(handleInput.value.trim());
            toast('Name saved. It shows on the leaderboards.', 'good', '✍️');
          } catch (err) { toast(err.message, 'bad'); }
        },
      }, 'Save name'),
    ),

    h('div', { class: 'panel-head' }, h('h3', {}, 'Per game')),
    h('div', { class: 'recent-list', style: { marginBottom: '18px' } },
      Object.keys(profile.summary).length
        ? Object.entries(profile.summary).map(([gid, s]) => h('div', { class: 'recent' },
            h('span', { class: 'r-icon' }, gameById[gid]?.icon || '🎮'),
            h('span', { class: 'r-game' }, gameById[gid]?.codename || gid),
            h('span', { class: 'hint' }, `${s.plays} run${s.plays === 1 ? '' : 's'} · ${s.wins} won`),
            h('b', { class: 'r-score' }, fmt(s.best)),
          ))
        : h('div', { class: 'empty' }, 'No runs yet.')),

    h('div', { class: 'panel-head' },
      h('h3', {}, 'Achievements'),
      h('span', { class: 'hint' }, `${profile.unlocked_count} / ${profile.achievement_total}`)),
    h('div', { class: 'ach-strip' }, profile.achievements.map((a) => h('div', {
      class: `ach ${a.unlocked ? 'on' : ''}`,
      title: `${a.name} — ${a.desc}`,
    }, a.icon))),

    h('div', { class: 'panel-head', style: { marginTop: '18px' } }, h('h3', {}, 'Recent runs')),
    h('div', { class: 'recent-list' },
      profile.recent.length
        ? profile.recent.slice(0, 8).map((r) => h('div', { class: `recent ${r.outcome}` },
            h('span', { class: 'r-icon' }, gameById[r.game]?.icon || '🎮'),
            h('span', { class: 'r-game' }, gameById[r.game]?.codename || r.game),
            h('span', { class: 'hint' }, timeAgo(r.created)),
            h('b', { class: 'r-score' }, fmt(r.score)),
          ))
        : h('div', { class: 'empty' }, 'Nothing here yet.')),
  ], { wide: true });
}

export function howToSheet(game) {
  openSheet([
    sheetHead(`${game.codename} — how it works`),
    h('div', { style: { display: 'flex', gap: '12px', alignItems: 'flex-start', marginBottom: '14px' } },
      h('span', { style: { fontSize: '34px' } }, game.icon),
      h('div', {},
        h('div', { style: { fontWeight: '600', fontSize: 'var(--fs-lg)' } }, game.tagline),
        h('p', { class: 'hint', style: { marginTop: '6px' } }, game.blurb),
      ),
    ),
    h('ol', { class: 'how-list' }, game.how.map((line) => h('li', {}, line))),
    game.modes?.length
      ? h('div', { style: { marginTop: '16px' } },
          h('div', { class: 'panel-head' }, h('h3', {}, 'Modes')),
          h('div', { class: 'recent-list' }, game.modes.map((m) => h('div', { class: 'recent' },
            h('span', { class: 'r-game' }, m.name), h('span', { class: 'hint' }, m.desc)))))
      : null,
  ]);
}
