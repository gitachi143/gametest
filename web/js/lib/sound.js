// Synthesised sound effects. No audio assets: every cue is a few oscillators,
// which keeps the payload tiny and the arcade feeling snappy.
const KEY = 'nexus.sound';
let ctx = null;
let enabled = true;
try { enabled = localStorage.getItem(KEY) !== 'off'; } catch { /* ignore */ }

function ac() {
  if (!ctx) {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return null;
    ctx = new C();
  }
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

export function isOn() { return enabled; }
export function toggle() {
  enabled = !enabled;
  try { localStorage.setItem(KEY, enabled ? 'on' : 'off'); } catch { /* ignore */ }
  if (enabled) blip(660, 0.05, 'triangle', 0.05);
  return enabled;
}
// Browsers require a gesture before audio; call this from the first click.
export function unlock() { if (enabled) ac(); }

function tone({ freq = 440, dur = 0.12, type = 'sine', gain = 0.07, slide = 0, delay = 0, attack = 0.005 }) {
  const c = ac();
  if (!c || !enabled) return;
  const t0 = c.currentTime + delay;
  const osc = c.createOscillator();
  const g = c.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  if (slide) osc.frequency.exponentialRampToValueAtTime(Math.max(40, freq + slide), t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  osc.connect(g).connect(c.destination);
  osc.start(t0);
  osc.stop(t0 + dur + 0.02);
}

function noise({ dur = 0.18, gain = 0.06, delay = 0, band = 900 }) {
  const c = ac();
  if (!c || !enabled) return;
  const frames = Math.floor(c.sampleRate * dur);
  const buf = c.createBuffer(1, frames, c.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < frames; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
  const src = c.createBufferSource();
  src.buffer = buf;
  const filter = c.createBiquadFilter();
  filter.type = 'bandpass';
  filter.frequency.value = band;
  const g = c.createGain();
  g.gain.value = gain;
  src.connect(filter).connect(g).connect(c.destination);
  src.start(c.currentTime + delay);
}

export const blip = (freq = 520, dur = 0.06, type = 'square', gain = 0.045) => tone({ freq, dur, type, gain });

export const sfx = {
  click: () => tone({ freq: 340, dur: 0.045, type: 'square', gain: 0.03 }),
  hover: () => tone({ freq: 880, dur: 0.03, type: 'sine', gain: 0.012 }),
  send: () => { tone({ freq: 520, dur: 0.07, type: 'triangle', gain: 0.05, slide: 260 }); },
  receive: () => tone({ freq: 300, dur: 0.09, type: 'sine', gain: 0.04, slide: 120 }),
  type: () => tone({ freq: 1200 + Math.random() * 400, dur: 0.012, type: 'square', gain: 0.006 }),
  tick: () => tone({ freq: 900, dur: 0.03, type: 'square', gain: 0.02 }),
  warn: () => { tone({ freq: 420, dur: 0.1, type: 'sawtooth', gain: 0.04 }); tone({ freq: 300, dur: 0.14, type: 'sawtooth', gain: 0.035, delay: 0.09 }); },
  wrong: () => { tone({ freq: 220, dur: 0.16, type: 'sawtooth', gain: 0.05, slide: -80 }); noise({ dur: 0.12, gain: 0.03, band: 400 }); },
  burn: () => { noise({ dur: 0.3, gain: 0.07, band: 1600 }); tone({ freq: 180, dur: 0.24, type: 'sawtooth', gain: 0.05, slide: -60 }); },
  solved: () => { [660, 880].forEach((f, i) => tone({ freq: f, dur: 0.11, type: 'triangle', gain: 0.05, delay: i * 0.07 })); },
  combo: (n = 2) => { const base = 520 + Math.min(6, n) * 90; [0, 1, 2].forEach(i => tone({ freq: base + i * 120, dur: 0.09, type: 'triangle', gain: 0.045, delay: i * 0.05 })); },
  win: () => { [523, 659, 784, 1046].forEach((f, i) => tone({ freq: f, dur: 0.34, type: 'triangle', gain: 0.06, delay: i * 0.085 })); },
  lose: () => { [392, 330, 262].forEach((f, i) => tone({ freq: f, dur: 0.3, type: 'sine', gain: 0.055, delay: i * 0.12 })); },
  unlock: () => { [784, 988, 1319].forEach((f, i) => tone({ freq: f, dur: 0.26, type: 'sine', gain: 0.05, delay: i * 0.07 })); },
  open: () => tone({ freq: 240, dur: 0.16, type: 'sine', gain: 0.035, slide: 320 }),
  close: () => tone({ freq: 480, dur: 0.12, type: 'sine', gain: 0.03, slide: -220 }),
  signal: () => { [1200, 1500].forEach((f, i) => tone({ freq: f, dur: 0.08, type: 'square', gain: 0.03, delay: i * 0.1 })); },
  heartbeat: () => { tone({ freq: 90, dur: 0.16, type: 'sine', gain: 0.09 }); tone({ freq: 80, dur: 0.2, type: 'sine', gain: 0.07, delay: 0.2 }); },
};
