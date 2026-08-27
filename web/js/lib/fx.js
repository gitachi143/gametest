// Canvas particle effects, screen shake and floating score pops.
const canvas = document.getElementById('fx-canvas');
const ctx = canvas.getContext('2d');
let particles = [];
let running = false;
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function resize() {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = window.innerWidth * dpr;
  canvas.height = window.innerHeight * dpr;
  canvas.style.width = window.innerWidth + 'px';
  canvas.style.height = window.innerHeight + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
resize();
window.addEventListener('resize', resize);

function loop() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  particles = particles.filter((p) => p.life > 0);
  for (const p of particles) {
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
    } else {
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * (p.shape === 'strip' ? 0.42 : 1));
    }
    ctx.restore();
  }
  if (particles.length) requestAnimationFrame(loop);
  else running = false;
}

function push(list) {
  if (reduced) return;
  particles.push(...list);
  if (!running) { running = true; requestAnimationFrame(loop); }
}

function accent() {
  const s = getComputedStyle(document.documentElement);
  return [
    s.getPropertyValue('--accent').trim() || '#7c8cff',
    s.getPropertyValue('--accent-2').trim() || '#4cc9f0',
    '#ffffff',
    s.getPropertyValue('--gold').trim() || '#ffd166',
  ];
}

export function confetti(count = 130, origin = null) {
  const colors = accent();
  const cx = origin?.x ?? window.innerWidth / 2;
  const cy = origin?.y ?? window.innerHeight * 0.34;
  push(Array.from({ length: count }, () => {
    const angle = Math.random() * Math.PI * 2;
    const speed = 4 + Math.random() * 11;
    return {
      x: cx, y: cy,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 4,
      gravity: 0.28, drag: 0.985,
      size: 5 + Math.random() * 7,
      color: colors[(Math.random() * colors.length) | 0],
      life: 90 + Math.random() * 70, fade: 110,
      rot: Math.random() * 6, spin: (Math.random() - 0.5) * 0.35,
      shape: Math.random() < 0.55 ? 'strip' : 'square',
    };
  }));
}

export function embers(count = 44, origin = null) {
  const cx = origin?.x ?? window.innerWidth / 2;
  const cy = origin?.y ?? window.innerHeight * 0.5;
  push(Array.from({ length: count }, () => ({
    x: cx + (Math.random() - 0.5) * 120,
    y: cy + (Math.random() - 0.5) * 40,
    vx: (Math.random() - 0.5) * 3.2,
    vy: -1.6 - Math.random() * 3.4,
    gravity: -0.035, drag: 0.982,
    size: 2 + Math.random() * 4,
    color: ['#ff6b35', '#ffd166', '#ff2e63'][(Math.random() * 3) | 0],
    life: 60 + Math.random() * 50, fade: 80,
    rot: 0, spin: 0.1, shape: 'circle',
  })));
}

export function sparks(count = 26, origin = null) {
  const colors = accent();
  const cx = origin?.x ?? window.innerWidth / 2;
  const cy = origin?.y ?? window.innerHeight / 2;
  push(Array.from({ length: count }, () => {
    const a = Math.random() * Math.PI * 2;
    const s = 3 + Math.random() * 7;
    return {
      x: cx, y: cy, vx: Math.cos(a) * s, vy: Math.sin(a) * s,
      gravity: 0.02, drag: 0.93, size: 2 + Math.random() * 3,
      color: colors[(Math.random() * colors.length) | 0],
      life: 26 + Math.random() * 20, fade: 34, rot: 0, spin: 0, shape: 'circle',
    };
  }));
}

export function shake(el = document.querySelector('.view')) {
  if (!el || reduced) return;
  el.classList.remove('shake');
  void el.offsetWidth;
  el.classList.add('shake');
  setTimeout(() => el.classList.remove('shake'), 420);
}

export function flash(kind = 'good') {
  if (reduced) return;
  const div = document.createElement('div');
  div.className = kind === 'good' ? 'flash-good' : 'flash-bad';
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 560);
}

export function scorePop(text, target) {
  if (reduced || !target) return;
  const rect = target.getBoundingClientRect();
  const el = document.createElement('div');
  el.className = 'score-pop';
  el.textContent = text;
  el.style.left = `${rect.left + rect.width / 2 - 20}px`;
  el.style.top = `${rect.top}px`;
  el.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent');
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 1000);
}
