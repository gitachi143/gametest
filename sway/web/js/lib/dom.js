// Minimal hyperscript. Declarative views with no framework and no build step.
const SVG_NS = 'http://www.w3.org/2000/svg';
const SVG_TAGS = new Set(['svg', 'circle', 'path', 'rect', 'line', 'g', 'text', 'polyline',
                          'polygon', 'ellipse', 'defs', 'linearGradient', 'stop', 'use']);

export function h(tag, props = null, ...kids) {
  const el = SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag)
                               : document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class' || k === 'className') el.setAttribute('class', v);
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'style' && typeof v === 'object') style(el, v);
      else if (k === 'dataset') for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = dv;
      else if (k.startsWith('on') && typeof v === 'function') {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    }
  }
  add(el, kids);
  return el;
}

// Custom properties are invisible to `el.style.foo`, so they need setProperty.
export function style(el, props) {
  for (const [prop, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (prop.startsWith('--')) el.style.setProperty(prop, String(value));
    else el.style[prop] = value;
  }
}

function add(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false || kid === '') continue;
    if (Array.isArray(kid)) add(el, kid);
    else if (kid instanceof Node) el.appendChild(kid);
    else el.appendChild(document.createTextNode(String(kid)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function clear(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...kids) {
  clear(el);
  add(el, kids);
  return el;
}

export const fmt = (n) => Number(n || 0).toLocaleString('en-US');

export function plural(n, one, many = null) {
  return `${fmt(n)} ${n === 1 ? one : (many || one + 's')}`;
}

export function timeAgo(seconds) {
  const d = Date.now() / 1000 - seconds;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function countUp(el, to, ms = 900) {
  const start = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - start) / ms);
    el.textContent = fmt(Math.round(to * (1 - Math.pow(1 - p, 3))));
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

export function autoGrow(textarea, max = 148) {
  const fit = () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(max, textarea.scrollHeight) + 'px';
  };
  textarea.addEventListener('input', fit);
  requestAnimationFrame(fit);
  return fit;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const reduced = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export const isTouch = () => window.matchMedia('(max-width: 760px)').matches;

export function tpl(id) {
  return document.getElementById(id).content.firstElementChild.cloneNode(true);
}

/** Set the page-wide mind hue, which drives half the palette. */
export function setHue(hue) {
  document.documentElement.style.setProperty('--mind-hue', String(hue ?? 40));
}
