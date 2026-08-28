/**
 * The resolve chain - the best three seconds in the game.
 *
 * The server sends an ordered list of steps, each carrying the running BASE and
 * MULT *after* it applies. This module only plays that back, so what the player
 * watches add up is exactly the number the server scored. Any step can be
 * skipped with a click, a tap or any key, because the fifth time you see it you
 * want the result, not the show.
 */
import { h, fmt, reduced } from '../lib/dom.js';
import { sfx, buzz } from '../lib/sound.js';
import * as fx from '../lib/fx.js';

const STEP_MS = (n) => (n > 7 ? 128 : n > 4 ? 168 : 205);

export function playChain(payload, opts = {}) {
  const guard = payload.mode === 'guard';
  const steps = payload.steps || [];
  const total = guard ? payload.guard : payload.damage;

  const baseNum = h('b', {}, '0');
  const multNum = h('b', {}, '1.00');
  const stepList = h('div', { class: 'chain-steps scroller' });
  const totalSlot = h('div', { class: 'chain-total' });
  const skipHint = h('div', { class: 'chain-skip' }, 'click to skip');

  const card = h('section', { class: 'chain' },
    h('div', { class: 'chain-read' },
      payload.read || (guard ? 'Holding.' : 'Scored.'),
      payload.fallback
        ? h('div', { class: 'fallback' }, 'offline read — model unavailable')
        : null),
    h('div', { class: 'chain-eq' },
      h('div', { class: 'side base' }, baseNum, h('span', {}, guard ? 'ward' : 'base')),
      h('div', { class: 'times' }, '×'),
      h('div', { class: 'side mult' }, multNum, h('span', {}, 'mult'))),
    stepList,
    totalSlot,
    skipHint,
  );

  const wrap = h('div', { class: 'chain-wrap', role: 'status', 'aria-live': 'polite' }, card);
  document.getElementById('chain').appendChild(wrap);

  let cancelled = false;
  const timers = [];
  const wait = (ms) => new Promise((r) => timers.push(setTimeout(r, ms)));

  return new Promise((resolve) => {
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      timers.forEach(clearTimeout);
      wrap.remove();
      resolve();
    };

    const skip = () => {
      if (cancelled || finished) return;
      cancelled = true;
      timers.forEach(clearTimeout);
      timers.length = 0;
      renderAll();
      setTimeout(done, 420);
    };

    function bump(el, value) {
      el.textContent = value;
      if (reduced()) return;
      el.classList.add('bump');
      setTimeout(() => el.classList.remove('bump'), 120);
    }

    function stepRow(step) {
      const isMul = step.op === 'mul';
      const value = isMul ? `×${Number(step.value).toFixed(2)}`
                          : `${step.value > 0 ? '+' : ''}${fmt(Math.round(step.value))}`;
      return h('div', { class: 'chain-step', dataset: { tone: step.tone || '', kind: step.kind } },
        h('span', { class: 'label' }, step.label),
        step.note ? h('span', { class: 'note' }, step.note) : null,
        h('span', { class: 'val' }, step.value === 0 && !isMul ? '—' : value));
    }

    function renderTotal() {
      const big = total >= 900;
      totalSlot.className = `chain-total ${guard ? 'guard' : ''} ${total <= 0 ? 'zero' : ''}`;
      totalSlot.replaceChildren(
        h('div', { class: 'n' }, fmt(total)),
        h('div', { class: 'cap' },
          guard ? (payload.incoming > 0
                    ? `${fmt(payload.threat)} pressure · ${fmt(payload.incoming)} got through`
                    : `${fmt(payload.threat)} pressure · nothing got through`)
                : (payload.backlash ? `ground lost · +${fmt(payload.backlash)} back to them`
                                    : (big ? 'that landed' : 'ground taken'))),
      );
      skipHint.textContent = '';
    }

    function renderAll() {
      stepList.replaceChildren(...steps.map(stepRow));
      baseNum.textContent = fmt(Math.round(payload.base || 0));
      multNum.textContent = Number(payload.mult || 1).toFixed(2);
      renderTotal();
    }

    wrap.addEventListener('click', skip);
    const onKey = () => skip();
    document.addEventListener('keydown', onKey, { once: true });
    timers.push(setTimeout(() => document.removeEventListener('keydown', onKey), 20000));

    (async () => {
      if (reduced()) {
        renderAll();
        await wait(1100);
        return done();
      }

      await wait(230);
      const pace = STEP_MS(steps.length);
      for (let i = 0; i < steps.length; i++) {
        if (cancelled) return;
        const step = steps[i];
        stepList.appendChild(stepRow(step));
        stepList.scrollTop = stepList.scrollHeight;
        if (step.op === 'mul') bump(multNum, Number(step.mult).toFixed(2));
        else bump(baseNum, fmt(Math.round(step.base)));
        if (step.tone === 'crit') { sfx.critStep(i); fx.sparks(12, stepList); }
        else if (step.tone === 'bad') sfx.badStep();
        else sfx.step(i);
        await wait(pace);
      }
      if (cancelled) return;

      await wait(140);
      const big = total >= 900;
      renderTotal();
      sfx.slam(big);
      buzz(big ? [18, 40, 26] : 16);
      if (total <= 0) {
        sfx.whiff();
      } else {
        fx.hitstop(big ? 110 : 70);
        fx.shake(document.getElementById('view'), big);
        if (big) fx.confetti(70, totalSlot);
        else fx.sparks(26, totalSlot);
      }
      opts.onTotal?.(total);

      // Savour a big number for longer than a small one.
      await wait(total >= 2000 ? 1450 : big ? 1150 : 820);
      done();
    })();
  });
}
