// The run summary. The last thing a player sees, so it has to make them want
// to press the button again.
import { h, fmt, countUp, plural } from '../lib/dom.js';
import { sheet, closeSheet } from '../ui/sheet.js';
import { toast } from '../ui/toast.js';
import { sfx } from '../lib/sound.js';
import * as fx from '../lib/fx.js';

const MODE_NAME = {
  ascent: 'Ascent', blitz: 'Blitz', holdout: 'Holdout', daily: 'The Daily', endless: 'Endless',
};

function shareText(result, run) {
  const mode = MODE_NAME[result.stats?.mode] || 'SWAY';
  const head = result.stats?.daily
    ? `SWAY · The Daily · ${new Date().toISOString().slice(0, 10)}`
    : `SWAY · ${mode}`;
  const lines = [head, ...(result.share || [])];
  lines.push(`${fmt(result.score)} points · ${plural(result.stats?.cleared || 0, 'floor')}`);
  if (result.stats?.best_hit) lines.push(`biggest hit ${fmt(result.stats.best_hit)}`);
  lines.push(location.origin);
  return lines.join('\n');
}

export function resultSheet(result, { run, onAgain, onHome } = {}) {
  const win = result.outcome === 'win';
  const stats = result.stats || {};
  const scoreNum = h('b', {}, '0');

  const statCell = (value, label) => h('div', { class: 'stat-cell' },
    h('b', {}, value), h('span', {}, label));

  const body = h('div', { class: 'result' },
    h('div', { class: `verdict ${result.outcome}` }, win ? 'run complete' : 'run over'),
    h('h2', {}, result.headline),
    h('p', { class: 'detail' }, result.detail),

    h('div', { class: 'score-slab' },
      scoreNum,
      h('span', {}, 'points'),
      h('span', { class: 'xp' }, `+${fmt(result.xp || 0)} xp`)),

    result.share?.length
      ? h('div', { class: 'share-grid' },
          ...result.share.map((row) => h('span', { class: 'share-row' }, row)))
      : null,

    h('div', { class: 'result-stats' },
      statCell(fmt(stats.cleared || 0), 'floors'),
      statCell(fmt(stats.best_hit || 0), 'best hit'),
      statCell(fmt(stats.crits || 0), 'masterful'),
      statCell(fmt(stats.fumbles || 0), 'fumbled'),
      stats.relics ? statCell(fmt(stats.relics), 'relics') : null,
      stats.words ? statCell(fmt(stats.words), 'words') : null),

    result.levelled && result.level
      ? h('div', { class: 'level-up' },
          '✦ Level ', String(result.level.level), ' — ', result.level.title)
      : null,

    result.unlocked?.length
      ? h('div', { class: 'unlock-strip' },
          ...result.unlocked.map((u) => h('span', { class: 'unlock-pill' },
            u.glyph || '✦', ' ', u.name, ' unlocked')))
      : null,

    result.badges?.length
      ? h('div', { class: 'unlock-strip' }, ...result.badges.map((b) =>
          h('span', { class: 'unlock-pill' }, b.glyph, ' ', b.name)))
      : null,

    (result.reveal?.history || []).length
      ? h('div', { class: 'floor-list' }, ...result.reveal.history.map((row) => h('div', {
          class: `floor-row ${row.outcome}`,
        },
          h('span', { class: 'n' }, String(row.floor)),
          h('span', { class: 's' }, row.sigil || '·'),
          h('span', { class: 'nm' }, row.mind || '—'),
          h('span', { class: 'dim', style: { fontSize: '11px' } },
            `${row.turns}/${row.of}`),
          h('span', { class: 'p' }, row.outcome === 'win' ? `+${fmt(row.points)}` : 'stopped'))))
      : null,
  );

  const copy = async () => {
    const text = shareText(result, run);
    try {
      await navigator.clipboard.writeText(text);
      toast('Share card copied.', 'good', '⧉');
    } catch {
      // Clipboard is blocked without a secure context or a user gesture chain.
      sheet({ title: 'Share card', body: h('pre', {
        style: { whiteSpace: 'pre-wrap', fontSize: '13px', lineHeight: '1.7' },
      }, text) });
    }
    sfx.click();
  };

  sheet({
    dismissable: false,
    body,
    foot: [
      h('button', { class: 'btn btn-primary', onclick: () => { closeSheet(); onAgain?.(); } },
        win ? 'Run it again' : 'Try again'),
      h('button', { class: 'btn btn-ghost', onclick: copy }, '⧉ Share'),
      h('button', { class: 'btn btn-ghost', onclick: () => { closeSheet(); onHome?.(); } },
        'Home'),
    ],
  });

  countUp(scoreNum, result.score || 0, 1100);
  if (win) { sfx.clear(); fx.confetti(160); }
  else sfx.fail();
}
