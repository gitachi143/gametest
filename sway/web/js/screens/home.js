// Home: the pitch, the modes, and everything you have collected.
import { h, mount, fmt, plural, timeAgo, setHue, sleep } from '../lib/dom.js';
import { cardEl } from '../ui/card.js';
import { sfx } from '../lib/sound.js';
import * as fx from '../lib/fx.js';

const DIFF = 5;

/** A looping miniature of the resolve chain. It teaches the whole game in 4s. */
function heroDemo() {
  const rows = [
    { t: 'Persuasion 8 / 10', v: '440', cls: 'g' },
    { t: 'Flatter · masterful', v: '×1.70', cls: 'y' },
    { t: 'Proud of the Post · vulnerable', v: '×2.00', cls: 'y' },
    { t: 'Momentum ×3', v: '×1.36', cls: 'y' },
  ];
  const list = h('div', { class: 'demo-chain' });
  const total = h('div', { class: 'demo-total' }, '—');
  const wrap = h('div', { class: 'hero-demo' },
    h('div', { class: 'cap tiny' }, 'a good turn, floor three'),
    list, total);

  let stopped = false;
  (async () => {
    while (!stopped) {
      list.replaceChildren();
      total.textContent = '—';
      for (const r of rows) {
        if (stopped) return;
        list.appendChild(h('div', { class: `r ${r.cls}` },
          h('span', {}, r.t), h('b', {}, r.v)));
        await sleep(430);
      }
      if (stopped) return;
      total.textContent = '2,034';
      total.animate?.([{ transform: 'scale(1.7)', opacity: 0 }, { transform: 'scale(1)', opacity: 1 }],
        { duration: 420, easing: 'cubic-bezier(0.34,1.46,0.64,1)' });
      await sleep(2600);
    }
  })();
  wrap._stop = () => { stopped = true; };
  return wrap;
}

export function renderHome(host, data, hooks) {
  setHue(40);
  const profile = data.profile || {};
  const g = data.global || {};
  const dailyDone = data.daily?.played;
  const active = (profile.active || []).filter((r) => r.phase !== 'done');
  const demo = heroDemo();

  const modeCard = (m) => {
    const played = m.one_shot && dailyDone;
    return h('button', {
      class: `mode ${played ? 'played' : ''}`,
      style: { '--accent': m.accent },
      onpointerenter: () => sfx.hover(),
      onclick: () => {
        if (played) { hooks.leaderboard('daily'); return; }
        sfx.click();
        hooks.play(m.id);
      },
    },
      played ? h('span', { class: 'mode-flag' }, 'played') : null,
      m.endless ? h('span', { class: 'mode-flag' }, 'no ceiling') : null,
      h('div', { class: 'mode-top' },
        h('span', { class: 'mode-glyph' }, m.glyph),
        h('h3', {}, m.name),
        h('span', { class: 'mode-mins' }, `${m.minutes} min`)),
      h('div', { class: 'mode-tag' }, m.tagline),
      h('p', { class: 'mode-blurb' }, m.blurb),
      h('div', { class: 'mode-foot' },
        h('span', { class: 'pips' },
          ...Array.from({ length: DIFF }, (_, i) =>
            h('i', { class: i < m.difficulty ? 'on' : '' }))),
        h('span', { class: 'meta' },
          m.endless ? 'endless' : plural(m.floors, 'floor'),
          m.cards ? '' : ' · no cards'),
        h('span', { class: 'go' }, played ? 'board ▸' : 'enter ▸')),
    );
  };

  const view = h('div', { class: 'home scroller' },
    h('div', { class: 'home-inner' },
      h('section', { class: 'hero' },
        h('div', {},
          h('div', { class: 'hero-eyebrow' },
            h('span', { class: `dot ${data.demo_mode ? 'demo' : ''}` }),
            data.demo_mode ? 'demo mode · scripted opponent' : `live · ${data.provider}`),
          h('h1', {}, 'Every word is a ', h('em', {}, 'weapon'), '.'),
          h('p', { class: 'hero-sub' },
            'A persuasion roguelike. Talk your way past eight minds who each want something '
            + 'different, playing ',
            h('b', {}, 'tactic cards'),
            ' that only pay out if your message actually does what they promise.'),
          h('div', { class: 'hero-cta' },
            h('button', { class: 'btn btn-primary btn-lg',
                          onclick: () => { sfx.click(); hooks.play('ascent'); } },
              '◈ Start an Ascent'),
            h('button', {
              class: 'btn btn-ghost btn-lg',
              onclick: () => { sfx.click(); dailyDone ? hooks.leaderboard('daily') : hooks.play('daily'); },
            }, dailyDone ? '◉ Today\'s board' : '◉ Play the Daily'),
            h('button', { class: 'btn btn-ghost btn-lg', onclick: () => { sfx.click(); hooks.how(); } },
              'How it works')),
          h('div', { class: 'hero-stats' },
            h('div', {}, h('b', {}, fmt(g.runs || 0)), h('span', {}, 'runs played')),
            h('div', {}, h('b', {}, fmt(g.best || 0)), h('span', {}, 'best score')),
            h('div', {}, h('b', {}, fmt(g.players || 0)), h('span', {}, 'talkers')))),
        demo),

      active.length
        ? h('section', { class: 'resume' },
            h('span', { style: { fontSize: '1.6rem' } }, '↻'),
            h('div', { class: 'body' },
              h('b', {}, `Unfinished ${active[0].mode}`),
              h('p', {}, active[0].mind
                ? `Floor ${active[0].floor} · ${active[0].mind} is waiting · ${timeAgo(active[0].updated)}`
                : `Floor ${active[0].floor} · ${timeAgo(active[0].updated)}`)),
            h('button', { class: 'btn btn-primary', onclick: () => hooks.resume(active[0].id) },
              'Resume'),
            h('button', { class: 'btn btn-ghost btn-sm', onclick: () => hooks.drop(active[0].id) },
              'Discard'))
        : null,

      h('section', {},
        h('div', { class: 'sec-head' },
          h('h2', {}, 'Five ways in'),
          h('span', { class: 'spacer' }),
          h('span', { class: 'hint' }, 'one engine, five shapes')),
        h('div', { class: 'modes' }, ...(data.modes || []).map(modeCard))),

      h('section', { class: 'home-cols' },
        h('div', { class: 'panel' },
          h('div', { class: 'panel-head' },
            h('h3', {}, 'Collection'),
            h('span', { class: 'tiny' },
              `${(data.collection?.cards || []).filter((c) => !c.locked).length}`
              + ` / ${(data.collection?.cards || []).length} tactics`)),
          h('div', { class: 'panel-body' },
            h('p', { class: 'dim', style: { fontSize: '13px', marginBottom: '12px' } },
              'Levels unlock tactics and relics permanently. They start appearing in run '
              + 'rewards the moment you own them.'),
            h('div', { class: 'how-demo', style: { marginBottom: '12px' } },
              ...(data.collection?.cards || []).filter((c) => !c.locked).slice(0, 2)
                .map((c) => cardEl(c))),
            h('button', { class: 'btn btn-ghost btn-block btn-sm',
                          onclick: () => hooks.collection() }, 'Open collection'))),

        h('div', { class: 'panel' },
          h('div', { class: 'panel-head' },
            h('h3', {}, 'Badges'),
            h('span', { class: 'tiny' }, `${profile.badge_count || 0} / ${profile.badge_total || 0}`)),
          h('div', { class: 'panel-body' },
            h('div', { class: 'badge-strip' }, ...(profile.badges || []).map((b) => h('div', {
              class: `badge-pip ${b.unlocked ? 'got' : ''}`,
              title: `${b.name} — ${b.desc}`,
            }, b.glyph))),
            (profile.recent || []).length
              ? h('div', {},
                  h('h4', { class: 'tiny', style: { margin: '18px 0 4px' } }, 'Last runs'),
                  ...profile.recent.slice(0, 5).map((r) => h('div', { class: 'recent-row' },
                    h('span', { class: 'm' }, r.mode),
                    h('span', { class: `o ${r.outcome}` },
                      r.outcome === 'win' ? 'cleared' : `floor ${r.floor}`),
                    h('span', { class: 'v' }, fmt(r.score)))))
              : h('p', { class: 'dim', style: { fontSize: '13px', marginTop: '14px' } },
                  'Finish a run and it shows up here.'),
            h('button', { class: 'btn btn-ghost btn-block btn-sm', style: { marginTop: '14px' },
                          onclick: () => hooks.leaderboard() }, 'Leaderboards')))),

      h('footer', { class: 'tiny', style: { textAlign: 'center', opacity: 0.5 } },
        'SWAY · one conversation at a time'),
    ),
  );

  mount(host, view);
  host._stopDemo = demo._stop;
  fx.motes(20);
  return view;
}
