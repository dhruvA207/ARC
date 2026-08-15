/* The orb, as the desktop panel draws it.
 *
 * A point cloud rather than a sphere mesh: the same approach the app window uses, kept
 * because it is what makes the thing read as *gathering* rather than as a spinner. The
 * points are the interface, so the transitions between states are the design work.
 *
 * States
 *   arriving  points converge from the left and right edges into the middle
 *   centre    a settled sphere, with a translucent halo behind it
 *   leaving   points break formation and stream to the right
 *   corner    a small orb, parked
 *   thinking  coloured satellites stack vertically beneath the main orb
 */

const TAU = Math.PI * 2;

/** Fibonacci sphere: even coverage without the pole bunching of a lat/long grid. */
function sphere(count) {
  const points = new Float32Array(count * 3);
  const step = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i += 1) {
    const y = 1 - (i / (count - 1)) * 2;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = step * i;
    points[i * 3] = Math.cos(theta) * radius;
    points[i * 3 + 1] = y;
    points[i * 3 + 2] = Math.sin(theta) * radius;
  }
  return points;
}

const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** Satellite colours for thinking mode — one per concurrent piece of work. */
const SATELLITE_COLOURS = [
  [74, 158, 255],
  [138, 120, 255],
  [86, 204, 242],
  [120, 220, 180],
  [255, 176, 102],
];

export class Orb {
  constructor(canvas, { count = 1400 } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.base = sphere(count);
    this.count = count;

    // Where each point starts when arriving: alternating sides, spread vertically, so
    // the entrance reads as two streams meeting rather than a puff of smoke.
    this.entry = new Float32Array(count * 2);
    for (let i = 0; i < count; i += 1) {
      const side = i % 2 === 0 ? -1 : 1;
      this.entry[i * 2] = side * (1.6 + Math.random() * 1.4);
      this.entry[i * 2 + 1] = (Math.random() - 0.5) * 1.2;
    }

    this.state = 'corner';
    this.progress = 1;
    this.spin = 0;
    this.satellites = 0;
    this.muted = false;
    this._raf = null;
    this._last = 0;
  }

  setState(state) {
    if (state === this.state) return;
    // Arriving and leaving are transitions; the others are resting states.
    this.state = state;
    this.progress = state === 'arriving' || state === 'leaving' ? 0 : 1;
  }

  setActivity(activity) {
    this.muted = activity === 'MUTED';
    if (activity === 'THINKING' || activity === 'WORKING') {
      this.satellites = Math.max(this.satellites, 3);
    } else {
      this.satellites = 0;
    }
  }

  start() {
    if (this._raf) return;
    const frame = (now) => {
      const dt = Math.min(0.05, (now - this._last) / 1000 || 0.016);
      this._last = now;
      this._step(dt);
      this._draw();
      this._raf = requestAnimationFrame(frame);
    };
    this._raf = requestAnimationFrame(frame);
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  _step(dt) {
    this.spin += dt * 0.22;
    if (this.state === 'arriving' || this.state === 'leaving') {
      this.progress = Math.min(1, this.progress + dt / 0.55);
      if (this.progress >= 1) this.state = this.state === 'arriving' ? 'centre' : 'corner';
    }
  }

  _draw() {
    const { ctx, canvas } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;

    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const centred = this.state === 'centre' || this.state === 'arriving';
    const radius = Math.min(w, h) * (centred ? 0.20 : 0.30);
    const cx = centred ? w / 2 : w - radius * 1.6;
    const cy = centred ? h * 0.42 : h * 0.42;

    if (centred) this._halo(cx, cy, radius);
    this._points(cx, cy, radius);
    if (this.satellites) this._satelliteStack(cx, cy, radius, centred);
  }

  /** The translucent disc behind the orb when it is front and centre.
   *
   *  Without it the point cloud sits directly on whatever window is behind and the
   *  contrast is whatever that window happens to be. */
  _halo(cx, cy, radius) {
    const { ctx } = this;
    const gradient = ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius * 2.6);
    gradient.addColorStop(0, 'rgba(12, 18, 28, 0.72)');
    gradient.addColorStop(0.55, 'rgba(12, 18, 28, 0.42)');
    gradient.addColorStop(1, 'rgba(12, 18, 28, 0)');
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 2.6, 0, TAU);
    ctx.fill();

    ctx.strokeStyle = 'rgba(74, 158, 255, 0.16)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.55, 0, TAU);
    ctx.stroke();
  }

  _points(cx, cy, radius) {
    const { ctx, base, entry, count } = this;
    const sin = Math.sin(this.spin);
    const cos = Math.cos(this.spin);

    // Arriving eases in; leaving runs the same path backwards and to the right.
    let blend = 1;
    let drift = 0;
    if (this.state === 'arriving') blend = easeOut(this.progress);
    else if (this.state === 'leaving') {
      blend = 1 - easeInOut(this.progress);
      drift = easeInOut(this.progress);
    }

    for (let i = 0; i < count; i += 1) {
      const x0 = base[i * 3];
      const y0 = base[i * 3 + 1];
      const z0 = base[i * 3 + 2];

      // Spin about Y so the cloud has depth rather than reading as a flat ring.
      const x = x0 * cos - z0 * sin;
      const z = x0 * sin + z0 * cos;

      const ex = entry[i * 2];
      const ey = entry[i * 2 + 1];

      // Off-formation position: where it came from when arriving, where it is going when
      // leaving. `drift` pushes everything right, which is the shrink animation.
      const ox = ex + drift * 2.4;
      const oy = ey * (1 - drift * 0.6);

      const px = x * blend + ox * (1 - blend);
      const py = y0 * blend + oy * (1 - blend);

      // Cheap perspective: nearer points are bigger and brighter.
      const depth = (z + 1.6) / 3.2;
      const size = (0.6 + depth * 1.5) * (this.muted ? 0.7 : 1);
      const alpha = (0.12 + depth * 0.62) * blend + 0.10 * (1 - blend);

      const sx = cx + px * radius;
      const sy = cy - py * radius;

      ctx.fillStyle = this.muted
        ? `rgba(150, 162, 176, ${alpha})`
        : `rgba(${120 + depth * 90 | 0}, ${180 + depth * 50 | 0}, 255, ${alpha})`;
      ctx.fillRect(sx, sy, size, size);
    }
  }

  /** Coloured satellites, stacked vertically below the orb.
   *
   *  Replaces the constellation of tool cubes the app window used: same idea — one mark
   *  per thing being worked on — but a column reads as a queue, which is what it is. */
  _satelliteStack(cx, cy, radius, centred) {
    const { ctx } = this;
    const gap = radius * (centred ? 0.46 : 0.62);
    const size = radius * (centred ? 0.13 : 0.17);
    const x = cx + radius * (centred ? 1.75 : 0.0);

    for (let i = 0; i < this.satellites; i += 1) {
      const [r, g, b] = SATELLITE_COLOURS[i % SATELLITE_COLOURS.length];
      const y = cy + radius * 1.15 + i * gap;
      // Each satellite breathes on its own phase so the column looks alive rather than
      // like a progress bar with three segments.
      const pulse = 0.6 + 0.4 * Math.sin(this.spin * 2.4 + i * 1.3);

      ctx.beginPath();
      ctx.arc(x, y, size * (0.75 + pulse * 0.35), 0, TAU);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${0.28 + pulse * 0.45})`;
      ctx.fill();
    }
  }
}
