import { h } from '../lib/dom.js';
import { sfx } from '../lib/sound.js';

const root = document.getElementById('toasts');

export function toast(text, kind = 'info', icon = '') {
  const node = h('div', { class: `toast ${kind}` },
    icon ? h('span', { class: 'toast-icon' }, icon) : null,
    h('div', {}, h('b', {}, text)),
  );
  root.appendChild(node);
  if (kind === 'bad') sfx.warn();
  setTimeout(() => {
    node.classList.add('out');
    setTimeout(() => node.remove(), 260);
  }, kind === 'bad' ? 4200 : 3000);
  return node;
}

export function badgeToast(badge) {
  const node = h('div', { class: 'toast badge' },
    h('span', { class: 'toast-icon' }, badge.icon || '🏅'),
    h('div', {},
      h('b', {}, `Unlocked: ${badge.name}`),
      h('small', {}, badge.desc || ''),
    ),
  );
  root.appendChild(node);
  sfx.unlock();
  setTimeout(() => {
    node.classList.add('out');
    setTimeout(() => node.remove(), 280);
  }, 5200);
}
