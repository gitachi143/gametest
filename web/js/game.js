/**
 * The game view controller.
 *
 * Owns the chat transcript, the SSE turn loop, and the effect layer. Everything
 * game-specific lives in js/games/views.js - this file only knows about events.
 */
import { h, mount, clear, tpl, $, fmt } from './lib/dom.js';
import { api, streamAction } from './lib/api.js';
import { VIEWS } from './games/views.js';
import { toast, badgeToast } from './ui/toast.js';
import { resultSheet } from './ui/result.js';
import { howToSheet } from './ui/panels.js';
import { sfx } from './lib/sound.js';
import * as fx from './lib/fx.js';

const AVATARS = { player: '🧑', system: '◈', you: '🧑' };

export class GameView {
  constructor(view, { meta, sessionId, pub, daily }, hooks = {}) {
    this.view = view;
    this.meta = meta;
    this.sid = sessionId;
    this.pub = pub;
    this.daily = daily;
    this.hooks = hooks;
    this.streaming = new Map();
    this.timers = [];
    this._busy = false;
    this.logCount = 0;
    this.abort = new AbortController();
    this.ctx = {
      send: (action) => this.act(action),
      busy: () => this._busy,
      meta,
      registerTimer: (id) => this.timers.push(id),
    };
    this.render();
  }

  destroy() {
    this.clearTimers();
    this.abort.abort();
  }

  clearTimers() {
    this.timers.forEach(clearInterval);
    this.timers = [];
  }

  /* ------------------------------------------------------------- shell */
  render() {
    const node = tpl('tpl-game');
    mount(this.view, node);
    const root = document.documentElement;
    root.style.setProperty('--accent', this.meta.accent);
    root.style.setProperty('--accent-2', this.meta.accent2);

    $('#game-icon', node).textContent = this.meta.icon;
    $('#game-title', node).textContent = this.meta.codename;
    $('#game-mode', node).textContent =
      (this.daily ? 'daily · ' : '') + (this.pub.mode || this.meta.modes?.[0]?.id || '');
    $('#game-back', node).onclick = () => this.hooks.onHome && this.hooks.onHome();
    $('#game-help', node).onclick = () => howToSheet(this.meta);

    this.chat = $('#chat', node);
    this.hud = $('#hud', node);
    this.composerSlot = $('#composer', node);
    this.statStrip = $('#stat-strip', node);

    this.renderLog(this.collectLog(this.pub), true);
    this.refresh();
    this.focusInput();
  }

  view_() { return this.view; }

  collectLog(pub) {
    const entries = [...(pub.log || [])];
    if (pub.sub && pub.sub.log) entries.push(...pub.sub.log);
    return entries.sort((a, b) => (a.t || 0) - (b.t || 0));
  }

  viewFor(pub) {
    return VIEWS[this.meta.hud] || VIEWS[pub.sub_hud] || VIEWS.generic;
  }

  refresh() {
    this.clearTimers();
    const pub = this.pub;
    const v = this.viewFor(pub);

    mount(this.statStrip, (v.stats(pub, this.ctx) || []).map((s) =>
      h('div', { class: `stat ${s.tone || ''}` }, h('b', {}, String(s.value)), h('span', {}, s.label))));

    mount(this.hud, (v.hud(pub, this.ctx) || []).filter(Boolean));

    // Rebuild the composer (it is a function of state) while preserving whatever
    // the player was in the middle of typing.
    const old = this.composerSlot.querySelector('textarea, input[type="text"]');
    const carry = old ? { value: old.value, focus: document.activeElement === old } : null;
    const composer = v.composer(pub, this.ctx);
    const slot = composer.classList?.contains('composer') ? composer : h('div', { class: 'composer' }, composer);
    this.composerSlot.replaceWith(slot);
    this.composerSlot = slot;
    if (carry) {
      const next = slot.querySelector('textarea, input[type="text"]');
      if (next && carry.value) {
        next.value = carry.value;
        next.dispatchEvent(new Event('input'));
      }
      if (next && carry.focus) next.focus({ preventScroll: true });
    }
    this.setBusy(this._busy);
  }

  focusInput() {
    if (window.matchMedia('(max-width: 720px)').matches) return; // keep the mobile keyboard down
    const input = this.composerSlot?.querySelector('textarea, input[type="text"]');
    if (input) input.focus({ preventScroll: true });
  }

  setBusy(busy) {
    this._busy = busy;
    this.composerSlot?.classList.toggle('locked', busy);
  }

  /* -------------------------------------------------------------- chat */
  bubble(entry) {
    const isPlayer = entry.actor === 'player' || entry.actor === 'you';
    const tone = entry.tone || (isPlayer ? '' : 'ai');
    const body = h('div', { class: 'bubble' }, entry.text || '');
    const node = h('div', { class: `msg ${isPlayer ? 'player' : ''} tone-${tone}` },
      h('div', { class: 'msg-av' }, entry.avatar || AVATARS[entry.actor] || '💬'),
      h('div', { class: 'msg-body' },
        entry.name || (!isPlayer && entry.actor !== 'system')
          ? h('div', { class: 'msg-name' }, entry.name || entry.actor) : null,
        body),
    );
    node._body = body;
    return node;
  }

  renderLog(entries, replace = false) {
    if (replace) clear(this.chat);
    for (const entry of entries) this.chat.appendChild(this.bubble(entry));
    this.logCount = entries.length;
    this.scroll(true);
  }

  scroll(instant = false) {
    requestAnimationFrame(() => {
      this.chat.scrollTo({ top: this.chat.scrollHeight, behavior: instant ? 'auto' : 'smooth' });
    });
  }

  thinking(label) {
    this.clearThinking();
    this._thinking = h('div', { class: 'thinking' },
      h('span', {}, label || 'thinking'),
      h('span', { class: 'dots' }, h('i'), h('i'), h('i')));
    this.chat.appendChild(this._thinking);
    this.scroll();
  }

  clearThinking() {
    if (this._thinking) { this._thinking.remove(); this._thinking = null; }
  }

  /* -------------------------------------------------------------- turn */
  async act(action) {
    if (this._busy) return;
    this.setBusy(true);
    this.clearThinking();
    let sawError = false;
    try {
      await streamAction(this.sid, action, (name, data) => this.onEvent(name, data), this.abort.signal);
    } catch (err) {
      if (err.name !== 'AbortError') {
        sawError = true;
        toast(err.message || 'Connection lost.', 'bad', '⚠️');
      }
    } finally {
      this.clearThinking();
      this.setBusy(false);
      if (sawError) this.refresh();
      this.focusInput();
    }
  }

  onEvent(name, data) {
    switch (name) {
      case 'msg':
        this.clearThinking();
        this.chat.appendChild(this.bubble(data));
        if (data.actor !== 'player' && data.actor !== 'you') sfx.receive();
        this.scroll();
        break;

      case 'msg_start': {
        this.clearThinking();
        const node = this.bubble({ ...data, text: '' });
        node._body.classList.add('streaming');
        this.chat.appendChild(node);
        this.streaming.set(data.id, node);
        this.scroll();
        break;
      }
      case 'chunk': {
        const node = this.streaming.get(data.id);
        if (!node) break;
        node._body.textContent += data.text;
        if (Math.random() < 0.24) sfx.type();
        this.scroll(true);
        break;
      }
      case 'msg_end': {
        const node = this.streaming.get(data.id);
        if (node) {
          node._body.classList.remove('streaming');
          if (data.text) node._body.textContent = data.text;
          this.streaming.delete(data.id);
        }
        break;
      }

      case 'thinking':
        this.thinking(data.label);
        break;

      case 'state':
        this.pub = data;
        this.logCount = this.collectLog(data).length;
        this.refresh();
        break;

      case 'score':
        this.onScore(data);
        break;

      case 'fx':
        this.onFx(data);
        break;

      case 'toast':
        toast(data.text, data.kind || 'info', data.icon || '');
        break;

      case 'profile':
        this.hooks.onProfile && this.hooks.onProfile(data);
        break;

      case 'unlock':
        badgeToast(data);
        fx.sparks(20);
        break;

      case 'end':
        this.onEnd(data.result);
        break;

      case 'error':
        this.clearThinking();
        toast(data.message, 'bad', '⚠️');
        break;

      default:
        break;
    }
  }

  onScore(data) {
    const rows = [...this.hud.querySelectorAll('.judge-row')];
    const row = rows.find((r) => r.textContent.includes(data.judge));
    if (row) {
      fx.scorePop(`${data.score}`, row);
      row.animate?.(
        [{ transform: 'scale(1)' }, { transform: 'scale(1.04)' }, { transform: 'scale(1)' }],
        { duration: 320, easing: 'ease-out' },
      );
    }
    if (data.score >= 8) sfx.solved(); else if (data.score <= 3) sfx.wrong(); else sfx.tick();
  }

  onFx(data) {
    const kind = data.kind;
    switch (kind) {
      case 'signal': sfx.signal(); fx.sparks(24); break;
      case 'blocked': sfx.wrong(); fx.shake(this.view); break;
      case 'redacted': sfx.burn(); fx.shake(this.view); fx.embers(30); break;
      case 'wrong': sfx.wrong(); fx.shake(this.view); break;
      case 'lockout': sfx.lose(); fx.flash('bad'); break;
      case 'floor_clear':
        sfx.solved(); fx.confetti(70); fx.flash('good');
        if (data.points) fx.scorePop(`+${fmt(data.points)}`, this.statStrip);
        break;
      case 'burn': sfx.burn(); fx.embers(40); fx.shake(this.view); break;
      case 'solved':
        (data.combo > 1 ? sfx.combo(data.combo) : sfx.solved());
        fx.sparks(28);
        if (data.points) fx.scorePop(`+${data.points}`, this.statStrip);
        break;
      case 'skip': sfx.click(); break;
      case 'miss': sfx.receive(); break;
      case 'timeup': sfx.warn(); break;
      case 'tick': sfx.tick(); break;
      case 'hint': sfx.click(); break;
      case 'sweep': sfx.win(); fx.confetti(90); break;
      case 'round': case 'phase': case 'next_round': sfx.open(); break;
      case 'round_clear':
        sfx.combo(3); fx.confetti(60);
        if (data.points) fx.scorePop(`+${fmt(data.points)}`, this.statStrip);
        break;
      case 'life_lost': sfx.heartbeat(); fx.flash('bad'); fx.shake(this.view); break;
      case 'out_of_questions': sfx.warn(); break;
      case 'win': case 'lose': break; // the result sheet handles these
      default: break;
    }
  }

  onEnd(result) {
    this.clearThinking();
    this.clearTimers();
    const isVaultRun = this.meta.id === 'vault';
    resultSheet(result, this.meta, {
      daily: this.daily,
      onAgain: () => this.hooks.onReplay && this.hooks.onReplay(this.pub.mode),
      onHome: () => this.hooks.onHome && this.hooks.onHome(),
      onNext: isVaultRun && result.stats?.floor >= 1 && result.stats?.floor < 8 && !this.daily
        ? () => this.hooks.onReplay && this.hooks.onReplay('floor', { floor: (result.stats.floor || 0) + 1 })
        : null,
    });
  }
}

export async function loadSession(view, sid, hooks) {
  const data = await api.session(sid);
  return new GameView(view, {
    meta: data.game, sessionId: data.session_id, pub: data.public, daily: data.daily,
  }, hooks);
}
