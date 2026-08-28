/**
 * The run view: one conversation at a time, plus the beats between them.
 *
 * It owns the transcript, the hand, the SSE turn loop and the effect layer.
 * Every mechanical number comes from the server; this file never scores
 * anything, it only plays back what arrived.
 */
import { h, $, mount, clear, fmt, autoGrow, setHue, isTouch, sleep } from '../lib/dom.js';
import { streamAction, api } from '../lib/api.js';
import { cardEl, relicEl, layoutFan, dealAnimation } from '../ui/card.js';
import { playChain } from '../ui/chain.js';
import { sheet, closeSheet } from '../ui/sheet.js';
import { toast, badgeToast } from '../ui/toast.js';
import { sfx, buzz } from '../lib/sound.js';
import * as fx from '../lib/fx.js';
import { resultSheet } from './result.js';

const SLOT_LABEL = { gate: 'GATE', boss: 'FINAL' };
const PLACEHOLDER = {
  sway: 'Say something that moves them…',
  holdout: 'Careful. Anything you say, they can use…',
};

export class RunView {
  constructor(host, { runId, run }, hooks = {}) {
    this.host = host;
    this.runId = runId;
    this.run = run;
    this.hooks = hooks;
    this.picked = [];
    this.streams = new Map();
    this.busy = false;
    this.abort = new AbortController();
    this.pendingChain = null;
    this.render();
    if (run.encounter) this.floorIntro(true);
  }

  destroy() {
    this.abort.abort();
    closeSheet();
  }

  get enc() { return this.run.encounter || {}; }

  /* ------------------------------------------------------------- shell */
  render() {
    setHue(this.enc.mind?.hue ?? 40);
    const view = h('section', { class: 'enc' },
      (this.head = h('header', { class: 'mind-head' })),
      h('div', { class: 'enc-main' },
        h('div', { class: 'enc-col' },
          (this.talk = h('div', { class: 'talk scroller', role: 'log', 'aria-live': 'polite' })),
          (this.dock = h('div', { class: 'dock' }))),
        // The side rail only exists on wide screens; CSS hides it otherwise.
        (this.side = h('aside', { class: 'enc-side scroller' }))),
    );
    mount(this.host, view);
    this.paintHead();
    this.paintSide();
    this.paintLog(true);
    this.paintDock(true);
  }

  /* -------------------------------------------------------------- head */
  paintHead() {
    const e = this.enc;
    const mind = e.mind || {};
    const guard = e.kind === 'holdout';
    const cur = guard ? e.resolve : e.resistance;
    const max = guard ? e.max_resolve : e.max_resistance;
    const pct = Math.max(0, Math.min(100, (cur / Math.max(1, max)) * 100));
    const left = Math.max(0, (e.turns || 0) - (e.turn || 0));

    this.barFill = h('i', { class: 'bar-fill', style: { width: `${pct}%` } });
    this.barChip = h('i', { class: 'bar-chip', style: { width: `${pct}%` } });
    this.bar = h('div', {
      class: `bar ${guard ? 'guard' : ''}`, role: 'progressbar',
      'aria-label': guard ? 'Your resolve' : 'Their resistance',
      'aria-valuenow': String(Math.round(cur)), 'aria-valuemax': String(max),
    }, this.barChip, this.barFill,
       h('div', { class: 'bar-ticks' }, ...Array.from({ length: 8 }, () => h('i'))));

    this.barNum = h('span', { class: 'num' }, `${fmt(Math.max(0, Math.round(cur)))} / ${fmt(max)}`);

    mount(this.head,
      h('div', { class: 'mind-row' },
        h('div', { class: `sigil ${mind.boss ? 'boss' : ''}` }, mind.sigil || '?'),
        h('div', { class: 'mind-id' },
          h('h2', {}, mind.name || ''),
          h('div', { class: 'who' }, mind.title || '')),
        h('div', { class: 'mind-gauges' },
          h('div', { class: `gauge momentum ${e.momentum > 1 ? 'hot' : ''}` },
            h('b', {}, `×${e.momentum || 0}`), h('span', {}, 'mntm')),
          h('div', { class: `gauge turns ${left <= 2 ? 'low' : ''}` },
            h('b', {}, String(left)), h('span', {}, 'left')))),
      h('div', { class: 'bar-wrap' },
        this.bar,
        h('div', { class: 'bar-legend' },
          h('span', { class: 'obj' },
            h('b', {}, guard ? 'HOLD: ' : 'GOAL: '),
            guard ? `never admit ${e.secret || 'it'}` : (e.objective || '')),
          this.barNum)),
      this.traitRow(),
    );
  }

  traitRow() {
    const e = this.enc;
    const traits = e.traits || [];
    return h('div', { class: 'traits' },
      ...traits.map((t) => (t.unknown
        ? h('span', { class: 'trait unknown', title: 'Unknown - land a tactic to find out' }, '? ? ?')
        : h('span', {
            class: `trait ${t.kind} ${t.active === false ? 'off' : ''}`,
            title: `${t.desc}${t.active === false ? ' (dormant right now)' : ''}`,
          }, t.name, t.mult ? h('em', {}, `×${t.mult}`) : null))),
      !e.sized_up && traits.some((t) => t.unknown)
        ? h('button', {
            class: 'btn btn-sm btn-ghost btn-read',
            onclick: () => this.act({ type: 'size-up' }),
          }, '◐ Read them')
        : null,
    );
  }

  /* -------------------------------------------------------------- side */
  paintSide() {
    const run = this.run;
    mount(this.side,
      h('div', { class: 'side-block' },
        h('h4', {}, run.endless ? 'Climb' : 'The floors'),
        run.endless
          ? h('div', { class: 'stat-cell' }, h('b', {}, `#${run.floor}`), h('span', {}, 'floor'))
          : h('div', { class: 'mini-map' }, ...(run.map || []).map((n) => h('div', {
              class: ['map-node', n.outcome === 'now' && 'now', n.outcome === 'win' && 'win',
                      n.outcome === 'loss' && 'loss', n.slot === 'gate' && 'gate',
                      n.slot === 'boss' && 'boss'].filter(Boolean).join(' '),
            },
              h('span', { class: 'n' }, String(n.floor)),
              h('span', { class: 's' }, n.sigil || '·'),
              h('span', { class: 'nm' }, n.name || SLOT_LABEL[n.slot] || '—'))))),
      h('div', { class: 'side-block' },
        h('h4', {}, 'Score'),
        h('div', { class: 'stat-cell' },
          h('b', { class: 'gold' }, fmt(run.score || 0)), h('span', {}, 'this run'))),
      (run.relics || []).length
        ? h('div', { class: 'side-block' },
            h('h4', {}, `Relics · ${run.relics.length}`),
            h('div', { class: 'relic-pips' }, ...run.relics.map((r) => h('button', {
              class: 'relic-pip', title: `${r.name} — ${r.text}`,
              onclick: () => sheet({ title: 'Relics', body: h('div', { class: 'relic-grid' },
                ...run.relics.map((x) => relicEl(x))) }),
            }, r.glyph))))
        : null,
      h('div', { class: 'side-block' },
        h('h4', {}, 'Deck'),
        h('button', { class: 'btn btn-sm btn-ghost btn-block', onclick: () => this.deckSheet() },
          `${(run.deck || []).length} cards`)),
    );
  }

  runSheet() {
    const run = this.run;
    sheet({
      title: `${run.mode_name} · floor ${run.floor}${run.endless ? '' : ` of ${run.floors}`}`,
      body: h('div', {},
        h('div', { class: 'stat-grid', style: { marginBottom: '18px' } },
          h('div', { class: 'stat-cell' },
            h('b', { class: 'gold' }, fmt(run.score || 0)), h('span', {}, 'score')),
          h('div', { class: 'stat-cell' },
            h('b', {}, String((run.deck || []).length)), h('span', {}, 'cards')),
          h('div', { class: 'stat-cell' },
            h('b', {}, String((run.relics || []).length)), h('span', {}, 'relics'))),
        (run.map || []).length
          ? h('div', {},
              h('h4', { class: 'tiny', style: { margin: '0 0 8px' } }, 'The floors'),
              h('div', { class: 'floor-list' }, ...run.map.map((n) => h('div', {
                class: `floor-row ${n.outcome === 'loss' ? 'loss' : ''}`,
              },
                h('span', { class: 'n' }, String(n.floor)),
                h('span', { class: 's' }, n.sigil || '·'),
                h('span', { class: 'nm' }, n.name || SLOT_LABEL[n.slot] || '—'),
                h('span', { class: 'dim', style: { fontSize: '11px' } },
                  n.outcome === 'now' ? 'here' : n.outcome || '')))))
          : null,
        (run.relics || []).length
          ? h('div', { style: { marginTop: '18px' } },
              h('h4', { class: 'tiny', style: { margin: '0 0 8px' } }, 'Relics'),
              h('div', { class: 'relic-grid' }, ...run.relics.map((r) => relicEl(r))))
          : null,
      ),
      foot: [h('button', { class: 'btn btn-ghost btn-block', onclick: () => this.deckSheet() },
        `Look at your deck (${(run.deck || []).length})`)],
    });
  }

  deckSheet() {
    const deck = [...(this.run.deck || [])].sort((a, b) =>
      (a.rarity || '').localeCompare(b.rarity || '') || a.name.localeCompare(b.name));
    sheet({
      title: `Your deck · ${deck.length}`, wide: true,
      body: h('div', { class: 'coll-grid' }, ...deck.map((c) => cardEl(c))),
    });
  }

  /* --------------------------------------------------------- transcript */
  lineEl(entry) {
    const mine = entry.who === 'player';
    const mid = ['scene', 'tell', 'system', 'phase'].includes(entry.who) ||
                ['scene', 'tell', 'phase', 'fail'].includes(entry.tone);
    const body = h('div', { class: 'bubble' }, entry.text || '');
    const el = h('div', {
      class: ['line', mine && 'me', mid && 'mid', entry.tone && entry.tone,
              entry.who === 'scene' && 'scene', entry.who === 'tell' && 'tell']
        .filter(Boolean).join(' '),
    },
      mid ? null : h('div', { class: 'line-av' }, mine ? 'YOU' : (entry.sigil || '·')),
      h('div', { class: 'line-body' },
        mid ? null : h('div', { class: 'line-name' }, mine ? 'you' : (entry.name || '')),
        body,
        entry.meta?.cards?.length
          ? h('div', { class: 'line-cards' }, ...entry.meta.cards.map((c) => h('span', {
              class: 'mini-card', dataset: { exec: String(c.exec ?? '') },
            }, h('b', {}, c.glyph), c.name)))
          : null),
    );
    el._body = body;
    el._entry = entry;
    return el;
  }

  paintLog(replace = false) {
    if (replace) clear(this.talk);
    for (const entry of this.enc.log || []) this.talk.appendChild(this.lineEl(entry));
    this.scroll(true);
  }

  scroll(instant = false) {
    requestAnimationFrame(() => {
      this.talk.scrollTo({ top: this.talk.scrollHeight, behavior: instant ? 'auto' : 'smooth' });
    });
  }

  thinking(label) {
    this.clearThinking();
    this._think = h('div', { class: 'thinking' },
      h('span', {}, label || 'thinking'),
      h('span', { class: 'dots' }, h('i'), h('i'), h('i')));
    this.talk.appendChild(this._think);
    this.scroll();
  }

  clearThinking() {
    this._think?.remove();
    this._think = null;
  }

  /**
   * Reveal a reply at a readable pace regardless of how it arrived.
   * The chain blocks the SSE reader while it plays, so chunks land in a burst;
   * without this the character would never appear to speak.
   */
  typewriter(el) {
    const CPF = 2.4;                       // characters per frame ≈ 140 wpm
    let carry = 0;
    const tick = () => {
      if (!el.isConnected) return;
      if (el._pending) {
        carry += CPF;
        const take = Math.max(1, Math.floor(carry));
        carry -= take;
        el._body.textContent += el._pending.slice(0, take);
        el._pending = el._pending.slice(take);
        if (Math.random() < 0.16) sfx.type();
        this.scroll(true);
      } else if (el._done) {
        el._body.classList.remove('streaming');
        return;
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  /* -------------------------------------------------------------- dock */
  paintDock(deal = false) {
    const e = this.enc;
    const cards = e.hand || [];
    const spent = this.spend();
    const focus = e.focus ?? 0;

    this.handEl = h('div', { class: `hand ${cards.length > 5 ? 'tight' : ''}` },
      ...cards.map((c, i) => cardEl(c, {
        slot: c.slot,
        cost: this.costOf(c, i),
        on: this.picked.includes(c.slot),
        order: this.picked.indexOf(c.slot) + 1,
        dud: !this.picked.includes(c.slot) && this.costOf(c, i) > focus - spent,
        onpick: () => this.toggle(c),
      })));

    const carry = this.input?.value || '';
    this.input = h('textarea', {
      rows: 1, maxlength: 900, placeholder: PLACEHOLDER[e.kind] || PLACEHOLDER.sway,
      'aria-label': 'Your message',
      oninput: () => this.paintHint(),
      onkeydown: (ev) => this.onKey(ev),
    });
    this.input.value = carry;

    this.hintRow = h('div', { class: 'hint-row' });
    this.composer = h('div', { class: `composer ${this.busy ? 'locked' : ''}` },
      this.input,
      h('div', { class: 'composer-side' },
        h('button', {
          class: 'btn btn-mind send', onclick: () => this.send(),
          disabled: this.busy,
        }, 'Send', h('span', { class: 'kbd' }, isTouch() ? '' : '⏎'))),
    );

    mount(this.dock,
      h('div', { class: 'dock-bar' },
        h('div', { class: 'focus' },
          h('span', { class: 'tiny' }, 'focus'),
          h('div', { class: 'focus-pips' },
            ...Array.from({ length: e.focus_max || 0 }, (_, i) => h('i', {
              class: i < focus - spent ? 'on' : (i < focus ? 'spent on' : ''),
            })))),
        h('div', { class: 'counts' },
          h('span', {}, '⛁ ', h('b', {}, String(e.counts?.draw ?? 0))),
          h('span', {}, '⛃ ', h('b', {}, String(e.counts?.discard ?? 0)))),
        h('span', { class: 'spacer' }),
        e.hide_tactics ? h('span', { class: 'tiny' }, '☻ masked') : null,
        h('button', { class: 'chip chip-icon run-chip', title: 'This run',
                      onclick: () => this.runSheet() }, '◈'),
        h('button', { class: 'chip chip-icon', title: 'How to play',
                      onclick: () => this.hooks.onHelp?.() }, '?'),
        h('button', { class: 'chip chip-icon', title: 'Give up on this run',
                      onclick: () => this.confirmQuit() }, '⏻')),
      cards.length ? this.handEl : null,
      this.hintRow,
      this.composer,
    );

    autoGrow(this.input);
    layoutFan(this.handEl);
    if (deal && cards.length) dealAnimation(this.handEl);
    this.paintHint();
    if (!isTouch() && !this.busy) this.input.focus({ preventScroll: true });
  }

  /** Cost the server will charge, mirroring the first-card-free relic. */
  costOf(card, indexInHand) {
    const order = this.picked.indexOf(card.slot);
    if (this.enc.first_card_free && (order === 0 || (order === -1 && !this.picked.length))) return 0;
    return card.cost ?? 0;
  }

  spend() {
    const hand = this.enc.hand || [];
    let total = 0;
    this.picked.forEach((slot, i) => {
      const card = hand.find((c) => c.slot === slot);
      if (!card) return;
      total += (this.enc.first_card_free && i === 0) ? 0 : (card.cost ?? 0);
    });
    return total;
  }

  paintHint() {
    const hand = this.enc.hand || [];
    const chosen = this.picked.map((s) => hand.find((c) => c.slot === s)).filter(Boolean);
    const words = this.input.value.trim() ? this.input.value.trim().split(/\s+/).length : 0;
    mount(this.hintRow,
      chosen.length
        ? h('span', {},
            h('span', { class: 'contract' }, 'you promised: '),
            chosen.map((c) => `${c.glyph} ${c.rule}`).join('   '))
        : h('span', {}, this.enc.kind === 'holdout'
            ? 'Short answers hold. Long ones leak.'
            : 'Pick up to three tactics, then write a message that actually does them.'),
      h('span', { class: 'spacer', style: { flex: 1 } }),
      h('span', { class: 'num' }, `${words} w`),
    );
  }

  toggle(card) {
    if (this.busy) return;
    const at = this.picked.indexOf(card.slot);
    if (at >= 0) {
      this.picked.splice(at, 1);
      sfx.drop();
    } else {
      if (this.picked.length >= 3) { toast('Three tactics is the most one message carries.', 'warn'); return; }
      const cost = (this.enc.first_card_free && !this.picked.length) ? 0 : (card.cost ?? 0);
      if (this.spend() + cost > (this.enc.focus ?? 0)) {
        toast('Not enough focus for that.', 'warn', '◆');
        sfx.warn();
        return;
      }
      this.picked.push(card.slot);
      sfx.pick(this.picked.length);
      buzz(8);
    }
    this.paintDock();
  }

  onKey(ev) {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); this.send(); return; }
    if (ev.key === 'Escape' && this.picked.length) {
      ev.preventDefault();
      this.picked = [];
      sfx.drop();
      this.paintDock();
    }
  }

  send() {
    if (this.busy) return;
    const text = this.input.value.trim();
    const silentOk = this.picked.some((s) =>
      ['silence', 'cold-shoulder'].includes((this.enc.hand || []).find((c) => c.slot === s)?.id));
    if (!text && !silentOk) { toast('Say something.', 'warn'); this.input.focus(); return; }
    const cards = [...this.picked];
    this.input.value = '';
    this.picked = [];
    sfx.send();
    [...this.handEl.querySelectorAll('.card.on')].forEach((el) => el.classList.add('spend'));
    this.act({ type: 'send', text, cards });
  }

  confirmQuit() {
    sheet({
      title: 'Walk out?',
      body: h('p', { class: 'dim' },
        'The run ends here and the score stands. Nobody is going to be impressed.'),
      foot: [
        h('button', { class: 'btn btn-ghost', onclick: () => closeSheet() }, 'Keep going'),
        h('button', { class: 'btn btn-danger', onclick: () => {
          closeSheet();
          this.act({ type: 'forfeit' });
        } }, 'Walk out'),
      ],
    });
  }

  /* -------------------------------------------------------------- turn */
  setBusy(busy) {
    this.busy = busy;
    this.composer?.classList.toggle('locked', busy);
    this.composer?.querySelector('.send')?.toggleAttribute('disabled', busy);
  }

  async act(action) {
    if (this.busy) return;
    this.setBusy(true);
    this.clearThinking();
    let failed = false;
    try {
      await streamAction(this.runId, action, (n, d) => this.onEvent(n, d), this.abort.signal);
    } catch (err) {
      if (err.name !== 'AbortError') {
        failed = true;
        toast(err.message || 'Connection lost.', 'bad', '⚠');
      }
    } finally {
      this.clearThinking();
      this.setBusy(false);
      if (failed) this.paintDock();
    }
  }

  async onEvent(name, data) {
    switch (name) {
      case 'line':
        this.clearThinking();
        this.talk.appendChild(this.lineEl(data));
        this.scroll();
        break;

      case 'thinking':
        this.thinking(data.label);
        break;

      case 'say_start': {
        this.clearThinking();
        const el = this.lineEl({ ...data, text: '', name: data.name, sigil: data.sigil });
        el._body.classList.add('streaming');
        el._pending = '';
        el._done = false;
        this.talk.appendChild(el);
        this.streams.set(data.id, el);
        sfx.speak();
        this.scroll();
        this.typewriter(el);
        break;
      }
      case 'chunk': {
        const el = this.streams.get(data.id);
        if (el) el._pending += data.text;
        break;
      }
      case 'say_end': {
        const el = this.streams.get(data.id);
        if (el) {
          if (data.text) el._pending = data.text.slice(el._body.textContent.length);
          el._done = true;
          this.streams.delete(data.id);
        }
        break;
      }

      case 'resolve':
        await this.showResolve(data);
        break;

      case 'reveal':
        sfx.reveal();
        fx.sparks(16, this.head);
        toast(`${data.name} — ${data.desc}`, 'good', data.kind === 'vuln' ? '◉' : '▮');
        break;

      case 'state':                       // encounter-level: same conversation
        this.run.encounter = data;
        this.paintHead();
        this.paintDock();
        break;

      case 'run':                           // run-level: floors, deck, relics
        this.run = data;
        if (this.awaitingFloor && data.encounter) {
          this.awaitingFloor = false;
          this.picked = [];
          closeSheet();
          this.render();
          this.floorIntro();
        } else {
          this.paintSide();
        }
        break;

      case 'fx':
        this.onFx(data);
        break;

      case 'toast':
        toast(data.text, data.kind || 'info', data.icon || '');
        break;

      case 'floor':
        // The run event that follows carries the new encounter; rebuild then.
        this.awaitingFloor = true;
        break;

      case 'reward':
        await this.showReward(data);
        break;

      case 'revive':
        toast('Second Wind. You are still in this.', 'good', '❋');
        fx.flash('good');
        break;

      case 'took':
        break;

      case 'profile':
        this.hooks.onProfile?.(data);
        break;

      case 'badge':
        badgeToast(data);
        fx.confetti(40, this.head);
        break;

      case 'end':
        await this.showEnd(data);
        break;

      case 'error':
        this.clearThinking();
        toast(data.message, 'bad', '⚠');
        break;

      default:
        break;
    }
  }

  /** Play the chain, then drain the bar by the amount it reported. */
  async showResolve(payload) {
    this.clearThinking();
    const guard = payload.mode === 'guard';
    await playChain(payload);

    // Mark the played cards on the player's own line with their grades.
    const lastMine = [...this.talk.querySelectorAll('.line.me')].pop();
    if (lastMine) {
      const chips = [...lastMine.querySelectorAll('.mini-card')];
      (payload.cards || []).forEach((c, i) => {
        if (chips[i]) chips[i].dataset.exec = String(c.exec ?? 0);
      });
    }

    const max = payload.max || 1;
    const after = Math.max(0, payload.after ?? 0);
    const pct = (after / max) * 100;
    this.barChip.style.width = `${((payload.before ?? after) / max) * 100}%`;
    requestAnimationFrame(() => { this.barChip.style.width = `${pct}%`; });
    this.barFill.style.width = `${pct}%`;
    this.barNum.textContent = `${fmt(Math.round(after))} / ${fmt(max)}`;
    this.bar.classList.remove('hit');
    void this.bar.offsetWidth;
    if ((payload.before ?? 0) > after) this.bar.classList.add('hit');
    if (guard && payload.incoming > 0) { sfx.heartbeat(); fx.shake(this.head); }
    if (payload.backlash) toast(`Backlash — they took ${payload.backlash} back.`, 'bad', '↯');
    await sleep(120);
  }

  onFx(data) {
    switch (data.kind) {
      case 'cleared': sfx.clear(); fx.flash('good'); fx.confetti(110); break;
      case 'failed': sfx.fail(); fx.flash('bad'); fx.shake(undefined, true); break;
      case 'break': sfx.break(); fx.confetti(90, this.head); break;
      case 'phase': sfx.warn(); fx.flash('gold'); fx.embers(34, this.head); break;
      case 'leaked': sfx.fail(); fx.flash('bad'); break;
      case 'broken': sfx.fail(); fx.embers(40); break;
      case 'reveal': sfx.reveal(); break;
      default: break;
    }
  }

  /* ------------------------------------------------------------- beats */
  floorIntro(first = false) {
    const e = this.enc;
    const mind = e.mind || {};
    const guard = e.kind === 'holdout';
    setHue(mind.hue ?? 40);
    const label = this.run.endless
      ? `Floor ${e.floor}`
      : `Floor ${e.floor} of ${this.run.floors}`;
    sheet({
      dismissable: false,
      klass: 'intro-sheet',
      body: h('div', { class: 'intro' },
        h('div', { class: 'floor-tag' }, label + (mind.boss ? ' · final' : '')),
        h('div', { class: `sigil sigil-lg ${mind.boss ? 'boss' : ''}` }, mind.sigil || '?'),
        h('h2', {}, mind.name || ''),
        h('div', { class: 'who' }, mind.title || ''),
        h('p', { class: 'scene' }, mind.scene || ''),
        guard
          ? h('div', { class: 'warn' }, '▮ Holdout — they are working on you')
          : null,
        h('div', { class: 'obj' },
          h('span', {}, guard ? 'hold the line' : 'your objective'),
          guard ? `Get through ${e.turns} exchanges without admitting ${e.secret}.`
                : e.objective),
      ),
      foot: [
        h('button', {
          class: 'btn btn-mind btn-lg btn-block',
          onclick: () => { closeSheet(); this.paintDock(true); },
        }, first ? 'Begin' : 'Go in'),
      ],
    });
  }

  async showReward(data) {
    this.run.score = data.score ?? this.run.score;
    const row = data.row || {};
    const offers = data.offers || [];
    const cleared = row.outcome === 'win';

    const pick = (index) => { closeSheet(); this.act({ type: 'take', index }); };
    const skip = () => { closeSheet(); this.act({ type: 'continue' }); };

    sheet({
      dismissable: false, wide: offers.length > 0,
      body: h('div', {},
        h('div', { class: 'reward-head' },
          h('div', { class: 'banner' }, cleared ? `Floor ${row.floor} cleared` : `Floor ${row.floor}`),
          h('div', { class: 'sub' },
            cleared
              ? [h('span', { class: 'points' }, `+${fmt(row.points || 0)}`), ' points · ',
                 `${row.turns}/${row.of} exchanges`, row.crits ? ` · ${row.crits} masterful` : '']
              : 'you survived it, which is not the same thing')),
        offers.length
          ? h('div', { class: 'offers' }, ...offers.map((o, i) => h('div', { class: 'offer new' },
              o.type === 'relic' ? relicEl(o) : cardEl(o),
              h('button', { class: 'btn btn-primary btn-sm', onclick: () => pick(i) }, 'Take'))))
          : h('p', { class: 'dim', style: { textAlign: 'center' } }, 'Straight on to the next one.'),
      ),
      foot: [
        offers.length && this.run.rerolls > 0
          ? h('button', { class: 'btn btn-ghost', onclick: () => this.act({ type: 'reroll' }) },
              `⚂ Reroll (${this.run.rerolls})`)
          : null,
        h('button', { class: 'btn btn-ghost', onclick: skip },
          offers.length ? 'Take nothing' : 'Continue'),
      ].filter(Boolean),
    });
    sfx.clear();
  }

  async showEnd(data) {
    this.run = data.run || this.run;
    resultSheet(data.result, {
      run: this.run,
      onAgain: () => this.hooks.onAgain?.(this.run.mode),
      onHome: () => this.hooks.onHome?.(),
    });
  }
}
