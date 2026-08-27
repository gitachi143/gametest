import { h, $ } from '../lib/dom.js';
import { sfx } from '../lib/sound.js';

const root = document.getElementById('modal-root');
let onEsc = null;

export function closeSheet() {
  const back = $('.backdrop', root);
  if (!back) return;
  sfx.close();
  back.style.animation = 'fade 140ms var(--ease) reverse forwards';
  setTimeout(() => back.remove(), 140);
  if (onEsc) { document.removeEventListener('keydown', onEsc); onEsc = null; }
}

export function openSheet(content, { dismissable = true, wide = false } = {}) {
  closeSheet();
  const sheet = h('div', { class: 'sheet', style: wide ? { width: 'min(720px, 96vw)' } : null,
                           role: 'dialog', 'aria-modal': 'true' }, content);
  const back = h('div', {
    class: 'backdrop',
    onclick: (e) => { if (dismissable && e.target === back) closeSheet(); },
  }, sheet);
  root.appendChild(back);
  sfx.open();
  if (dismissable) {
    onEsc = (e) => { if (e.key === 'Escape') closeSheet(); };
    document.addEventListener('keydown', onEsc);
  }
  const focusable = sheet.querySelector('button, input, [tabindex]');
  if (focusable) focusable.focus({ preventScroll: true });
  return sheet;
}

export function sheetHead(title, extra = null) {
  return h('div', { class: 'sheet-head' },
    h('h2', {}, title),
    h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
      extra,
      h('button', { class: 'btn-icon-only', 'aria-label': 'Close', onclick: closeSheet }, '✕'),
    ),
  );
}
