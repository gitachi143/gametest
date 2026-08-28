// One modal at a time. Route changes close it, so a non-dismissable result
// sheet can never leave an invisible backdrop swallowing clicks.
import { h, $, clear } from '../lib/dom.js';
import { sfx } from '../lib/sound.js';

let open = null;
let onEsc = null;

export function closeSheet() {
  if (!open) return;
  open.remove();
  open = null;
  if (onEsc) { document.removeEventListener('keydown', onEsc); onEsc = null; }
}

/**
 * @param {object} opts
 * @param {string} opts.title            heading text (omit for a bare sheet)
 * @param {Node|Node[]} opts.body
 * @param {Node[]} [opts.foot]
 * @param {boolean} [opts.dismissable=true]
 * @param {boolean} [opts.wide=false]
 * @param {string} [opts.klass]
 */
export function sheet({ title, body, foot, dismissable = true, wide = false, klass = '' }) {
  closeSheet();
  const card = h('section', {
    class: `sheet ${wide ? 'sheet-wide' : ''} ${klass}`,
    role: 'dialog', 'aria-modal': 'true',
    'aria-label': title || 'dialog',
  },
    title && h('header', { class: 'sheet-head' },
      h('h2', {}, title),
      dismissable && h('button', { class: 'close-x', 'aria-label': 'Close',
                                   onclick: () => { sfx.close(); closeSheet(); } }, '✕')),
    h('div', { class: 'sheet-body scroller' }, body),
    foot && foot.length ? h('footer', { class: 'sheet-foot' }, foot) : null,
  );

  const back = h('div', {
    class: 'backdrop',
    onclick: (e) => { if (dismissable && e.target === back) { sfx.close(); closeSheet(); } },
  }, card);

  clear($('#modal'));
  $('#modal').appendChild(back);
  open = back;
  sfx.open();

  if (dismissable) {
    onEsc = (e) => { if (e.key === 'Escape') { sfx.close(); closeSheet(); } };
    document.addEventListener('keydown', onEsc);
  }
  card.querySelector('button, [href], input, textarea')?.focus({ preventScroll: true });
  return { card, close: closeSheet };
}

export const isSheetOpen = () => Boolean(open);
