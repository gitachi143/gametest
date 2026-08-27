/**
 * Per-game HUD panels, header stats and composers.
 *
 * Every view is a pure function of the game's *public* state, which is why the
 * GAUNTLET can render any other game's panel by simply passing its nested
 * `sub` state straight through.
 */
import { h, mount, fmt } from '../lib/dom.js';
import { sfx } from '../lib/sound.js';
import { openSheet, sheetHead, closeSheet } from '../ui/sheet.js';
import { toast } from '../ui/toast.js';

/* ------------------------------------------------------------- helpers */
export const block = (title, ...kids) =>
  h('div', { class: 'hud-block' }, title ? h('div', { class: 'hud-title' }, title) : null, ...kids);

const kv = (k, v, cls = '') => h('div', { class: 'kv' }, h('span', {}, k), h('b', { class: cls }, String(v)));
const meter = (pct, warn = false) =>
  h('div', { class: `meter ${warn ? 'warn' : ''}` }, h('i', { style: { width: `${Math.max(0, Math.min(100, pct * 100))}%` } }));
const stat = (label, value, tone = '') => ({ label, value, tone });

function sendBtn(onclick, label = '➤') {
  return h('button', { class: 'send', onclick, 'aria-label': 'Send' }, label);
}

/**
 * The standard composer: a growing textarea, a send button, and an optional row
 * of auxiliary controls underneath.
 */
function textComposer(ctx, {
  placeholder = 'Say something…', maxlen = 1200, aux = null, hint = null,
  build = (text) => ({ type: 'say', text }), single = false, submitLabel = '➤',
} = {}) {
  const input = single
    ? h('input', { type: 'text', placeholder, maxlength: maxlen })
    : h('textarea', { placeholder, maxlength: maxlen, rows: 1 });
  const counter = maxlen <= 400 ? h('span', { class: 'char-count' }, `0/${maxlen}`) : null;

  const submit = () => {
    const text = input.value.trim();
    if (!text || ctx.busy()) return;
    input.value = '';
    if (counter) counter.textContent = `0/${maxlen}`;
    if (!single) input.style.height = 'auto';
    sfx.send();
    ctx.send(build(text));
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (single || !e.shiftKey)) { e.preventDefault(); submit(); }
  });
  input.addEventListener('input', () => {
    if (!single) {
      input.style.height = 'auto';
      input.style.height = Math.min(150, input.scrollHeight) + 'px';
    }
    if (counter) {
      counter.textContent = `${input.value.length}/${maxlen}`;
      counter.classList.toggle('over', input.value.length >= maxlen);
    }
  });

  const node = h('div', { class: 'composer' },
    h('div', { class: 'composer-row' }, input, sendBtn(submit, submitLabel)),
    (aux || counter || hint)
      ? h('div', { class: 'composer-aux' }, aux, hint ? h('span', { class: 'hint' }, hint) : null, counter)
      : null,
  );
  node.focusInput = () => input.focus({ preventScroll: true });
  return node;
}

function buttonRow(buttons) {
  return h('div', { class: 'quick' }, buttons.filter(Boolean).map((b) => h('button', {
    class: b.active ? 'active' : '',
    disabled: b.disabled || false,
    title: b.title || '',
    onclick: () => { sfx.click(); b.onclick(); },
  }, b.label)));
}

/** A one-field inline prompt used for VAULT cracks and ORACLE naming. */
function inlinePrompt(ctx, { title, placeholder, confirm, build }) {
  const input = h('input', { type: 'text', placeholder, maxlength: 120,
    style: { flex: '1', height: '42px', padding: '0 13px', borderRadius: 'var(--r-md)',
             border: '1px solid var(--line)', background: 'rgba(0,0,0,0.35)',
             fontFamily: 'var(--font-mono)', letterSpacing: '0.06em' } });
  const go = () => {
    const v = input.value.trim();
    if (!v) return;
    closeSheet();
    ctx.send(build(v));
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } });
  openSheet([
    sheetHead(title),
    h('div', { style: { display: 'flex', gap: '8px' } }, input,
      h('button', { class: 'btn btn-primary', onclick: go }, confirm)),
  ]);
  setTimeout(() => input.focus(), 60);
}

/* ================================================================ VAULT */
const FLOOR_NAMES = ['TRAINEE', 'GUARDED', 'PARANOID', 'AUDITOR', 'GATEKEEPER', 'CIPHER', 'AMNESIAC', 'ARGUS PRIME'];

const vault = {
  stats: (p) => [
    stat('floor', `${p.floor}/8`),
    stat('turns', `${p.turns}/${p.limit}`, p.limit - p.turns <= 2 ? 'alert' : ''),
    stat('banked', fmt(p.run_score), 'good'),
  ],
  hud: (p) => [
    p.leaked ? block(null, h('div', { class: 'leak-banner' }, '📡', h('div', {},
      h('b', {}, 'Signal in the transcript.'),
      h('div', { class: 'hint' }, 'Something ARGUS said carries the phrase. Read it again, then CRACK.')))) : null,
    block('the tower', h('div', { class: 'tower' }, FLOOR_NAMES.map((name, i) => {
      const n = i + 1;
      const state = p.cleared.includes(n) ? 'cleared' : (n === p.floor ? 'current' : '');
      return h('div', { class: `tower-floor ${state}` },
        h('span', { class: 'tf-n' }, n),
        h('span', { class: 'tf-name' }, name),
        h('span', { class: 'tf-mark' }, p.cleared.includes(n) ? '🔓' : (n === p.floor ? '▶' : '🔒')));
    }))),
    block('this floor', h('div', { style: { fontSize: 'var(--fs-sm)', color: 'var(--ink-2)', marginBottom: '10px' } }, p.floor_desc),
      h('div', { class: 'def-chips' }, (p.defences || []).map((d) =>
        h('span', { class: `def-chip ${d === 'none' ? 'none' : ''}` }, d))),
      h('div', { style: { marginTop: '12px' } }, meter(1 - p.turns / Math.max(1, p.limit), p.limit - p.turns <= 2)),
      kv('messages left', Math.max(0, p.limit - p.turns)),
      p.char_limit ? kv('char limit', p.char_limit, 'alert') : null,
      p.blocked ? kv('intercepted', p.blocked, 'alert') : null,
      p.redacted ? kv('redacted', p.redacted, 'alert') : null,
    ),
  ],
  composer: (p, ctx) => textComposer(ctx, {
    placeholder: p.char_limit
      ? `Max ${p.char_limit} characters. Make them count…`
      : 'Talk to ARGUS. Flattery, misdirection, poetry — anything but asking nicely…',
    maxlen: p.char_limit || 1200,
    hint: 'Enter to send · Shift+Enter for a new line',
    aux: buttonRow([
      { label: '🔑 Crack it', title: 'Submit the passphrase', onclick: () => inlinePrompt(ctx, {
        title: `Floor ${p.floor} — enter the passphrase`,
        placeholder: 'e.g. VELVET-ORBIT',
        confirm: 'Crack',
        build: (v) => ({ type: 'guess', value: v }),
      }) },
      p.cleared.length ? { label: `🏦 Cash out (${fmt(p.run_score)})`, onclick: () => ctx.send({ type: 'cash_out' }) } : null,
    ]),
  }),
};

/* =============================================================== ORACLE */
const oracle = {
  stats: (p) => [
    stat('asked', `${p.used}/${p.limit}`, p.left <= 3 ? 'alert' : ''),
    stat('left', p.left, p.left <= 3 ? 'alert' : 'good'),
  ],
  hud: (p) => [
    block('questions', h('div', { class: 'q-dots' }, Array.from({ length: p.limit }, (_, i) => {
      const entry = p.history[i];
      return h('div', { class: `q-dot ${entry ? entry.a : ''}` });
    }))),
    p.category ? block('category', h('div', { style: { fontSize: 'var(--fs-lg)', fontFamily: 'var(--font-mono)' } }, p.category)) : null,
    p.history.length ? block('transcript', h('div', { class: 'qa-list' }, p.history.slice().reverse().map((entry) =>
      h('div', { class: 'qa' },
        h('span', { class: 'qa-q' }, entry.q),
        h('span', { class: `qa-a ${entry.a}` }, entry.a))))) : null,
  ],
  composer: (p, ctx) => {
    if (p.mode === 'stump' && p.phase === 'declare') {
      return textComposer(ctx, {
        placeholder: 'Think of one specific thing, then seal it…', maxlen: 120, single: true,
        submitLabel: '🔒', hint: 'The Oracle never sees this. Answer honestly and it might still find it.',
        build: (v) => ({ type: 'declare', value: v }),
      });
    }
    if (p.mode === 'stump') {
      const pad = h('div', { class: 'answer-pad' },
        ['yes', 'no', 'sometimes', 'unclear'].map((v) => h('button', {
          dataset: { v }, disabled: !p.pending,
          onclick: () => { sfx.click(); ctx.send({ type: 'answer', value: v }); },
        }, v)));
      return h('div', { class: 'composer' },
        h('div', { class: 'hint', style: { marginBottom: '4px' } },
          p.pending ? 'Answer honestly:' : 'The Oracle is thinking…'),
        pad);
    }
    return textComposer(ctx, {
      placeholder: 'Ask a yes/no question…', maxlen: 300, single: true,
      hint: 'Non-yes/no questions are free — the Oracle just refuses them.',
      aux: buttonRow([
        { label: '🎯 Name it', onclick: () => inlinePrompt(ctx, {
          title: 'Name the secret', placeholder: 'e.g. a lighthouse', confirm: 'Name it',
          build: (v) => ({ type: 'guess', value: v }),
        }) },
        !p.hint_bought ? { label: '💡 Buy category (−4)', onclick: () => ctx.send({ type: 'hint' }) } : null,
      ]),
    });
  },
};

/* ============================================================== HOTWIRE */
const hotwire = {
  stats: (p) => [
    stat('score', fmt(p.score), 'good'),
    stat('combo', `×${p.multiplier}`, p.combo >= 3 ? 'warn' : ''),
    stat('cards', p.solved.length),
  ],
  hud: (p, ctx) => {
    const R = 34, C = 2 * Math.PI * R;
    const bar = h('circle', { class: 'bar', cx: 44, cy: 44, r: R,
      'stroke-dasharray': C, 'stroke-dashoffset': C * (1 - p.remaining / Math.max(1, p.seconds)) });
    const num = h('div', { class: 'timer-num' }, Math.ceil(p.remaining));
    const ring = h('div', { class: `timer-ring ${p.remaining <= 10 ? 'low' : ''}` },
      h('svg', { width: 88, height: 88, viewBox: '0 0 88 88' },
        h('circle', { class: 'track', cx: 44, cy: 44, r: R }), bar), num);

    // Client-side countdown between turns; the server is still the referee.
    let left = p.remaining;
    const id = setInterval(() => {
      left = Math.max(0, left - 0.25);
      num.textContent = Math.ceil(left);
      bar.setAttribute('stroke-dashoffset', C * (1 - left / Math.max(1, p.seconds)));
      ring.classList.toggle('low', left <= 10);
      if (left <= 10 && Math.abs(left % 1) < 0.01) sfx.tick();
      if (left <= 0) { clearInterval(id); if (!ctx.busy()) ctx.send({ type: 'timeup' }); }
    }, 250);
    ctx.registerTimer(id);

    return [
      block(null, h('div', { class: 'wire-card' },
        h('div', { class: 'wk' }, `target · tier ${p.tier}`),
        h('div', { class: 'wire-target' }, p.target),
        h('div', { class: 'wk', style: { marginBottom: '8px' } }, 'banned'),
        h('div', { class: 'taboo-list' }, (p.taboo || []).map((t) => h('div', { class: 'taboo' }, t))),
      )),
      block('clock', h('div', { style: { display: 'flex', gap: '14px', alignItems: 'center' } }, ring,
        h('div', { style: { flex: '1' } },
          h('div', { class: `combo-flame ${p.combo >= 3 ? 'hot' : ''}` },
            p.combo >= 3 ? '🔥' : '', `×${p.multiplier}`),
          h('div', { class: 'hint', style: { textAlign: 'center' } }, `${p.combo} in a row`),
        ))),
      (p.solved.length || p.burned.length) ? block('this run', h('div', { class: 'solved-chips' }, [
        ...p.solved.map((s) => h('span', { class: 'solved-chip' }, `${s.word} +${s.points}`)),
        ...p.burned.map((w) => h('span', { class: 'solved-chip burn' }, w)),
      ])) : null,
    ];
  },
  composer: (p, ctx) => textComposer(ctx, {
    placeholder: `Describe “${p.target}” without saying it…`, maxlen: 240, single: true,
    hint: 'Your partner answers with three guesses.',
    aux: buttonRow([{ label: '⏭️ Skip (−4s)', onclick: () => ctx.send({ type: 'skip' }) }]),
  }),
};

/* ============================================================ COLD CASE */
const coldcase = {
  stats: (p) => [
    stat('questions', `${p.used}/${p.budget}`, p.left <= 2 ? 'alert' : ''),
    stat('left', p.left, p.left <= 2 ? 'alert' : 'good'),
  ],
  hud: (p, ctx) => [
    block('case file', h('div', { class: 'case-file' },
      h('div', { class: 'cf-row' }, h('span', { class: 'cf-k' }, 'victim'), h('span', {}, `${p.brief.victim}, ${p.brief.role}`)),
      h('div', { class: 'cf-row' }, h('span', { class: 'cf-k' }, 'time'), h('span', {}, p.brief.time)),
      h('div', { class: 'cf-row' }, h('span', { class: 'cf-k' }, 'scene'), h('span', {}, p.brief.murder_room)),
      h('div', { class: 'cf-row' }, h('span', { class: 'cf-k' }, 'cause'), h('span', {}, p.brief.weapon)),
      h('div', { class: 'cf-row' }, h('span', { class: 'cf-k' }, 'place'), h('span', {}, p.brief.place)),
    )),
    block('suspects', h('div', { class: 'suspects' }, p.suspects.map((s) => h('button', {
      class: `suspect ${s.id === p.focus ? 'on' : ''}`,
      onclick: () => { sfx.click(); ctx.send({ type: 'focus', value: s.id }); },
    },
      h('span', { class: 's-av' }, s.avatar),
      h('div', {}, h('div', { class: 's-name' }, s.name), h('div', { class: 's-role' }, s.profession)),
      h('span', { class: 's-asked' }, `${s.asked}q`),
    )))),
    block('alibis', p.suspects.map((s) => kv(s.name, s.alibi_room))),
  ],
  composer: (p, ctx) => {
    const focused = p.suspects.find((s) => s.id === p.focus) || p.suspects[0];
    return textComposer(ctx, {
      placeholder: p.left > 0
        ? `Question ${focused.name}…`
        : 'Out of questions. Make the accusation.',
      maxlen: 400,
      hint: p.left > 0 ? `Asking ${focused.name} · ${p.left} question${p.left === 1 ? '' : 's'} left` : '',
      build: (text) => ({ type: 'say', target: p.focus, text }),
      aux: buttonRow([
        { label: '⚖️ Accuse', onclick: () => accuseSheet(p, ctx) },
        ...p.suspects.map((s) => ({
          label: `${s.avatar} ${s.name.split(' ')[0]}`,
          active: s.id === p.focus,
          onclick: () => ctx.send({ type: 'focus', value: s.id }),
        })),
      ]),
    });
  },
};

function accuseSheet(p, ctx) {
  let killer = '';
  let witness = '';
  const render = () => mount(list, p.suspects.map((s) => h('button', {
    class: `suspect ${killer === s.id ? 'accuse-pick' : ''} ${witness === s.id ? 'witness-pick' : ''}`,
    onclick: () => {
      if (!killer) killer = s.id;
      else if (killer === s.id) killer = '';
      else if (witness === s.id) witness = '';
      else witness = s.id;
      sfx.click();
      render();
      go.disabled = !killer;
      go.textContent = killer && witness ? 'Charge them' : (killer ? 'Charge (no witness)' : 'Pick the killer');
    },
  },
    h('span', { class: 's-av' }, s.avatar),
    h('div', {}, h('div', { class: 's-name' }, s.name), h('div', { class: 's-role' }, s.profession)),
    h('span', { class: 's-asked' }, killer === s.id ? 'KILLER' : (witness === s.id ? 'WITNESS' : '')),
  )));
  const list = h('div', { class: 'suspects' });
  const go = h('button', { class: 'btn btn-primary', disabled: true, onclick: () => {
    closeSheet();
    ctx.send({ type: 'accuse', value: killer, witness });
  } }, 'Pick the killer');
  render();
  openSheet([
    sheetHead('Make the accusation'),
    h('p', { class: 'hint', style: { marginBottom: '12px' } },
      'Tap once for the killer, again on a second name for the witness whose testimony breaks the alibi. The witness is worth points, and getting it wrong costs nothing but pride.'),
    list,
    h('div', { class: 'sheet-actions' }, go,
      h('button', { class: 'btn btn-ghost', onclick: closeSheet }, 'Keep digging')),
  ]);
}

/* ============================================================== SLEEPER */
const PHASES = [['clue', 'clues'], ['discuss', 'talk'], ['vote', 'vote'], ['done', 'reveal']];

const sleeper = {
  stats: (p) => [
    stat('phase', p.phase),
    stat('round', `${p.round}/${p.rounds}`),
    stat('role', p.you_are_sleeper ? 'SLEEPER' : 'CREW', p.you_are_sleeper ? 'alert' : 'good'),
  ],
  hud: (p) => [
    block(null, h('div', { class: `secret-box ${p.you_are_sleeper ? 'sleeper' : ''}` },
      h('div', { class: 'sk' }, p.you_are_sleeper ? 'you are the sleeper' : 'the secret word'),
      h('div', { class: 'sv' }, p.you_are_sleeper ? `category: ${p.category}` : p.word),
      p.you_are_sleeper ? h('div', { class: 'hint', style: { marginTop: '6px' } }, 'Bluff. Read the clues. Survive the vote.') : null,
    )),
    block('phase', h('div', { class: 'phase-steps' }, PHASES.map(([id, label], i) => {
      const order = PHASES.findIndex(([x]) => x === p.phase);
      return h('div', { class: `phase-step ${p.phase === id ? 'on' : (i < order ? 'done' : '')}` }, label);
    }))),
    block('the table', h('div', { class: 'table-players' }, p.players.map((pl) => h('div', {
      class: `tp ${pl.id === 'you' ? 'you' : ''}`,
    },
      h('span', { class: 'tp-av' }, pl.avatar),
      h('span', {}, pl.name),
      h('span', { class: 'tp-clues' }, p.clues.filter((c) => c.id === pl.id).map((c) =>
        h('span', { class: 'tp-clue' }, c.clue))),
    )))),
  ],
  composer: (p, ctx) => {
    if (p.phase === 'clue') {
      return textComposer(ctx, {
        placeholder: 'Your clue — three words maximum…', maxlen: 60, single: true,
        hint: p.you_are_sleeper ? 'You are guessing. Sound like you are not.' : 'Prove you know it. Do not hand it over.',
        build: (v) => ({ type: 'clue', value: v }),
      });
    }
    if (p.phase === 'discuss') {
      return textComposer(ctx, {
        placeholder: 'Accuse someone, defend yourself, point at a clue…', maxlen: 240,
        hint: 'One line each, then the vote.',
      });
    }
    if (p.phase === 'vote') {
      return h('div', { class: 'composer' },
        h('div', { class: 'hint', style: { marginBottom: '8px' } }, 'Vote out the sleeper:'),
        buttonRow(p.players.filter((pl) => pl.id !== 'you').map((pl) => ({
          label: `${pl.avatar} ${pl.name}`,
          onclick: () => ctx.send({ type: 'vote', value: pl.id }),
        }))));
    }
    return h('div', { class: 'composer' }, h('div', { class: 'hint' }, 'Round over.'));
  },
};

/* ============================================================ CROSSFIRE */
const crossfire = {
  stats: (p) => [
    stat('round', `${p.round}/${p.rounds}`),
    stat('bench', `${p.total}/${p.max_total}`, p.total >= p.target ? 'good' : ''),
    stat('need', p.target),
  ],
  hud: (p) => [
    block('the motion', h('div', { class: 'motion' }, `“${p.claim}”`,
      h('div', {}, h('span', { class: 'side' }, `you argue ${p.your_side}`)))),
    block('the bench', h('div', { class: 'judges' }, p.judges.map((j) => h('div', {},
      h('div', { class: 'judge-row' },
        h('div', { class: 'judge-av' }, j.avatar),
        h('div', { class: 'judge-info' },
          h('div', { class: 'judge-name' }, j.name),
          h('div', { class: 'judge-value' }, j.value)),
        h('div', { class: 'judge-total' }, j.total)),
      h('div', { class: 'judge-blocks' }, Array.from({ length: p.rounds }, (_, i) => {
        const s = j.scores[i];
        const cls = s === undefined ? '' : (s >= 9 ? 's9' : s >= 6 ? 's6' : s >= 4 ? 's4' : 's0');
        return h('div', { class: `jb ${cls}`, title: s === undefined ? 'to come' : `round ${i + 1}: ${s}/10` });
      })),
    )))),
    block('progress', meter(p.total / Math.max(1, p.target)),
      kv('to win', Math.max(0, p.target - p.total))),
  ],
  composer: (p, ctx) => textComposer(ctx, {
    placeholder: `Round ${p.round}. Make your case for ${p.your_side} the motion…`,
    maxlen: p.max_chars || 900,
    hint: 'Vex wants proof · Bloom wants humanity · Riot wants nerve',
  }),
};

/* ============================================================= GAUNTLET */
const gauntlet = {
  stats: (p) => [
    stat('round', `${p.round}/${p.max_rounds}`),
    stat('lives', '♥'.repeat(Math.max(0, p.lives)) || '—', p.lives <= 1 ? 'alert' : ''),
    stat('score', fmt(p.score), 'good'),
    stat('mult', `×${p.multiplier}`, 'warn'),
    // Nested round stats, relabelled so "score" can't appear twice.
    ...(p.sub && VIEWS[p.sub_game]
      ? VIEWS[p.sub_game].stats(p.sub).slice(0, 2).map((s) => ({ ...s, label: `rnd ${s.label}` }))
      : []),
  ],
  hud: (p, ctx) => [
    block('run', h('div', { class: 'lives' }, Array.from({ length: p.max_lives }, (_, i) =>
      h('span', { class: i < p.lives ? '' : 'lost' }, '♥'))),
      h('div', { class: 'mult', style: { marginTop: '8px' } }, `×${p.multiplier}`),
      kv('banked', fmt(p.score)),
      kv('cleared', `${p.cleared}/${p.max_rounds}`),
    ),
    p.sub_game ? block(null, h('div', { class: 'now-playing' },
      h('span', { class: 'np-icon' }, GAME_ICONS[p.sub_game] || '🎮'),
      h('div', {},
        h('div', { class: 'np-label' }, `round ${p.round} · ${p.sub_label}`),
        h('div', { class: 'np-game' }, p.sub_title)))) : null,
    p.history.length ? block('ladder', h('div', { class: 'ladder' }, [
      ...p.history.map((rung) => h('div', { class: `rung ${rung.cleared ? 'cleared' : 'failed'}` },
        h('span', { class: 'rn' }, rung.round),
        h('span', { class: 'rl' }, rung.label),
        h('span', { class: 'rp' }, rung.cleared ? `+${fmt(rung.points)}` : '✕'))),
      h('div', { class: 'rung now' },
        h('span', { class: 'rn' }, p.round),
        h('span', { class: 'rl' }, p.sub_label || '—'),
        h('span', { class: 'rp' }, 'live')),
    ])) : null,
    ...(p.sub && VIEWS[p.sub_game] ? VIEWS[p.sub_game].hud(p.sub, ctx) : []),
  ],
  composer: (p, ctx) => (p.sub && VIEWS[p.sub_game]
    ? VIEWS[p.sub_game].composer(p.sub, ctx)
    : h('div', { class: 'composer' }, h('div', { class: 'hint' }, 'Run complete.'))),
};

const GAME_ICONS = {
  vault: '🔓', oracle: '🔮', hotwire: '⚡', coldcase: '🕵️', sleeper: '🐺', crossfire: '⚖️', gauntlet: '🎰',
};

/* --------------------------------------------------------------- export */
const generic = {
  stats: () => [],
  hud: () => [],
  composer: (p, ctx) => textComposer(ctx, {}),
};

export const VIEWS = { vault, oracle, hotwire, coldcase, sleeper, crossfire, gauntlet, generic };
export { GAME_ICONS };
