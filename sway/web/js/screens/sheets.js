// Everything that lives in a modal and is not part of a run: the collection,
// leaderboards, the profile, and how to play.
import { h, fmt, plural } from '../lib/dom.js';
import { sheet, closeSheet } from '../ui/sheet.js';
import { cardEl, relicEl } from '../ui/card.js';
import { api } from '../lib/api.js';
import { toast } from '../ui/toast.js';
import { sfx } from '../lib/sound.js';

const MODES = ['ascent', 'blitz', 'holdout', 'daily', 'endless'];
const MODE_NAME = {
  ascent: 'Ascent', blitz: 'Blitz', holdout: 'Holdout', daily: 'Daily', endless: 'Endless',
};

/* ------------------------------------------------------------- collection */
export function collectionSheet(collection, profile) {
  const level = profile?.level?.level ?? 1;
  const groups = {
    Tactics: collection.cards,
    Defences: collection.wards,
    Relics: collection.relics,
  };
  const body = h('div', {});
  const slot = h('div', {});
  let active = 'Tactics';

  const paint = () => {
    const items = groups[active] || [];
    const owned = items.filter((i) => !i.locked).length;
    slot.replaceChildren(
      h('div', { class: 'coll-progress', style: { marginBottom: '12px' } },
        `${owned} / ${items.length} unlocked · level ${level}`),
      active === 'Relics'
        ? h('div', { class: 'relic-grid' }, ...items.map((r) =>
            relicEl(r, { locked: r.locked, unlockLevel: r.unlock_level })))
        : h('div', { class: 'coll-grid' }, ...items.map((c) =>
            cardEl(c, { locked: c.locked, unlockLevel: c.unlock_level }))),
    );
  };

  const tabs = h('div', { class: 'tabs' }, ...Object.keys(groups).map((name) => h('button', {
    role: 'tab', 'aria-selected': String(name === active),
    onclick: (e) => {
      active = name;
      [...tabs.children].forEach((b) => b.setAttribute('aria-selected', String(b === e.currentTarget)));
      sfx.click();
      paint();
    },
  }, name)));

  body.append(h('div', { class: 'coll-head' }, tabs), slot);
  paint();
  sheet({ title: 'Collection', body, wide: true });
}

/* ------------------------------------------------------------ leaderboard */
export function leaderboardSheet(startMode = null, playerId = null) {
  const slot = h('div', {}, h('div', { class: 'empty' }, 'Loading…'));
  let mode = startMode;
  let daily = false;

  const load = async () => {
    try {
      const data = await api.leaderboard(mode, daily ? 1 : 0);
      const rows = data.entries || [];
      slot.replaceChildren(rows.length
        ? h('div', { class: 'rows' }, ...rows.map((r) => h('div', {
            class: `row-item ${r.player_id === playerId ? 'me' : ''}`,
          },
            h('span', { class: 'rank' }, `#${r.rank}`),
            h('span', { class: 'who' }, r.handle || 'anon',
              r.player_id === playerId ? h('span', { class: 'dim' }, '  (you)') : null),
            data.by === 'floor'
              ? h('span', { class: 'val' }, `floor ${r.floor}`)
              : h('span', { class: 'val' }, fmt(r.score)))))
        : h('div', { class: 'empty' }, 'Nobody has finished a run here yet. Be first.'));
    } catch {
      slot.replaceChildren(h('div', { class: 'empty' }, 'Could not load the board.'));
    }
  };

  const tabs = h('div', { class: 'tabs' },
    ...[null, ...MODES].map((m) => h('button', {
      role: 'tab', 'aria-selected': String(m === mode),
      onclick: (e) => {
        mode = m;
        daily = false;
        [...tabs.children].forEach((b) =>
          b.setAttribute('aria-selected', String(b === e.currentTarget)));
        sfx.click();
        load();
      },
    }, m ? MODE_NAME[m] : 'All')));

  sheet({
    title: 'Leaderboard',
    body: h('div', {}, h('div', { style: { marginBottom: '14px' } }, tabs), slot),
    foot: [h('button', { class: 'btn btn-ghost btn-sm', onclick: () => {
      daily = !daily;
      sfx.click();
      load();
    } }, 'Today only')],
  });
  load();
}

/* ---------------------------------------------------------------- profile */
export function profileSheet(profile, onSaved) {
  const level = profile.level || {};
  const summary = profile.summary || {};
  const input = h('input', { value: profile.handle || '', maxlength: 18,
                             placeholder: 'pick a handle', 'aria-label': 'Handle' });

  const save = async () => {
    try {
      const data = await api.save({ handle: input.value.trim() });
      toast('Saved.', 'good', '✓');
      onSaved?.(data.profile);
    } catch (e) {
      toast(e.message || 'Could not save that.', 'bad');
    }
  };

  const cell = (v, l) => h('div', { class: 'stat-cell' }, h('b', {}, v), h('span', {}, l));

  sheet({
    title: 'You',
    body: h('div', {},
      h('div', { class: 'prof-top' },
        h('div', { class: 'prof-level' }, String(level.level ?? 1)),
        h('div', { class: 'prof-id' },
          h('h3', {}, level.title || ''),
          h('p', {}, `${fmt(level.xp || 0)} xp · ${fmt(level.to_next || 0)} to level ${(level.level ?? 1) + 1}`),
          h('div', { class: 'handle-row' }, input,
            h('button', { class: 'btn btn-sm btn-ghost', onclick: save }, 'Save')))),
      h('div', { class: 'meter', style: { marginBottom: '18px' } },
        h('i', { style: { width: `${(level.progress ?? 0) * 100}%` } })),

      h('div', { class: 'stat-grid' },
        cell(String(profile.streak || 0), 'day streak'),
        cell(String(profile.best_streak || 0), 'best streak'),
        cell(`${profile.badge_count || 0}/${profile.badge_total || 0}`, 'badges'),
        cell(String((profile.beaten || []).length), 'minds beaten')),

      h('h4', { class: 'tiny', style: { margin: '20px 0 9px' } }, 'By mode'),
      Object.keys(summary).length
        ? h('div', { class: 'rows' }, ...MODES.filter((m) => summary[m]).map((m) => h('div', {
            class: 'row-item',
          },
            h('span', { class: 'who' }, MODE_NAME[m]),
            h('span', { class: 'dim' }, plural(summary[m].runs, 'run')),
            h('span', { class: 'val' }, m === 'endless'
              ? `floor ${summary[m].deepest}` : fmt(summary[m].best)))))
        : h('div', { class: 'empty' }, 'No finished runs yet.'),

      h('h4', { class: 'tiny', style: { margin: '20px 0 9px' } }, 'Badges'),
      h('div', { class: 'badge-strip' }, ...(profile.badges || []).map((b) => h('div', {
        class: `badge-pip ${b.unlocked ? 'got' : ''}`, title: `${b.name} — ${b.desc}`,
      }, b.glyph))),
    ),
  });
}

/* ----------------------------------------------------------- how to play */
export function howSheet(demoCards = []) {
  const step = (n, title, text) => h('div', { class: 'how-step' },
    h('span', { class: 'n' }, String(n)),
    h('div', {}, h('b', {}, title), h('p', { html: text })));

  sheet({
    title: 'How to sway someone',
    body: h('div', { class: 'how' },
      h('p', { class: 'dim' },
        'Every floor is one conversation with one mind. They have a position, and you have '
        + 'a limited number of exchanges to move them off it.'),
      step(1, 'Read the room',
        'Each mind has a <em>vulnerability</em> and a <em>resistance</em>, hidden at first. '
        + 'Their replies leak which is which — or spend your one free <em>Read them</em>.'),
      step(2, 'Load your tactics',
        'Cards cost <em>focus</em>. Up to three per message. A card is a promise: play '
        + '<em>Flatter</em> and your message has to actually flatter, specifically, or it '
        + 'fizzles and they notice.'),
      step(3, 'Write the thing',
        'The message is the move. A judge grades how well you executed each card and how '
        + 'much force the message has for <em>this</em> person, then the numbers multiply out.'),
      step(4, 'Chain it',
        'Card multipliers stack with their vulnerability, your <em>momentum</em> and your '
        + 'relics. Good turns compound. That is where the huge numbers come from.'),
      step(5, 'Build the deck',
        'Every floor cleared offers a new tactic or relic. A run is won in the reward '
        + 'screens as much as in the conversations.'),
      demoCards.length
        ? h('div', { class: 'how-demo' }, ...demoCards.slice(0, 3).map((c) => cardEl(c)))
        : null,
      h('p', { class: 'dim', style: { fontSize: '13px' } },
        'Shortcuts: <b>Enter</b> sends · <b>Esc</b> clears your tactics · click the chain to skip it.'),
    ),
    foot: [h('button', { class: 'btn btn-primary btn-block', onclick: () => closeSheet() },
      'Got it')],
  });
}
