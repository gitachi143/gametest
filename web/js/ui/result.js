import { h, countUp, fmt } from '../lib/dom.js';
import { openSheet, sheetHead, closeSheet } from './sheet.js';
import { toast } from './toast.js';
import { confetti, flash } from '../lib/fx.js';
import { sfx } from '../lib/sound.js';

function statRow(label, value) {
  return h('div', { class: 'stat-row' }, h('b', {}, String(value)), h('span', {}, label));
}

const STAT_LABELS = {
  floor: 'top floor', cleared: 'cleared', turns: 'turns', blocked: 'blocked', redacted: 'redacted',
  questions: 'questions', wrong: 'wrong guesses', solved: 'cards', best_combo: 'best combo',
  burned: 'burned', skipped: 'skipped', unused: 'spare qs', total: 'bench score',
  votes_against: 'votes on you', lives_lost: 'lives lost', rounds: 'rounds', seconds: 'seconds',
  multiplier: 'multiplier',
};

function shareText(game, result, daily) {
  const lines = [`NEXUS ARCADE · ${game.codename}${daily ? ' · DAILY' : ''}`];
  lines.push(`${result.headline} — ${fmt(result.score)} pts`);
  for (const row of result.share || []) lines.push(row);
  lines.push('');
  lines.push(location.origin);
  return lines.join('\n');
}

export function resultSheet(result, game, { daily, onAgain, onHome, onNext } = {}) {
  const won = result.outcome === 'win';
  if (won) { confetti(150); flash('good'); sfx.win(); } else { flash('bad'); sfx.lose(); }

  const scoreEl = h('div', { class: 'score-big' }, '0');
  const stats = Object.entries(result.stats || {})
    .filter(([k, v]) => STAT_LABELS[k] && typeof v !== 'object' && v !== false && v !== '')
    .slice(0, 6)
    .map(([k, v]) => statRow(STAT_LABELS[k], typeof v === 'boolean' ? (v ? 'yes' : 'no') : v));

  const reveal = Object.entries(result.reveal || {})
    .filter(([, v]) => typeof v === 'string' || typeof v === 'number')
    .map(([k, v]) => h('div', { style: { marginTop: '6px' } },
      h('div', { class: 'k' }, k.replace(/_/g, ' ')), h('div', {}, String(v))));

  const share = shareText(game, result, daily);

  const sheet = openSheet([
    sheetHead(daily ? `${game.codename} · daily` : game.codename),
    h('div', { class: 'result' },
      h('div', { class: `result-outcome ${won ? 'win' : 'loss'}` }, won ? '✦ success' : '✦ run over'),
      h('h1', {}, result.headline || (won ? 'You win' : 'You lose')),
      result.detail ? h('p', { class: 'detail' }, result.detail) : null,
      scoreEl,
      h('div', { class: 'score-label' }, `points  ·  +${fmt(result.xp || 0)} xp`),
      stats.length ? h('div', { class: 'stat-rows' }, stats) : null,
      (result.share || []).length ? h('div', { class: 'share-card' }, (result.share || []).join('\n')) : null,
      reveal.length ? h('div', { class: 'reveal-box' }, reveal) : null,
      (result.badges || []).length
        ? h('div', { class: 'badge-row' }, (result.badges || []).map((b, i) =>
            h('div', { class: 'badge', style: { animationDelay: `${180 + i * 130}ms` } },
              h('span', {}, b.icon), h('span', {}, b.name))))
        : null,
      h('div', { class: 'sheet-actions' },
        onNext ? h('button', { class: 'btn btn-primary', onclick: () => { closeSheet(); onNext(); } }, 'Next floor →') : null,
        onAgain ? h('button', { class: onNext ? 'btn' : 'btn btn-primary', onclick: () => { closeSheet(); onAgain(); } }, 'Run it again') : null,
        h('button', {
          class: 'btn btn-ghost',
          onclick: async (e) => {
            try {
              await navigator.clipboard.writeText(share);
              toast('Result copied to clipboard.', 'good', '📋');
              e.currentTarget.textContent = 'Copied ✓';
            } catch {
              toast('Clipboard blocked - select the card above.', 'warn', '📋');
            }
          },
        }, 'Share'),
        h('button', { class: 'btn btn-ghost', onclick: () => { closeSheet(); onHome && onHome(); } }, 'Arcade'),
      ),
    ),
  ], { dismissable: false });

  setTimeout(() => countUp(scoreEl, result.score || 0, 1100), 220);
  return sheet;
}
