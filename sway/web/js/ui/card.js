// The tactic card, and the fan it sits in.
import { h, style, isTouch, reduced } from '../lib/dom.js';
import { sfx, buzz } from '../lib/sound.js';

const EFFECT_LABEL = {
  draw: (n) => `draw ${n}`,
  focus: (n) => `+${n} focus`,
  linger: (n) => `+${n}× next`,
  reveal: (n) => `read ${n}`,
  pierce: () => 'ignores resist',
  chain: () => 'links',
  echo: () => 'copies',
  copy: () => 'mirrors',
  backlash: (n) => `risk ${n}`,
};

function effectText(card) {
  const out = [];
  for (const [k, v] of Object.entries(card.effect || {})) {
    const fn = EFFECT_LABEL[k];
    if (fn) out.push(fn(v));
  }
  return out.join(' · ');
}

/**
 * @param {object} card
 * @param {object} [opts] - { cost, on, order, dud, locked, unlockLevel, onpick, compact }
 */
export function cardEl(card, opts = {}) {
  const cost = opts.cost ?? card.cost ?? 0;
  const free = cost === 0 && (card.cost ?? 0) > 0;
  const eff = effectText(card);

  const el = h(opts.onpick ? 'button' : 'div', {
    class: ['card', opts.onpick && 'pick', opts.on && 'on', opts.dud && 'dud',
            opts.locked && 'locked'].filter(Boolean).join(' '),
    dataset: { rarity: card.rarity || 'common', id: card.id, slot: String(opts.slot ?? '') },
    type: opts.onpick ? 'button' : null,
    title: `${card.name} — ${card.rule}${card.flavor ? `\n\n“${card.flavor}”` : ''}`,
    'aria-pressed': opts.onpick ? String(Boolean(opts.on)) : null,
    onclick: opts.onpick || null,
  },
    opts.on && opts.order ? h('span', { class: 'card-order' }, opts.order) : null,
    h('div', { class: 'card-top' },
      cost === 0
        ? h('span', { class: 'card-cost free' }, free ? 'FREE' : '0')
        : h('span', { class: 'card-cost' },
            ...Array.from({ length: cost }, () => h('i'))),
      h('span', { class: 'card-glyph' }, card.glyph || '◆')),
    h('div', { class: 'card-name' }, card.name),
    h('div', { class: 'card-rule' }, card.rule),
    h('div', { class: 'card-stats' },
      h('span', { class: 'pow' }, card.power ? `+${card.power}` : '—'),
      eff ? h('span', { class: 'eff' }, eff) : null,
      h('span', { class: 'mul' }, card.mult > 1 ? `×${card.mult}` : '—')),
    opts.locked ? h('div', { class: 'card-lock' }, `Level ${opts.unlockLevel}`) : null,
  );

  if (opts.onpick && !isTouch()) {
    el.addEventListener('pointerenter', () => sfx.hover());
    tilt(el);
  }
  return el;
}

/** Pointer-tracked 3D tilt. Costs nothing when the pointer is elsewhere. */
function tilt(el) {
  if (reduced()) return;
  const move = (e) => {
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    el.style.setProperty('--t-hover',
      `translateY(-15px) scale(1.07) rotateX(${-py * 11}deg) rotateY(${px * 13}deg)`);
  };
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerleave', () => el.style.removeProperty('--t-hover'));
}

/**
 * Lay a hand out as a fan. Rotation and lift are set per card from JS because
 * the CSS would need abs() to do it, which is not dependable yet.
 */
export function layoutFan(host) {
  const cards = [...host.querySelectorAll('.card')];
  const n = cards.length;
  if (!n || isTouch()) return;
  const spread = Math.min(3.4, 15 / Math.max(1, n));
  cards.forEach((card, i) => {
    const off = i - (n - 1) / 2;
    const rot = off * spread;
    const lift = Math.abs(off) * Math.abs(off) * 1.9;
    style(card, {
      '--t-rest': `rotate(${rot}deg) translateY(${lift}px)`,
      '--t-fan-hover': `translateY(-15px) scale(1.07) rotate(${rot * 0.25}deg)`,
      '--t-on': `translateY(-24px) scale(1.045) rotate(${rot * 0.2}deg)`,
      zIndex: String(10 - Math.round(Math.abs(off))),
    });
  });
}

export function dealAnimation(host) {
  [...host.querySelectorAll('.card')].forEach((card, i) => {
    card.style.animationDelay = `${i * 55}ms`;
    card.classList.add('deal');
    setTimeout(() => sfx.deal(0), i * 55);
  });
  buzz(6);
}

export function relicEl(relic, opts = {}) {
  return h(opts.onpick ? 'button' : 'div', {
    class: `relic ${opts.locked ? 'locked' : ''}`,
    dataset: { rarity: relic.rarity || 'common' },
    type: opts.onpick ? 'button' : null,
    onclick: opts.onpick || null,
    title: relic.flavor ? `“${relic.flavor}”` : null,
  },
    h('span', { class: 'relic-glyph' }, relic.glyph || '♦'),
    h('div', { class: 'relic-body' },
      h('b', {}, relic.name),
      h('p', {}, relic.text),
      relic.flavor ? h('i', {}, `“${relic.flavor}”`) : null,
      opts.locked ? h('i', { class: 'gold' }, `Unlocks at level ${opts.unlockLevel}`) : null),
  );
}
