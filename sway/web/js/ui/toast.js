import { h, $ } from '../lib/dom.js';
import { sfx } from '../lib/sound.js';

const host = () => $('#toasts');

export function toast(text, kind = 'info', icon = '') {
  const el = h('div', { class: `toast ${kind}` },
    icon && h('span', { class: 'toast-icon' }, icon),
    h('span', {}, text));
  host().appendChild(el);
  const kill = () => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 280);
  };
  setTimeout(kill, kind === 'bad' ? 5200 : 3400);
  if (kind === 'bad') sfx.warn();
  return el;
}

export function badgeToast(badge) {
  const el = h('div', { class: 'toast badge-toast' },
    h('div', { class: 'row' },
      h('span', { class: 'glyph' }, badge.glyph || '✦'),
      h('b', {}, badge.name)),
    h('span', {}, badge.desc));
  host().appendChild(el);
  sfx.badge();
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 280);
  }, 5200);
}
