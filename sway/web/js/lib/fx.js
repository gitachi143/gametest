// Canvas particles, screen shake, full-screen flashes and floating numbers.
import { reduced } from './dom.js';

const canvas = document.getElementById('fx');
const ctx = canvas.getContext('2d');
let parts = [];
let running = false;

function resize() {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  canvas.style.width = `${window.innerWidth}px`;
  canvas.style.height = `${window.innerHeight}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
resize();
window.addEventListener('resize', resize);

function loop() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  parts = parts.filter((p) => p.life > 0);
  for (const p of parts) {
    p.vy += p.gravity;
    p.vx *= p.drag;
    p.vy *= p.drag;
    p.x += p.vx;
    p.y += p.vy;
    p.life -= 1;
    p.rot += p.spin;
    ctx.save();
    ctx.globalAlpha = Math.max(0, Math.min(1, p.life / p.fade));
    ctx.translate(p.x, p.y);
    ctx.rotate(p.rot);
    ctx.fillStyle = p.color;
    if (p.shape === 'circle') {
      ctx.beginPath();
      ctx.arc(0, 0, p.size / 2, 0, Math.PI * 2);
      ctx.fill();
    } else if (p.shape === 'strip') {
      ctx.fillRect(-p.size / 2, -p.size / 5, p.size, p.size * 0.4);
    } else {
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size);
    }
    ctx.restore();
  }
  if (parts.length) requestAnimationFrame(loop);
  else running = false;
}

function push(list) {
  if (reduced()) return;
  parts.push(...list);
  if (!running) { running = true; requestAnimationFrame(loop); }
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const GOLD = () => [cssVar('--gold-2', '#f7dc9c'), cssVar('--gold', '#e8b44a'), '#ffffff'];
const MIND = () => [cssVar('--mind', '#e8b44a'), cssVar('--gold', '#e8b44a'), '#ffffff'];

function centre(origin) {
  if (origin instanceof Element) {
    const r = origin.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  }
  return origin || { x: window.innerWidth / 2, y: window.innerHeight * 0.42 };
}

export function confetti(count = 120, origin = null, colors = null) {
  const c = colors || GOLD();
  const { x, y } = centre(origin);
  push(Array.from({ length: count }, () => {
    const a = Math.random() * Math.PI * 2;
    const s = 4 + Math.random() * 12;
    return {
      x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s - 4,
      gravity: 0.3, drag: 0.985, size: 5 + Math.random() * 7,
      color: c[(Math.random() * c.length) | 0],
      life: 80 + Math.random() * 70, fade: 105,
      rot: Math.random() * 6, spin: (Math.random() - 0.5) * 0.4,
      shape: Math.random() < 0.6 ? 'strip' : 'square',
    };
  }));
}

export function sparks(count = 26, origin = null, colors = null) {
  const c = colors || MIND();
  const { x, y } = centre(origin);
  push(Array.from({ length: count }, () => {
    const a = Math.random() * Math.PI * 2;
    const s = 3 + Math.random() * 8;
    return {
      x, y, vx: Math.cos(a) * s, vy: Math.sin(a) * s,
      gravity: 0.02, drag: 0.92, size: 2 + Math.random() * 3,
      color: c[(Math.random() * c.length) | 0],
      life: 24 + Math.random() * 20, fade: 32, rot: 0, spin: 0, shape: 'circle',
    };
  }));
}

export function embers(count = 40, origin = null) {
  const { x, y } = centre(origin);
  push(Array.from({ length: count }, () => ({
    x: x + (Math.random() - 0.5) * 140, y: y + (Math.random() - 0.5) * 46,
    vx: (Math.random() - 0.5) * 3, vy: -1.4 - Math.random() * 3.2,
    gravity: -0.03, drag: 0.982, size: 2 + Math.random() * 4,
    color: ['#e0533f', '#e8b44a', '#ff7a63'][(Math.random() * 3) | 0],
    life: 55 + Math.random() * 50, fade: 76, rot: 0, spin: 0.1, shape: 'circle',
  })));
}

/** Motes drifting up behind the whole page. Ambient, cheap, runs once. */
export function motes(count = 26) {
  if (reduced()) return;
  push(Array.from({ length: count }, () => ({
    x: Math.random() * window.innerWidth,
    y: window.innerHeight + Math.random() * 200,
    vx: (Math.random() - 0.5) * 0.25, vy: -0.25 - Math.random() * 0.4,
    gravity: 0, drag: 1, size: 1 + Math.random() * 2,
    color: 'rgba(232,180,74,0.5)',
    life: 700 + Math.random() * 500, fade: 900, rot: 0, spin: 0, shape: 'circle',
  })));
}

export function shake(el = document.getElementById('view'), hard = false) {
  if (!el || reduced()) return;
  el.classList.remove('shake');
  void el.offsetWidth;                       // force a reflow so it re-triggers
  el.style.setProperty('--shake', hard ? '1.8' : '1');
  el.classList.add('shake');
  setTimeout(() => {
    el.classList.remove('shake');
    el.style.removeProperty('--shake');
  }, 420);
}

export function flash(kind = 'gold') {
  if (reduced()) return;
  const div = document.createElement('div');
  div.className = `flash ${kind}`;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 560);
}

export function pop(text, target, color = null) {
  if (reduced() || !target) return;
  const { x, y } = centre(target);
  const el = document.createElement('div');
  el.className = 'pop';
  el.textContent = text;
  el.style.left = `${x}px`;
  el.style.top = `${y}px`;
  el.style.color = color || cssVar('--gold', '#e8b44a');
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1000);
}

/**
 * Brief bloom on a big hit. Sells impact better than any particle.
 * Deliberately an overlay rather than a `filter` on <body>: a filter would turn
 * body into a containing block and jolt every fixed layer on the page.
 */
export function hitstop(ms = 90) {
  if (reduced()) return;
  const el = document.createElement('div');
  el.className = 'hitstop';
  el.style.setProperty('--ms', `${ms * 3}ms`);
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms * 3 + 60);
}
