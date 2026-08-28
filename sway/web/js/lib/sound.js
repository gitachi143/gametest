// Synthesised sound. No audio files: every cue is a handful of oscillators, so
// the whole soundtrack costs nothing to download and never desyncs.
const KEY = 'sway.sound';
let ctx = null;
let enabled = true;
let master = null;
try { enabled = localStorage.getItem(KEY) !== 'off'; } catch { /* ignore */ }

function ac() {
  if (!ctx) {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return null;
    ctx = new C();
    master = ctx.createGain();
    master.gain.value = 0.85;
    master.connect(ctx.destination);
  }
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

export function isOn() { return enabled; }
export function toggle() {
  enabled = !enabled;
  try { localStorage.setItem(KEY, enabled ? 'on' : 'off'); } catch { /* ignore */ }
  if (enabled) tone({ freq: 700, dur: 0.07, type: 'triangle', gain: 0.05 });
  return enabled;
}
// Browsers require a gesture before audio starts; called from the first click.
export function unlock() { if (enabled) ac(); }

function tone({ freq = 440, dur = 0.12, type = 'sine', gain = 0.06, slide = 0, delay = 0,
                attack = 0.006, detune = 0 }) {
  const c = ac();
  if (!c || !enabled) return;
  const t0 = c.currentTime + delay;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.detune.value = detune;
  osc.frequency.setValueAtTime(freq, t0);
  if (slide) osc.frequency.exponentialRampToValueAtTime(Math.max(30, freq + slide), t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g).connect(master);
  osc.start(t0);
  osc.stop(t0 + dur + 0.03);
}

function noise({ dur = 0.18, gain = 0.06, delay = 0, band = 900, q = 1 }) {
  const c = ac();
  if (!c || !enabled) return;
  const frames = Math.max(1, Math.floor(c.sampleRate * dur));
  const buf = c.createBuffer(1, frames, c.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < frames; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
  const src = c.createBufferSource();
  src.buffer = buf;
  const filter = c.createBiquadFilter();
  filter.type = 'bandpass';
  filter.frequency.value = band;
  filter.Q.value = q;
  const g = c.createGain();
  g.gain.value = gain;
  src.connect(filter).connect(g).connect(master);
  src.start(c.currentTime + delay);
}

const chord = (freqs, opts = {}) =>
  freqs.forEach((f, i) => tone({ freq: f, dur: 0.3, type: 'triangle', gain: 0.05,
                                 delay: i * 0.07, ...opts }));

export const sfx = {
  hover: () => tone({ freq: 1100, dur: 0.02, gain: 0.008 }),
  click: () => tone({ freq: 380, dur: 0.04, type: 'square', gain: 0.028 }),
  back:  () => tone({ freq: 300, dur: 0.09, type: 'sine', gain: 0.03, slide: -110 }),

  // cards
  deal:  (i = 0) => noise({ dur: 0.07, gain: 0.028, band: 2600, delay: i * 0.05, q: 0.7 }),
  pick:  (n = 1) => tone({ freq: 520 + n * 130, dur: 0.06, type: 'triangle', gain: 0.045,
                           slide: 130 }),
  drop:  () => tone({ freq: 420, dur: 0.06, type: 'triangle', gain: 0.03, slide: -140 }),
  send:  () => { tone({ freq: 480, dur: 0.09, type: 'triangle', gain: 0.05, slide: 300 });
                 noise({ dur: 0.1, gain: 0.02, band: 1800 }); },
  type:  () => tone({ freq: 1300 + Math.random() * 500, dur: 0.01, type: 'square', gain: 0.005 }),
  speak: () => tone({ freq: 260, dur: 0.08, type: 'sine', gain: 0.032, slide: 90 }),

  // the chain
  step:  (i = 0) => tone({ freq: 380 * Math.pow(1.09, Math.min(14, i)), dur: 0.05,
                           type: 'triangle', gain: 0.04 }),
  critStep: (i = 0) => { tone({ freq: 760 * Math.pow(1.06, Math.min(10, i)), dur: 0.09,
                                type: 'square', gain: 0.035 });
                         tone({ freq: 1140, dur: 0.07, type: 'triangle', gain: 0.02,
                                delay: 0.03 }); },
  badStep: () => tone({ freq: 190, dur: 0.11, type: 'sawtooth', gain: 0.035, slide: -60 }),
  slam:  (big = false) => { tone({ freq: big ? 90 : 130, dur: 0.34, type: 'sine', gain: 0.11,
                                   slide: -40 });
                            noise({ dur: 0.2, gain: big ? 0.06 : 0.035, band: 500, q: 0.6 });
                            if (big) chord([392, 523, 659], { gain: 0.045, dur: 0.4 }); },
  whiff: () => { noise({ dur: 0.2, gain: 0.03, band: 380 });
                 tone({ freq: 150, dur: 0.2, type: 'sawtooth', gain: 0.035, slide: -50 }); },

  // outcomes
  reveal: () => { tone({ freq: 900, dur: 0.07, type: 'triangle', gain: 0.035 });
                  tone({ freq: 1350, dur: 0.1, type: 'triangle', gain: 0.03, delay: 0.06 }); },
  clear: () => chord([523, 659, 784, 1046], { gain: 0.055, dur: 0.36 }),
  fail:  () => chord([330, 262, 196], { gain: 0.05, dur: 0.4, type: 'sine' }),
  break: () => { noise({ dur: 0.45, gain: 0.07, band: 900, q: 0.5 });
                 chord([440, 587, 880], { gain: 0.05, dur: 0.5, delay: 0.05 }); },
  badge: () => chord([784, 988, 1319, 1568], { gain: 0.05, dur: 0.28, type: 'sine' }),
  levelUp: () => chord([523, 659, 784, 1046, 1318], { gain: 0.05, dur: 0.3 }),
  open:  () => tone({ freq: 240, dur: 0.18, type: 'sine', gain: 0.035, slide: 340 }),
  close: () => tone({ freq: 520, dur: 0.13, type: 'sine', gain: 0.028, slide: -260 }),
  tick:  () => tone({ freq: 920, dur: 0.025, type: 'square', gain: 0.018 }),
  warn:  () => { tone({ freq: 420, dur: 0.11, type: 'sawtooth', gain: 0.04 });
                 tone({ freq: 300, dur: 0.15, type: 'sawtooth', gain: 0.033, delay: 0.1 }); },
  heartbeat: () => { tone({ freq: 88, dur: 0.16, type: 'sine', gain: 0.1 });
                     tone({ freq: 78, dur: 0.2, type: 'sine', gain: 0.08, delay: 0.22 }); },
};

/** Short haptic on touch devices, where sound is often muted. */
export function buzz(pattern = 12) {
  try { navigator.vibrate?.(pattern); } catch { /* unsupported */ }
}
