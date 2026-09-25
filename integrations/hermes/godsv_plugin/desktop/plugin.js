/**
 * Skaldir Agent — desktop panel + chat branding for the Hermes desktop app.
 *
 * Contributions:
 *   • ROUTES_AREA      /gods-voice — the status panel (tabs: Live, Pauses,
 *                      Memory, Skills, Treasury, System) — read-only.
 *   • SIDEBAR_NAV_AREA sidebar entry "Skaldir Agent"
 *   • PALETTE_AREA     ⌘K command
 *   • CHAT_EMPTY_AREA  the Skaldir Agent face for empty sessions (branding
 *                      instead of the neutral blank transcript).
 * Backend: dashboard/plugin_api.py → /api/plugins/godsv/ (read-only sqlite).
 * Branding: colors come from the `godsvoice` skin (gold accent → midground),
 * never hardcoded here — same design language as the app.
 *
 * Plain ESM, loaded uncompiled — jsx() calls, no JSX syntax.
 * Only @hermes/plugin-sdk, react, react/jsx-runtime resolve: anything else the
 * UI needs is inlined into this file (see the vendored thinking-orbs block).
 */
import * as sdk from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { Fragment, useEffect, useRef, useState, useSyncExternalStore } from 'react'


// >>> BEGIN thinking-orbs (vendored — regenerate: node tools/vendor_thinking_orbs.mjs)
// thinking-orbs v0.3.1 (MIT (c) Jakub Antalik) — inlined, not imported: the
// runtime plugin loader evaluates plugin.js as a blob module and a blob URL is not
// a hierarchical base, so a RELATIVE import cannot resolve (the reason this plugin
// is one file); npm is not reachable from the renderer either. Both dist files
// declare a top-level `function _`, so each keeps its own IIFE scope and only the
// parameters the components need cross the boundary. Nothing here is hand-edited —
// regenerate with `npm install && node tools/vendor_thinking_orbs.mjs`.
const __gvOrbsEngine = (() => {
function U(n, s, t) {
  return n + (s - n) * t;
}
function nt(n) {
  return n - Math.floor(n);
}
function G(n, s) {
  const t = Math.floor(n), r = Math.floor(s);
  let a = n - t, o = s - r;
  a = a * a * (3 - 2 * a), o = o * o * (3 - 2 * o);
  const c = E(t, r), M = E(t + 1, r), h = E(t, r + 1), m = E(t + 1, r + 1);
  return c + (M - c) * a + (h - c) * o + (c - M - h + m) * a * o;
}
function E(n, s) {
  const t = Math.sin(n * 12.9898 + s * 78.233) * 43758.5453;
  return t - Math.floor(t);
}
function J(n, s) {
  const t = Math.PI * (3 - Math.sqrt(5)), r = 1 - 2 * (n + 0.5) / s, a = Math.sqrt(1 - r * r), o = n * t;
  return [a * Math.cos(o), r, a * Math.sin(o)];
}
function et(n, s) {
  return Math.atan2(Math.sin(n - s), Math.cos(n - s));
}
function _(n, s, t, r, a) {
  const o = Math.sin(s), c = Math.cos(s), M = Math.sin(n), h = Math.cos(n);
  return (m, D, p) => {
    const e = m * h + p * M, l = -m * M + p * h, R = D * c - l * o, w = D * o + l * c;
    return [t + e * a, r - R * a, w];
  };
}
function rt(n, s, t, r = 0.3) {
  for (const a of s) {
    const o = a.a ?? 1, c = Math.min(1, Math.max(0, a.white)), M = Math.round((t ? 1 - c : c) * 255);
    n.fillStyle = `rgba(${M},${M},${M},${o})`, n.beginPath(), n.arc(a.x, a.y, a.r, 0, Math.PI * 2), n.fill();
  }
}
function it(n, s, t) {
  for (const r of s) {
    const a = r.a ?? 1, o = Math.min(1, Math.max(0, r.white)), c = Math.round((t ? 1 - o : o) * 255);
    n.strokeStyle = `rgba(${c},${c},${c},${a})`, n.lineWidth = r.w, n.beginPath(), n.moveTo(r.x1, r.y1), n.lineTo(r.x2, r.y2), n.stroke();
  }
}
function L(n, s, t = 0.3) {
  const r = [];
  for (const a of n)
    (a.a ?? 1) < 0.02 || (a.r = Math.max(t, a.r), r.push(a));
  return r.sort((a, o) => a.z - o.z), { dots: r, lines: s.filter((a) => (a.a ?? 1) >= 0.02) };
}
function ht(n, s, t) {
  s.lines.length && it(n, s.lines, t), rt(n, s.dots, t);
}
function $(n, s) {
  return (n / 300) ** s;
}
const Mt = (n, s, t) => {
  const r = n / 2, a = n / 2, o = n / 2 * 0.76, c = _(s * 0.4, 0.3, r, a, 1), M = $(n, t.rsPow ?? 0.6), h = [], m = t.ghostN ?? 150;
  for (let e = 0; e < m; e++) {
    const l = J(e, m), [R, w, i] = c(l[0] * o, l[1] * o, l[2] * o), u = (i / o + 1) / 2;
    h.push({ x: R, y: w, z: i, r: 0.8 * M, white: 0.78, a: 0.1 + 0.22 * u });
  }
  const D = t.strandN ?? 52, p = t.turns ?? 3;
  for (let e = 0; e < 3; e++) {
    const l = e / 3 * 2 * Math.PI;
    for (let R = 0; R < D; R++) {
      const w = (nt(R / D + s * 0.045) * 2 - 1) * 0.96, i = Math.sqrt(Math.max(0, 1 - w * w)), u = Math.min(1, (1 - Math.abs(w)) / 0.1), y = w * Math.PI * p + l, b = 1 + 0.075 * Math.sin(w * Math.PI * p * 2 + l * 2 + s * 0.8), f = i * o * b, [P, x, g] = c(Math.cos(y) * f, w * o * b, Math.sin(y) * f), d = (g / o + 1) / 2;
      h.push({
        x: P,
        y: x,
        z: g,
        r: ((t.rBase ?? 1.2) + (t.rDepth ?? 1.8) * d) * M,
        white: 0.55 - 0.45 * d,
        a: u * (0.45 + 0.55 * d)
      });
    }
  }
  return L(h, [], t.rMin);
};
function lt(n, s, t, r) {
  const a = 2 * s * t + r, o = n % a, c = new Array(s).fill(0);
  let M = -1;
  if (o < 2 * s * t) {
    const h = Math.floor(o / t), m = (o - h * t) / t, p = 1 - (1 - Math.min(1, m / 0.7)) ** 3;
    if (h < s) {
      for (let e = 0; e < h; e++) c[e] = 1;
      c[h] = p, M = h;
    } else {
      const e = 2 * s - 1 - h;
      for (let l = 0; l < e; l++) c[l] = 1;
      c[e] = 1 - p, M = e;
    }
  }
  return { amount: c, active: M };
}
function pt(n, s, t) {
  let [r, a, o] = n, c = !1;
  for (let M = 0; M < s.length; M++) {
    if (t.amount[M] <= 0) continue;
    const h = s[M], m = h.axis === 0 ? r : h.axis === 1 ? a : o;
    if (m < h.lo || m >= h.hi) continue;
    M === t.active && (c = !0);
    const D = h.ang * t.amount[M], p = Math.cos(D), e = Math.sin(D);
    if (h.axis === 0) {
      const l = a * p - o * e;
      o = a * e + o * p, a = l;
    } else if (h.axis === 1) {
      const l = r * p + o * e;
      o = -r * e + o * p, r = l;
    } else {
      const l = r * p - a * e;
      a = r * e + a * p, r = l;
    }
  }
  return [r, a, o, c];
}
function ut(n) {
  const s = [];
  for (let t = 0; t < n; t++) {
    const r = Math.min(2, Math.floor(E(t, 2.3) * 3)), a = -1 + 0.5 * Math.min(3, Math.floor(E(t, 5.9) * 4)), o = E(t, 7.7) < 0.5 ? 1 : -1;
    s.push({ axis: r, lo: a, hi: a + 0.5, ang: o * Math.PI / 2 });
  }
  return s;
}
const ft = (n, s, t) => {
  const a = n / 2, o = n / 2, c = n / 2 * 0.82, M = 0.4 + 0.06 * Math.sin(s * 0.35), h = _(s * 0.5, M, a, o, c), m = s * (0.5 + (1.7 - 0.5) * (t.scanMul ?? 1)), D = $(n, t.rsPow ?? 0.6), p = t.dimBase ?? 1, e = [], l = t.latRings ?? 17, R = t.lonDensity ?? 44;
  for (let w = 0; w <= l; w++) {
    const i = -Math.PI / 2 + w / l * Math.PI, u = Math.cos(i), y = Math.sin(i), b = Math.max(1, Math.round(Math.abs(u) * R));
    for (let f = 0; f < b; f++) {
      const P = f / b * 2 * Math.PI, [x, g, d] = h(u * Math.cos(P), y, u * Math.sin(P)), v = (d + 1) / 2, k = et(P + s * 0.5, m), N = Math.exp(-(k * k) / 0.18) * Math.max(0, d);
      e.push({
        x,
        y: g,
        z: d,
        r: ((t.rBase ?? 0.6) + (t.rDepth ?? 1.7) * v + (t.rBoost ?? 1) * N) * D,
        white: (t.inkFar ?? 0.62) - (t.inkSpan ?? 0.54) * v,
        // dimBase < 1 fades un-scanned dots so the meridian reads clearly
        a: p + (1 - p) * Math.min(1, N)
      });
    }
  }
  return L(e, [], t.rMin);
}, dt = (n, s, t) => {
  const r = n / 2, a = n / 2, o = n / 2 * 0.82, c = _(s * 0.55, 0.35 + 0.1 * Math.sin(s * 0.9), r, a, o), M = $(n, t.rsPow ?? 0.6), h = t.moveCount ?? 14, m = ut(h), D = lt(s, h, 0.42, 1.2), p = [], e = t.latRings ?? 15, l = t.lonDensity ?? 40;
  for (let R = 0; R <= e; R++) {
    const w = -Math.PI / 2 + R / e * Math.PI, i = Math.cos(w), u = Math.sin(w), y = Math.max(1, Math.round(Math.abs(i) * l));
    for (let b = 0; b < y; b++) {
      const f = b / y * 2 * Math.PI, [P, x, g, d] = pt([i * Math.cos(f), u, i * Math.sin(f)], m, D), [v, k, N] = c(P, x, g), z = (N + 1) / 2;
      p.push({
        x: v,
        y: k,
        z: N,
        r: ((t.rBase ?? 0.6) + (t.rDepth ?? 1.7) * z + (d ? t.rActive ?? 0.3 : 0)) * M,
        white: (t.inkFar ?? 0.62) - (t.inkSpan ?? 0.54) * z - (d ? 0.14 : 0)
      });
    }
  }
  return L(p, [], t.rMin);
}, bt = (n, s, t) => {
  const r = n / 2, a = n / 2, o = n / 2 * 0.874, c = _(s * 0.18, 0.38, r, a, 1), M = $(n, t.rsPow ?? 0.6), h = [], m = t.rings ?? 15, D = t.lonDensity ?? 40;
  for (let p = 0; p <= m; p++) {
    const e = -Math.PI / 2 + p / m * Math.PI, l = Math.cos(e), R = Math.sin(e), w = 0.62 * Math.sin(s * 2.1 - p * 0.52) + 0.38 * Math.sin(s * 1.27 + p * 0.83), i = o * (0.88 + 0.105 * w), u = Math.max(1, Math.round(Math.abs(l) * D));
    for (let y = 0; y < u; y++) {
      const b = y / u * 2 * Math.PI, [f, P, x] = c(l * Math.cos(b) * i, R * i, l * Math.sin(b) * i), g = (x / o + 1) / 2, d = Math.max(0, w);
      h.push({
        x: f,
        y: P,
        z: x,
        r: ((t.rBase ?? 0.6) + (t.rDepth ?? 1.7) * g) * (1 + 0.4 * d) * M,
        white: 0.66 - 0.56 * g - 0.1 * d
      });
    }
  }
  return L(h, [], t.rMin);
};
function xt(n) {
  return n * n * (3 - 2 * n);
}
function st(n) {
  const s = n.length, t = [];
  let r = 0;
  for (let a = 0; a < s; a++) {
    const o = n[a], c = n[(a + 1) % s], M = Math.hypot(c[0] - o[0], c[1] - o[1]);
    t.push(M), r += M;
  }
  return (a) => {
    let o = a * r, c = 0;
    for (; o > t[c] && c < s - 1; )
      o -= t[c], c++;
    const M = n[c], h = n[(c + 1) % s], m = t[c] ? Math.min(1, o / t[c]) : 0;
    return [M[0] + (h[0] - M[0]) * m, M[1] + (h[1] - M[1]) * m];
  };
}
const yt = (n) => {
  const s = -Math.PI / 2 + n * 2 * Math.PI;
  return [Math.cos(s) * 0.24, Math.sin(s) * 0.24];
}, gt = st([
  [0, -0.26],
  [0.24, 0.16],
  [-0.24, 0.16]
]), mt = st([
  [0, -0.2],
  [0.2, -0.2],
  [0.2, 0.2],
  [-0.2, 0.2],
  [-0.2, -0.2]
]), H = [yt, gt, mt];
function wt(n) {
  return Math.max(6, Math.round(34 * n));
}
const V = 1.4, ot = 0.9, Q = V + ot, Pt = (n, s, t) => {
  const r = H.length, a = s % (Q * r), o = Math.floor(a / Q), c = a - o * Q, M = c > V ? xt((c - V) / ot) : 0, h = t.spread ?? 1, m = H[o], D = H[(o + 1) % r], p = 160, e = [];
  for (let x = 0; x < p; x++) {
    const g = x / p, d = m(g), v = D(g);
    e.push([(d[0] + (v[0] - d[0]) * M) * h, (d[1] + (v[1] - d[1]) * M) * h]);
  }
  const l = [];
  let R = 0;
  for (let x = 0; x < p; x++) {
    const g = e[x], d = e[(x + 1) % p], v = Math.hypot(d[0] - g[0], d[1] - g[1]);
    l.push(v), R += v;
  }
  const w = wt(t.iconD ?? 1), i = (t.rDot ?? 0.021) * 1.35 * h, u = 1 + 0.02 * Math.sin(c * 3.1), y = [], b = n / 2;
  let f = 0, P = 0;
  for (let x = 0; x < w; x++) {
    const g = x / w * R;
    for (; P + l[f] < g && f < p - 1; )
      P += l[f], f++;
    const d = e[f], v = e[(f + 1) % p], k = l[f] ? Math.min(1, (g - P) / l[f]) : 0, N = (d[0] + (v[0] - d[0]) * k) * u, z = (d[1] + (v[1] - d[1]) * k) * u;
    y.push({
      x: b + N * n,
      y: b + z * n,
      z: 0,
      r: Math.max(0.35, i * n),
      white: 0.1
    });
  }
  return L(y, [], t.rMin);
}, Rt = (n, s, t) => {
  const r = n / 2, a = n / 2, o = n / 2 * 0.82, c = _(s * 0.12, 0.3, r, a, 1), M = $(n, t.rsPow ?? 0.6), h = [], m = t.orbitN ?? 12, D = t.ghostN ?? 40, p = t.particles ?? 3;
  for (let e = 0; e < m; e++) {
    const l = E(e, 1.7), R = E(e, 5.2), w = E(e, 8.9), i = o * (0.45 + 0.52 * l), u = l * 2 * Math.PI, y = Math.acos(2 * R - 1), b = Math.sin(y) * Math.cos(u), f = Math.cos(y), P = Math.sin(y) * Math.sin(u);
    let x = -f, g = b;
    const d = 0, v = Math.max(1e-6, Math.sqrt(x * x + g * g));
    x /= v, g /= v;
    const k = f * d - P * g, N = P * x - b * d, z = b * g - f * x, O = (0.25 + 0.55 * w) * (w > 0.5 ? 1 : -1);
    for (let B = 0; B < D; B++) {
      const I = B / D * 2 * Math.PI, [S, A, T] = c(
        (x * Math.cos(I) + k * Math.sin(I)) * i,
        (g * Math.cos(I) + N * Math.sin(I)) * i,
        (d * Math.cos(I) + z * Math.sin(I)) * i
      ), C = (T / i + 1) / 2;
      h.push({
        x: S,
        y: A,
        z: T,
        r: (t.ghostR ?? 0.9) * M,
        white: 0.72,
        a: (t.ghostA ?? 0.5) * (0.4 + 0.6 * C)
      });
    }
    for (let B = 0; B < p; B++) {
      const I = s * O + B / p * 2 * Math.PI + R * 6, [S, A, T] = c(
        (x * Math.cos(I) + k * Math.sin(I)) * i,
        (g * Math.cos(I) + N * Math.sin(I)) * i,
        (d * Math.cos(I) + z * Math.sin(I)) * i
      ), C = (T / i + 1) / 2;
      h.push({
        x: S,
        y: A,
        z: T,
        r: ((t.partR ?? 1.2) + (t.partRDepth ?? 1.6) * C) * M,
        white: 0.3 - 0.22 * C
      });
    }
  }
  return L(h, [], t.rMin);
}, Z = (n, s, t) => {
  const r = n / 2, a = n / 2, o = n / 2 * 0.78, c = t.spin ?? 1, M = 0.3, h = _(s * 0.1 * c, M, r, a, 1), m = $(n, t.rsPow ?? 0.6), D = [], p = t.ghostN ?? 150;
  for (let z = 0; z < p; z++) {
    const O = J(z, p), [B, I, S] = h(O[0] * o, O[1] * o, O[2] * o), A = (S / o + 1) / 2;
    D.push({ x: B, y: I, z: S, r: 0.8 * m, white: 0.78, a: 0.1 + 0.22 * A });
  }
  const e = s * 0.24 * c, l = t.faceOn ? -M : 0.55 + 0.3 * Math.sin(s * 0.18) * c, R = Math.cos(e), w = 0, i = Math.sin(e), u = -i * Math.sin(l), y = Math.cos(l), b = R * Math.sin(l), f = w * b - i * y, P = i * u - R * b, x = R * y - w * u, g = 0.23 * (t.wobMul ?? 1), d = t.faceOn ? o / (1 + 0.85 * g) : o, v = t.lanes ?? 5, k = t.segs ?? 88, N = Math.max(1, Math.round(v * (t.bandMul ?? 1)));
  for (let z = 0; z < N; z++) {
    const O = (z - (N - 1) / 2) * 0.075, B = Math.abs(z - (N - 1) / 2) / Math.max(1, (N - 1) / 2);
    for (let I = 0; I < k; I++) {
      const S = I / k * 2 * Math.PI, A = (0.16 * Math.sin(S * 3 - s * 1.7 + z * 0.22) + 0.07 * Math.sin(S * 5 + s * 1.1)) * (t.wobMul ?? 1), T = t.faceOn ? 1 + A : 1, C = t.faceOn ? O : O + A, q = R * Math.cos(S) + u * Math.sin(S) + f * C, F = w * Math.cos(S) + y * Math.sin(S) + P * C, j = i * Math.cos(S) + b * Math.sin(S) + x * C, W = Math.sqrt(q * q + F * F + j * j), Y = d * T, [ct, at, X] = h(q / W * Y, F / W * Y, j / W * Y), K = (X / o + 1) / 2;
      D.push({
        x: ct,
        y: at,
        z: X,
        r: ((t.rBase ?? 1.1) + (t.rDepth ?? 1.7) * K) * (1 - 0.25 * B) * m,
        white: 0.52 - 0.44 * K + 0.18 * B,
        a: 0.4 + 0.6 * K
      });
    }
  }
  return L(D, [], t.rMin);
}, Dt = (n, s, t) => {
  const r = n / 2, a = n / 2, o = n / 2 * 0.8 * (t.spread ?? 1), c = _(s * 0.12, 0.32, r, a, o), M = $(n, t.rsPow ?? 0.6), h = t.nodeN ?? 30, m = t.thr ?? 0.72, D = t.nodeR ?? 1.4, p = t.nodeRDepth ?? 1.8, e = [];
  for (let i = 0; i < h; i++) {
    const u = J(i, h), y = u[0] + 0.3 * (G(i * 0.31 + 9, s * 0.24) - 0.5) * 2, b = u[1] + 0.3 * (G(i * 0.53 + 27, s * 0.21) - 0.5) * 2, f = u[2] + 0.3 * (G(i * 0.77 + 55, s * 0.27) - 0.5) * 2, P = Math.sqrt(y * y + b * b + f * f);
    e.push([y / P, b / P, f / P]);
  }
  const l = [], R = [];
  for (let i = 0; i < h; i++)
    for (let u = i + 1; u < h; u++) {
      const y = e[i][0] - e[u][0], b = e[i][1] - e[u][1], f = e[i][2] - e[u][2], P = Math.sqrt(y * y + b * b + f * f);
      if (P >= m) continue;
      const [x, g, d] = c(e[i][0], e[i][1], e[i][2]), [v, k, N] = c(e[u][0], e[u][1], e[u][2]), z = ((d + N) / 2 + 1) / 2;
      l.push({
        x1: x,
        y1: g,
        x2: v,
        y2: k,
        white: 0.42,
        a: (1 - P / m) * (0.3 + 0.55 * z),
        w: Math.max(0.6, (t.lineW ?? 0.8) * M)
      });
    }
  for (let i = 0; i < h; i++) {
    const [u, y, b] = c(e[i][0], e[i][1], e[i][2]), f = (b + 1) / 2, P = 1 + 0.25 * Math.sin(s * 1.4 + i * 2.7);
    R.push({
      x: u,
      y,
      z: b,
      r: (D + p * f) * P * M,
      white: 0.55 - 0.45 * f
    });
  }
  const w = t.signals ?? 5;
  for (let i = 0; i < w; i++) {
    const u = Math.floor(s * 0.55 + i * 7.31), y = Math.floor(E(u, i * 3.1 + 1.7) * h), b = Math.floor(E(u, i * 5.7 + 4.2) * h);
    if (y === b) continue;
    const f = nt(s * 0.55 + i * 7.31), P = U(e[y][0], e[b][0], f), x = U(e[y][1], e[b][1], f), g = U(e[y][2], e[b][2], f), d = Math.max(1e-6, Math.sqrt(P * P + x * x + g * g)), [v, k, N] = c(P / d, x / d, g / d), z = (N + 1) / 2;
    R.push({
      x: v,
      y: k,
      z: N,
      r: (D * 1.5 + p * z) * M,
      white: 0.05,
      a: 0.5 + 0.5 * z
    });
  }
  return L(R, l, t.rMin);
}, vt = {
  orbits: Rt,
  globe: ft,
  rubik: dt,
  wave: bt,
  web: Dt,
  braid: Mt,
  ribbon: Z,
  // ring shares ribbon's geometry — the `faceOn` profile flag switches it
  ring: Z,
  morph: Pt
}, Ct = Object.fromEntries(
  Object.entries(vt).map(([n, s]) => [
    n,
    (t, r, a, o, c) => ht(t, s(r, a, c), o)
  ])
), zt = [
  ["latRings", "lonDensity"],
  ["rings", "lonDensity"],
  ["lanes", "segs"]
], Nt = ["orbitN", "ghostN", "nodeN", "strandN", "signals"], It = ["iconD"], kt = [
  "rBase",
  "rDepth",
  "rActive",
  "rDot",
  "ghostR",
  "partR",
  "partRDepth",
  "nodeR",
  "nodeRDepth"
];
function St(n, s) {
  const t = { ...n }, r = /* @__PURE__ */ new Set(), a = Math.sqrt(s);
  for (const [o, c] of zt) {
    const M = t[o], h = t[c];
    M != null && h != null && !r.has(o) && !r.has(c) && (t[o] = Math.max(2, Math.round(M * a)), t[c] = Math.max(2, Math.round(h * a)), r.add(o), r.add(c));
  }
  for (const o of Nt) {
    const c = t[o];
    c != null && c !== 0 && !r.has(o) && (t[o] = Math.max(1, Math.round(c * s)));
  }
  for (const o of It) {
    const c = t[o];
    c != null && (t[o] = Math.max(0.02, c * s));
  }
  return t;
}
function Bt(n, s) {
  const t = { ...n };
  for (const r of kt) {
    const a = t[r];
    a != null && (t[r] = a * s);
  }
  return t.rSizeMul = (t.rSizeMul ?? 1) * s, t;
}
const Et = {
  globe: {
    latRings: 17,
    lonDensity: 44,
    rBase: 0.6,
    rDepth: 1.7,
    rBoost: 1,
    inkFar: 0.62,
    inkSpan: 0.54,
    rsPow: 0.6,
    rMin: 0.3
  },
  orbits: {
    orbitN: 12,
    ghostN: 40,
    ghostR: 0.9,
    ghostA: 0.5,
    particles: 3,
    partR: 1.2,
    partRDepth: 1.6,
    rsPow: 0.6,
    rMin: 0.3
  },
  rubik: {
    latRings: 15,
    lonDensity: 40,
    moveCount: 14,
    rBase: 0.6,
    rDepth: 1.7,
    rActive: 0.3,
    inkFar: 0.62,
    inkSpan: 0.54,
    rsPow: 0.6,
    rMin: 0.3
  },
  wave: {
    rings: 15,
    lonDensity: 40,
    rBase: 0.6,
    rDepth: 1.7,
    rsPow: 0.6,
    rMin: 0.3
  },
  web: {
    nodeN: 30,
    thr: 0.72,
    signals: 5,
    nodeR: 1.4,
    nodeRDepth: 1.8,
    lineW: 0.8,
    rsPow: 0.6,
    rMin: 0.3
  },
  braid: {
    strandN: 52,
    turns: 3,
    ghostN: 150,
    rBase: 1.2,
    rDepth: 1.8,
    rsPow: 0.6,
    rMin: 0.3
  },
  ribbon: {
    lanes: 5,
    segs: 88,
    ghostN: 150,
    rBase: 1.1,
    rDepth: 1.7,
    rsPow: 0.6,
    rMin: 0.3
  },
  // ring shares ribbon's painter; faceOn cancels the camera tilt and moves
  // the undulation onto the radius, and there is no ghost sphere behind it
  ring: {
    lanes: 5,
    segs: 88,
    ghostN: 0,
    faceOn: 1,
    rBase: 1.1,
    rDepth: 1.7,
    rsPow: 0.6,
    rMin: 0.3
  },
  morph: {
    rDot: 0.021,
    iconD: 1,
    rMin: 0.25
  }
}, Ot = {
  working: "orbits",
  searching: "globe",
  solving: "rubik",
  listening: "wave",
  connecting: "web",
  weaving: "braid",
  composing: "ribbon",
  breathing: "ring",
  shaping: "morph"
}, At = {
  orbits: {
    64: { speed: 1.885, count: 1, size: 1 },
    20: { speed: 3.9, count: 0.238, size: 2.4 }
  },
  globe: {
    64: { speed: 2.015, count: 0.42, size: 1.15, extra: { scanMul: 4.08, dimBase: 0.45 } },
    20: { speed: 2.665, count: 0.105, size: 1.75, extra: { scanMul: 4.335, dimBase: 0.45 } }
  },
  rubik: {
    64: { speed: 1.82, count: 0.35, size: 1.05 },
    20: { speed: 1.95, count: 0.088, size: 1.9 }
  },
  wave: {
    64: { speed: 4.388, count: 0.341, size: 1 },
    20: { speed: 3.998, count: 0.105, size: 1.6 }
  },
  web: {
    64: { speed: 3.315, count: 1.35, size: 0.95 },
    20: { speed: 6.63, count: 0.25, size: 1.52 }
  },
  braid: {
    64: { speed: 1.625, count: 0.5, size: 1 },
    20: { speed: 2.75, count: 0.1125, size: 1.36 }
  },
  ribbon: {
    64: { speed: 2.34, count: 0.25, size: 0.85, extra: { spin: 0, bandMul: 3.9, wobMul: 1 } },
    20: { speed: 3.12, count: 0.051, size: 1.073, extra: { spin: 0, bandMul: 4.94, wobMul: 1 } }
  },
  ring: {
    64: { speed: 3.24, count: 0.25, size: 0.956, extra: { spin: 0, bandMul: 3.627, wobMul: 0.368 } },
    20: { speed: 3.78, count: 0.028, size: 1.622, extra: { spin: 0, bandMul: 3.968, wobMul: 0.565 } }
  },
  morph: {
    64: { speed: 2.405, count: 0.702, size: 0.395, extra: { spread: 1.45 } },
    20: { speed: 2.08, count: 0.53, size: 1.011, extra: { spread: 1.45 } }
  }
}, tt = /* @__PURE__ */ new Map();
function Lt(n, s) {
  const t = `${n}-${s}`, r = tt.get(t);
  if (r) return r;
  const a = Ot[n], o = At[a][s];
  let c = { ...Et[a] };
  o.count !== 1 && (c = St(c, o.count)), o.size !== 1 && (c = Bt(c, o.size)), o.extra && (c = { ...c, ...o.extra });
  const M = { mode: a, speed: o.speed, opts: c };
  return tt.set(t, M), M;
}
  return { MODE_DRAWS: Ct, MODE_FRAMES: vt, STATE_TO_MODE: Ot, finalizeFrame: L, makeProj: _, paint: rt, paintFrame: ht, paintLines: it, radiusScale: $, resolvePreset: Lt }
})()

const __gvThinkingOrbs = (({ R, O, M, F, C, I, N }) => {
function P(t) {
  let e = t;
  for (; e; ) {
    const n = e.getAttribute("data-theme");
    if (n === "dark") return !0;
    if (n === "light") return !1;
    if (e.classList.contains("dark")) return !0;
    if (e.classList.contains("light")) return !1;
    e = e.parentElement;
  }
  return null;
}
function W() {
  return typeof matchMedia > "u" || matchMedia("(prefers-color-scheme: dark)").matches;
}
function _(t, e) {
  const [n, r] = O(!0);
  return M(() => {
    if (t === "dark") {
      r(!0);
      return;
    }
    if (t === "light") {
      r(!1);
      return;
    }
    const i = () => {
      const c = P(e.current);
      r(c ?? W());
    };
    i();
    const o = typeof matchMedia < "u" ? matchMedia("(prefers-color-scheme: dark)") : null, d = () => i();
    o == null || o.addEventListener("change", d);
    let s = null;
    return typeof MutationObserver < "u" && e.current && (s = new MutationObserver(i), s.observe(document.documentElement, {
      attributes: !0,
      attributeFilter: ["class", "data-theme"],
      subtree: !0
    })), () => {
      o == null || o.removeEventListener("change", d), s == null || s.disconnect();
    };
  }, [t, e]), n;
}
function j() {
  const [t, e] = O(!1);
  return M(() => {
    if (typeof matchMedia > "u") return;
    const n = matchMedia("(prefers-reduced-motion: reduce)");
    e(n.matches);
    const r = (i) => e(i.matches);
    return n.addEventListener("change", r), () => n.removeEventListener("change", r);
  }, []), t;
}
const q = {
  working: "Working…",
  searching: "Searching…",
  solving: "Solving…",
  listening: "Listening…",
  connecting: "Connecting…",
  weaving: "Weaving…",
  composing: "Composing…",
  breathing: "Thinking…",
  shaping: "Shaping…"
};
function H({
  state: t = "working",
  size: e = 64,
  theme: n = "auto",
  speed: r = 1,
  paused: i = !1,
  style: o,
  "aria-label": d,
  ...s
}) {
  const c = F(null), y = _(n, c), E = j();
  return M(() => {
    const u = c.current;
    if (!u) return;
    const l = Math.min(2, typeof devicePixelRatio < "u" && devicePixelRatio || 1);
    u.width = Math.round(e * l), u.height = Math.round(e * l);
    const f = u.getContext("2d");
    if (!f) return;
    const { mode: T, speed: x, opts: A } = C(t, e), D = I[T], w = x * r, h = (k) => {
      f.setTransform(l, 0, 0, l, 0, 0), f.clearRect(0, 0, e, e), D(f, e, k, y, A);
    };
    if (E) {
      h(0.6);
      return;
    }
    let g = 0, m = !1;
    const L = () => {
      h(performance.now() / 1e3 * w), m && (g = requestAnimationFrame(L));
    }, v = () => {
      m || i || (m = !0, g = requestAnimationFrame(L));
    }, p = () => {
      m = !1, cancelAnimationFrame(g);
    };
    h(performance.now() / 1e3 * w);
    let b = !0;
    const a = typeof IntersectionObserver < "u" ? new IntersectionObserver(([k]) => {
      b = k.isIntersecting, b && document.visibilityState !== "hidden" ? v() : p();
    }) : null;
    a == null || a.observe(u);
    const S = () => {
      document.visibilityState === "hidden" ? p() : b && v();
    };
    return document.addEventListener("visibilitychange", S), a || v(), () => {
      p(), a == null || a.disconnect(), document.removeEventListener("visibilitychange", S);
    };
  }, [t, e, y, r, i, E]), /* @__PURE__ */ R(
    "canvas",
    {
      ref: c,
      role: "img",
      "aria-label": d ?? q[t],
      style: { width: e, height: e, display: "block", ...o },
      ...s
    }
  );
}
  return { MODE_DRAWS: I, STATE_TO_MODE: N, ThinkingOrb: H, resolvePreset: C }
})({
  R: jsx, O: useState, M: useEffect, F: useRef, C: __gvOrbsEngine.resolvePreset, I: __gvOrbsEngine.MODE_DRAWS, N: __gvOrbsEngine.STATE_TO_MODE
})

const ThinkingOrb = __gvThinkingOrbs.ThinkingOrb
// <<< END thinking-orbs

const ID = 'godsv'
const ROUTE = '/gods-voice'

// The GV app build embeds this plugin as the app's own experience — its UA
// carries the product name. There the panel areas become native sidebar
// destinations (no single "plugin" entry, no second nav layer). The normal
// Hermes keeps the quiet one-entry integration.
const INTEGRATED =
  typeof navigator !== 'undefined' && /(?:God'sVoice|SkaldirAgent)\//.test(String(navigator.userAgent || ''))
const VIEW_ROUTES = {
  cockpit: ROUTE,
  verification: '/gods-verification',
  activity: '/gods-activity',
  tools: '/gods-tools',
  settings: '/gods-settings',
  treasury: '/gods-treasury'
}
const VIEW_ICONS = {
  verification: 'checklist',
  activity: 'pulse',
  tools: 'tools',
  settings: 'settings-gear',
  treasury: 'key'
}

function viewFromHash() {
  const path = String((typeof window !== 'undefined' && window.location.hash) || '')
    .replace(/^#\/?/, '')
    .split('?')[0]
  const hit = Object.entries(VIEW_ROUTES).find(([, route]) => route === `/${path}`)
  // Home is the cockpit view (route /gods-voice) even after it left the sidebar:
  // the live block + status strip show the same state inline in chat.
  return hit ? hit[0] : 'cockpit'
}
// Navigation: the chat live block is the cockpit (V2-01) and the activity
// history lives in the live block + Inspect drawers (V2-11) — the nav layer
// keeps only views with unique function. The cockpit stays reachable as the
// panel home route and via the palette/strip/drawer "Open cockpit" actions.
const NAV = [
  ['Control', [
    ['verification', 'Verification'],
    ['tools', 'Tools']
  ]],
  ['Manage', [
    ['settings', 'Settings'],
    ['treasury', 'Treasury']
  ]]
]

// Settings bundles the administrative views (UI audit: one door, sub-tabs) —
// Sidebar keeps only views with unique function — verification, tools, settings, treasury
// (orders 53/55/56/57); cockpit and activity left the sidebar (V2-01/V2-11) and stay reachable as
// the panel home route plus palette/strip/drawer actions.
const SETTINGS_TABS = [
  ['models', 'Models'],
  ['tools', 'Tools'],
  ['cu', 'Computer Use'],
  ['memory', 'Memory'],
  ['skills', 'Skills'],
  ['system', 'System']
]
const SETTINGS_KEYS = SETTINGS_TABS.map(([key]) => key)

function SettingsView({ data, tab, onTab }) {
  const tabs = jsx('div', {
    className: 'flex flex-wrap gap-1 mb-5',
    children: SETTINGS_TABS.map(([key, label]) =>
      jsx('button', {
        className: `px-2.5 py-1 rounded text-xs ${
          tab === key
            ? 'font-medium text-foreground'
            : 'text-(--ui-text-secondary) hover:text-foreground'
        }`,
        style: tab === key
          ? { background: 'color-mix(in srgb, var(--ui-text-primary, #f5f5f5) 14%, transparent)' }
          : undefined,
        onClick: () => onTab(key),
        children: label
      }, key))
  })
  let body
  if (tab === 'tools') body = jsx(ToolsTab, {})
  else if (tab === 'cu') body = jsx(ComputerUseTab, {})
  else if (tab === 'memory') body = jsx(MemoryTab, {})
  else if (tab === 'skills') body = jsx(SkillsTab, {})
  else if (tab === 'system') body = jsx(SystemTab, { data })
  else body = jsx(ModelsTab, {})
  return jsxs('div', { children: [tabs, body] })
}

// sdk.* reads keep the plugin loadable on older app builds (no named-import
// binding failures when an area or component is not exported yet).
const { PALETTE_AREA, ROUTES_AREA, SIDEBAR_NAV_AREA, host, useQuery } = sdk
const CHAT_EMPTY = sdk.CHAT_EMPTY_AREA || 'chat.empty'
const COMPOSER = sdk.COMPOSER_AREAS || null
const Wordmark = sdk.Wordmark || null

// --- session identity ---------------------------------------------------
// Chat surfaces must resolve the session the user is actually looking at —
// never "the newest contract in the store": that leaked another session's
// goal/verdict into fresh chats. host.state.focusedSessionId is the plugin
// door to the focused pane's runtime session (same id the agent hooks and
// the completion store use). Old app builds without the atom fall back to
// the unscoped panel default instead of crashing.
const focusedState = (sdk.host && sdk.host.state) || null
const focusedSessionAtom = (focusedState && focusedState.focusedSessionId) || null
const focusedStoredAtom = (focusedState && focusedState.focusedStoredSessionId) || null
function readAtom(atom) {
  try {
    return String((atom && atom.get && atom.get()) || '')
  } catch (err) {
    return ''
  }
}
// The completion store keys contracts by the DURABLE session id (the
// session_key the agent hooks receive), not the transient runtime/socket id —
// prefer the stored id and fall back to the runtime one only if the durable
// atom is unavailable on this app build.
function readFocusedSessionId() {
  return readAtom(focusedStoredAtom) || readAtom(focusedSessionAtom)
}
function readFocusedStoredSessionId() {
  try {
    const atom = sdk.host && sdk.host.state && sdk.host.state.focusedStoredSessionId
    return String((atom && atom.get && atom.get()) || '')
  } catch (err) {
    return ''
  }
}
function useFocusedSessionId() {
  const subscribe = (cb) => {
    const unsubs = []
    for (const atom of [focusedStoredAtom, focusedSessionAtom]) {
      try {
        const un = atom && atom.subscribe && atom.subscribe(cb)
        if (un) unsubs.push(un)
      } catch (err) { /* atom unavailable — skip */ }
    }
    return () => {
      for (const un of unsubs) {
        try { un() } catch (err) { /* already gone */ }
      }
    }
  }
  return useSyncExternalStore(subscribe, readFocusedSessionId, readFocusedSessionId)
}
const completionPath = (sid) => (sid ? `/completion?session_id=${encodeURIComponent(sid)}` : '/completion')
const eventsPath = (sid, limit, before = 0) =>
  (sid
    ? `/events?limit=${limit}&session_id=${encodeURIComponent(sid)}`
    : `/events?limit=${limit}`) + (before ? `&before=${encodeURIComponent(before)}` : '')
const scopedBody = (sid, body) => (sid ? { ...(body || {}), session_id: sid } : body || {})
let rest = null

// -- formatting helpers -------------------------------------------------------

function fmtTs(ts) {
  if (ts == null) return '—'
  try {
    return new Date(Number(ts) * 1000).toLocaleTimeString()
  } catch {
    return '—'
  }
}

function fmtDay(ts) {
  if (ts == null) return '—'
  try {
    return new Date(Number(ts) * 1000).toLocaleDateString()
  } catch {
    return '—'
  }
}

function shorten(value, max) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? {})
  return text.length > max ? text.slice(0, max) + '…' : text
}

// -- small building blocks ----------------------------------------------------

function kpi(label, value, hint) {
  return jsxs('div', {
    className: 'rounded-lg border border-border bg-card px-4 py-3',
    children: [
      jsx('div', {
        className: 'text-xs uppercase tracking-wide text-(--ui-text-secondary)',
        children: label
      }),
      jsx('div', { className: 'text-2xl font-semibold mt-1', children: value ?? '—' }),
      hint ? jsx('div', { className: 'text-xs text-(--ui-text-secondary) mt-1', children: hint }) : null
    ]
  })
}

function Section({ title, hint, children }) {
  return jsxs('div', { children: [
    jsx('div', { className: 'font-semibold mb-2', children: title }),
    hint ? jsx('div', { className: 'text-xs text-(--ui-text-secondary) mb-2', children: hint }) : null,
    children
  ] })
}

function Empty({ children }) {
  return jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children })
}

/** columns: [{ key, label, fmt?, max? }] */
function SimpleTable({ columns, rows, empty }) {
  if (!rows || !rows.length) return jsx(Empty, { children: empty || 'no data' })
  return jsxs('table', {
    className: 'w-full text-sm',
    children: [
      jsx('thead', { children: jsx('tr', {
        className: 'text-left text-(--ui-text-secondary)',
        children: columns.map((c, i) => jsx('th', { className: 'py-1 pr-3 whitespace-nowrap', children: c.label }, `h${i}`))
      }) }),
      jsx('tbody', { children: rows.map((row, r) =>
        jsx('tr', {
          className: 'border-t border-border align-top',
          children: columns.map((c, i) => {
            const raw = row[c.key]
            const value = c.fmt ? c.fmt(raw, row) : (c.max ? shorten(raw, c.max) : raw)
            if (value == null || value === '') return jsx('td', { className: 'py-1 pr-3', children: '—' }, `c${i}`)
            const isElement = typeof value === 'object'
            return jsx('td', { className: 'py-1 pr-3', children: isElement ? value : String(value) }, `c${i}`)
          })
        }, `r${r}`)
      ) })
    ]
  })
}

// -- data blocks --------------------------------------------------------------

function PauseRows({ pauses }) {
  if (!pauses.length) return jsx(Empty, { children: 'no pauses — quiet.' })
  return jsxs('table', {
    className: 'w-full text-sm',
    children: [
      jsx('thead', { children: jsxs('tr', {
        className: 'text-left text-(--ui-text-secondary)',
        children: [
          jsx('th', { className: 'py-1 pr-3', children: 'created' }),
          jsx('th', { className: 'py-1 pr-3', children: 'session' }),
          jsx('th', { className: 'py-1 pr-3', children: 'kind' }),
          jsx('th', { className: 'py-1 pr-3', children: 'status' }),
          jsx('th', { className: 'py-1 pr-3', children: 'attempts' }),
          jsx('th', { className: 'py-1', children: 'ttl' })
        ]
      }) }),
      jsx('tbody', { children: pauses.map((p, i) =>
        jsxs('tr', {
          className: 'border-t border-border',
          children: [
            jsx('td', { className: 'py-1 pr-3 whitespace-nowrap', children: fmtTs(p.created) }),
            jsx('td', { className: 'py-1 pr-3 font-mono text-xs', children: shorten(p.session_id ?? p.session ?? '—', 16) }),
            jsx('td', { className: 'py-1 pr-3', children: String(p.kind ?? '—') }),
            jsx('td', { className: 'py-1 pr-3', children: String(p.status ?? '—') }),
            jsx('td', { className: 'py-1 pr-3', children: p.attempts != null ? String(p.attempts) : '—' }),
            jsx('td', { className: 'py-1', children: p.ttl != null ? `${p.ttl}s` : '—' })
          ]
        }, `p${i}`)
      ) })
    ]
  })
}

function EventRows({ events, maxHeight = 320 }) {
  if (!events.length) return jsx(Empty, { children: 'no events — the harness is still quiet.' })
  return jsx('div', {
    className: 'text-xs font-mono overflow-auto',
    style: { maxHeight },
    children: events.map((e, i) =>
      jsxs('div', {
        className: 'py-0.5 border-b border-border whitespace-nowrap',
        children: [
          jsx('span', { className: 'text-(--ui-text-secondary) mr-2', children: fmtTs(e.ts) }),
          jsx('span', { className: 'text-(--ui-text-secondary) mr-2', children: shorten(e.session, 14) }),
          jsx('span', { className: 'font-semibold mr-2', children: String(e.kind ?? '?') }),
          jsx('span', { className: 'text-(--ui-text-secondary)', children: shorten(e.payload, 110) })
        ]
      }, `e${i}`)
    )
  })
}

function AllowanceBlock({ allowance }) {
  if (!allowance) {
    return jsx(Empty, { children: 'no spend data (treasury is owner-local only, test money).' })
  }
  const parts = Object.entries(allowance.by_status || {}).map(([k, v]) => `${k}: ${v}`)
  return jsxs('div', {
    className: 'text-sm',
    children: [
      jsx('div', { className: 'text-(--ui-text-secondary)', children: 'Test money — no payment path in the code.' }),
      jsx('div', { className: 'mt-1 font-mono text-xs', children: parts.length ? parts.join(' · ') : 'no entries' })
    ]
  })
}

// -- panel tabs ----------------------------------------------------------------

function LiveTab({ data }) {
  const kinds = data?.kinds || {}
  const pauses = data?.pauses || []
  const recent = data?.recent || []
  const active = pauses.filter((p) => ['requested', 'researching', 'ready'].includes(p.status)).length
  const kindList = Object.entries(kinds).slice(0, 14)
  return jsxs(Fragment, { children: [
    jsxs('div', { className: 'grid gap-3', style: { gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))' }, children: [
      kpi('Pauses active', active, `${pauses.length} in history`),
      kpi('Delivered', kinds['pause_delivered'] || 0, 'Redirects in the loop'),
      kpi('Guard-Checks', kinds['guard_check'] || 0, 'Completion channel'),
      kpi('Claims', kinds['claim'] || 0, 'observed')
    ] }),
    jsxs('div', { className: 'grid gap-6 mt-6', style: { gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }, children: [
      jsx(Section, { title: 'Event types', children: jsx('div', {
        className: 'text-xs font-mono',
        children: kindList.length
          ? kindList.map(([k, n], i) => jsx('div', { className: 'py-0.5 border-b border-border', children: `${String(k).padEnd(28, ' ')} ${n}` }, `k${i}`))
          : jsx(Empty, { children: 'no events yet' })
      }) }),
      jsx(Section, { title: 'Allowance (test money)', children: jsx(AllowanceBlock, { allowance: data?.allowance }) })
    ] }),
    jsx('div', { className: 'mt-6', children: jsx(Section, { title: 'Recent events', children: jsx(EventRows, { events: recent }) }) })
  ] })
}

function PausenTab({ data }) {
  const pauses = data?.pauses || []
  const recent = (data?.recent || []).filter((e) => /pause|guard|redirect|research/.test(String(e.kind || '')))
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Pause → Research → Redirect → Resume',
      hint: "Triggers only on concrete signals (2× loop/burst, cooldown 60 s, max 4/h). Delivered exactly once (ready → delivered); the agent gets a condensed redirect instead of raw research.",
      children: jsx(PauseRows, { pauses })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Recent pause/guard events',
      children: jsx(EventRows, { events: recent, maxHeight: 260 })
    }) })
  ] })
}

function MemoryTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: [ID, 'memory'],
    queryFn: () => rest('/memory?limit=60'),
    staleTime: 10_000
  })
  if (isLoading && !data) return jsx(Empty, { children: 'loading memory …' })
  if (error) return jsx(Empty, { children: 'memory.db not reachable (write path stays with the agent).' })
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Incidents',
      hint: 'Recurring error classes (deduplicated by signature) with confirmation counter.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'title', label: 'Title', max: 60 },
          { key: 'kind', label: 'Kind' },
          { key: 'status', label: 'Status' },
          { key: 'occurrences', label: '×' },
          { key: 'confidence', label: 'conf' },
          { key: 'last_seen', label: 'last seen', fmt: (v) => fmtDay(v) }
        ],
        rows: data?.incidents || [],
        empty: 'no incidents — clean.'
      })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Research notes',
      hint: 'With source, trust level and validity — the basis for grounded citations.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'topic', label: 'Topic', max: 44 },
          { key: 'claim', label: 'Claim', max: 70 },
          { key: 'source_type', label: 'Source' },
          { key: 'trust', label: 'trust' },
          { key: 'status', label: 'Status' },
          { key: 'created', label: 'created', fmt: (v) => fmtDay(v) }
        ],
        rows: data?.notes || [],
        empty: 'no notes.'
      })
    }) })
  ] })
}

function SkillsTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: [ID, 'skills'],
    queryFn: () => rest('/skills?limit=60'),
    staleTime: 10_000
  })
  if (isLoading && !data) return jsx(Empty, { children: 'loading skills …' })
  if (error) return jsx(Empty, { children: 'Skill store not reachable.' })
  return jsx(Section, {
    title: 'Skill candidates',
    hint: 'Standalone chains (≥ 3 repetitions across ≥ 2 sessions) are proposed, never activated automatically. applied/succeeded counts consumption.',
    children: jsx(SimpleTable, {
      columns: [
        { key: 'name', label: 'Name', max: 34 },
        { key: 'title', label: 'Description', max: 56 },
        { key: 'status', label: 'Status' },
        { key: 'occurrences', label: '×' },
        { key: 'sessions', label: 'Sessions' },
        { key: 'applied', label: 'applied' },
        { key: 'succeeded', label: 'succeeded' },
        { key: 'updated', label: 'updated', fmt: (v) => fmtDay(v) }
      ],
      rows: data?.skills || [],
      empty: 'no candidates yet — skill mining collects from 3 repetitions across 2 sessions.'
    })
  })
}

function TreasuryTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: [ID, 'treasury'],
    queryFn: () => rest('/treasury?limit=60'),
    staleTime: 10_000
  })
  if (isLoading && !data) return jsx(Empty, { children: 'loading treasury …' })
  if (error) return jsx(Empty, { children: 'Treasury ledger not reachable.' })
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Spend requests (test money)',
      hint: 'Pure mechanism, no real payment path. Tier 0 = human confirms first; amounts are validated for finiteness/positivity.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'created', label: 'created', fmt: (v) => fmtDay(v) },
          { key: 'amount', label: 'Amount', fmt: (v, row) => (v == null ? '—' : `${Number(v).toFixed(2)} ${row.currency || 'EUR'}`) },
          { key: 'merchant', label: 'Payee', max: 30 },
          { key: 'reason', label: 'Purpose', max: 44 },
          { key: 'status', label: 'Status' }
        ],
        rows: data?.spends || [],
        empty: 'no spend requests.'
      })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Grants (Handles)',
      hint: 'The agent never sees raw secrets — only secret:// handles, approval via grant + TOTP.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'created', label: 'created', fmt: (v) => fmtDay(v) },
          { key: 'handle', label: 'Handle', max: 40 },
          { key: 'status', label: 'Status' },
          { key: 'decided', label: 'decided', fmt: (v) => fmtDay(v) }
        ],
        rows: data?.grants || [],
        empty: 'no grants.'
      })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(CredentialsPanel, { key: 'credentials' }) })
  ] })
}

// -- models: per-role provider/model, on top of Hermes' own registry ----------

const ROLE_LABELS = {
  main: 'Main Agent',
  watcher: 'Watcher / Triage',
  investigator: 'Investigator / Research',
  judge: 'Judge / Verification',
  fallback: 'Fallback'
}

function roleChip(providerRow) {
  if (!providerRow) {
    return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: 'not configured' })
  }
  const cached = providerRow.source === 'hermes-cache'
  return jsx('span', {
    className: `text-xs rounded px-1.5 py-0.5 border border-border ${cached ? 'text-foreground' : 'text-(--ui-text-secondary)'}`,
    children: cached ? '✓ provider live (Hermes cache)' : '○ Hermes config only'
  })
}

function ModelSelect({ providerRow, value, onChange }) {
  const models = providerRow?.models || []
  if (!models.length) {
    return jsx('input', {
      className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-56',
      placeholder: 'model name',
      value: value || '',
      onChange: (event) => onChange(event.target.value),
      spellCheck: false
    })
  }
  const options = value && !models.includes(value) ? [value, ...models] : models
  return jsx('select', {
    className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-56',
    value: value || '',
    onChange: (event) => onChange(event.target.value),
    children: [
      jsx('option', { value: '', children: '— choose a model —' }),
      ...options.map((name) => jsx('option', { value: name, children: name }, name))
    ]
  })
}

function StrictnessRow({ strictness, onSaved }) {
  const [save, setSave] = useState(null)
  const level = strictness?.level || 'standard'
  const options = strictness?.options || ['relaxed', 'standard', 'strict']
  const setLevel = async (next) => {
    setSave({ busy: true })
    try {
      await rest('/models/strictness', { method: 'POST', body: { level: next } })
      setSave({ ok: true })
      onSaved?.()
    } catch (err) {
      setSave({ ok: false, error: String(err?.message || err) })
    }
  }
  return jsx('div', { className: 'mt-4 border border-border rounded p-3', children: [
    jsx('div', { className: 'text-sm font-medium', children: 'Voice strictness' }, 'label'),
    jsx('div', {
      className: 'text-xs text-(--ui-text-secondary) mt-1',
      children: 'How early the voice intervenes — relaxed waits longer, strict pauses sooner (applies immediately to new signals).'
    }, 'hint'),
    jsx('div', { className: 'flex items-center gap-2 mt-2', children: [
      ...options.map((name) => jsx('button', {
        key: name,
        className: `text-xs rounded px-2 py-1 border ${name === level ? 'border-[#f5be36] text-[#f5be36]' : 'border-border text-(--ui-text-secondary)'}`,
        onClick: () => void setLevel(name),
        children: name
      }, name)),
      save?.ok ? jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: 'saved' }, 'saved') : null,
      save && save.ok === false ? jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: save.error || 'failed' }, 'err') : null
    ].filter(Boolean) }, 'opts')
  ] })
}

function RoleRow({ role, label, hint, providers, current, onSaved }) {
  const [provider, setProvider] = useState(current?.provider || '')
  const [model, setModel] = useState(current?.model || '')
  const [save, setSave] = useState(null)
  const [test, setTest] = useState(null)
  const providerRow = providers.find((row) => row.slug === provider)

  const doSave = async () => {
    setSave({ busy: true })
    try {
      const result = await rest('/models/roles', {
        method: 'POST',
        body: { role, provider, model }
      })
      setSave({ ok: true, result })
      onSaved?.()
    } catch (err) {
      setSave({ ok: false, error: String(err?.message || err) })
    }
  }

  const doTest = async () => {
    setTest({ busy: true })
    try {
      const result = await rest('/models/test', {
        method: 'POST',
        body: { provider, model }
      })
      setTest({ ok: result?.ok === true, result })
    } catch (err) {
      setTest({ ok: false, error: String(err?.message || err) })
    }
  }

  const testLine = () => {
    if (!test) return null
    if (test.busy) return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: 'testing …' })
    if (!test.ok) {
      const detail = test.error || test.result?.error || 'error'
      return jsx('span', {
        className: 'text-xs',
        style: { color: 'var(--ui-danger, #ff6b7d)' },
        children: `✗ ${shorten(detail, 90)}`
      })
    }
    const r = test.result
    const parts = ['✓ auth', r.response ? '✓ response' : '— response']
    parts.push(r.tool_call === true ? '✓ tool-call' : r.tool_call === false ? '✗ tool-call' : '— tool-call')
    if (r.latency_ms != null) parts.push(`${r.latency_ms} ms`)
    return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: parts.join(' · ') })
  }

  return jsxs('div', {
    className: 'rounded-lg border border-border bg-card px-4 py-3',
    children: [
      jsxs('div', { className: 'flex items-baseline gap-2 flex-wrap', children: [
        jsx('div', { className: 'text-sm font-semibold w-48', children: label }),
        roleChip(providerRow),
        hint ? jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: hint }) : null
      ] }),
      jsxs('div', { className: 'flex items-center gap-2 mt-2 flex-wrap', children: [
        jsx('select', {
          className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-40',
          value: provider,
          onChange: (event) => { setProvider(event.target.value); setModel('') },
          children: [
            jsx('option', { value: '', children: '— Provider —' }),
            ...providers.map((row) => jsx('option', { value: row.slug, children: row.slug }, row.slug))
          ]
        }),
        jsx(ModelSelect, { providerRow, value: model, onChange: setModel }),
        jsx('button', {
          className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover) disabled:opacity-40',
          disabled: !provider || !model,
          onClick: doTest,
          children: 'Test model'
        }),
        jsx('button', {
          className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover) disabled:opacity-40',
          disabled: !provider || !model,
          onClick: doSave,
          children: 'Save'
        })
      ] }),
      jsx('div', { className: 'mt-1 flex gap-3 flex-wrap', children: [
        testLine(),
        save?.busy ? jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: 'saving …' }) : null,
        save?.ok
          ? jsx('span', {
              className: 'text-xs text-(--ui-text-secondary)',
              children: `✓ saved${save.result?.model_listed === false ? ' (model not in the live list — please test)' : ''}`
            })
          : null,
        save?.ok === false
          ? jsx('span', { className: 'text-xs', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: `✗ ${shorten(save.error, 80)}` })
          : null
      ] })
    ]
  })
}

function ModelsTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'models'],
    queryFn: () => rest('/models'),
    staleTime: 8_000
  })
  if (isLoading && !data) return jsx(Empty, { children: 'loading models …' })
  if (error) return jsx(Empty, { children: 'Models backend not reachable.' })
  const providers = data?.providers || []
  const roles = data?.roles || {}
  const hermes = data?.hermes || {}
  const fallbacks = (hermes.fallbacks || []).map((f) => `${f.provider}/${f.model}`).join(' · ')
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Origin — Hermes registry',
      hint: `Providers, auth and live model lists come from Hermes (${data?.roles_file || '…'}). Skaldir Agent only stores names per role — no secrets, no provider infrastructure of its own.`,
      children: jsxs('div', { className: 'flex gap-2 flex-wrap text-sm', children: [
        jsx('span', {
          className: 'rounded px-2 py-1 border border-border bg-card',
          children: `Hermes Default: ${hermes.provider || '—'}/${hermes.model || '—'}`
        }),
        fallbacks
          ? jsx('span', { className: 'rounded px-2 py-1 border border-border bg-card', children: `Hermes Fallback: ${fallbacks}` })
          : null
      ] })
    }),
    jsx('div', {
      className: 'mt-4 text-xs text-(--ui-text-secondary)',
      children: "The main-agent model belongs to Hermes (above, change via `hermes model`) — the roles below apply to Skaldir Agent only."
    }),
    jsx('div', { className: 'grid gap-3 mt-3', children: [
      jsx(RoleRow, { role: 'main', label: ROLE_LABELS.main, hint: 'the working agent — the model your chats run on', providers, current: roles.main, onSaved: refetch }, 'main'),
      jsx(RoleRow, { role: 'watcher', label: ROLE_LABELS.watcher, hint: 'cheap & fast — anomaly detection', providers, current: roles.watcher, onSaved: refetch }, 'watcher'),
      jsx(RoleRow, { role: 'investigator', label: ROLE_LABELS.investigator, hint: 'research & redirect construction', providers, current: roles.investigator, onSaved: refetch }, 'investigator'),
      jsx(RoleRow, { role: 'judge', label: ROLE_LABELS.judge, hint: 'verification (guard LLM channel)', providers, current: roles.judge, onSaved: refetch }, 'judge'),
      jsx(RoleRow, { role: 'fallback', label: ROLE_LABELS.fallback, hint: 'applies when a role call fails', providers, current: roles.fallback, onSaved: refetch }, 'fallback')
    ] }),
    jsx(StrictnessRow, { key: 'strictness', strictness: data?.strictness ?? null, onSaved: refetch }),
    jsx('div', {
      className: 'mt-4 text-xs text-(--ui-text-secondary)',
      children: "Roles without an entry fall back to Hermes' default resolution. The test calls the real model through Hermes' LLM runtime (no key leaves the host, no key is shown)."
    }),
    jsx('div', {
      className: 'mt-2 text-xs text-(--ui-text-secondary)',
      children: "Provider credentials live in the Treasury — Skaldir Agent only ever sees secret:// handles. Add or replace keys there (Treasury tab); exporting a key from here is impossible by design."
    })
  ] })
}

// -- credentials (treasury mutations, local-origin only, never values back) ---

function CredentialsPanel() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'credentials'],
    queryFn: () => rest('/treasury/credentials'),
    staleTime: 8_000
  })
  const [form, setForm] = useState({ service: '', name: '', secret: '', description: '' })
  const [busy, setBusy] = useState(null)
  const [msg, setMsg] = useState(null)
  const [testResult, setTestResult] = useState(null)

  const add = async () => {
    setBusy(true); setMsg(null)
    try {
      const result = await rest('/treasury/credentials', { method: 'POST', body: form })
      setMsg({ ok: true, text: `✓ saved — handle ${result?.handle} (the value can no longer be read)` })
      setForm({ service: '', name: '', secret: '', description: '' })
      refetch()
    } catch (err) {
      setMsg({ ok: false, text: `✗ ${shorten(String(err?.message || err), 140)}` })
    } finally { setBusy(false) }
  }

  const revoke = async (handle) => {
    if (!globalThis.confirm(`Remove credential ${handle}? Grants for it become invalid.`)) return
    try {
      await rest('/treasury/credentials/revoke', { method: 'POST', body: { handle } })
      refetch()
    } catch (err) { setMsg({ ok: false, text: `✗ ${shorten(String(err?.message || err), 140)}` }) }
  }

  const test = async (handle) => {
    setMsg(null)
    setTestResult({ handle, pending: true })
    try {
      const result = await rest('/treasury/credentials/test', { method: 'POST', body: { handle } })
      setTestResult(result)
    } catch (err) {
      setTestResult({ handle, ok: false, denied: true, detail: shorten(String(err?.message || err), 140) })
    }
  }

  if (isLoading && !data) return jsx(Empty, { children: 'loading credentials …' })
  if (error) return jsx(Empty, { children: 'Credentials backend not reachable.' })
  const available = data?.available !== false
  const creds = data?.credentials || []
  const grants = data?.grants || []

  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'API credentials (handles)',
      hint: 'The value is stored encrypted and never shown again — tools only receive it at runtime via a grant.',
      children: available
        ? jsxs(Fragment, { children: [
            jsxs('div', { className: 'flex gap-2 flex-wrap items-end', children: [
              jsx('input', { className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-32', placeholder: 'service', value: form.service, onChange: (e) => setForm({ ...form, service: e.target.value }), spellCheck: false }),
              jsx('input', { className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-32', placeholder: 'name', value: form.name, onChange: (e) => setForm({ ...form, name: e.target.value }), spellCheck: false }),
              jsx('input', { className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-56', placeholder: 'secret value', type: 'password', value: form.secret, onChange: (e) => setForm({ ...form, secret: e.target.value }), spellCheck: false }),
              jsx('input', { className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-48', placeholder: 'description (optional)', value: form.description, onChange: (e) => setForm({ ...form, description: e.target.value }) }),
              jsx('button', {
                className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover) disabled:opacity-40',
                disabled: busy || !form.service || !form.name || !form.secret,
                onClick: add,
                children: busy ? 'saving …' : '+ Add credential'
              })
            ] }),
            msg ? jsx('div', { className: 'text-xs mt-2', style: msg.ok ? undefined : { color: 'var(--ui-danger, #ff6b7d)' }, children: msg.text }) : null,
            jsx('div', { className: 'mt-3', children: jsx(SimpleTable, {
              columns: [
                { key: 'handle', label: 'Handle', max: 44 },
                { key: 'service', label: 'Service' },
                { key: 'scopes', label: 'Scopes', fmt: (v) => (v || []).join(', ') },
                { key: 'used_by', label: 'used by', fmt: (v) => ((v || []).length ? v.join(', ') : '—') },
                { key: 'handle', label: '', fmt: (v) => jsxs('div', { className: 'flex gap-2', children: [
                    jsx('button', { className: 'text-xs underline', onClick: () => test(v), children: 'test' }),
                    jsx('button', { className: 'text-xs underline', onClick: () => revoke(v), children: 'remove' })
                  ] }) }
              ],
              rows: creds,
              empty: 'no credentials yet — add one above.'
            }) }),
            testResult ? jsx('div', {
              className: 'text-xs mt-2',
              style: testResult.ok === false ? { color: 'var(--ui-danger, #ff6b7d)' } : undefined,
              children: testResult.pending
                ? `… testing ${testResult.handle} (decryption + injection into an isolated process)`
                : testResult.ok
                  ? `✓ ${testResult.handle}: handle decryptable, the key only reaches the isolated subprocess — no plaintext leaves it. (${testResult.detail || 'ok'})`
                  : `✗ ${testResult.handle}: ${testResult.detail || 'test failed'}`
            }) : null
          ] })
        : jsx('div', {
            className: 'text-sm text-(--ui-text-secondary)',
            children: 'Vault locked: GODSV_VAULT_KEY is not in the gateway environment. Set it before starting the app (e.g. export GODSV_VAULT_KEY=…) and restart the app.'
          })
    }),
    jsx('div', { className: 'mt-4', children: jsx(Section, {
      title: 'Tool grants',
      hint: 'A grant allows exactly ONE tool to use the handle — no wildcards. Revoke removes access immediately.',
      children: jsx(GrantTable, { grants, onChanged: refetch })
    }) })
  ] })
}

function GrantTable({ grants, onChanged }) {
  const [msg, setMsg] = useState(null)
  const revoke = async (grantId) => {
    if (!globalThis.confirm('Revoke grant? The tool loses credential access immediately.')) return
    try {
      await rest('/treasury/grants/revoke', { method: 'POST', body: { grant_id: grantId } })
      onChanged?.()
    } catch (err) { setMsg(`✗ ${shorten(String(err?.message || err), 120)}`) }
  }
  return jsxs(Fragment, { children: [
    jsx(SimpleTable, {
      columns: [
        { key: 'created', label: 'created', fmt: (v) => fmtDay(v) },
        { key: 'handle', label: 'Handle', max: 40 },
        { key: 'tool_id', label: 'Tool', max: 24, fmt: (v) => v || '— (global)' },
        { key: 'status', label: 'Status' },
        { key: 'id', label: '', fmt: (v, row) => (row.status === 'approved'
            ? jsx('button', { className: 'text-xs underline', onClick: () => revoke(v), children: 'revoke' })
            : null) }
      ],
      rows: grants,
      empty: 'no grants.'
    }),
    msg ? jsx('div', { className: 'text-xs mt-1', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: msg }) : null
  ] })
}

/** §7/§10: scope of an installed tool at a glance — never a permission. */
function toolsScopeCell(scope, row) {
  const s = String(scope || '')
  const candidate = !!(row && row.promotion && row.promotion.candidate)
  const label =
    s === 'global' ? (row && row.promoted_from ? 'Global ↑' : 'Global')
      : s === 'project' ? ('project' + (candidate ? ' · candidate' : ''))
      : s === 'legacy' ? 'Legacy'
      : '—'
  const bits = []
  if (row && row.project_id) bits.push(`project: ${row.project_id}`)
  if (row && row.promoted_from) {
    bits.push(
      `promoted from ${row.promoted_from.project_id} · ${(row.promoted_from.evidence_projects || []).length} evidence`
    )
  }
  if (candidate && row.promotion.blockers && row.promotion.blockers.length) {
    bits.push(`missing: ${row.promotion.blockers.join('; ')}`)
  }
  return jsx('span', {
    className: 'text-xs' + (s === 'global' ? '' : ' text-(--ui-text-secondary)'),
    style: s === 'global' ? { color: 'var(--ui-text-primary, #f5f5f5)' } : undefined,
    title: bits.join(' · ') || undefined,
    children: label
  })
}

// -- tools: installed / generated / build queue / MCP + detail drilldown ------

function toolsStamp(value) {
  if (!value) return '—'
  return fmtDay(value)
}

function CapabilityCard({ req, onOpen }) {
  const status = req.status || 'open'
  const build = req.build
  const stages = (build && build.stages) || []
  // The running badge IS the one indicator, projected from the build's own last
  // observed stage (`toolbuilder.STAGES`) — no second spinner, and no invented
  // progress: an open request with no recorded stage yet is QUEUED, so the
  // honest verb is `breathing` (waiting) — never `working`.
  const lastStage = stages.length ? String(stages[stages.length - 1] || '') : ''
  const badge = status === 'fulfilled'
    ? { label: '✓ ready' }
    : status === 'failed'
      ? { label: '✗ failed', color: 'var(--ui-danger, #ff6b7d)' }
      : { label: 'running', orb: gvOrbBuildStage(lastStage) || 'breathing' }
  const secrets = req.required_secrets || []
  const domains = req.allowed_domains || []
  const chip = (stage, index) => jsx('span', {
    key: `st-${index}-${stage}`,
    className: `text-xs rounded px-1.5 py-0.5 border border-border ${stage === 'ready' ? 'text-foreground' : 'text-(--ui-text-secondary)'}`,
    style: stage === 'failed' ? { color: 'var(--ui-danger, #ff6b7d)' } : undefined,
    children: stage
  })
  return jsxs('div', { className: 'border border-border rounded p-3 space-y-1.5', children: [
    jsxs('div', { className: 'flex items-start justify-between gap-3', children: [
      jsxs('div', { className: 'min-w-0', children: [
        jsx('div', { className: 'text-xs tracking-wide text-(--ui-text-secondary)', children: 'CAPABILITY REQUEST' }),
        jsx('div', { className: 'font-medium truncate', children: req.capability || '—' })
      ] }),
      jsxs('span', {
        className: 'inline-flex items-center gap-1.5 text-xs shrink-0',
        style: badge.color ? { color: badge.color } : undefined,
        children: [
          badge.orb
            ? jsx(GvOrb, { state: badge.orb, label: req.capability || 'Capability build' })
            : null,
          jsx('span', { children: badge.label })
        ]
      })
    ] }),
    jsx('div', {
      className: 'text-xs text-(--ui-text-secondary)',
      children: [`requested by: ${req.requested_by || 'main-agent'}`, req.kind ? ` · ${req.kind}` : '', req.name ? ` · Tool: ${req.name}` : ''].join('')
    }),
    req.reason ? jsx('div', { className: 'text-sm', children: req.reason }) : null,
    (secrets.length || domains.length) ? jsx('div', {
      className: 'text-xs text-(--ui-text-secondary)',
      children: [
        secrets.length ? `Secrets (Handles): ${secrets.join(', ')}` : '',
        secrets.length && domains.length ? ' · ' : '',
        domains.length ? `Domains: ${domains.join(', ')}` : ''
      ].join('')
    }) : null,
    build ? jsxs('div', { className: 'space-y-1', children: [
      jsx('div', {
        className: 'text-xs text-(--ui-text-secondary)',
        children: `Build ${build.build_id || ''}${build.ok === false ? ' — failed' : ''}`
      }),
      jsx('div', { className: 'flex flex-wrap gap-1', children: (build.stages || []).map(chip) })
    ] }) : null,
    req.error ? jsx('div', {
      className: 'text-xs',
      style: { color: 'var(--ui-danger, #ff6b7d)' },
      children: String(req.error).slice(0, 200)
    }) : null,
    req.result_tool ? jsx('button', {
      className: 'text-xs underline',
      onClick: () => onOpen(req.result_tool),
      children: `↗ open ${req.result_tool}`
    }) : null
  ] })
}

function ToolsTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: [ID, 'tools'],
    queryFn: () => rest('/tools'),
    staleTime: 6_000
  })
  const [openTool, setOpenTool] = useState(null)
  if (openTool) return jsx(ToolDetail, { name: openTool, onBack: () => setOpenTool(null) })
  if (isLoading && !data) return jsx(Empty, { children: 'loading tools …' })
  if (error) return jsx(Empty, { children: 'Tools backend not reachable.' })
  const installed = data?.installed || []
  const queue = data?.queue || []
  const requests = data?.requests || []
  const mcp = data?.mcp_servers || []

  // The queue's Stage cell is the build's CURRENT phase: while it is a phase
  // and not a terminal outcome it carries the one orb (projected from the same
  // stage string the cell prints); `ready`/`failed`/… stay static.
  const stageChip = (stage) => {
    const orb = gvOrbBuildStage(stage)
    return jsxs('span', {
      key: `s-${stage}`,
      className: `inline-flex items-center gap-1.5 text-xs rounded px-1.5 py-0.5 border border-border ${stage === 'ready' ? 'text-foreground' : stage === 'failed' ? '' : 'text-(--ui-text-secondary)'}`,
      style: stage === 'failed' ? { color: 'var(--ui-danger, #ff6b7d)' } : undefined,
      children: [
        orb ? jsx(GvOrb, { state: orb, label: `Build stage: ${stage}` }) : null,
        jsx('span', { children: stage })
      ]
    })
  }

  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Capability Requests',
      hint: 'Capabilities reported missing by the main agent — the build runs during the voice pause; here: status and result only (no build transcript).',
      children: requests.length
        ? jsx('div', {
            className: 'space-y-3',
            children: requests.map((req) => jsx(CapabilityCard, { key: req.request_id, req, onOpen: setOpenTool }))
          })
        : jsx('div', {
            className: 'text-sm text-(--ui-text-secondary)',
            children: 'no capability requests — the agent has everything it needs.'
          })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Installed',
      hint: 'Tool registry (propose-first): only ✓ approved after gates. Click opens details with actions.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'name', label: 'Name', fmt: (v) => jsx('button', { className: 'underline', onClick: () => setOpenTool(v), children: v }) },
          { key: 'type', label: 'Type', fmt: (v, row) => v || row.kind },
          { key: 'scope', label: 'Scope', fmt: (v, row) => toolsScopeCell(v, row) },
          { key: 'version', label: 'v', fmt: (v) => (v == null ? '—' : String(v)) },
          { key: 'status', label: 'Status' },
          { key: 'source', label: 'Origin', max: 26 },
          { key: 'last_tested_at', label: 'last test', fmt: (v) => toolsStamp(v) },
          { key: 'usage_count', label: 'Calls', fmt: (v) => (v == null ? '—' : String(v)) },
          { key: 'required_secrets', label: 'Secrets', fmt: (v) => ((v || []).length ? v.join(', ') : '—') },
          { key: 'risk_level', label: 'Risk', fmt: (v) => v || '—' }
        ],
        rows: installed,
        empty: 'no tools registered.'
      })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Build Queue',
      hint: 'Real pipeline stages of the autonomous builder (ToolBuilder) — no progress theater.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'build_id', label: 'Build', max: 22 },
          { key: 'capability', label: 'Capability', max: 46 },
          { key: 'stage', label: 'Stage', fmt: (v) => stageChip(v) },
          { key: 'stages', label: 'History', fmt: (v) => (v || []).map((s) => s.stage).join(' → '), max: 80 }
        ],
        rows: queue,
        empty: 'no builds yet.'
      })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'MCP-Server',
      hint: "From the Hermes configuration (mcp_servers) — transport and scope, no secrets.",
      children: mcp.length
        ? jsx(SimpleTable, {
            columns: [
              { key: 'name', label: 'Server' },
              { key: 'transport', label: 'Transport' },
              { key: 'tool_count', label: 'Tools', fmt: (v) => (v == null ? '—' : String(v)) },
              { key: 'auth_configured', label: 'Auth', fmt: (v) => (v ? '✓' : '—') },
              { key: 'enabled', label: 'active', fmt: (v) => (v ? '✓' : 'off') }
            ],
            rows: mcp,
            empty: 'no MCP servers.'
          })
        : jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'no MCP servers in the Hermes configuration (mcp_servers).' })
    }) })
  ] })
}

function ToolDetail({ name, onBack }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'tool', name],
    queryFn: () => rest(`/tools/${encodeURIComponent(name)}`),
    staleTime: 4_000
  })
  const credsQuery = useQuery({
    queryKey: [ID, 'credentials'],
    queryFn: () => rest('/treasury/credentials'),
    staleTime: 15_000
  })
  const [action, setAction] = useState(null)
  const [grantHandle, setGrantHandle] = useState('')
  if (isLoading && !data) return jsx(Empty, { children: 'loading tool …' })
  if (error) return jsx(Empty, { children: 'tool not found.' })
  const spec = data?.spec || {}
  const manifest = data?.manifest || {}
  const grants = data?.grants || []
  const builds = data?.builds || []
  const credits = (credsQuery.data?.credentials || [])

  const run = async (act) => {
    if (act === 'disable' && !globalThis.confirm('Disable tool? It leaves the MCP surface and the execute path.')) return
    setAction({ busy: act })
    try {
      const result = await rest(`/tools/${encodeURIComponent(name)}/action`, { method: 'POST', body: { action: act } })
      setAction({ ok: result?.ok !== false, act, result })
      refetch()
    } catch (err) {
      setAction({ ok: false, act, error: String(err?.message || err) })
    }
  }

  const grant = async () => {
    if (!grantHandle) return
    setAction({ busy: 'grant' })
    try {
      await rest('/treasury/grants', { method: 'POST', body: { handle: grantHandle, tool_id: name, ttl: 3600 } })
      setAction({ ok: true, act: 'grant', result: { grant_id: 'ok' } })
      setGrantHandle('')
      refetch()
    } catch (err) {
      setAction({ ok: false, act: 'grant', error: String(err?.message || err) })
    }
  }

  const actionLine = () => {
    if (!action) return null
    if (action.busy) return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: `${action.busy} …` })
    if (!action.ok) return jsx('span', { className: 'text-xs', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: `✗ ${shorten(action.error || JSON.stringify(action.result), 140)}` })
    if (action.act === 'test') return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: `✓ test run: ${shorten(String(action.result?.output || ''), 140)}` })
    if (action.act === 'recheck') {
      const checks = action.result?.checks || []
      return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: `✓ Gates: ${checks.map((c) => `${c.name}${c.ok ? '✓' : '✗'}`).join(' · ')}` })
    }
    if (action.act === 'grant') return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: '✓ grant issued' })
    return jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: `✓ ${action.act}` })
  }

  const row = (label, value) => jsxs('div', { className: 'flex gap-2 text-sm', children: [
    jsx('div', { className: 'w-44 text-(--ui-text-secondary)', children: label }),
    jsx('div', { className: 'flex-1', children: value })
  ] })

  const usage = data?.usage || {}

  return jsxs(Fragment, { children: [
    jsx('button', { className: 'text-sm underline mb-3', onClick: onBack, children: '← back to Tools' }),
    jsx(Section, { title: `Tool: ${name}`, hint: spec.description || spec.title || '', children: jsxs('div', { className: 'grid gap-1', children: [
      row('Status', spec.status || '—'),
      row('Type', `${manifest.type || '—'} (${spec.kind || '—'})`),
      row('Origin', spec.provenance || '—'),
      row('Purpose', spec.rationale || manifest.purpose || '—'),
      row('Risk', manifest.risk_level || '—'),
      row('Secrets (Handles)', (manifest.required_secrets || []).join(', ') || '—'),
      row('Network', (manifest.network?.allowed_domains || []).join(', ') || '—'),
      row('Dependencies', (manifest.dependencies || []).join(', ') || '— (stdlib)'),
      row('Tests', (manifest.tests || []).join(' ') || '—'),
      row('Scanner', spec.scan_summary || '—'),
      row('Last test', toolsStamp(manifest.last_tested_at)),
      row('Wrapper', data?.wrapper || '—')
    ] }) }),
    jsx('div', { className: 'mt-4', children: jsx(Section, {
      title: 'Usage & Errors',
      hint: 'Usage ledger (tool_usage.jsonl): every wrapper call counted — metadata, no payloads, error texts redacted.',
      children: usage.count
        ? jsxs('div', { className: 'grid gap-1', children: [
            row('Calls', String(usage.count)),
            row('Successes / failures', `${usage.ok} / ${usage.failed}`),
            row('Success rate', usage.success_rate == null ? '—' : `${Math.round(usage.success_rate * 100)} %`),
            row('Avg duration', usage.avg_duration_ms == null ? '—' : `${usage.avg_duration_ms} ms`),
            row('Last used', toolsStamp(usage.last_used)),
            (usage.last_errors || []).length
              ? row('Last errors', jsx('div', { className: 'space-y-1', children: (usage.last_errors || []).map((entry, index) => jsx('div', {
                  key: `e-${index}`,
                  className: 'text-xs break-all',
                  children: entry
                })) }))
              : null
          ].filter(Boolean) })
        : jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'no calls recorded yet.' })
    }) }),
    jsx('div', { className: 'mt-4 flex gap-2 flex-wrap', children: [
      jsx('button', { className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover)', onClick: () => run('test'), children: 'Test' }),
      jsx('button', { className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover)', onClick: () => run(spec.status === 'approved' ? 'disable' : 'enable'), children: spec.status === 'approved' ? 'Disable' : 'Enable' }),
      jsx('button', { className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover)', onClick: () => run('recheck'), children: 'Re-run gates' }),
      ...(credits.length ? [
        jsx('select', {
          key: 'grant-select',
          className: 'bg-(--card) border border-border rounded px-2 py-1 text-sm w-56',
          value: grantHandle,
          onChange: (e) => setGrantHandle(e.target.value),
          children: [
            jsx('option', { value: '', children: '— credential handle —' }),
            ...credits.map((c) => jsx('option', { value: c.handle, children: c.handle }, c.handle))
          ]
        }),
        jsx('button', { key: 'grant-btn', className: 'text-sm rounded px-3 py-1 border border-border hover:bg-(--chrome-action-hover) disabled:opacity-40', disabled: !grantHandle, onClick: grant, children: 'Grant credential' })
      ] : [])
    ] }),
    actionLine() ? jsx('div', { className: 'mt-2', children: actionLine() }) : null,
    jsx('div', { className: 'mt-4', children: jsx(Section, {
      title: 'Grants for this tool',
      hint: 'Only grants with exactly this tool_id count for runtime access.',
      children: jsx(GrantTable, { grants, onChanged: refetch })
    }) }),
    jsx('div', { className: 'mt-4', children: jsx(Section, {
      title: 'Build history',
      hint: 'Builder stages for this tool.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'build_id', label: 'Build', max: 22 },
          { key: 'stages', label: 'Stages', fmt: (v) => (v || []).map((s) => s.stage).join(' → '), max: 90 },
          { key: 'updated', label: 'last', fmt: (v) => fmtDay(v) }
        ],
        rows: builds,
        empty: 'manually registered (no builder build).'
      })
    }) })
  ] })
}

function ComputerUseTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'cu'],
    queryFn: () => rest('/cu'),
    staleTime: 10_000
  })
  const [busy, setBusy] = useState(false)
  const [lastRun, setLastRun] = useState(null)
  const [actionError, setActionError] = useState(null)
  if (isLoading && !data) return jsx(Empty, { children: 'loading Computer Use …' })
  if (error) return jsx(Empty, { children: 'Computer Use backend not reachable.' })
  const runners = data?.runners || []
  const policy = data?.policy
  const runs = data?.runs || []
  const runDemo = async () => {
    setBusy(true)
    setActionError(null)
    try {
      const result = await rest('/cu/demo', { method: 'POST', body: {} })
      setLastRun(result.run || null)
      refetch()
    } catch (err) {
      setActionError(String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Isolation — runners',
      hint: 'Live probe of the isolation levels on this host. CU never runs unisolated on the real machine.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'name', label: 'Runner' },
          { key: 'status', label: 'Status' }
        ],
        rows: runners,
        empty: 'no runner information.'
      })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Policy',
      hint: 'Order API > DOM > Vision; vision is the last escalation. Limits are fixed policy values.',
      children: policy ? jsxs('div', { className: 'space-y-1.5 text-sm', children: [
        jsxs('div', { children: [
          jsx('span', { className: 'text-(--ui-text-secondary)', children: 'Channel order: ' }),
          jsx('span', { children: policy.channel_order })
        ] }),
        jsx('div', { className: 'flex flex-wrap gap-1', children: (policy.allowed_kinds || []).map((kind) => jsx('span', {
          key: `k-${kind}`,
          className: 'text-xs rounded px-1.5 py-0.5 border border-border text-(--ui-text-secondary)',
          children: kind
        })) }),
        jsxs('div', { className: 'text-(--ui-text-secondary) text-xs', children: [
          `max actions: ${policy.max_actions} · max fallbacks: ${policy.max_fallbacks} · `,
          policy.require_isolation ? 'isolation required' : 'isolation optional'
        ].join('') })
      ] }) : jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'policy not available.' })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Dry-Run (deterministisch)',
      hint: 'Runs the real CU executor with DryRunRunner — no real clicks, real ordering/fallback logic.',
      children: jsxs('div', { className: 'space-y-2', children: [
        jsx('button', {
          className: 'text-sm rounded border border-border px-3 py-1.5 hover:bg-(--chrome-action-hover)',
          disabled: busy,
          onClick: runDemo,
          children: busy ? 'running …' : 'Run demo'
        }),
        actionError ? jsx('div', { className: 'text-sm', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: actionError }) : null,
        lastRun ? jsxs('div', { className: 'text-sm', children: [
          `[${lastRun.status}] channel: ${lastRun.channel || '—'} · actions: ${lastRun.actions} · fallbacks: ${lastRun.fallbacks}`,
          lastRun.detail ? jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: lastRun.detail }) : null
        ] }) : null
      ] })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Recent runs',
      hint: 'Persisted dry-run results (cu_runs.jsonl) — real executions, no demo data.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'ts', label: 'Time', fmt: (v) => toolsStamp(v) },
          { key: 'status', label: 'Status' },
          { key: 'channel', label: 'Channel', fmt: (v) => v || '—' },
          { key: 'actions', label: 'Actions' },
          { key: 'fallbacks', label: 'Fallbacks' }
        ],
        rows: runs.slice().reverse(),
        empty: 'no runs yet.'
      })
    }) })
  ] })
}

function CompletionTab() {
  const sid = useFocusedSessionId()
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'completion', sid || 'latest'],
    queryFn: () => rest(completionPath(sid)),
    staleTime: 5_000
  })
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [goalDraft, setGoalDraft] = useState('')
  const [criteriaDraft, setCriteriaDraft] = useState('')
  if (isLoading && !data) return jsx(Empty, { children: 'loading goal state …' })
  if (error) return jsx(Empty, { children: 'Completion backend not reachable.' })
  const state = data?.state || ''
  const terminal = !!data?.terminal
  const verified = data?.verified_count || 0
  const total = data?.total_count || 0
  const requirements = data?.requirements || []
  const gates = data?.gates || []
  const timeline = data?.timeline || []
  const metrics = data?.metrics || {}
  const last = data?.last_verdict || {}
  const post = async (path, body) => {
    setBusy(true)
    setActionError(null)
    try {
      await rest(path, { method: 'POST', body: scopedBody(sid, body) })
      refetch()
    } catch (err) {
      setActionError(String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }
  const override = () => {
    const reason = globalThis.prompt(
      'Reason to ship despite open blockers (recorded as user override — never shown as VERIFIED):'
    )
    if (reason) post('/completion/override', { reason })
  }
  const kindLabel = { state: 'loop', completion_request: 'request', verification: 'verdict' }
  const tone = completionTone(state)
  const progress = total ? Math.round((verified / total) * 100) : 0
  return jsxs(Fragment, { children: [
    jsxs('div', { className: 'flex items-start gap-6 flex-wrap', children: [
      jsxs('div', { className: 'flex-1 min-w-[280px]', children: [
        jsx('div', { className: 'text-[11px] uppercase tracking-wider text-(--ui-text-secondary)', children: 'The gate' }),
        jsx('div', { className: 'text-xl font-semibold mt-1 leading-snug', children: data?.goal || 'No goal yet — begin one below.' }),
        jsx('div', {
          className: 'text-sm text-(--ui-text-secondary) mt-1',
          children: 'The main agent may only request completion — this supervisor decides. INCOMPLETE never ends the run.'
        })
      ] }),
      jsxs('div', { className: 'shrink-0 text-right pt-1', children: [
        jsx(StateChip, {
          tone: data?.goal ? tone : 'idle',
          label: data?.goal ? state : 'NO GOAL',
          detail: data?.goal ? (terminal ? 'terminal' : '') : (state !== 'WORKING' ? `last: ${state}` : ''),
          // Only a running goal animates; a terminal verdict keeps the ✓/✕ mark.
          orb: data?.goal && !terminal ? gvOrbState(state) : null
        }),
        total ? jsx('div', { className: 'text-sm mt-2', children: `${verified} / ${total} requirements verified` }) : null
      ] })
    ] }),
    total ? jsx('div', { className: 'mt-4', children: jsx('div', {
      className: 'h-1.5 rounded-full',
      style: { background: 'color-mix(in srgb, var(--ui-text-primary, #e9e6dd) 8%, transparent)' },
      children: jsx('div', {
        className: 'h-1.5 rounded-full',
        style: { width: `${progress}%`, background: tone === 'ok' ? TONE_COLORS.ok : TONE_COLORS.warn }
      })
    }) }) : null,
    jsxs('div', { className: 'grid gap-4 mt-5', style: { gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }, children: [
      jsx(Card, { children: jsx(Section, {
        title: total ? `Requirements  ${verified}/${total}` : 'Requirements',
        hint: 'Every blocking requirement needs its own evidence before VERIFIED_COMPLETE.',
        children: requirements.length
          ? jsx('div', { className: 'space-y-1', children: requirements.map((r) => jsxs('div', {
              key: r.id,
              className: 'text-sm flex gap-2 items-baseline',
              children: [
                jsx('span', { style: { color: r.verified ? TONE_COLORS.ok : TONE_COLORS.bad }, children: r.verified ? '✓' : '✗' }),
                jsx('span', { children: r.text }),
                r.blocking ? null : jsx('span', { className: 'text-xs text-(--ui-text-secondary)', children: 'non-blocking' })
              ]
            })) })
          : jsx(Empty, { children: 'no requirements yet — begin a goal below' })
      }) }),
      jsx(Card, { children: jsx(Section, {
        title: 'Gate reports',
        hint: data?.goal
          ? 'The eight gates, run over the real repository on every completion request.'
          : 'From the last run — begin a new goal below to re-arm the gate.',
        children: gates.length
          ? jsx('div', {
              className: 'grid gap-x-6 gap-y-1',
              style: { gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' },
              children: gates.map((g) => jsxs('div', {
                key: g.name,
                className: 'text-sm flex items-center gap-2',
                children: [
                  jsx('span', { style: { color: g.status === 'PASS' ? TONE_COLORS.ok : TONE_COLORS.bad }, children: g.status === 'PASS' ? '✓' : '✗' }),
                  jsx('span', { children: g.name }),
                  jsx('span', {
                    className: 'text-xs',
                    style: { color: g.status === 'PASS' ? TONE_COLORS.ok : TONE_COLORS.bad, opacity: 0.85 },
                    children: g.status
                  })
                ]
              }))
            })
          : jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'no verdict yet — the gates run on the first completion request.' })
      }) })
    ] }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Supervisor actions',
      hint: 'Re-run the real gate pipeline, begin a new goal, or accept blockers as the user (§35).',
      children: jsxs('div', { className: 'space-y-2', children: [
        jsx('div', { className: 'flex gap-2', children: [
          jsx('button', {
            className: 'text-sm rounded border border-border px-3 py-1.5 hover:bg-(--chrome-action-hover)',
            disabled: busy || terminal,
            onClick: () => post('/completion/verify', {}),
            children: busy ? 'running …' : 'Verify now'
          }),
          jsx('button', {
            className: 'text-sm rounded border border-border px-3 py-1.5 hover:bg-(--chrome-action-hover)',
            disabled: busy,
            onClick: override,
            children: 'Accept override (user)'
          })
        ] }),
        jsx('div', { className: 'flex gap-2', children: [
          jsx('input', {
            className: 'text-sm rounded border border-border px-2 py-1 bg-transparent flex-1',
            placeholder: 'new goal — re-arms the supervisor',
            value: goalDraft,
            onChange: (event) => setGoalDraft(event.target.value)
          }),
          jsx('input', {
            className: 'text-sm rounded border border-border px-2 py-1 bg-transparent flex-1',
            placeholder: 'criteria, separated by ;',
            value: criteriaDraft,
            onChange: (event) => setCriteriaDraft(event.target.value)
          }),
          jsx('button', {
            className: 'text-sm rounded border border-border px-3 py-1.5 hover:bg-(--chrome-action-hover)',
            disabled: busy || !goalDraft.trim(),
            onClick: () => post('/completion/goal', {
              goal: goalDraft.trim(),
              criteria: criteriaDraft.split(';').map((c) => c.trim()).filter(Boolean)
            }),
            children: 'Begin goal'
          })
        ] }),
        actionError ? jsx('div', { className: 'text-sm', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: actionError }) : null,
        last.status ? jsxs('div', { className: 'space-y-2', children: [
          jsx('div', {
            className: 'text-sm font-medium',
            style: { color: TONE_COLORS[completionTone(last.status || state)] },
            children: `last verdict: ${last.status}`
          }),
          (last.blocking_failures || []).map((blob) => jsx('div', { key: blob, className: 'text-sm', children: `✗ ${blob}` })),
          (last.major_failures || []).length ? jsx('div', { className: 'text-sm', children: `major: ${(last.major_failures || []).join(' · ')}` }) : null,
          (last.next_actions || []).length && last.status !== 'VERIFIED_COMPLETE'
            ? jsxs('div', { className: 'text-sm text-(--ui-text-secondary)', children: (last.next_actions || []).map((a) => jsx('div', { key: a, children: `→ ${a}` })) })
            : null
        ] }) : null
      ] })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Completion timeline (§38)',
      hint: 'Every request, rejection and verification of the loop — the visible proof that rejections kept the work going.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'kind', label: 'Step', fmt: (v) => kindLabel[v] || v || '—' },
          { key: 'status', label: 'Status', fmt: (v) => v || '—' },
          { key: 'reason', label: 'Detail', fmt: (v) => v || '—', max: 70 },
          { key: 'created', label: 'When', fmt: (v) => fmtTs(v) }
        ],
        rows: timeline.slice().reverse(),
        empty: 'no requests yet — the loop starts when the agent requests completion.'
      })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Metrics (§39)',
      hint: 'MEASURED counters from the completion history.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'label', label: 'Metric' },
          { key: 'value', label: 'Value', fmt: (v) => (v == null ? '—' : String(v)) }
        ],
        rows: Object.keys(metrics).map((key) => ({ key, label: key, value: metrics[key] })),
        empty: 'no metrics recorded yet.'
      })
    }) })
  ] })
}

function EvalsTab() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'evals'],
    queryFn: () => rest('/evals'),
    staleTime: 10_000
  })
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)
  const [lastResult, setLastResult] = useState(null)
  if (isLoading && !data) return jsx(Empty, { children: 'loading evals …' })
  if (error) return jsx(Empty, { children: 'Eval backend not reachable.' })
  const episodes = data?.episodes || []
  const metrics = data?.metrics || {}
  const measured = metrics.measured || {}
  const estimated = metrics.estimated || {}
  const lastRun = lastResult || data?.last_run
  const runEval = async () => {
    setBusy(true)
    setActionError(null)
    try {
      const result = await rest('/evals/run', { method: 'POST', body: {} })
      setLastResult(result.run || null)
      refetch()
    } catch (err) {
      setActionError(String((err && err.message) || err))
    } finally {
      setBusy(false)
    }
  }
  const measuredRows = Object.keys(measured).map((key) => ({ key, label: key, value: measured[key] }))
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Episodes',
      hint: 'Deterministic scenarios of the eval harness — same voice pipeline as live, no LLM.',
      children: jsx(SimpleTable, {
        columns: [
          { key: 'name', label: 'Episode' },
          { key: 'events', label: 'Events' },
          { key: 'expect_signals', label: 'expected signals', fmt: (v) => ((v || []).length ? v.join(', ') : '—'), max: 60 },
          { key: 'expect_guard', label: 'Guard', fmt: (v) => v || '—' },
          { key: 'expect_pause_delivered', label: 'Redirect', fmt: (v) => (v ? '✓' : '—') }
        ],
        rows: episodes,
        empty: 'no episodes registered.'
      })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Eval run',
      hint: 'Runs the complete episode suite for real (seconds, deterministic) and persists the result.',
      children: jsxs('div', { className: 'space-y-2', children: [
        jsx('button', {
          className: 'text-sm rounded border border-border px-3 py-1.5 hover:bg-(--chrome-action-hover)',
          disabled: busy,
          onClick: runEval,
          children: busy ? 'running …' : 'Run eval'
        }),
        actionError ? jsx('div', { className: 'text-sm', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: actionError }) : null,
        lastRun ? jsxs('div', { className: 'space-y-2', children: [
          jsx('div', {
            className: 'text-sm font-medium',
            children: `${lastRun.passed}/${lastRun.total} passed${lastRun.passed === lastRun.total ? ' ✓' : ' — see details'}`
          }),
          jsx(SimpleTable, {
            columns: [
              { key: 'name', label: 'Episode' },
              { key: 'passed', label: 'Result', fmt: (v) => (v ? '✓ PASS' : '✗ FAIL') },
              { key: 'signals', label: 'Signals', fmt: (v) => ((v || []).join(', ') || '—'), max: 50 },
              { key: 'notes', label: 'Notes', fmt: (v) => ((v || []).join('; ') || '—'), max: 60 }
            ],
            rows: lastRun.results || [],
            empty: '—'
          })
        ] }) : jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'no run yet — "Run eval" starts the suite.' })
      ] })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Token metrics (§20)',
      hint: 'MEASURED = directly counted events from the event store; ESTIMATED = clearly labeled estimate, never presented as exact.',
      children: jsxs('div', { className: 'space-y-3', children: [
        jsx('div', { children: [
          jsx('div', { className: 'text-xs text-(--ui-text-secondary) mb-1', children: 'MEASURED' }),
          jsx(SimpleTable, {
            columns: [
              { key: 'label', label: 'Metric' },
              { key: 'value', label: 'Value' }
            ],
            rows: measuredRows,
            empty: 'no event store.'
          })
        ] }),
        jsx('div', { children: [
          jsx('div', { className: 'text-xs text-(--ui-text-secondary) mb-1', children: 'ESTIMATED' }),
          jsx(SimpleTable, {
            columns: [
              { key: 'label', label: 'Metric' },
              { key: 'value', label: 'Value' }
            ],
            rows: [
              { label: 'voice_ctx_tokens (≈ chars/4)', value: estimated.voice_ctx_tokens ?? 0 },
              { label: 'investigator_tokens', value: estimated.investigator_tokens ?? 0 }
            ],
            empty: '—'
          }),
          (estimated.notes || []).map((note) => jsx('div', {
            key: `n-${note}`,
            className: 'text-xs text-(--ui-text-secondary) mt-1',
            children: note
          }))
        ] })
      ] })
    }) })
  ] })
}

function SystemTab({ data }) {
  return jsxs(Fragment, { children: [
    jsx(Section, {
      title: 'Runtime',
      children: jsxs('div', { className: 'text-sm space-y-1', children: [
        jsx('div', { className: 'font-mono text-xs', children: `Data: ${data?.runtime || '—'} (events · pauses · memory · treasury)` }),
        jsx('div', { className: 'font-mono text-xs', children: `Snapshot: ${fmtTs(data?.generated_at)}` }),
        jsx('div', { className: 'text-(--ui-text-secondary) text-xs', children: "Skaldir Agent panel — read-only. All write paths stay with the agent itself." })
      ] })
    }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Limits',
      hint: 'What this panel deliberately cannot do.',
      children: jsx('div', { className: 'text-sm text-(--ui-text-secondary) space-y-1', children: [
        jsx('div', { children: '· Pause/resume: the agent decides — the panel observes.' }),
        jsx('div', { children: '· No vault access: handles and ledger entries, never secrets.' }),
        jsx('div', { children: '· No writes: no session, memory or treasury mutation from the UI.' })
      ] })
    }) }),
    jsx('div', { className: 'mt-6', children: jsx(Section, {
      title: 'Engine',
      children: jsx('div', { className: 'text-sm', children: [
        jsx('div', { className: 'text-(--ui-text-secondary) text-xs', children: 'Skaldir Agent runs on the Hermes engine (same desktop app, its own look, its own instance).' })
      ] })
    }) })
  ] })
}

// -- the page ------------------------------------------------------------------

// -- the control layer: persistent status + cockpit ---------------------------

const TONE_COLORS = {
  ok: 'var(--ui-success, #56d364)',
  warn: 'var(--ui-warn, #d29922)',
  bad: 'var(--ui-danger, #ff6b7d)',
  idle: 'var(--ui-text-tertiary, #8b8b8b)'
}

const STATE_LINES = {
  WORKING: 'The agent is working. No completion request is pending.',
  COMPLETION_REQUESTED: 'Completion requested — the supervisor verifies now.',
  VERIFYING: 'Verification in progress against the real repository.',
  INCOMPLETE: 'Rejected — the loop keeps running until the blockers are fixed.',
  VERIFIED_COMPLETE: 'Independently verified — the goal is provably done.',
  BLOCKED_USER: 'Blocked: a user decision is required.',
  BLOCKED_EXTERNAL: 'Blocked: an external dependency is unavailable.',
  FAILED_UNRECOVERABLE: 'Stopped: not reachable with the current capabilities.',
  USER_CANCELLED: 'Cancelled by the user.',
  SHIPPED_WITH_USER_OVERRIDE: 'Shipped with a user override — never verified.'
}

function completionTone(state) {
  if (state === 'VERIFIED_COMPLETE') return 'ok'
  if (state === 'INCOMPLETE' || state === 'SHIPPED_WITH_USER_OVERRIDE') return 'warn'
  if (state === 'BLOCKED_USER' || state === 'BLOCKED_EXTERNAL' || state === 'FAILED_UNRECOVERABLE') return 'bad'
  return 'idle'
}

// -- thinking-orbs: the ONE visible working-state indicator -------------------
//
// Every observable agent working phase renders through `thinking-orbs` (the
// block vendored at the top of this file) — the panel gate card, the composer
// strip, the transcript card and the capability/build rows all show the same
// indicator. The old `●`/`◉`/`…` working glyphs are gone: nothing else
// animates a working state.
//
// This is a PROJECTION, never a second status model. It maps runtime facts the
// app already produced — the completion record's `state`, the transcript's
// `streaming` flag, a build's `stage` (`toolbuilder.STAGES` and the
// `buildflow.PHASES` behind them) — onto the library's nine verbs. Nothing is
// invented here, and the labels name observable work only ("Working on the
// task"), never a private reasoning trace.
//
//   solving     analysis, verification (VERIFYING, gates, dependency checks)
//   searching   research, retrieval, memory lookup, inspection
//   working     tool execution and general work (WORKING), observation
//   connecting  wiring a capability: contract, adapter, publish, bind
//   weaving     several parallel paths aggregated (run/agent merges)
//   composing   response generation (the transcript is streaming)
//   breathing   waiting, queued, recovery, blocked on a decision
//   shaping     an artifact is being produced (generating, manifest, contract)
//
// Two tuned sizes ship — 20 (inline) and 64 (avatar scale). Inline is the
// default everywhere; 64 is reserved for the live card that IS the work
// surface (a session with no goal yet), where the phase is central and long.
const GV_ORB_INLINE = 20
const GV_ORB_FOCUS = 64

const GV_ORB_LABELS = {
  solving: 'Checking the result',
  searching: 'Searching sources and memory',
  working: 'Working on the task',
  connecting: 'Connecting a capability',
  weaving: 'Combining parallel work',
  composing: 'Composing the response',
  breathing: 'Waiting',
  shaping: 'Producing an artifact'
}

//: Completion states that are final — a finished goal never shows an orb.
const GV_ORB_TERMINAL_STATES = new Set([
  'VERIFIED_COMPLETE',
  'INCOMPLETE',
  'FAILED_UNRECOVERABLE',
  'SHIPPED_WITH_USER_OVERRIDE'
])

/** Completion `state` (+ `streaming`) → an orb verb, or null when nothing runs. */
function gvOrbState(state, { streaming = false } = {}) {
  const s = String(state || '').toUpperCase()
  if (!s || GV_ORB_TERMINAL_STATES.has(s)) return null
  if (s === 'WORKING') return streaming ? 'composing' : 'working'
  if (s === 'VERIFYING') return 'solving'
  if (s === 'RESEARCHING') return 'searching'
  if (s === 'BLOCKED_USER' || s === 'BLOCKED_EXTERNAL' || s === 'WAITING' || s === 'RECOVERING') {
    return 'breathing'
  }
  return null
}

//: `toolbuilder.STAGES` (and the `buildflow.PHASES` behind them) → one verb.
//: Terminal stages (`ready`, `failed`, `blocked`, `cancelled`, `superseded`)
//: are absent on purpose — a finished build shows no indicator.
const GV_ORB_BUILD_STAGES = {
  requested: 'breathing',
  researching: 'searching',
  generating: 'shaping',
  manifest: 'shaping',
  dependency_inspection: 'solving',
  gates: 'solving',
  register: 'connecting',
  need: 'breathing',
  search: 'searching',
  decide: 'solving',
  inspect: 'searching',
  contract: 'shaping',
  adapter: 'connecting',
  verify: 'solving',
  validate: 'solving',
  publish: 'connecting',
  bind: 'connecting',
  observe: 'working'
}

/** A build stage → orb verb; terminal/unknown stages → null (no indicator). */
function gvOrbBuildStage(stage) {
  return GV_ORB_BUILD_STAGES[String(stage || '').toLowerCase()] || null
}

/** The one indicator. `state` is an orb verb, or null/false to render nothing.
 *  Wrapped in a marked span: the UI contract (`tools/gv_ui_audit.py`) counts
 *  `[data-godsv-orb]` to prove that every visible working state goes through
 *  thinking-orbs — and the wrapper is what makes that countable, since the
 *  library's own props are its canvas API. */
function GvOrb({ state, label, size = GV_ORB_INLINE, className }) {
  if (!state) return null
  return jsx('span', {
    'data-godsv-orb': state,
    className: 'inline-flex shrink-0 items-center',
    children: jsx(ThinkingOrb, {
      state,
      size,
      theme: 'auto',
      'aria-label': label || GV_ORB_LABELS[state] || state,
      className,
      style: { flexShrink: 0 }
    })
  })
}

/** Hand the vendored thinking-orbs to the host UI (once).
 *
 *  The chat's own status stack (Hermes' composer rows) used to animate a braille
 *  spinner for the same fact this plugin shows as an orb — two indicators, two
 *  animations, one state (UI audit P1 #5). The library is vendored INTO this file
 *  (a runtime plugin blob cannot resolve a relative import), so the only way the
 *  host can use the same implementation without a second copy is to be handed
 *  it: `globalThis.__GV_THINKING_ORB__` plus a `gv-orb-ready` event for
 *  components that mounted before the plugin registered. The overlay's
 *  `components/ui/gv-orb.tsx` is the consumer; nothing else is published. */
function publishOrbRuntime() {
  try {
    if (typeof globalThis === 'undefined') return
    globalThis.__GV_THINKING_ORB__ = {
      ThinkingOrb,
      resolvePreset: __gvOrbsEngine.resolvePreset,
      MODE_DRAWS: __gvOrbsEngine.MODE_DRAWS,
      STATE_TO_MODE: __gvOrbsEngine.STATE_TO_MODE,
      version: 'thinking-orbs@0.3.1'
    }
    if (typeof document !== 'undefined' && typeof Event === 'function') {
      document.dispatchEvent(new Event('gv-orb-ready'))
    }
  } catch {
    /* the host indicator falls back to a static dot — never to a spinner */
  }
}

function StateChip({ tone, label, detail, orb }) {
  const color = TONE_COLORS[tone] || TONE_COLORS.idle
  return jsxs('span', {
    className: 'inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-0.5 text-xs whitespace-nowrap',
    style: { color },
    children: [
      // An active phase shows the one orb; a final/idle state keeps the plain
      // tone dot — the library is strictly monochrome, so the dot is what still
      // carries the tone (ok/warn/bad) into the chip.
      orb
        ? jsx(GvOrb, { state: orb, label })
        : jsx('span', { className: 'inline-block w-1.5 h-1.5 rounded-full', style: { background: color } }),
      jsx('span', { className: 'font-medium', children: label }),
      detail ? jsx('span', { className: 'opacity-75', children: detail }) : null
    ]
  })
}

/** One node in the interface: a concluded (✓ ! ◇ ◆) or an active (orb) state.
 *  The state is always a projection of a runtime fact; `unknown` renders as `?`
 *  and never as success. Active states use the one thinking-orb (no second
 *  spinner, no legacy glyph). */
function GvNode({ uiState, label, detail, compact, size = GV_ORB_INLINE }) {
  const state = GV_UI_STATES.includes(uiState) ? uiState : 'unknown'
  const orb = GV_NODE_ORB[state]
  const color = GV_STATE_TONES[gvNodeTone(state)] || GV_STATE_TONES.idle
  const symbol = GV_NODE_SYMBOLS[state] || GV_NODE_SYMBOLS.unknown
  const indicator = orb
    ? jsx(GvOrb, { state: orb, label, size })
    : jsx('span', {
        className: 'shrink-0 font-mono leading-none',
        style: { color },
        'aria-hidden': 'true',
        children: symbol
      })
  return jsxs('span', {
    className: 'inline-flex items-center gap-1.5',
    'data-godsv-node': state,
    'aria-label': `${state}: ${String(label || '')}`.trim(),
    children: compact
      ? [indicator]
      : [
          indicator,
          jsx('span', { style: { color }, children: label }),
          detail ? jsx('span', { className: 'opacity-75', children: detail }) : null
        ]
  })
}

/** An action strand (§3): several steps that belong together, drawn as ONE
 *  connected run instead of loose lines. Only for runs where the order is the
 *  point — a strand for trivial work would be noise, so it needs two steps. */
function GvStrand({ steps, className }) {
  const list = (steps || []).filter(Boolean)
  if (list.length < 2) return null
  return jsx('div', {
    className: 'mt-1.5 ' + (className || ''),
    'data-godsv-strand': String(list.length),
    children: list.map((step, index) =>
      jsxs(
        'div',
        {
          className: 'flex items-stretch gap-1.5',
          children: [
            // the rail: every row carries the connecting line except the last
            jsx('span', {
              className: 'w-1.5 shrink-0 border-l border-border',
              style: index === list.length - 1 ? { borderColor: 'transparent' } : undefined
            }),
            jsx(GvNode, { uiState: step.uiState, label: step.label, detail: step.detail })
          ]
        },
        `strand-${index}`
      )
    )
  })
}

/** The completion check as a strand (§11): what is being verified, what still
 *  needs fresh evidence, and the verdict — derived from the runtime record only.
 *  The full criteria list stays behind the fold (§14); this is the readable
 *  summary, never one line per criterion. */
function gvVerificationSteps({ state, stage, total, verifiedCount, failedCount, blockers }) {
  if (!total) return []
  const steps = [
    {
      uiState: gvUiStateFromRecord({ state }),
      label:
        stage === 'verified'
          ? 'Goal verified'
          : stage === 'rejected'
            ? 'Completion not confirmed'
            : 'Verifying completion',
      detail: `${verifiedCount}/${total}`
    }
  ]
  const open = Math.max(0, total - verifiedCount - failedCount)
  if (open > 0) steps.push({ uiState: 'waiting', label: `${open} criteria need current evidence` })
  if (failedCount > 0) {
    steps.push({ uiState: 'failed', label: `${failedCount} criteria failed verification` })
  }
  if ((blockers || []).length) {
    steps.push({
      uiState: 'approval_required',
      label: `${blockers.length} requirements remain open`,
      detail: 'Details ›'
    })
  }
  return steps
}

// The old `StatusStrip` (SKALDIR · state · whisper mode · as of <time> ·
// ↻ refresh, sticky on every panel page) is gone: UI audit P1 #2/#11 named it as
// the telemetry header that made a settings page look like a control room. Its
// one real signal — the state chip and the goal — lives in `GvPlanRow` inside
// the chat, which is where the work is. Nothing else read it.

function Card({ children }) {
  return jsx('div', {
    className: 'border border-border rounded-lg p-4',
    style: { background: 'color-mix(in srgb, var(--ui-text-primary, #e9e6dd) 4%, transparent)' },
    children
  })
}

function CockpitView({ summary, completion, onRefresh, onNavigate }) {
  const c = completion || {}
  const loading = !completion && !c.state
  const state = c.state || ''
  const reqs = c.requirements || []
  const total = c.total_count || 0
  const verified = c.verified_count || 0
  const last = c.last_verdict || {}
  const blockers = last.blocking_failures || []
  const nextActions = last.next_actions || []
  const evidence = last.evidence || []
  const recent = summary?.recent || []
  const pauses = summary?.pauses || []
  const kinds = summary?.kinds || {}
  const activePauses = pauses.filter((p) => ['requested', 'researching', 'ready'].includes(p.status)).length
  const delivered = pauses.filter((p) => p.status === 'delivered')
  const lastDelivered = delivered[delivered.length - 1]
  const pct = total ? Math.round((verified / total) * 100) : 0
  const eventsTotal = Object.values(kinds).reduce((a, b) => a + (Number(b) || 0), 0)
  const blockerRuns = recent.filter((e) => /guard|completion|pause|redirect|research/.test(String(e.kind || ''))).slice(0, 3)
  const noGoal = !c.goal
  const lastStatus = String(last.status || '')
  const stateLine = noGoal
    ? (state === 'VERIFIED_COMPLETE'
        ? 'No active goal — the previous goal passed independent verification.'
        : lastStatus === 'INCOMPLETE'
          ? 'No active goal — the previous task ended INCOMPLETE; the findings below are history, not current state.'
          : 'No active goal — begin one in Verification.')
    : (STATE_LINES[state] || '')
  const stateLineOk = noGoal && state === 'VERIFIED_COMPLETE'

  return jsxs(Fragment, { children: [
    jsxs('div', { className: 'flex items-start gap-6 flex-wrap', children: [
      jsxs('div', { className: 'flex-1 min-w-[280px]', children: [
        jsx('div', { className: 'text-[11px] uppercase tracking-wider text-(--ui-text-secondary)', children: 'Current goal' }),
        jsx('div', {
          className: 'text-2xl font-semibold mt-1 leading-snug',
          children: c.goal || (loading ? 'loading …' : 'No goal yet — begin one in Verification.')
        }),
        jsx('div', {
          className: `text-sm mt-1 ${stateLineOk ? '' : 'text-(--ui-text-secondary)'}`,
          style: stateLineOk ? { color: TONE_COLORS.ok } : undefined,
          children: (stateLineOk ? '✓ ' : '') + stateLine
        })
      ] }),
      jsxs('div', { className: 'shrink-0 text-right pt-1', children: [
        total
          ? jsxs(Fragment, { children: [
              jsx('div', { className: 'text-sm', children: `${verified} / ${total} requirements verified` }),
              jsx('div', {
                className: 'text-[11px] mt-1 uppercase tracking-wider',
                style: { color: TONE_COLORS[completionTone(state)] },
                children: state || '—'
              })
            ] })
          : jsx('button', {
              className: 'rounded border px-3 py-1.5 text-sm hover:bg-(--chrome-action-hover)',
              style: {
                borderColor: 'color-mix(in srgb, var(--ui-text-primary, #f5f5f5) 60%, transparent)',
                color: 'var(--ui-text-primary, #f5f5f5)'
              },
              onClick: () => onNavigate && onNavigate('verification'),
              children: 'Begin a goal →'
            })
      ] })
    ] }),
    total
      ? jsx('div', { className: 'mt-4', children: jsx('div', {
          className: 'h-1.5 rounded-full overflow-hidden',
          style: { background: 'var(--ui-hover, #232323)' },
          children: jsx('div', {
            className: 'h-1.5 rounded-full transition-all',
            style: { width: `${pct}%`, background: blockers.length ? TONE_COLORS.warn : TONE_COLORS.ok }
          })
        }) })
      : null,
    jsx('div', {
      className: 'mt-3 font-mono text-[11px] text-(--ui-text-secondary)',
      children: [
        `pauses ${activePauses} active / ${pauses.length} history`,
        `redirects ${kinds['pause_delivered'] || 0}`,
        `guard checks ${kinds['guard_check'] || 0}`,
        `claims ${kinds['claim'] || 0}`,
        `events ${eventsTotal}`
      ].join('   ·   ')
    }),
    jsxs('div', { className: 'grid gap-4 mt-6', style: { gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }, children: [
      jsx(Card, { children: jsx(Section, {
        title: 'Blocking now',
        hint: 'Independent gate results over the real repository — not self-assessments.',
        children: blockers.length
          ? jsx('div', { className: 'space-y-1.5', children: blockers.map((b, i) => jsx('div', {
              key: `b${i}`,
              className: 'text-sm',
              style: { color: TONE_COLORS.bad },
              children: `✗ ${b}`
            })) })
          : jsx('div', {
              className: state === 'VERIFIED_COMPLETE' ? 'text-sm' : 'text-sm text-(--ui-text-secondary)',
              style: state === 'VERIFIED_COMPLETE' ? { color: TONE_COLORS.ok } : undefined,
              children: state === 'VERIFIED_COMPLETE' ? '✓ none — the gate is satisfied' : 'no verdict yet — nothing has been rejected'
            })
      }) }),
      jsx(Card, { children: jsx(Section, {
        title: total ? `Requirements  ${verified}/${total}` : 'Requirements',
        hint: 'Every blocking requirement needs its own evidence before VERIFIED_COMPLETE.',
        children: reqs.length
          ? jsx('div', { className: 'space-y-1', children: reqs.map((r, i) => jsxs('div', {
              key: r.id || `r${i}`,
              className: 'text-sm flex items-baseline gap-2',
              children: [
                jsx('span', {
                  style: { color: r.verified ? TONE_COLORS.ok : TONE_COLORS.bad },
                  children: r.verified ? '✓' : '✗'
                }),
                jsx('span', { className: 'flex-1', children: r.text }),
                r.blocking ? null : jsx('span', { className: 'text-[11px] uppercase tracking-wider text-(--ui-text-secondary)', children: 'non-blocking' })
              ]
            })) })
          : jsx(Empty, { children: 'no requirements yet — begin a goal in Verification' })
      }) })
    ] }),
    jsxs('div', { className: 'grid gap-4 mt-4', style: { gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }, children: [
      jsx(Card, { children: jsx(Section, {
        title: noGoal ? 'Latest evidence (previous task)' : 'Latest evidence',
        hint: 'Only checks that actually ran — fresh after the last change.',
        children: evidence.length
          ? jsx('div', { className: 'space-y-0.5 font-mono text-xs', children: evidence.map((e, i) => jsx('div', { key: `e${i}`, className: 'py-0.5 border-b border-border', children: e })) })
          : jsx('div', { className: 'text-sm text-(--ui-text-secondary)', children: 'none yet — no check has produced evidence so far' })
      }) }),
      jsx(Card, { children: jsx(Section, {
        title: noGoal ? 'Voice activity (history)' : 'Voice activity',
        hint: 'Pause → research → redirect → resume; delivered exactly once.',
        children: jsxs('div', { className: 'space-y-1 text-sm', children: [
          jsx('div', { children: `${activePauses} pause(s) active · ${delivered.length} redirect(s) delivered` }),
          lastDelivered
            ? jsx('div', { className: 'text-(--ui-text-secondary)', children: `last redirect: ${fmtTs(lastDelivered.updated_at || lastDelivered.created_at || lastDelivered.created)}` })
            : jsx('div', { className: 'text-(--ui-text-secondary)', children: 'no redirect delivered yet' }),
          nextActions.length
            ? jsx('div', { className: 'pt-2 space-y-1', children: nextActions.map((a, i) => jsx('div', { key: `n${i}`, className: 'text-xs text-(--ui-text-secondary)', children: `→ ${a}` })) })
            : null
        ] })
      }) })
    ] }),
    jsx('div', { className: 'mt-4', children: jsx(Card, { children: jsx(Section, {
      title: 'Recent signal',
      hint: 'Voice- and completion-relevant events, newest first.',
      children: blockerRuns.length
        ? jsx(EventRows, { events: blockerRuns, maxHeight: 200 })
        : jsx(Empty, { children: 'no voice/completion events yet' })
    }) }) })
  ] })
}

function Page() {
  // Integrated app build: the panel areas are the app's own navigation (the
  // sidebar carries them), so the page renders no second nav layer and the
  // view follows the route hash — sidebar clicks land here as hash changes.
  const [view, setViewState] = useState(() =>
    INTEGRATED && typeof window !== 'undefined' ? viewFromHash() : 'cockpit'
  )
  const setView = (key) => {
    if (INTEGRATED && VIEW_ROUTES[key]) {
      try {
        host.navigate(VIEW_ROUTES[key])
      } catch {
        // fall through — the local state still shows the area
      }
    }
    setViewState(key)
  }
  useEffect(() => {
    if (!INTEGRATED) return undefined
    const sync = () => setViewState(viewFromHash())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])
  const [settingsTab, setSettingsTab] = useState('models')
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: [ID, 'summary'],
    queryFn: () => rest('/summary?limit=40'),
    staleTime: 5_000,
    refetchInterval: 15_000
  })
  const sid = useFocusedSessionId()
  const completionQ = useQuery({
    queryKey: [ID, 'completion', sid || 'latest'],
    queryFn: () => rest(completionPath(sid)),
    staleTime: 5_000,
    refetchInterval: 20_000
  })

  const nav = INTEGRATED ? null : jsx('aside', {
    className: 'w-44 shrink-0 sticky top-16 self-start',
    children: NAV.map(([group, items]) => jsxs('div', { key: group, className: 'mb-4', children: [
      jsx('div', {
        className: 'px-2 pb-1 text-[11px] uppercase tracking-[0.16em] text-(--ui-text-secondary)',
        children: group
      }),
      items.map(([key, label]) => jsx('button', {
        className: `block w-full text-left px-2 py-1 rounded text-sm ${
          view === key
            ? 'font-medium text-foreground'
            : 'text-(--ui-text-secondary) hover:text-foreground'
        }`,
        style: view === key
          ? { background: 'color-mix(in srgb, var(--ui-text-primary, #f5f5f5) 14%, transparent)' }
          : undefined,
        onClick: () => setView(key),
        children: label
      }, key))
    ] }))
  })

  if (isLoading && !data) {
    return jsx('div', { className: 'p-6 text-(--ui-text-secondary)', children: "Skaldir Agent: loading status …" })
  }
  if (error) {
    return jsxs('div', { className: 'p-6', children: [
      jsx('div', { className: 'font-semibold', children: 'Backend not reachable' }),
      jsx('div', {
        className: 'text-sm text-(--ui-text-secondary) mt-1',
        children: "Enable the plugin in Settings → Plugins or add 'godsv' to plugins.enabled. The panel is read-only."
      })
    ] })
  }

  let body
  if (view === 'verification') {
    body = jsxs(Fragment, { key: 'verification', children: [
      jsx(CompletionTab, {}),
      jsx('div', { className: 'mt-8', children: jsx(EvalsTab, {}) })
    ] })
  } else if (view === 'activity') {
    body = jsxs(Fragment, { key: 'activity', children: [
      jsx(LiveTab, { data }),
      jsx('div', { className: 'mt-8', children: jsx(PausenTab, { data }) })
    ] })
  } else if (view === 'tools') {
    body = jsx(ToolsTab, { key: 'tools' })
  } else if (view === 'settings') {
    body = jsx(SettingsView, { key: 'settings', data, tab: settingsTab, onTab: setSettingsTab })
  } else if (view === 'treasury') body = jsx(TreasuryTab, { key: 'treasury' })
  else body = jsx(CockpitView, {
    summary: data,
    completion: completionQ.data,
    onRefresh: () => { refetch(); completionQ.refetch() },
    onNavigate: (to) => {
      if (SETTINGS_KEYS.includes(to)) {
        setSettingsTab(to)
        setView('settings')
      } else setView(to)
    },
    key: 'cockpit'
  })

  // UI audit (P1 #2/#11): the page used to open with a telemetry header every
  // time — `SKALDIR · <state> · whisper mode · as of <time> · ↻ refresh`, plus a
  // panel-wide refetch button. A global refresh and a live runtime header do not
  // belong on a settings or tool page (01_PRODUCT_UX_DESIGN.md:15), and the
  // header claimed runtime state that the page it sat on had nothing to do with.
  // Each view now carries its own name and its own data; nothing polls harder
  // than its own query needs. `<main>` also gives the panel a landmark, which is
  // what the axe `region` rule asks for.
  const viewTitle = {
    cockpit: 'Diagnostics',
    verification: 'Verification',
    activity: 'Activity',
    tools: 'Tools',
    settings: 'Settings',
    treasury: 'Treasury'
  }[view] || 'Skaldir Agent'
  return jsxs('main', {
    className: 'px-6 pb-8',
    'aria-label': `${viewTitle} — Skaldir Agent`,
    children: [
      jsx('h1', { className: 'pt-3 text-sm font-medium text-foreground', children: viewTitle }),
      jsxs('div', { className: 'flex gap-6 mt-4 items-start', children: [nav, jsx('div', { className: 'flex-1 min-w-0', children: body })] })
    ]
  })
}

// -- the empty-chat face --------------------------------------------------------

// __LOGO_ART_START__
// Empty-state branding: the Skaldir mark (owner artwork, scaled to 96x96).
// The wordmark beside it is plain text — no font download, no second asset.
// Stored as base64 so the plugin bundle needs no external asset fetch.
// Do not edit the payload by hand.
const LOGO_ART_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAMAAADVRocKAAAAqFBMVEX+/v77+/vc3NyxsbGNjY1sbGxaWlpMTExTU1N8e3ypqano6OjDw8Nzc3MzMjIICAcDAwMKCgpkZGTu7u4HCAcUFBS7u7v19fWFhYWtra0jIyPl5eVYV1fZ2dnHx8fLy8vX19j8+/zg4OAgHx/Pz8/S0tKampocHBylpaWjo6Oko6RERES8vLw7Ojrw8PGUlJT39/irq6zj4+O0tLTV1NUrKyv8+/uAf4AX+5/oAAADYElEQVRo3u2ZaX+iMBCHJ3iBVjJi8ECFuquga9Xdbu1+/2+2vwngVZFIsK+YF1qv/5NJ5khSgMoqq6yyyr7TWGrPEb94Vbo8PRi1eqPZMptWu1MygsReujbH1HpOp0QCA+jXbdIVqSG6g9IIDIYjF1Fw0k1McMSSCAxg7NHY8dIE9iYlDb+JyK/lEZHjtAQXGMy8r6NPffC1CQyCHnLEDICpC2AQCBSYaeIV5jr6c/jh3tXHrq4Ldub8xICfQ70J6t7VJ8JYw4U5LPDeBGkvMwMzQ1+kSa2ZbMvbw06TjhgCw8IuMLBuOSAwWvnrkWP3ZOnDlkag2jdnSFAdBYDJr4180Smsb2REkEAcxfPS5igK11QGb1khJARaMKfGXEOO9cKAVWaMCkphYGwIU0SnMCDMTgIhcLOkbwWIrWcA5EK0wmUftugaxZY4BxB/5npu4YKXB5BJoJPMuYCUQSFVxAUVQJp4/Wd5kBIKpcIjAG/31CkSiMG5C4pbb3UP0K5d/K5sD6Yvl+q79mavglEDCNycRk1Pi26EanGrCpjC76N6P9zKLWWpANn15boGjitzr0QAZbIZq3f2cvC8TADFJ3dm9GWfBs+PHXBUCuAo39nbyVs9b9u0wsFSKe/uA+iAwxsGQL/dQsTIbjn1P8H7IxvJPA+kPLw1pt3wxThVOzpDq+1j7gEERpYhI3N3LvxYxbsPcIZxbF6ezWNT3Ijdb/oomrJC+N2pM1r/9T8mD+/jcxYZEbfhLl1k5G502JqOtV8NgllHaa7ydhWUBZElw1QWiLPlV2tAaokmTJloQSOi0OXJLUCppWIdl4rdepO8oV4qxkrFbk1inyT4YXnx4UQNMM86f1wB0uZCj6xm9mgN/qn1taGX74LAxkmM/ninWxm1jRIDRwVgX/xG9oWmUsuk9qfUlBdQ9KqNXOD5Lnw5HzxQk/qRgg+84PY9vgrBXB+07isY+L2bd1EXAI1zJjAwtseTQNYUqbXgTAKEB0yuG7Nc8HSuXD4BYOAcYnFxEyOwrXVrJGvAbGy1vGS9rzF0naBnSWAzY1B3bPeMEoMERoWOONeQZBb6i5VlHlw8cThyjTi6TYFJsLKa2yiZtEZJ+l8wO8Mfh/vQL1f/SHnKPylucb6HVFlllVVW2fPtP669pKw+qNmSAAAAAElFTkSuQmCC'
// __LOGO_ART_END__

function SkaldirAgentEmpty({ sessionId }) {
  // Active work surface, not a splash screen: the composer is the entry
  // point. The large branding is the brand banner (wordmark included in the
  // bitmap), then one short question and the rule.
  return jsxs('div', { className: 'py-8 px-6 max-w-2xl mx-auto text-center', 'data-session': sessionId, children: [
    jsx('img', {
      src: 'data:image/png;base64,' + LOGO_ART_B64,
      alt: 'Skaldir Agent',
      width: 96,
      height: 96,
      draggable: false,
      className: 'mx-auto select-none'
    }),
    jsx('div', {
      className: 'mt-4 text-sm tracking-[0.28em] uppercase',
      // Space Grotesk is the brand face (01_PRODUCT_UX_DESIGN.md §3): the token
      // is injected by the app overlay, and a host without it keeps its own sans
      // (the fallback is the UI face, never a missing glyph).
      style: { color: 'var(--ui-text-primary, #f5f5f5)', fontFamily: 'var(--dt-font-brand, inherit)' },
      children: 'Skaldir Agent'
    }),
    jsx('div', { className: 'text-lg mt-3', children: 'What should get done?' }),
    jsx('div', { className: 'text-xs text-(--ui-text-secondary) mt-2', children: '@goal marks done — the voice verifies before anything counts as complete.' }),
    jsx('div', { className: 'text-[11px] text-(--ui-text-secondary) mt-6', children: "Panel: sidebar → Skaldir · Engine: Hermes" })
  ] })
}

// -- chat integration: @goal mentions + the session status strip --------------
//
// Skaldir Agent lives inside the normal session experience: `@goal` rows land in
// the composer's `@` popover, a submit-time middleware turns `@goal …` lines
// into pact criteria, and a quiet status strip above the composer carries the
// live goal state. All three ride the app's contribution registry — no core
// fork, same schema as the panel.

const GV_STATE_TONES = {
  ok: 'var(--ui-success, #5fb45c)',
  warn: 'var(--ui-warn, #d29922)',
  bad: 'var(--ui-danger, #ff6b7d)',
  idle: 'var(--ui-text-tertiary, #8b8b8b)'
}

function gvStateTone(state) {
  if (state === 'VERIFIED_COMPLETE') return 'ok'
  if (state === 'INCOMPLETE' || state === 'SHIPPED_WITH_USER_OVERRIDE') return 'warn'
  if (state === 'BLOCKED_USER' || state === 'BLOCKED_EXTERNAL' || state === 'FAILED_UNRECOVERABLE') return 'bad'
  return 'idle'
}

/** Rows contributed to the composer's `@` popover (composer.atCompletions). */
function goalCompletionRows(query) {
  const q = String(query || '').toLowerCase()
  const rows = []
  if ('goal'.startsWith(q)) {
    rows.push({
      insert: '@goal ',
      display: '@goal',
      meta: "Skaldir Agent · acceptance criterion (blocking)",
      icon: 'target'
    })
  }
  if ('tool'.startsWith(q)) {
    rows.push({
      insert: '@tool:',
      display: '@tool',
      meta: "Skaldir Agent · pin a capability for this task",
      icon: 'tools'
    })
  }
  return rows
}

/** Extract `@goal …` criteria lines from a draft (whole lines, case-insensitive). */
// --- honest goal-action feedback -----------------------------------------
// The composer middleware used to swallow every completion error (`catch {}`), so a failed
// `@goal` looked like nothing happened while the strip kept saying "no goal yet". Measured:
// POST /completion/criteria answered 400 "state VERIFIED_COMPLETE is terminal — begin a new
// goal to add criteria" and the user never saw it. The last failure is kept here and rendered
// by the status strip; the raw text stays behind Details >.
let goalActionFailure = null
let goalDraftTask = null
const goalActionListeners = new Set()
function setGoalActionFailure(next) {
  goalActionFailure = next
  for (const listener of goalActionListeners) {
    try {
      listener(next)
    } catch (err) {
      // a listener must never break the composer middleware
    }
  }
}
function useGoalActionFailure() {
  const [value, setValue] = useState(() => goalActionFailure)
  useEffect(() => {
    goalActionListeners.add(setValue)
    setValue(goalActionFailure)
    return () => goalActionListeners.delete(setValue)
  }, [])
  // Fall back to the module value when the host's state slot has no value yet: a failure that
  // happened before this strip mounted must still be visible (the strip can mount late).
  return value === undefined || value === null ? goalActionFailure : value
}
/** The user-facing sentence of a failed panel call (the host wraps it in IPC noise). */
function gvGoalErrorMessage(err) {
  const raw = String((err && (err.message || err.detail)) || err || '').trim()
  const detail = /"detail"\s*:\s*"([^"]+)"/.exec(raw)
  if (detail) return detail[1].slice(0, 200)
  const cleaned = raw
    .replace(/^Error invoking remote method '[^']*':\s*/, '')
    .replace(/^Error:\s*/, '')
    .split('\n')[0]
    .trim()
  return cleaned.slice(0, 200) || 'the goal action did not reach the runtime'
}
/** The goal sentence is what the user wrote; the @goal/! lines are the directives. */
function gvGoalTextFromDraft(text, criteria) {
  const prose = String(text || '')
    .split('\n')
    .filter((line) => !/^\s*[@!]/.test(line))
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
  return prose || String((criteria && criteria[0]) || '').trim()
}
function gvSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** `@goal` in a NEW chat: the renderer has no session id before the send is submitted, so the
 *  write used to land in the unscoped store while the strip polls the focused session — the user
 *  saw "no goal yet" although a contract existed (measured 17.09., app 0.17.3). Wait for the app
 *  to assign the session, then write there; the send itself is never blocked. When no id appears
 *  the write still happens (unscoped), but the user is told instead of being left in silence. */
async function gvGoalDraftAfterSend(text, { attempts = 24, intervalMs = 250 } = {}) {
  let sid = readFocusedSessionId()
  for (let i = 0; !sid && i < attempts; i += 1) {
    await gvSleep(intervalMs)
    sid = readFocusedSessionId()
  }
  try {
    await gvApplyGoalDraft(sid, text)
  } catch (err) {
    setGoalActionFailure({
      message: gvGoalErrorMessage(err),
      detail: String((err && err.message) || err || '')
    })
    return
  }
  if (sid) {
    setGoalActionFailure(null)
    return
  }
  setGoalActionFailure({
    message: 'the goal was stored without a session — the app has not delivered a session id yet',
    detail: 'no focused session id within 6s (older app build or a session without an id yet)'
  })
}

/** `@goal` opens a goal when none is open for this session, else adds criteria to it. */
async function gvApplyGoalDraft(sid, text) {
  const criteria = goalCriteriaFromDraft(text)
  const trajectory = goalTrajectoryFromDraft(text)
  if (criteria.length) {
    let current = null
    try {
      current = await rest(completionPath(sid))
    } catch (err) {
      current = null // an unreadable state must not silently downgrade to "append"
    }
    const open = Boolean(current && !current.terminal && String(current.goal || '').trim())
    if (open) {
      await rest('/completion/criteria', { method: 'POST', body: scopedBody(sid, { criteria }) })
    } else {
      await rest('/completion/goal', {
        method: 'POST',
        body: scopedBody(sid, { goal: gvGoalTextFromDraft(text, criteria), criteria })
      })
    }
  }
  for (const entry of trajectory) {
    await rest('/completion/trajectory', { method: 'POST', body: scopedBody(sid, entry) })
  }
}

function goalCriteriaFromDraft(text) {
  const out = []
  for (const line of String(text || '').split('\n')) {
    const match = /^\s*@goal\s+(.+)$/i.exec(line)
    if (match) out.push(match[1].trim())
  }
  return out.filter(Boolean)
}

/** Trajectory intake (P0-1): `!decision: …` / `!assumption: …` / `!non-goal: …`
 *  lines ride the same composer middleware as @goal criteria and land on the
 *  per-session contract — outside the conversation. */
function goalTrajectoryFromDraft(text) {
  const out = []
  for (const line of String(text || '').split('\n')) {
    const match = /^\s*!(decision|assumption|non-?goal)\s*:\s*(.+)$/i.exec(line)
    if (!match) continue
    const raw = match[1].toLowerCase()
    out.push({ kind: raw === 'non-goal' || raw === 'nongoal' ? 'non_goal' : raw, text: match[2].trim() })
  }
  return out.filter((entry) => entry.text)
}

/** What the window did NOT show: a cut window (`truncated`/`remaining`) and events the live
 *  freshness rule hid (`staleCount`). Neither may vanish silently (Wave 9 / audit AF-14). */
function gvFeedMeta(payload) {
  const events = (payload && payload.events) || []
  const now = Date.now() / 1000
  const staleCount = events.filter((event) => {
    const ts = Number(event && event.ts)
    return Boolean(ts) && now - ts > 2700
  }).length
  return {
    truncated: Boolean(payload && payload.truncated),
    remaining: Number((payload && payload.remaining) || 0),
    staleCount,
    nextBefore: Number((payload && payload.next_before) || 0)
  }
}

/** Elapsed time since the goal started — the honest half of "how long". */
function gvElapsed(started) {
  const since = Number(started || 0)
  if (!since) return ''
  const secs = Math.max(0, Math.round(Date.now() / 1000 - since))
  if (secs < 60) return '<1m'
  if (secs < 3600) return `${Math.round(secs / 60)}m`
  return `${Math.floor(secs / 3600)}h ${Math.round((secs % 3600) / 60)}m`
}

// The composer used to carry an approval-mode pill here. Owner decision 17.09.:
// approvals leave the chat surface. The status bar already offers the same three
// engine modes as ONE labelled choice (its own radio menu, fed by the same
// `/approvals` contract), so the chat carries one control fewer and there is no
// second place where a safety switch can be flipped differently. Nothing about
// the runtime semantics moved.

// -- the quiet live feed (§8–§11, §16, §22) -----------------------------------
//
// Only important events reach the chat (the backend filters by CHAT_KINDS);
// the strip shows the newest few, fresh ones with their age, one line each.
// Voice interventions are gold, gate verdicts read ok/bad, the rest stays
// neutral — no log dump, no noise.

// -- §4: one shared runtime -> UI state vocabulary ------------------------------
// The runtime is the source of truth. Everything the UI shows about progress,
// waiting, blocking or completion is a PROJECTION of a runtime fact. No state is
// invented, and `unknown` is never rendered as success.
const GV_UI_STATES = Object.freeze([
  'queued',
  'running',
  'waiting',
  'approval_required',
  'paused',
  'succeeded',
  'failed',
  'unknown',
  'cancelled'
])

/** Static symbols for CONCLUDED or interactive states. Active states carry no
 *  glyph at all: the one thinking-orb is the only working indicator (docs/UI.md). */
const GV_NODE_SYMBOLS = Object.freeze({
  queued: '◇',
  waiting: '◇',
  approval_required: '◆',
  paused: '‖',
  succeeded: '✓',
  failed: '!',
  unknown: '?',
  cancelled: '×'
})

/** Orb verb for the ACTIVE states (queued/waiting breathe, running works). */
const GV_NODE_ORB = Object.freeze({ running: 'working', queued: 'breathing', waiting: 'breathing' })

/** Completion record state -> UI state. Absent or unknown state -> `unknown`. */
function gvUiStateFromRecord(record) {
  const state = String((record && record.state) || '').toUpperCase()
  if (state === 'VERIFIED_COMPLETE' || state === 'SHIPPED_WITH_USER_OVERRIDE') return 'succeeded'
  if (state === 'BLOCKED_USER' || state === 'BLOCKED_EXTERNAL') return 'approval_required'
  if (state === 'FAILED_UNRECOVERABLE') return 'failed'
  if (state === 'USER_CANCELLED') return 'cancelled'
  if (state === 'INCOMPLETE') return 'failed'
  if (state === 'WORKING' || state === 'VERIFYING') return 'running'
  return 'unknown'
}

/** Feed event -> UI state, from runtime facts only. An unmapped kind is
 *  `unknown`; it is never promoted to a success just because it arrived. */
function gvUiStateFromEvent(event) {
  const kind = String((event && event.kind) || '')
  const payload = (event && event.payload) || {}
  if (kind === 'completion_verdict') {
    const status = String(payload.status || '').toUpperCase()
    if (status === 'VERIFIED_COMPLETE') return 'succeeded'
    if (status === 'BLOCKED_USER' || status === 'BLOCKED_EXTERNAL') return 'approval_required'
    if (status === 'USER_CANCELLED') return 'cancelled'
    return 'failed'
  }
  if (kind === 'completion_blocked') return 'approval_required'
  if (kind === 'completion_rejected' || kind === 'completion_cap_reached') return 'failed'
  if (kind === 'pause_requested' || kind === 'pause_delivered' || kind === 'pause_expired') return 'paused'
  if (kind === 'pause_released') return 'running'
  if (kind === 'research_started' || kind === 'goal_set_from_chat') return 'running'
  if (kind === 'research_done') return 'succeeded'
  if (kind === 'research_failed') return 'failed'
  if (kind === 'capability_requested') return 'queued'
  if (kind === 'spend_requested' || kind === 'treasury_grant_requested') return 'approval_required'
  if (kind === 'spend_denied' || kind === 'treasury_denied') return 'failed'
  if (kind === 'tool_build_requested') return 'queued'
  if (kind === 'tool_build_ready') return 'succeeded'
  if (kind === 'tool_build_failed') return 'failed'
  if (kind.startsWith('tool_build_')) return 'running'
  if (kind === 'cu_blocked' || kind === 'cu_failed' || kind === 'guard_nudge' || kind === 'contradiction') return 'failed'
  if (kind.startsWith('pattern_') || kind === 'tool_call_intercepted') return 'failed'
  if (kind === 'prompt_injection_signal' || kind === 'untrusted_instruction_attempt') return 'failed'
  // outcome kinds that exist so the chat can show a RESULT, not only failures
  if (kind === 'cu_verified' || kind === 'spend_completed') return 'succeeded'
  if (kind === 'spend_approved') return 'running'
  if (kind === 'pause_stale' || kind === 'system_one_decision') return 'waiting'
  if (kind === 'system_one_blocked') return 'approval_required'
  return 'unknown'
}

/** Tone for a UI state. `failed` stays a warning when the runtime calls it
 *  INCOMPLETE — the check did not pass, the work continues there. */
function gvNodeTone(uiState, runtimeState) {
  if (uiState === 'succeeded') return 'ok'
  if (uiState === 'failed') return String(runtimeState || '').toUpperCase() === 'INCOMPLETE' ? 'warn' : 'bad'
  if (uiState === 'cancelled') return 'bad'
  if (uiState === 'approval_required' || uiState === 'paused' || uiState === 'waiting') return 'warn'
  return 'idle'
}

const GV_FEED_SOURCES = [
  [/^(pause_|research_)/, 'VOICE'],
  [/^(completion_|guard_)/, 'GATE'],
  [/^(treasury_|spend_)/, 'VAULT'],
  [/^cu_/, 'CU'],
  [/^capability_/, 'TOOLS']
]

function gvFeedSource(kind) {
  for (const [re, label] of GV_FEED_SOURCES) {
    if (re.test(kind)) return label
  }
  return 'VOICE'
}

const GV_FEED_TEXTS = {
  pause_requested: (p) => 'Voice paused the agent' + (p && p.reason ? ` — ${p.reason}` : ''),
  pause_delivered: () => 'Redirect delivered — the agent resumes',
  pause_released: () => 'Pause released',
  pause_expired: () => 'Pause lease expired',
  research_started: () => 'Investigating the blocked step',
  research_done: () => 'Investigation complete',
  research_failed: () => 'Investigation failed — template redirect',
  cu_blocked: () => 'Computer Use blocked',
  cu_failed: () => 'Computer Use action failed',
  cu_verified: () => 'Computer Use action verified',
  pause_stale: () => 'Late result discarded — the pause was already resolved',
  spend_approved: (p) => 'Spend approved' + (p && p.amount ? ` — ${p.amount}` : ''),
  spend_completed: () => 'Spend completed',
  system_one_decision: (p) => 'Deeper verification required' + (p && p.rule ? ` — ${p.rule}` : ''),
  system_one_blocked: () => 'Blocked — no credential for the deeper check',
  goal_set_from_chat: (p) => 'Goal set from chat' + (p && p.goal ? ` — ${p.goal}` : ''),
  completion_verdict: (p) => `Verification: ${(p && p.status) || 'verdict'}`,
  completion_rejected: () => 'Completion rejected — the loop continues',
  completion_blocked: () => 'Blocked — a decision is required',
  completion_cap_reached: () => 'Completion cap reached',
  guard_nudge: () => 'Unbacked completion claim stopped',
  contradiction: () => 'Claim contradicted by a later error',
  capability_requested: (p) => {
    const name = p && (p.name || p.capability || p.tool)
    return 'Capability requested' + (name ? `: ${name}` : '')
  },
  treasury_denied: () => 'Secret access denied',
  treasury_grant_requested: () => 'Credential grant requested',
  spend_requested: () => 'Spend awaits approval',
  spend_denied: () => 'Spend denied',
  cu_blocked: () => 'Computer Use blocked by policy',
  cu_failed: () => 'Computer Use verification failed'
}

function gvFeedTone(event) {
  const kind = String(event?.kind || '')
  if (kind === 'completion_verdict') {
    const status = String((event.payload || {}).status || '')
    if (status === 'VERIFIED_COMPLETE') return 'ok'
    if (status === 'INCOMPLETE') return 'warn'
    return 'bad'
  }
  if (
    kind === 'completion_rejected' ||
    kind === 'completion_blocked' ||
    kind === 'completion_cap_reached' ||
    kind === 'research_failed' ||
    kind === 'contradiction' ||
    kind === 'treasury_denied' ||
    kind === 'spend_denied' ||
    kind === 'cu_blocked' ||
    kind === 'cu_failed'
  ) {
    return 'bad'
  }
  if (kind.startsWith('pause_') || kind === 'guard_nudge' || kind === 'treasury_grant_requested' || kind === 'spend_requested') {
    return 'warn'
  }
  return 'idle'
}

/** Last-resort label for a kind without its own text: readable words instead of
 *  the raw identifier (`tool_build_gates` must never reach a user verbatim, §16). */
function gvHumanizeKind(kind) {
  const words = String(kind || '')
    .replace(/[._]+/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .trim()
  if (!words) return 'Runtime event'
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** Newest important events, filtered to the last 45 minutes, mapped for the feed. */
function gvFeedRows(payload, { includeStale = false } = {}) {
  const events = (payload && payload.events) || []
  const now = Date.now() / 1000
  const rows = []
  for (const event of events) {
    const ts = Number(event && event.ts)
    if (!ts || (!includeStale && now - ts > 2700)) continue
    const kind = String(event.kind || '')
    const text = (GV_FEED_TEXTS[kind] || (() => gvHumanizeKind(kind)))(event.payload || {})
    const ageSec = Math.max(0, Math.round(now - ts))
    rows.push({
      key: String(event.seq || `${kind}-${ts}`),
      source: gvFeedSource(kind),
      tone: gvFeedTone(event),
      uiState: gvUiStateFromEvent(event),
      runtimeState: String((event.payload || {}).status || (event.payload || {}).state || ''),
      text,
      event,
      age: ageSec < 60 ? 'now' : ageSec < 3600 ? `${Math.round(ageSec / 60)}m` : `${Math.round(ageSec / 3600)}h`
    })
    if (rows.length >= 3) break
  }
  return rows
}

/** Inspect drawer (§14): deep evidence for ONE event, docked beside the chat.
 *  Opens a closeable workspace tile; falls back to the cockpit when the host
 *  has no openWorkspace (older desktops). */
function openEventDrawer(row) {
  const canOpen = host && typeof host.openWorkspace === 'function'
  if (!canOpen) {
    host.navigate(ROUTE)
    return
  }
  const title = `${row.source} · ${row.text}`.slice(0, 80)
  const close = host.openWorkspace(`gv-event-${row.key}`, {
    title,
    dock: { pane: 'workspace', pos: 'right' },
    minWidth: '23rem',  // UI audit: 368px default; the host pane resizes it
    render: () => jsx(GvEventDetail, { row, onClose: close })
  })
}

/** Live supervisor block in the transcript (`::godsv-live{stage="…"}`).
 *
 *  The spec: the conversation is the live cockpit — compact, self-updating
 *  state instead of separate pages. Reads the real completion record + the
 *  session's recent voice events (same sources as the strip/feed), updates
 *  itself every 4s while it matters, and offers Inspect on click. */
// --- the criteria editor, shared by both goal surfaces ------------------------
//
// The transcript's plan block and the composer's plan row show the SAME
// criteria, so they must also EDIT them the same way: one hook, one write path
// (`POST /completion/criteria` with the record's `expected_revision` fence and
// an idempotent `request_id`), one place where a 409 is handled. This is the
// block that used to live inside `GvLiveBlock`, moved — not rewritten — so there
// is still exactly one editor and no second copy of the contract.

function gvNewRequestId() {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : String(Date.now()) + ':' + Math.random().toString(36).slice(2)
}

function useGvCriteriaEditor({ sid, revision, canEdit, onChanged }) {
  const [editDraft, setEditDraft] = useState(null)
  const [opError, setOpError] = useState('')
  const [opBusy, setOpBusy] = useState(false)
  const [lastOp, setLastOp] = useState(null)
  const runOp = async (op) => {
    setOpBusy(true)
    setOpError('')
    setLastOp(op)
    try {
      await rest('/completion/criteria', {
        method: 'POST',
        body: scopedBody(sid, {
          ...op.body,
          expected_revision: revision,
          request_id: op.requestId
        })
      })
      setLastOp(null)
      setEditDraft(null)
      if (typeof onChanged === 'function') onChanged()
    } catch (err) {
      const msg = String((err && err.message) || err)
      if (/409|conflict/i.test(msg)) {
        // Stale view: reload, keep the intent, retry under a fresh id.
        setOpError('Criteria changed in the meantime — reloaded, please retry.')
        if (typeof onChanged === 'function') onChanged()
        setLastOp({ body: op.body, requestId: gvNewRequestId() })
      } else {
        setOpError(msg.slice(0, 140))
      }
    } finally {
      setOpBusy(false)
    }
  }
  const retryLastOp = () => {
    if (lastOp && !opBusy) runOp(lastOp)
  }
  const startEdit = (item) =>
    setEditDraft({ id: item.id, text: String(item.text || ''), requestId: gvNewRequestId() })
  const submitEdit = () => {
    if (!editDraft || opBusy) return
    const text = editDraft.text.trim()
    if (!text) return
    runOp({ body: { op: 'update', id: editDraft.id, text }, requestId: editDraft.requestId })
  }
  const removeItem = (item) => {
    if (!opBusy) runOp({ body: { op: 'remove', id: item.id }, requestId: gvNewRequestId() })
  }
  // The plan surface adds, reorders and re-classifies points; all three are the
  // same contract as an edit (`/completion/criteria`, revision fence, request
  // id), which is why they live here instead of next to the buttons.
  const addCriteria = (texts) => {
    const list = (Array.isArray(texts) ? texts : [texts])
      .map((text) => String(text || '').trim())
      .filter(Boolean)
    if (!list.length || opBusy) return
    runOp({ body: { op: 'add', criteria: list }, requestId: gvNewRequestId() })
  }
  const moveItem = (item, orientation, anchorId) => {
    if (!item || !anchorId || opBusy) return
    const body = { op: 'move', id: item.id }
    if (orientation === 'before') body.before = anchorId
    else body.after = anchorId
    runOp({ body, requestId: gvNewRequestId() })
  }
  const setBlocking = (id, blocking) => {
    if (!id || opBusy) return
    runOp({ body: { op: 'update', id, blocking: !!blocking }, requestId: gvNewRequestId() })
  }
  const removeItemById = (id) => {
    if (!id || opBusy) return
    runOp({ body: { op: 'remove', id }, requestId: gvNewRequestId() })
  }
  return {
    editDraft,
    setEditDraft,
    opBusy,
    opError,
    lastOp,
    retryLastOp,
    startEdit,
    submitEdit,
    removeItem,
    addCriteria,
    moveItem,
    setBlocking,
    removeItemById,
    editable: !!canEdit
  }
}

/** One criteria list + its operation error, rendered identically wherever the
 *  plan is shown. `items` are the record's `dod` entries (`id`, `text`,
 *  `status`) — the runtime's own shape, never a UI-invented status. */
function GvCriteriaList({ items, editor, className }) {
  const list = Array.isArray(items) ? items : []
  if (!list.length) return null
  return jsxs('div', { className: className || 'space-y-1', 'data-godsv-criteria': String(list.length), children: [
    ...list.map((item) => {
      const st = String((item && item.status) || 'open')
      const tone =
        st === 'verified'
          ? 'var(--ui-success, #5fb45c)'
          : st === 'failed'
            ? 'var(--ui-danger, #ff6b7d)'
            : 'var(--ui-text-tertiary, #8b8b8b)'
      const glyph = st === 'verified' ? '✓' : st === 'failed' ? '✕' : '○'
      const editing = !!(editor.editDraft && editor.editDraft.id === (item && item.id))
      return jsxs(
        'div',
        {
          className: 'flex items-start gap-1.5',
          'data-godsv-criterion': st,
          children: [
            jsx('span', { style: { color: tone }, 'aria-hidden': 'true', children: glyph }),
            editing
              ? jsxs('span', { className: 'flex flex-1 items-center gap-1.5', children: [
                  jsx('input', {
                    className:
                      'flex-1 rounded border border-border bg-transparent px-1.5 py-0.5 text-[12px] text-foreground',
                    'aria-label': 'Criterion text',
                    value: editor.editDraft.text,
                    autoFocus: true,
                    disabled: editor.opBusy,
                    onChange: (e) => editor.setEditDraft({ ...editor.editDraft, text: e.target.value }),
                    onKeyDown: (e) => {
                      if (e.key === 'Enter') editor.submitEdit()
                      if (e.key === 'Escape') editor.setEditDraft(null)
                    }
                  }),
                  jsx('button', {
                    className:
                      'shrink-0 rounded border border-border px-1.5 py-0.5 text-[11px] text-(--ui-text-secondary)',
                    disabled: editor.opBusy,
                    onClick: editor.submitEdit,
                    children: editor.opBusy ? '…' : 'Save'
                  }),
                  jsx('button', {
                    className: 'shrink-0 px-1 text-[11px] text-(--ui-text-secondary)',
                    onClick: () => editor.setEditDraft(null),
                    children: 'Cancel'
                  })
                ] })
              : jsxs('span', { className: 'flex flex-1 items-start justify-between gap-2', children: [
                  jsx('span', { className: 'text-(--ui-text-secondary)', children: item.text }),
                  editor.editable
                    ? jsxs('span', { className: 'flex shrink-0 gap-1.5', children: [
                        jsx('button', {
                          className: 'text-[11px] text-(--ui-text-secondary) hover:text-foreground',
                          title: 'Edit criterion',
                          'aria-label': `Edit criterion: ${String(item.text || '').slice(0, 60)}`,
                          onClick: () => editor.startEdit(item),
                          children: '✎'
                        }),
                        jsx('button', {
                          className: 'text-[11px] text-(--ui-text-secondary) hover:text-foreground',
                          title: 'Remove criterion',
                          'aria-label': `Remove criterion: ${String(item.text || '').slice(0, 60)}`,
                          onClick: () => editor.removeItem(item),
                          children: '✕'
                        })
                      ] })
                    : null
                ] })
          ]
        },
        String((item && item.id) || 'row')
      )
    }),
    null
  ].filter(Boolean) })
}

function GvLiveBlock({ attrs, streaming }) {
  // Stage 2 (plan): a historical block belongs to ITS session — the backend
  // stamps `sid` into the ::godsv-live marker, so an old card never becomes
  // today's status card. Focused session is the fallback for blocks that
  // predate the attribute. Query keys are shared with the strip: one cache
  // and one poll per session, no matter how many cards render.
  const focusedSid = useFocusedSessionId()
  const sid = String((attrs && attrs.sid) || '') || focusedSid
  const completionQ = useQuery({
    queryKey: [ID, 'completion', sid],
    queryFn: () => rest(completionPath(sid)),
    enabled: !!sid,
    refetchInterval: 5_000,
    staleTime: 3_000
  })
  const feedQ = useQuery({
    queryKey: [ID, 'chat-feed', sid],
    queryFn: () => rest(eventsPath(sid, 8)),
    enabled: !!sid,
    refetchInterval: 5_000,
    staleTime: 3_000
  })
  const record = completionQ.data || {}
  const state = String(record.state || '')
  const goal = String(record.goal || '').trim()
  const dod = Array.isArray(record.dod) ? record.dod : []
  const verifiedCount = dod.filter((item) => String((item && item.status) || 'open') === 'verified').length
  const failedCount = dod.filter((item) => String((item && item.status) || 'open') === 'failed').length
  const verdict = record.last_verdict || {}
  const blockers = (verdict.blocking_failures || []).slice(0, 3)
  const rows = gvFeedRows(feedQ.data).slice(0, 3)
  // --- Session-UI Stufe 3 (plan §5): criteria editable where they are shown.
  // Inline editor with revision fence (expected_revision), idempotent retry
  // (request_id), pending/error/retry states — and deliberately NO status
  // path: an edit can change text or drop an item, never fake verification.
  const [dodOpen, setDodOpen] = useState(false)
  // The editor is shared with the composer's plan row (useGvCriteriaEditor
  // above): one write path, one revision fence, one 409 handler.
  const criteriaEditable = !!sid && !!state && state !== 'VERIFIED_COMPLETE'
  const editor = useGvCriteriaEditor({
    sid,
    revision: record.revision,
    canEdit: criteriaEditable,
    onChanged: () => {
      completionQ.refetch()
      feedQ.refetch()
    }
  })
  const dodRows =
    dodOpen && dod.length
      ? jsx(GvCriteriaList, { items: dod, editor, className: 'mt-1.5 space-y-1' })
      : null
  const dodError = editor.opError
    ? jsxs('div', { className: 'mt-1 flex items-center gap-2 text-(--ui-text-secondary)', children: [
        jsx('span', { children: '⚠ ' + editor.opError }),
        editor.lastOp
          ? jsx('button', {
              className: 'rounded border border-border px-1.5 py-0.5 text-[11px]',
              onClick: editor.retryLastOp,
              children: 'Retry'
            })
          : null
      ] })
    : null
  if (!state && !goal && !rows.length) return null
  // The completion record is the truth. The directive's `stage` attribute is
  // only a hint while the record is still loading — a finished goal must
  // never render as "working" (owner requirement: never display working when
  // the session is not working).
  const stateStage =
    state === 'VERIFIED_COMPLETE' ? 'verified'
      : state === 'INCOMPLETE' ? 'rejected'
      : state === 'VERIFYING' ? 'verifying'
      : state === 'WORKING' ? 'working'
      : state ? 'state'
      : ''
  const hintStage = String((attrs && attrs.stage) || '').toLowerCase()
  const stage = stateStage || hintStage || 'status'
  const headline =
    stage === 'verified' ? '✓ VERIFIED COMPLETE'
      : stage === 'rejected' ? '✕ COMPLETION REJECTED'
      : stage === 'verifying' ? 'VERIFYING'
      : stage === 'state' ? state
      : stage === 'status' ? 'WATCHING'
      : 'WORKING'
  // The active phase is ONE orb, projected from the same runtime facts the
  // headline names (`record.state` + the transcript's `streaming` flag) — the
  // `●`/`◉` glyphs are gone. Terminal verdicts are not work in progress, so
  // they keep their static mark and show no orb. An UNKNOWN backend state gets
  // no orb either: `gvOrbState` returns null rather than defaulting to
  // `working`, which would re-introduce the "working while not working" lie.
  const orb =
    stage === 'verified' || stage === 'rejected' ? null
      : stage === 'verifying' ? 'solving'
        : stage === 'status' ? 'breathing'
          : gvOrbState(state || 'WORKING', { streaming })
  // A session without a goal yet has no other work surface: this card IS the
  // work, so the phase is central and takes the avatar-scale orb. With a goal
  // the card is about the goal — the orb stays compact inline.
  const orbSize = !orb || goal ? GV_ORB_INLINE : GV_ORB_FOCUS
  return jsxs('div', {
    className: 'my-2 rounded-lg border border-border bg-card/60 px-3.5 py-2.5 text-[12px]',
    'data-godsv-live': stage,
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx('span', {
          className: 'text-[11px] font-semibold tracking-[0.22em]',
          style: { color: 'var(--ui-text-primary, #f5f5f5)' },
          children: 'SKALDIR'
        }),
        jsx(GvOrb, { state: orb, size: orbSize, label: headline }),
        jsx('span', { className: 'text-(--ui-text-secondary)', children: headline })
      ] }),
      goal
        ? jsxs('div', {
            className: 'mt-1.5 text-(--ui-text-secondary)',
            onClick: dod.length ? () => setDodOpen((v) => !v) : undefined,
            style: dod.length ? { cursor: 'pointer' } : undefined,
            title: dod.length ? (dodOpen ? 'Hide criteria' : 'Show and edit criteria') : undefined,
            children: [
              jsx('span', { className: 'text-foreground', children: goal.slice(0, 120) }),
              dod.length
                ? jsx('span', {
                    children: ` · ${verifiedCount}/${dod.length} verified${failedCount ? ` · ${failedCount} failed` : ''} ${dodOpen ? '▾' : '▸'}`
                  })
                : null
            ]
          })
        : null,
      jsx(GvStrand, {
        steps: gvVerificationSteps({
          state,
          stage,
          total: dod.length,
          verifiedCount,
          failedCount,
          blockers
        })
      }),
      dodRows,
      dodError,
      ...rows.map((row) =>
        jsxs('div', { className: 'mt-1 flex items-start gap-1.5', children: [
          jsx(GvNode, {
            uiState: row.uiState,
            label: row.text
          })
        ] }, row.key)
      ),
      ...(stage === 'rejected' && blockers.length
        ? blockers.map((blocker) =>
            jsx('div', { className: 'mt-1 text-(--ui-text-secondary)', children: `✗ ${String(blocker).slice(0, 150)}` }, `b-${blocker}`)
          )
        : []),
      stage === 'rejected' && blockers.length
        ? jsx('div', { className: 'mt-1.5', children: 'Voice rejected the completion — redirect sent, no user input needed.' })
        : null,
      jsxs('div', { className: 'mt-2 flex items-center gap-2', children: [
        // Contextual, not navigational: the event drawer opens beside the chat
        // for the newest event; when there is no event to show, the same row
        // expands the criteria instead. UI audit (P1 #1): the old fallback
        // button navigated to the diagnostics cockpit — the chat must never
        // send the user to a control room to read its own state.
        rows.length
          ? jsx('button', {
              className: 'rounded border border-border px-2 py-0.5 text-[11px] hover:bg-(--chrome-action-hover)',
              onClick: () => openEventDrawer(rows[0]),
              children: 'Inspect'
            })
          : null,
        dod.length
          ? jsx('button', {
              className: 'rounded border border-border px-2 py-0.5 text-[11px] hover:bg-(--chrome-action-hover)',
              'aria-expanded': dodOpen ? 'true' : 'false',
              onClick: () => setDodOpen((value) => !value),
              children: dodOpen ? 'Hide criteria' : `Criteria (${dod.length})`
            })
          : null
      ] })
    ]
  })
}

/** The drawer body: what happened, why, and the evidence behind it. */
function GvEventDetail({ row, onClose }) {
  // Keyboard flow (UI audit): Escape closes the drawer from anywhere inside it.
  useEffect(() => {
    const onKey = (ev) => {
      if (ev.key === 'Escape' && typeof onClose === 'function') {
        ev.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const event = (row && row.event) || {}
  const payload = event.payload || {}
  const tone = GV_STATE_TONES[(row && row.tone) || 'idle']
  const label = (row && row.source) || 'VOICE'
  const text = (row && row.text) || String(event.kind || 'event')
  const when = event.ts ? new Date(Number(event.ts) * 1000).toLocaleString() : '—'
  const pairs = []
  if (payload.status) pairs.push(['status', String(payload.status)])
  if (payload.reason) pairs.push(['reason', String(payload.reason)])
  if (payload.trigger) pairs.push(['trigger', String(payload.trigger)])
  if (payload.pause) pairs.push(['pause', String(payload.pause)])
  if (payload.ttl != null) pairs.push(['ttl', `${payload.ttl}s`])
  if (payload.fingerprint) pairs.push(['fingerprint', String(payload.fingerprint)])
  if (payload.escalate != null) pairs.push(['escalate', String(payload.escalate)])
  const blockers = Array.isArray(payload.blockers) ? payload.blockers : []
  const majors = Array.isArray(payload.majors) ? payload.majors : []
  const evidence = Array.isArray(payload.evidence) ? payload.evidence : []
  return jsxs('div', { className: 'flex h-full flex-col gap-3 p-4 text-sm overflow-auto', children: [
    jsxs('div', { className: 'flex items-center gap-2', children: [
      jsx('span', { className: 'size-1.5 shrink-0 rounded-full', style: { background: tone } }),
      jsx('span', {
        className: 'font-mono text-[11px] tracking-wide',
        style: { color: label === 'VOICE' ? 'var(--ui-text-primary, #f5f5f5)' : 'var(--ui-text-secondary, #9b9b9b)' },
        children: label
      }),
      jsx('span', { className: 'text-(--ui-text-secondary) text-xs', children: "Skaldir Agent event" })
    ] }),
    jsx('div', { className: 'text-base font-medium leading-snug', children: text }),
    jsx('div', {
      className: 'font-mono text-[11px] text-(--ui-text-secondary)',
      children: `${when} · session ${event.session || '—'} · step ${event.step ?? '—'} · seq ${event.seq ?? '—'}`
    }),
    pairs.length
      ? jsx('div', { className: 'space-y-1', children: pairs.map(([key, value]) =>
          jsxs('div', { className: 'flex gap-2', children: [
            jsx('span', { className: 'w-24 shrink-0 font-mono text-[11px] text-(--ui-text-secondary)', children: key }),
            jsx('span', { className: 'text-xs break-all', children: value })
          ] }, key))
        })
      : null,
    blockers.length
      ? jsxs('div', { children: [
          jsx('div', { className: 'text-xs font-medium mb-1', children: `Blocking (${blockers.length})` }),
          ...blockers.map((blob) =>
            jsx('div', { className: 'text-xs leading-snug', style: { color: GV_STATE_TONES.bad }, children: `✗ ${blob}` }, String(blob))
          )
        ] })
      : null,
    majors.length
      ? jsxs('div', { children: [
          jsx('div', { className: 'text-xs font-medium mb-1', children: 'Major' }),
          ...majors.map((blob) =>
            jsx('div', { className: 'text-xs leading-snug', style: { color: GV_STATE_TONES.warn }, children: `! ${blob}` }, String(blob))
          )
        ] })
      : null,
    evidence.length
      ? jsxs('div', { children: [
          jsx('div', { className: 'text-xs font-medium mb-1', children: 'Evidence' }),
          jsx('div', { className: 'space-y-0.5 font-mono text-[11px] text-(--ui-text-secondary)', children: evidence.map((line, index) =>
            jsx('div', { children: line }, `e${index}`)
          ) })
        ] })
      : null,
    jsx('div', { className: 'mt-auto pt-3', children: jsx('button', {
      className: 'rounded border border-border px-3 py-1.5 text-xs hover:bg-(--chrome-action-hover)',
      onClick: () => host.navigate(ROUTE),
      children: 'Open diagnostics →'
    }) })
  ] })
}
/** The plan — goal, criteria and evidence, in the chat, expanded by default.
 *
 *  Owner decision 17.09. (and the UI audit before it): the goal belongs to the
 *  conversation. What used to be a one-line strip with a cockpit button is now a
 *  calm, hierarchical, quietly editable plan:
 *
 *    Goal            the runtime's goal + revision + how far it is verified
 *    Part of the goal  the criteria the runtime holds (`dod`) — the sub-outcomes
 *                    that make up the goal, each expandable to its evidence
 *    Backed by evidence  the independent verdict: evidence lines, blockers,
 *                    next actions — never the criteria list again
 *
 *  A goal counts as satisfied ONLY here when every blocking criterion is
 *  `verified`; that sentence is derived from the runtime's own counts, never
 *  asserted on its own. Nothing on this surface invents state: every value comes
 *  from `/completion` and `/events`, every edit goes through
 *  `/completion/criteria` (and `/goal`) with the record's `expected_revision`
 *  fence and an idempotent `request_id`, and nothing is shown as adopted before
 *  the refreshed record confirms it.
 *
 *  Active work appears as ONE thinking-orb and only where the runtime names it:
 *  an item carries an orb when the newest feed event mentions that criterion,
 *  otherwise the goal row carries the phase. No second state machine, no
 *  invented per-item activity.
 */
function GvPlanRow() {
  const sid = useFocusedSessionId()
  const completionQ = useQuery({
    queryKey: [ID, 'completion', sid],
    queryFn: () => rest(completionPath(sid)),
    enabled: !!sid,
    refetchInterval: 5_000,
    staleTime: 3_000
  })
  const feedQ = useQuery({
    queryKey: [ID, 'chat-feed', sid],
    queryFn: () => rest(eventsPath(sid, 6)),
    enabled: !!sid,
    refetchInterval: 4_000,
    staleTime: 2_000
  })
  const c = completionQ.data || {}
  const goal = String(c.goal || '').trim()
  const state = String(c.state || '')
  // Criteria come from the runtime's `dod`; a payload that only carries the
  // derived `requirements` view (older backend) is mapped onto the same shape so
  // the plan never shows a different list than the one being edited.
  const dod = Array.isArray(c.dod) && c.dod.length
    ? c.dod
    : (Array.isArray(c.requirements) ? c.requirements : []).map((r) => ({
        id: r.id,
        text: r.text,
        blocking: r.blocking,
        status: r.verified ? 'verified' : 'open'
      }))
  const total = Number(c.total_count || 0)
  const verified = Number(c.verified_count || 0)
  const verdict = c.last_verdict || {}
  const blockers = (verdict.blocking_failures || []).slice(0, 6)
  const majors = (verdict.major_failures || []).slice(0, 6)
  const evidence = (verdict.evidence || []).slice(0, 8)
  const nextActions = (verdict.next_actions || []).slice(0, 5)
  const feed = gvFeedRows(feedQ.data)
  const activeRow = feed[0] || null
  // The feed window can be CUT (limit + the live 45min rule). A cut window is
  // reported and walkable with the cursor — the audit's AF-14 contract, kept
  // here as one quiet line instead of a strip.
  const feedMeta = gvFeedMeta(feedQ.data)
  const [olderRows, setOlderRows] = useState([])
  const [olderCursor, setOlderCursor] = useState(0)
  const safeOlderRows = Array.isArray(olderRows) ? olderRows : []
  const [olderBusy, setOlderBusy] = useState(false)
  const [olderError, setOlderError] = useState('')
  const loadOlderEvents = async () => {
    const cursor = olderCursor || feedMeta.nextBefore
    if (!cursor || olderBusy) return
    setOlderBusy(true)
    setOlderError('')
    try {
      const page = await rest(eventsPath(sid, 40, cursor))
      const known = new Set([...feed, ...safeOlderRows].map((row) => row.key))
      const fresh = gvFeedRows(page, { includeStale: true }).filter((row) => !known.has(row.key))
      setOlderRows([...safeOlderRows, ...fresh])
      setOlderCursor(Number((page && page.next_before) || 0))
    } catch (err) {
      setOlderError(gvGoalErrorMessage(err))
    } finally {
      setOlderBusy(false)
    }
  }
  const feedOlderMessage = feedMeta.remaining
    ? `${feedMeta.remaining} older events outside the window`
    : feedMeta.staleCount
      ? `${feedMeta.staleCount} older events hidden by the live window`
      : ''
  const goalAction = useGoalActionFailure()
  const [goalEdit, setGoalEdit] = useState(false)
  const [draft, setDraft] = useState('')
  const [pendingGoal, setPendingGoal] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [openItems, setOpenItems] = useState({})
  const [selected, setSelected] = useState([])
  const [addOpen, setAddOpen] = useState(false)
  const [addDraft, setAddDraft] = useState('')
  // Expanded by default (owner): the plan IS the goal view in chat. Collapsing
  // is one click away and remembered for the session only.
  const [collapsed, setCollapsed] = useState(false)
  const criteriaEditable = !!sid && !!state && state !== 'VERIFIED_COMPLETE'
  const editor = useGvCriteriaEditor({
    sid,
    revision: c.revision,
    canEdit: criteriaEditable,
    onChanged: () => {
      completionQ.refetch()
      feedQ.refetch()
    }
  })
  // The runtime confirmed the edit when the refreshed record carries it.
  if (pendingGoal && goal === pendingGoal) setPendingGoal('')
  const blockingOpen = dod.filter(
    (item) => item.blocking !== false && String(item.status || 'open') !== 'verified'
  ).length
  const satisfied = dod.length > 0 && blockingOpen === 0
  const goalOrb = gvOrbState(state, {})
  const tone = gvStateTone(state)
  const elapsed = gvElapsed(c.started)
  /** The ONE orb, placed at the item the running event actually names. */
  const orbForItem = (item) => {
    if (!activeRow) return null
    const needle = String(item.text || '').trim().toLowerCase().slice(0, 40)
    if (!needle) return null
    const haystack =
      `${activeRow.text} ${JSON.stringify((activeRow.event || {}).payload || {})}`.toLowerCase()
    if (!haystack.includes(needle)) return null
    return GV_NODE_ORB[activeRow.uiState] || null
  }
  const saveGoal = async () => {
    const text = draft.trim()
    if (!text || text === goal) {
      setGoalEdit(false)
      return
    }
    setSaving(true)
    setSaveError('')
    try {
      const res = await rest('/goal', { method: 'POST', body: scopedBody(sid, { goal: text }) })
      if (res && res.error) throw new Error(String(res.error))
      setPendingGoal(text)
      setGoalEdit(false)
      completionQ.refetch()
    } catch (err) {
      setSaveError(String((err && err.message) || err || 'save failed'))
    } finally {
      setSaving(false)
    }
  }
  const submitAdd = () => {
    const text = addDraft.trim()
    if (!text) return
    editor.addCriteria([text])
    setAddDraft('')
    setAddOpen(false)
  }
  const toggleSelected = (id) =>
    setSelected((list) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]))
  const btn = 'rounded px-2 py-0.5 text-[11px] text-(--ui-text-secondary) hover:bg-(--chrome-action-hover)'
  if (!goal && !goalEdit) return null
  const goalRow = goalEdit
    ? jsxs('div', { className: 'flex items-center gap-2 px-2 py-1', children: [
        jsx('span', { className: 'shrink-0 text-[11px] uppercase tracking-[0.12em] text-(--ui-text-tertiary)', children: 'Goal' }),
        jsx('input', {
          className: 'min-w-0 flex-1 rounded border border-border bg-transparent px-2 py-0.5 text-xs',
          'aria-label': 'Goal text',
          value: draft,
          onChange: (event) => setDraft(event.target.value),
          onKeyDown: (event) => {
            if (event.key === 'Enter') saveGoal()
            if (event.key === 'Escape') setGoalEdit(false)
          }
        }),
        jsx('button', { className: btn, onClick: saveGoal, disabled: saving, children: saving ? 'Saving…' : saveError ? 'Retry' : 'Save' }),
        jsx('button', { className: btn, onClick: () => { setGoalEdit(false); setSaveError('') }, children: 'Cancel' }),
        saveError
          ? jsx('span', { className: 'text-[11px]', style: { color: 'var(--ui-danger, #ff6b7d)' }, children: saveError.slice(0, 80) })
          : null
      ] })
    : jsxs('div', { className: 'flex items-center gap-2 px-2.5 py-0.5 text-[11px]', children: [
        jsx('button', {
          className: `${btn} shrink-0`,
          'data-godsv-plan-toggle': '1',
          'aria-expanded': collapsed ? 'false' : 'true',
          'aria-label': collapsed ? 'Expand the plan' : 'Collapse the plan',
          onClick: () => setCollapsed((value) => !value),
          children: collapsed ? '▸' : '▾'
        }),
        jsx('span', {
          className: 'shrink-0 text-[11px] uppercase tracking-[0.12em] text-(--ui-text-tertiary)',
          children: 'Goal'
        }),
        goalOrb ? jsx(GvOrb, { state: goalOrb, label: state || 'working' }) : null,
        jsx('span', {
          className: 'min-w-0 truncate text-foreground',
          title: goal,
          children: goal
        }),
        jsx('span', {
          className: 'ml-auto shrink-0 font-mono text-(--ui-text-secondary)',
          children: [
            total ? `${verified}/${total}` : dod.length ? `${verified}/${dod.length}` : '',
            satisfied ? 'met' : '',
            state && !satisfied ? state : '',
            `rev ${Number(c.revision || 0)}`,
            elapsed,
            pendingGoal ? 'adopting…' : ''
          ].filter(Boolean).join(' · ')
        }),
        jsx('button', {
          className: `${btn} shrink-0`,
          'aria-label': 'Edit goal',
          onClick: () => {
            setDraft(goal)
            setGoalEdit(true)
          },
          children: '✎'
        })
      ] })
  const selectionBar = selected.length
    ? jsxs('div', {
        className: 'mx-2 mt-1 flex items-center gap-2 rounded-md border border-border px-2 py-1 text-[11px]',
        'data-godsv-plan-selection': String(selected.length),
        children: [
          jsx('span', { className: 'shrink-0 text-(--ui-text-secondary)', children: `${selected.length} selected` }),
          jsx('button', {
            className: btn,
            onClick: () => {
              selected.forEach((id) => editor.setBlocking?.(id, true))
              setSelected([])
            },
            children: 'required'
          }),
          jsx('button', {
            className: btn,
            onClick: () => {
              selected.forEach((id) => editor.setBlocking?.(id, false))
              setSelected([])
            },
            children: 'optional'
          }),
          jsx('button', {
            className: btn,
            onClick: () => {
              selected.forEach((id) => editor.removeItemById?.(id))
              setSelected([])
            },
            children: 'remove'
          }),
          jsx('button', { className: btn, onClick: () => setSelected([]), children: 'Clear selection' })
        ]
      })
    : null
  const criteriaRows = dod.map((item, index) => {
    const id = String(item.id || `row-${index}`)
    const status = String(item.status || 'open')
    const open = !!openItems[id]
    const orb = orbForItem(item)
    const glyph = status === 'verified' ? '✓' : status === 'failed' ? '✕' : '○'
    const statusTone =
      status === 'verified'
        ? 'var(--ui-success, #5fb45c)'
        : status === 'failed'
          ? 'var(--ui-danger, #ff6b7d)'
          : 'var(--ui-text-tertiary, #8b8b8b)'
    const editing = !!(editor.editDraft && editor.editDraft.id === item.id)
    const itemEvidence = evidence.filter((line) =>
      String(line).toLowerCase().includes(String(item.text || '').toLowerCase().slice(0, 30))
    )
    const itemBlockers = blockers.filter((line) =>
      String(line).toLowerCase().includes(String(item.text || '').toLowerCase().slice(0, 30))
    )
    return jsxs('div', {
      className: 'px-2.5',
      'data-godsv-plan-item': id,
      children: [
        editing
          ? jsxs('div', { className: 'flex items-center gap-1.5 py-0.5', children: [
              jsx('input', {
                className: 'min-w-0 flex-1 rounded border border-border bg-transparent px-1.5 py-0.5 text-xs text-foreground',
                'aria-label': 'Criterion text',
                value: editor.editDraft.text,
                autoFocus: true,
                disabled: editor.opBusy,
                onChange: (e) => editor.setEditDraft({ ...editor.editDraft, text: e.target.value }),
                onKeyDown: (e) => {
                  if (e.key === 'Enter') editor.submitEdit()
                  if (e.key === 'Escape') editor.setEditDraft(null)
                }
              }),
              jsx('button', { className: btn, disabled: editor.opBusy, onClick: editor.submitEdit, children: editor.opBusy ? '…' : 'Save' }),
              jsx('button', { className: btn, onClick: () => editor.setEditDraft(null), children: 'Cancel' })
            ] })
          : jsxs('div', { className: 'flex items-center gap-1.5 py-0.5 text-[11px]', children: [
              jsx('input', {
                type: 'checkbox',
                className: 'size-3 shrink-0',
                'aria-label': `Select criterion: ${String(item.text || '').slice(0, 60)}`,
                checked: selected.includes(String(item.id)),
                disabled: !criteriaEditable,
                onChange: () => toggleSelected(String(item.id))
              }),
              jsx('button', {
                className: `shrink-0 ${btn}`,
                'aria-expanded': open ? 'true' : 'false',
                'aria-label': open ? 'Hide evidence' : 'Show evidence',
                onClick: () => setOpenItems((map) => ({ ...map, [id]: !map[id] })),
                children: open ? '▾' : '▸'
              }),
              orb
                ? jsx(GvOrb, { state: orb, label: String(item.text || '') })
                : jsx('span', { style: { color: statusTone }, 'aria-hidden': 'true', children: glyph }),
              jsx('span', {
                className: 'min-w-0 truncate text-(--ui-text-secondary)',
                title: String(item.text || ''),
                children: item.text
              }),
              item.blocking === false
                ? jsx('span', { className: 'shrink-0 text-(--ui-text-tertiary)', children: 'optional' })
                : null,
              jsx('span', {
                className: 'ml-auto shrink-0 font-mono text-(--ui-text-tertiary)',
                children: status === 'verified' ? 'verified' : status === 'failed' ? 'failed' : 'open'
              }),
              criteriaEditable
                ? jsxs('span', { className: 'flex shrink-0 items-center gap-1', children: [
                    jsx('button', {
                      className: btn,
                      'aria-label': 'Move criterion up',
                      disabled: index === 0 || editor.opBusy,
                      onClick: () => editor.moveItem(item, 'before', String(dod[index - 1].id)),
                      children: '↑'
                    }),
                    jsx('button', {
                      className: btn,
                      'aria-label': 'Move criterion down',
                      disabled: index === dod.length - 1 || editor.opBusy,
                      onClick: () => editor.moveItem(item, 'after', String(dod[index + 1].id)),
                      children: '↓'
                    }),
                    jsx('button', { className: btn, 'aria-label': 'Edit criterion', onClick: () => editor.startEdit(item), children: '✎' }),
                    jsx('button', { className: btn, 'aria-label': 'Remove criterion', onClick: () => editor.removeItem(item), children: '✕' })
                  ] })
                : null
            ] }),
        open
          ? jsxs('div', { className: 'ml-5 mb-1 space-y-0.5 text-[11px] text-(--ui-text-secondary)', 'data-godsv-plan-evidence': id, children: [
              jsx('div', { children: `Status: ${status} · required: ${item.blocking === false ? 'no' : 'yes'}` }),
              ...itemBlockers.map((line) =>
                jsx('div', { style: { color: 'var(--ui-danger, #ff6b7d)' }, children: `✕ ${String(line).slice(0, 160)}` }, `b-${line}`)
              ),
              ...itemEvidence.map((line) =>
                jsx('div', { className: 'break-all font-mono text-[11px]', children: String(line).slice(0, 160) }, `e-${line}`)
              ),
              !itemBlockers.length && !itemEvidence.length
                ? jsx('div', { children: 'No evidence for this item yet — verification will supply it.' })
                : null
            ] })
          : null
      ]
    }, id)
  })
  const addRow = criteriaEditable
    ? addOpen
      ? jsxs('div', { className: 'flex items-center gap-1.5 px-2.5 py-0.5', children: [
          jsx('input', {
            className: 'min-w-0 flex-1 rounded border border-border bg-transparent px-1.5 py-0.5 text-xs text-foreground',
            'aria-label': 'New criterion text',
            placeholder: 'New item — what must hold?',
            value: addDraft,
            autoFocus: true,
            disabled: editor.opBusy,
            onChange: (e) => setAddDraft(e.target.value),
            onKeyDown: (e) => {
              if (e.key === 'Enter') submitAdd()
              if (e.key === 'Escape') setAddOpen(false)
            }
          }),
          jsx('button', { className: btn, disabled: editor.opBusy, onClick: submitAdd, children: editor.opBusy ? '…' : 'Add' }),
          jsx('button', { className: btn, onClick: () => setAddOpen(false), children: 'Cancel' })
        ] })
      : jsx('div', { className: 'px-2.5 pt-0.5', children: jsx('button', {
          className: btn,
          'data-godsv-plan-add': '1',
          disabled: editor.opBusy,
          onClick: () => setAddOpen(true),
          children: '+ item'
        }) })
    : null
  const activityRows = feed.map((row) =>
    jsxs('button', {
      className: 'flex w-full items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] text-left hover:bg-(--chrome-action-hover)',
      'aria-label': `Skaldir Agent event: ${row.text}`,
      onClick: () => openEventDrawer(row),
      children: [
        jsx(GvNode, { uiState: row.uiState, label: row.text, compact: true }),
        jsx('span', {
          className: 'shrink-0 font-mono text-[11px] text-(--ui-text-tertiary)',
          children: row.source
        }),
        jsx('span', { className: 'truncate text-(--ui-text-secondary)', children: row.text }),
        jsx('span', {
          className: 'ml-auto shrink-0 font-mono text-[11px] text-(--ui-text-tertiary)',
          children: row.age
        })
      ]
    }, row.key)
  )
  const activityBlock = activityRows.length
    ? jsx('div', { className: 'space-y-px px-2.5 pt-1', 'data-godsv-feed': '1', children: activityRows })
    : null
  const olderNotice = feedMeta.truncated || feedMeta.staleCount || safeOlderRows.length
    ? jsxs('div', {
        className: 'space-y-px px-2.5 pt-0.5',
        'data-godsv-feed-older': '1',
        children: [
          jsxs('span', {
            className: 'flex items-center gap-2 rounded-md px-2 py-0.5 text-[11px]',
            'aria-label': `Skaldir Agent: ${feedMeta.remaining} older events outside the window`,
            children: [
              jsx(GvNode, { uiState: 'waiting', label: 'older events', compact: true }),
              jsx('span', { className: 'truncate text-(--ui-text-secondary)', children: feedOlderMessage }),
              feedMeta.nextBefore
                ? jsx('button', {
                    className: btn,
                    disabled: olderBusy,
                    onClick: loadOlderEvents,
                    children: olderBusy ? 'Loading…' : 'Load older'
                  })
                : null
            ]
          }),
          olderError
            ? jsx('span', {
                className: 'px-2 text-[11px]',
                style: { color: 'var(--ui-danger, #ff6b7d)' },
                children: olderError.slice(0, 120)
              })
            : null,
          jsx('details', {
            className: 'px-2 text-[11px] text-(--ui-text-secondary)',
            children: [
              jsx('summary', { className: 'cursor-pointer', children: 'Details ›' }),
              jsx('pre', {
                className: 'mt-1 whitespace-pre-wrap',
                children: `remaining=${feedMeta.remaining} stale=${feedMeta.staleCount} next_before=${feedMeta.nextBefore || 0} loaded=${safeOlderRows.length}`
              })
            ]
          }),
          ...safeOlderRows.map((row) =>
            jsxs('button', {
              className: 'flex w-full items-center gap-2 rounded-md px-2 py-0.5 text-[11px] text-left hover:bg-(--chrome-action-hover)',
              'aria-label': `Skaldir Agent event: ${row.text}`,
              onClick: () => openEventDrawer(row),
              children: [
                jsx(GvNode, { uiState: row.uiState, label: row.text, compact: true }),
                jsx('span', { className: 'truncate text-(--ui-text-secondary)', children: row.text })
              ]
            })
          )
        ]
      })
    : null
  const opErrorRow = editor.opError
    ? jsxs('div', { className: 'flex items-center gap-2 px-2.5 py-0.5 text-[11px] text-(--ui-text-secondary)', children: [
        jsx('span', { children: '⚠ ' + editor.opError }),
        editor.lastOp
          ? jsx('button', { className: btn, onClick: editor.retryLastOp, children: 'Retry' })
          : null
      ] })
    : null
  const verdictBlock = jsxs('div', {
    className: 'px-2.5 pb-1 text-[11px] text-(--ui-text-secondary)',
    'data-godsv-plan-verdict': verdict.status || 'none',
    children: [
      jsx('div', {
        children: verdict.status
          ? `Verdict ${verdict.status} · ${verified}/${total || dod.length} criteria backed`
          : 'No independent verdict yet — nothing rejected, nothing certified.'
      }),
      satisfied ? jsx('div', { children: 'All required criteria are backed by evidence.' }) : null,
      blockers.length
        ? jsxs('div', { className: 'mt-0.5', children: [
            jsx('div', { className: 'font-medium text-foreground', children: `Blocked (${blockers.length})` }),
            ...blockers.map((line) =>
              jsx('div', { style: { color: 'var(--ui-danger, #ff6b7d)' }, children: `✕ ${String(line).slice(0, 160)}` }, `bl-${line}`)
            )
          ] })
        : null,
      majors.length
        ? jsxs('div', { className: 'mt-0.5', children: [
            jsx('div', { className: 'font-medium text-foreground', children: `Open, not blocking (${majors.length})` }),
            ...majors.map((line) =>
              jsx('div', { style: { color: 'var(--ui-warn, #d29922)' }, children: `! ${String(line).slice(0, 160)}` }, `mj-${line}`)
            )
          ] })
        : null,
      nextActions.length
        ? jsxs('div', { className: 'mt-0.5', children: [
            jsx('div', { className: 'font-medium text-foreground', children: 'Next steps' }),
            ...nextActions.map((line) => jsx('div', { children: `→ ${String(line).slice(0, 160)}` }, `na-${line}`))
          ] })
        : null,
      evidence.length
        ? jsxs('div', { className: 'mt-0.5', children: [
            jsx('div', { className: 'font-medium text-foreground', children: 'Evidence' }),
            ...evidence.map((line, i) =>
              jsx('div', { className: 'break-all font-mono text-[11px]', children: String(line).slice(0, 160) }, `ev-${i}`)
            )
          ] })
        : null
    ]
  })
  return jsxs('div', {
    className: 'w-full space-y-0.5 pb-1',
    'data-godsv-plan': '1',
    'data-godsv-expanded': collapsed ? '0' : '1',
    'data-godsv-sid': sid || '',
    'data-godsv-runtime': readAtom(focusedSessionAtom),
    'data-godsv-hasstate': focusedState ? '1' : '0',
    children: [
      goalRow,
      goalAction
        ? jsx('div', {
            className: 'px-2.5 text-[11px]',
            'data-godsv-goal-error': '1',
            style: { color: 'var(--ui-danger, #ff6b7d)' },
            children: `Goal action failed: ${goalAction.message}`
          })
        : null,
      collapsed
        ? null
        : jsxs(Fragment, { children: [
            jsx('div', {
              className: 'px-2.5 pt-1 text-[11px] uppercase tracking-[0.12em] text-(--ui-text-tertiary)',
              children: 'Part of the goal'
            }),
            criteriaRows.length
              ? jsx('div', { className: 'space-y-px', 'data-godsv-criteria': String(criteriaRows.length), children: criteriaRows })
              : jsx('div', {
                  className: 'px-2.5 text-[11px] text-(--ui-text-secondary)',
                  children: 'No items yet — a line with @goal in the prompt becomes a required criterion.'
                }),
            addRow,
            selectionBar,
            opErrorRow,
            activityBlock,
            olderNotice,
            jsx('div', {
              className: 'px-2.5 pt-2 text-[11px] uppercase tracking-[0.12em] text-(--ui-text-tertiary)',
              children: 'Backed by evidence'
            }),
            verdictBlock
          ] })
    ]
  })
}

// -- chrome styles: the v6 composer geometry --------------------------------
//
// The brief: a smaller, centered, shorter bar that floats further from the
// bottom edge with a softer radius (Command/Inspect surface — density and
// calm, no decoration). The engine already exposes the right tokens, so this
// is a token override instead of a layout hack, and it ships inside the
// plugin — every Hermes desktop that loads Skaldir Agent gets the same bar.
// 46rem ≈ half the old full-width bar and the width class the tool-user is
// used to; 2.5rem end padding lifts the bar off the edge without wasting the
// thread; 1.375rem matches the softer radius scale.

function installChromeStyles() {
  if (typeof document === 'undefined') return
  if (document.getElementById('godsv-chrome-styles')) return
  const style = document.createElement('style')
  style.id = 'godsv-chrome-styles'
  style.textContent = [
    ':root {',
    '  --composer-width: 46rem;',
    '  /* Chatbar (owner 17.09.): a little taller, and no bold ring around the',
    '     input. Both are existing tokens — the bar is re-tuned, not re-built:',
    '     `--composer-ring-strength: 0` collapses the ring mix to the plain input',
    '     border, the taller min-height gives the text room to breathe. */',
    '  --composer-ring-strength: 0;',
    '  --composer-input-min-height: 2.5rem;',
    '  --composer-surface-pad-y: 0.5rem;',
    '  --composer-control-size: 1.75rem;',
    '}',
    '[class~="group/composer"] {',
    '  /* element-scoped so it also wins inside [data-hud-shell] (0px there) */',
    '  --composer-shell-pad-block-end: 2.5rem;',
    '  border-radius: 1.375rem;',
    '}',
    '/* The ring is off; keep the hairline that says "this is the input". */',
    '[class~="group/composer"] [class~="group/composer-surface"] {',
    '  border-color: var(--dt-border, var(--ui-stroke-secondary, currentColor)) !important;',
    '}',
    '/* The anchored wrapper variant pads itself via a child-relative var that',
    '   never sees the element override — lift it here. */',
    'div:has(> [class~="group/composer"]) {',
    '  padding-bottom: 2.5rem;',
    '}',
    '/* UI audit: the composer keeps text and the decisions that matter —  */',
    '/* the rare AV controls (dictation, read-aloud, wake word) leave the  */',
    '/* row. Unique labels, element-scoped, nothing else is touched.      */',
    'button[aria-label="Voice dictation"],',
    'button[aria-label="Read replies aloud"],',
    'button[aria-label^="Wake word"] {',
    '  display: none !important;',
    '}'
  ].join('\n')
  document.head.appendChild(style)
}

/** Split composer text into runs, marking what Skaldir Agent will act on:
 *  the `@goal` tag and every `@mention` (tool, agent or file reference).
 *  Owner correction 13.09.: ONLY the tags light up — never the line or the
 *  prose behind them (the earlier whole-line rule turned a full @goal draft
 *  gold). Pure function — the smoke tests it directly; the overlay paints it. */
function gvDirectiveSegments(text) {
  const src = String(text || '')
  const gold = new Array(src.length).fill(false)
  let m
  const tokRe = /(^|[^\w@])@[A-Za-z][\w.-]*/g
  while ((m = tokRe.exec(src))) {
    const start = m.index + m[1].length
    for (let i = start; i < m.index + m[0].length && i < src.length; i += 1) gold[i] = true
  }
  const out = []
  let start = 0
  for (let i = 1; i <= src.length; i += 1) {
    if (i === src.length || gold[i] !== gold[start]) {
      if (i > start) out.push({ text: src.slice(start, i), gold: gold[start] })
      start = i
    }
  }
  return out
}

/** What the user actually sees — innerText keeps the pre-wrap line breaks. */
function gvEditorText(editor) {
  try {
    return editor.innerText || ''
  } catch {
    return editor.textContent || ''
  }
}

const GV_OVERLAY_STYLE_PROPS = [
  'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'letterSpacing', 'wordSpacing',
  'lineHeight', 'textIndent', 'textTransform', 'tabSize',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderWidth', 'textRendering'
]

function gvPaintOverlay(overlay, editor) {
  // Mirror the live editor exactly. The overlay paints ABOVE the editor (the
  // editor's own text must not overdraw the gold), so geometry and type have
  // to match to the pixel or the two texts read as doubled.
  overlay.className = 'godsv-hl-overlay ' + String(editor.className || '')
  overlay.style.top = editor.offsetTop + 'px'
  overlay.style.left = editor.offsetLeft + 'px'
  overlay.style.width = editor.offsetWidth + 'px'
  overlay.style.height = editor.offsetHeight + 'px'
  // The copied class list carries layout utilities (flex-1, max-h, …) that
  // would constrain the mirror — the editor's own box is the truth.
  overlay.style.flex = 'none'
  overlay.style.maxWidth = 'none'
  overlay.style.maxHeight = 'none'
  overlay.style.minHeight = '0px'
  const cs = typeof getComputedStyle === 'function' ? getComputedStyle(editor) : null
  if (cs) for (const prop of GV_OVERLAY_STYLE_PROPS) overlay.style[prop] = cs[prop]
  const segs = gvDirectiveSegments(gvEditorText(editor))
  const frag = document.createDocumentFragment()
  let painted = 0
  for (const seg of segs) {
    if (!seg.gold) {
      frag.appendChild(document.createTextNode(seg.text))
      continue
    }
    painted += 1
    const span = document.createElement('span')
    // The recognized token must fit the ACTIVE theme (owner 17.09.): the old
    // hardcoded brand gold survived every skin change and read as a foreign
    // colour in a monochrome UI. `--dt-midground` is the theme's own strong
    // foreground, so `@goal` / `@tool` / mentions are simply MORE EMPHATIC than
    // the body text — monochrome in light and dark, no hue, no literal colour.
    span.style.color = 'var(--dt-midground, var(--ui-text-primary, currentColor))'
    span.style.fontWeight = '500'
    span.textContent = seg.text
    frag.appendChild(span)
  }
  overlay.replaceChildren(frag)
  overlay.scrollTop = editor.scrollTop
  overlay.dataset.godsvDirectives = painted ? '1' : '0'
}

/** Paint recognized tokens gold inside the real composer. The core input is
 *  a contentEditable; typing, IME and the caret stay native because nothing
 *  in the editor DOM is touched — a transparent overlay in the same relative
 *  wrapper mirrors the text and colors only the recognized runs. */
function installComposerDirectiveHighlight() {
  if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') return
  if (document.getElementById('godsv-hl-styles')) return
  const style = document.createElement('style')
  style.id = 'godsv-hl-styles'
  style.textContent = [
    '.godsv-hl-overlay {',
    '  position: absolute; overflow: hidden; pointer-events: none;',
    '  user-select: none; color: transparent; z-index: 1; box-sizing: border-box;',
    '  white-space: pre-wrap; overflow-wrap: anywhere;',
    '}'
  ].join('\n')
  document.head.appendChild(style)
  const SLOT = 'composer-rich-input'
  const seen = new WeakSet()
  const initEditor = (editor) => {
    if (seen.has(editor) || !editor.parentElement) return
    seen.add(editor)
    const overlay = document.createElement('div')
    overlay.className = 'godsv-hl-overlay ' + String(editor.className || '')
    overlay.setAttribute('aria-hidden', 'true')
    // Insert BEFORE the editor: same stacking context, editor paints on top.
    editor.parentElement.insertBefore(overlay, editor)
    let queued = 0
    const paint = () => {
      queued = 0
      gvPaintOverlay(overlay, editor)
    }
    const schedule = () => {
      if (!queued && typeof requestAnimationFrame !== 'undefined') queued = requestAnimationFrame(paint)
    }
    editor.addEventListener('input', schedule)
    editor.addEventListener('scroll', () => {
      overlay.scrollTop = editor.scrollTop
    })
    new MutationObserver(schedule).observe(editor, { subtree: true, characterData: true, childList: true })
    // Layout changes (stacked toggle, window resize) never mutate the node —
    // without this the mirror keeps a stale box and the texts drift apart.
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(schedule).observe(editor)
    paint()
  }
  const scan = () => {
    document.querySelectorAll('[data-slot="' + SLOT + '"]').forEach(initEditor)
  }
  scan()
  new MutationObserver(scan).observe(document.documentElement, { subtree: true, childList: true })
}

export { gvDirectiveSegments }

export default {
  id: ID,
  name: "Skaldir Agent",
  description:
    "Live steering for agent sessions: pauses, completion guards, memory, skills and the test-money treasury — panel, chat status strip and @goal mentions (read-only).",
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    installChromeStyles()
    // The host chat's working indicator uses the SAME orb (see the function's
    // comment): one indicator, one animation, for panel and chat alike.
    publishOrbRuntime()
    installComposerDirectiveHighlight()
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: ROUTE }, render: () => jsx(Page, {}) })
    if (INTEGRATED) {
      // App build: the areas ARE the app's navigation. V2-11/V2-01: cockpit and
      // activity leave the sidebar — both are views over events the chat live
      // block + composer status strip already show inline; no unique function
      // lives only there. Verification/Tools/Settings/Treasury stay.
      const order = { verification: 53, tools: 55, settings: 56, treasury: 57 }
      for (const [view, label] of [
        ['verification', 'Verification'],
        ['tools', 'Tools'],
        ['settings', 'Settings'],
        ['treasury', 'Treasury']
      ]) {
        if (view !== 'cockpit') {
          ctx.register({
            id: `route-${view}`,
            area: ROUTES_AREA,
            data: { path: VIEW_ROUTES[view] },
            render: () => jsx(Page, {})
          })
        }
        ctx.register({
          id: `nav-${view}`,
          area: SIDEBAR_NAV_AREA,
          order: order[view],
          data: { codicon: VIEW_ICONS[view], label, path: VIEW_ROUTES[view] }
        })
      }
    } else {
      ctx.register({
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { codicon: 'comment-discussion', label: "Skaldir Agent", path: ROUTE }
      })
    }
    ctx.register({
      id: 'open',
      area: PALETTE_AREA,
      data: {
        id: 'godsv.open',
        label: "Skaldir Agent: open panel",
        keywords: ['godsv', 'voice', 'panel', 'status', 'pauses', 'memory', 'skills', 'treasury'],
        run: () => host.navigate(ROUTE)
      }
    })
    ctx.register({
      id: 'chat-empty',
      area: CHAT_EMPTY,
      data: { render: (props) => jsx(SkaldirAgentEmpty, { sessionId: props?.sessionId }) }
    })
    // Live supervisor block inside the transcript (spec: the conversation is
    // the live cockpit). The model — or the transform_llm_output hook — ends a
    // paragraph with `::godsv-live{stage="…"}`; the block renders the real
    // state and keeps updating itself.
    {
      const transcriptDirectives = sdk.TRANSCRIPT_DIRECTIVE_AREA || 'transcript.directives'
      ctx.register({
        id: 'live-block',
        area: transcriptDirectives,
        data: {
          name: 'godsv-live',
          render: (props) =>
            jsx(GvLiveBlock, {
              attrs: (props && props.attrs) || {},
              streaming: Boolean(props && props.streaming)
            })
        }
      })
    }
    if (COMPOSER) {
      if (COMPOSER.atCompletions) {
        ctx.register({
          id: 'at-completions',
          area: COMPOSER.atCompletions,
          data: { provide: goalCompletionRows }
        })
      }
      if (COMPOSER.middleware) {
        ctx.register({
          id: 'goal-middleware',
          area: COMPOSER.middleware,
          data: {
            handler: async (draft) => {
              const text = (draft && draft.text) || ''
              if (!goalCriteriaFromDraft(text).length && !goalTrajectoryFromDraft(text).length) {
                return draft
              }
              // The write must not block the send (the user's turn starts now), so it runs as a
              // task; the id it needs exists only after the app assigned the session. The task is
              // exposed so the smoke can await it — never to change behaviour.
              goalDraftTask = gvGoalDraftAfterSend(text)
              globalThis.__GV_GOAL_TASK__ = goalDraftTask
              return draft
            }
          }
        })
      }
      if (COMPOSER.top) {
        // The plan: event-fed from /completion, expanded by default, quiet, and
        // absent without a goal. It replaced a full-width strip whose only
        // affordance was "Open the Skaldir Agent cockpit" (UI audit P1 #1/#13)
        // and now carries the whole plan (goal -> criteria -> evidence).
        ctx.register({ id: 'plan-row', area: COMPOSER.top, render: () => jsx(GvPlanRow, {}) })
      }
      // No approval control in the composer (owner 17.09.): the status bar's own
      // approval menu is the single place for that safety setting.
      if (COMPOSER.microActions) {
        // S02 P1 fix (UX review wave): core ships a structural empty pill strip
        // (`composer.microActions` — use-micro-actions.ts: "Core registers
        // nothing"). Surface the design-mandated contextual actions
        // (01_PRODUCT_UX_DESIGN.md composer line): each badge either opens a
        // real panel route (host.navigate) or runs a real backend surface the
        // user already has — no dead affordances.
        //
        // Wave 9: "Docker-Image hinzufügen" was removed. It only navigated to the tools view,
        // which has no image flow, and the product has no surface for it either (no docker
        // subcommand in the CLI, no docker route in the panel API, docker_runtime.py only
        // classifies docker errors) — the badge promised a flow that does not exist.
        // A dead affordance is worse than a missing one.
        ctx.register({
          id: 'contextual-actions',
          area: COMPOSER.microActions,
          data: {
            resolve: (mctx) => {
              const actions = []
              // The tools view needs no goal and no session: it is the one badge that is
              // always valid, so a fresh chat never shows an empty strip (S02).
              actions.push({
                id: 'godsv.find-tool',
                label: 'Find tool',
                icon: 'tools',
                run: () => host.navigate(VIEW_ROUTES.tools)
              })
              // Criteria + analyze flow only make sense once a goal is set
              // (matches the strip: no goal → nothing watches it yet).
              if (mctx && mctx.sessionId) {
                actions.unshift({
                  id: 'godsv.open-verification',
                  label: 'Open plan',
                  icon: 'checklist',
                  run: () => host.navigate(VIEW_ROUTES.verification)
                })
              }
              return actions
            }
          }
        })
      }
    }
  }
}
