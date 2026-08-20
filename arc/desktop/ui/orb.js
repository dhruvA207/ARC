/* The orb, as the desktop panel draws it.
 *
 * A point cloud rather than a sphere mesh: the same approach the app window uses, kept
 * because it is what makes the thing read as *gathering* rather than as a spinner. The
 * points are the interface, so the transitions between states are the design work.
 *
 * States
 *   arriving  points converge from the left and right edges into the middle
 *   centre    a settled sphere over a tinted backdrop
 *   leaving   points break formation and stream to the right
 *   corner    a small orb, parked
 *   thinking  coloured satellites stack vertically beside the main orb
 *
 * The orb also answers to the microphone: it swells and brightens with your voice, and
 * goes amber when muted. Those are the only signals it has for "I can hear you" and
 * "I cannot", so they are deliberately unsubtle.
 */

const TAU = Math.PI * 2;

/** Matches TRANSITION_SECONDS in panel.py, so the orb and the window move together. */
const TRANSITION_SECONDS = 0.34;

/** One colour per tool category, so a glance says *what kind* of work is running —
 *  reading a file, driving the screen, going out to the web — rather than only that
 *  something is. `general` is the fallback for a tool the registry does not know. */
const CATEGORY_COLOURS = {
  filesystem: [90, 170, 255],
  shell: [150, 130, 255],
  web: [90, 215, 250],
  screen: [125, 235, 190],
  apps: [255, 185, 110],
  messaging: [255, 140, 180],
  camera: [190, 160, 255],
  input: [130, 210, 255],
  code: [160, 235, 150],
  general: [150, 175, 205],
};

/** Amber, not grey. Grey reads as "switched off"; muted is a live state you need to
 *  notice from across the desk, so it gets a hue of its own. */
const MUTED_RGB = [255, 178, 98];

/** The resting blue, used for the backdrop tint. Points span a range around it. */
const ACTIVE_RGB = [96, 186, 255];

/** Points are drawn in this many depth bands.
 *
 *  Colour is picked per band rather than per point. Assigning `fillStyle` from a
 *  template string forces the engine to parse a CSS colour every time — at 1500 points a
 *  frame that is 90,000 string allocations a second, which is what made the orb stutter
 *  and drop frames while the microphone was open. Twelve bands are visually
 *  indistinguishable from a continuous ramp and cost twelve parses instead. */
const BANDS = 12;

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

export class Orb {
  constructor(canvas, { count = 1500 } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.base = sphere(count);
    this.count = count;

    // Where each point starts when arriving: alternating sides, spread vertically, so
    // the entrance reads as two streams meeting rather than a puff of smoke.
    this.entry = new Float32Array(count * 2);
    // A fixed per-point wobble phase, so voice response looks like a surface rippling
    // rather than the whole ball scaling.
    this.phase = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      const side = i % 2 === 0 ? -1 : 1;
      this.entry[i * 2] = side * (1.6 + Math.random() * 1.4);
      this.entry[i * 2 + 1] = (Math.random() - 0.5) * 1.2;
      this.phase[i] = Math.random() * TAU;
    }

    // Scratch space for one frame of screen-space points. Preallocated because
    // allocating per frame is the other half of what made this stutter.
    this._sx = new Float32Array(count);
    this._sy = new Float32Array(count);
    this._ss = new Float32Array(count);
    this._sb = new Uint8Array(count);

    this.state = 'corner';
    this.progress = 1;
    this.spin = 0;
    //: One entry per tool currently running, in the order they started.
    this.tools = [];
    this.muted = false;

    //: Raw microphone amplitude, 0..1, and the smoothed value actually drawn. Smoothing
    //: with a fast attack and slow release: the orb should jump when you start talking
    //: and settle gently, not chatter at 30 Hz with the level packets.
    this.level = 0;
    this._level = 0;

    this._raf = null;
    this._last = 0;
    this._startedAt = performance.now();
  }

  setState(state) {
    if (state === this.state) return;
    // Arriving and leaving are transitions; the others are resting states.
    this.state = state;
    this.progress = state === 'arriving' || state === 'leaving' ? 0 : 1;
    this._startedAt = performance.now();
  }

  /** Mute is its own axis, deliberately *not* folded into setActivity.
   *
   *  Live mode emits SPEAKING and IDLE continuously while you talk, and when mute was
   *  derived from the activity string every one of those events silently un-muted the
   *  orb — so muting appeared to do nothing the moment anyone spoke. */
  setMuted(muted) {
    this.muted = Boolean(muted);
    if (this.muted) {
      this.level = 0;
      this._level = 0;
    }
  }

  setActivity(activity) {
    // Activity no longer invents markers. The satellites are driven by real tool calls
    // through setTools; a fixed three whenever ARC was thinking said the same thing
    // whether one tool ran or five, which is to say it said nothing.
    this.thinking = activity === 'THINKING' || activity === 'WORKING';
  }

  /** The tools running right now, as category names. */
  setTools(categories) {
    this.tools = Array.isArray(categories) ? categories.slice(0, 8) : [];
  }

  /** Microphone amplitude, 0..1. Muted input is ignored so a muted orb never twitches. */
  setLevel(level) {
    if (this.muted) return;
    const value = Number(level);
    this.level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  }

  start() {
    if (this._raf) return;
    const frame = (now) => {
      // Rescheduled *first*, and the body guarded. Previously this was the last
      // statement in the callback, so a single exception anywhere in step or draw
      // killed the loop permanently and the orb froze mid-conversation.
      this._raf = requestAnimationFrame(frame);
      try {
        const dt = Math.min(0.05, (now - this._last) / 1000 || 0.016);
        this._last = now;
        this._step(dt);
        this._draw();
      } catch (error) {
        // One bad frame must not cost the animation. Reported once per occurrence and
        // then dropped; the next frame is very likely fine.
        console.error('orb frame failed', error);
      }
    };
    this._raf = requestAnimationFrame(frame);
  }

  stop() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  _step(dt) {
    // Speech energy also drives the spin, so a loud sentence visibly stirs the cloud.
    this.spin += dt * (0.22 + this._level * 0.55);
    if (this.spin > TAU * 1e4) this.spin -= TAU * 1e4; // keep the float small and exact

    // Fast attack, slow release.
    const rate = this.level > this._level ? 14 : 4;
    this._level += (this.level - this._level) * Math.min(1, dt * rate);

    if (this.state !== 'arriving' && this.state !== 'leaving') return;

    // Progress comes from the clock, not from accumulated frame deltas. macOS throttles
    // an occluded webview's rAF to nothing, and with accumulated deltas the orb would
    // resume from wherever it froze — coming back to a half-finished transition minutes
    // later. Against the clock, a resumed panel is simply already settled.
    const elapsed = (performance.now() - this._startedAt) / 1000;
    this.progress = Math.min(1, elapsed / TRANSITION_SECONDS);
    if (this.progress >= 1) this.state = this.state === 'arriving' ? 'centre' : 'corner';
  }

  _draw() {
    const { ctx, canvas } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;

    // Mid-transition the panel can be reported at zero size; there is nothing to draw
    // and the gradients below would be degenerate.
    if (!w || !h) return;

    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const centred = this.state === 'centre' || this.state === 'arriving';
    const radius = Math.min(w, h) * (centred ? 0.26 : 0.30);
    const cx = centred ? w / 2 : w - radius * 1.6;
    const cy = h * 0.42;

    if (centred) this._backdrop(cx, cy, radius);
    this._points(cx, cy, radius);
    if (this.tools.length) this._satelliteStack(cx, cy, radius, centred, h);
  }

  /** A tint filling the sphere, in whatever colour the orb currently is.
   *
   *  This is what the points sit on when centred, so they read against something of
   *  their own rather than against whatever window happens to be behind the panel. It
   *  is contained *inside* the cloud and fades to nothing at the edge — an earlier
   *  version extended to 2.6× the radius, which clipped to a hard edge along the top of
   *  the panel and needed a stroked ring to hide the seam. Both are gone. */
  _backdrop(cx, cy, radius) {
    const { ctx } = this;
    const [r, g, b] = this.muted ? MUTED_RGB : ACTIVE_RGB;
    const lift = this._level * 0.10;

    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 1.06);
    glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.22 + lift).toFixed(3)})`);
    glow.addColorStop(0.55, `rgba(${r}, ${g}, ${b}, ${(0.12 + lift * 0.6).toFixed(3)})`);
    glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);

    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.06, 0, TAU);
    ctx.fill();
  }

  _points(cx, cy, radius) {
    const { ctx, base, entry, phase, count, _sx, _sy, _ss, _sb } = this;
    const sin = Math.sin(this.spin);
    const cos = Math.cos(this.spin);
    const level = this._level;

    // Arriving eases in; leaving runs the same path backwards and to the right.
    let blend = 1;
    let drift = 0;
    if (this.state === 'arriving') blend = easeOut(this.progress);
    else if (this.state === 'leaving') {
      blend = 1 - easeInOut(this.progress);
      drift = easeInOut(this.progress);
    }

    // The whole cloud swells while you speak, on top of the per-point ripple below.
    const swell = 1 + level * 0.16;
    const wobble = level * 0.13;
    const now = this.spin * 3.1;
    const sizeGain = (this.muted ? 0.9 : 1) * (1 + level * 0.35);

    // Pass one: project every point and note which depth band it lands in.
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

      // Each point breathes on its own phase, so a loud voice ripples the surface.
      const ripple = wobble ? 1 + Math.sin(now + phase[i]) * wobble : 1;
      const grow = swell * ripple;

      const px = (x * blend + ox * (1 - blend)) * grow;
      const py = (y0 * blend + oy * (1 - blend)) * grow;

      const depth = (z + 1.6) / 3.2;
      _sx[i] = cx + px * radius;
      _sy[i] = cy - py * radius;
      _ss[i] = (1.15 + depth * 2.2) * sizeGain;
      _sb[i] = Math.min(BANDS - 1, (depth * BANDS) | 0);
    }

    // Additive blending makes overlapping points build into a genuine glow rather than
    // averaging out to the flat haze they had before.
    ctx.globalCompositeOperation = 'lighter';

    // Pass two: one colour per band, then every point that belongs to it.
    for (let band = 0; band < BANDS; band += 1) {
      const depth = (band + 0.5) / BANDS;
      const alpha = Math.min(0.95, (0.44 + depth * 0.52) * blend + 0.16 * (1 - blend));

      if (this.muted) {
        const [r, g, b] = MUTED_RGB;
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
      } else {
        // Deep blue at the back, bright ice blue at the front, a shade brighter with
        // volume. Red is held well below the others on purpose: under additive blending
        // overlapping points sum, and letting red climb turns the whole cloud white.
        const red = (74 + depth * 74 + level * 20) | 0;
        const green = (168 + depth * 68 + level * 22) | 0;
        ctx.fillStyle = `rgba(${red}, ${green}, 255, ${alpha.toFixed(3)})`;
      }

      for (let i = 0; i < count; i += 1) {
        if (_sb[i] !== band) continue;
        ctx.fillRect(_sx[i], _sy[i], _ss[i], _ss[i]);
      }
    }

    ctx.globalCompositeOperation = 'source-over';
  }

  /** Coloured satellites, stacked vertically below the orb.
   *
   *  Replaces the constellation of tool cubes the app window used: same idea — one mark
   *  per thing being worked on — but a column reads as a queue, which is what it is. */
  _satelliteStack(cx, cy, radius, centred, height) {
    const { ctx } = this;
    const size = Math.max(1, radius * (centred ? 0.13 : 0.17));
    const x = cx + radius * (centred ? 1.75 : 0.0);
    const top = cy + radius * 1.15;

    // The stack has to fit the panel. At the preferred spacing a fourth tool ran off the
    // bottom edge, so the gap tightens once there are more than the panel can hold —
    // a cramped column is still readable, a clipped one is just missing information.
    const preferred = radius * (centred ? 0.46 : 0.62);
    const room = Math.max(0, height - top - size * 1.6);
    const count = this.tools.length;
    const gap = count > 1 ? Math.min(preferred, room / (count - 1)) : preferred;

    for (let i = 0; i < this.tools.length; i += 1) {
      const [r, g, b] = CATEGORY_COLOURS[this.tools[i]] || CATEGORY_COLOURS.general;
      const y = top + i * gap;
      // Each satellite breathes on its own phase so the column looks alive rather than
      // like a progress bar with three segments.
      const pulse = 0.6 + 0.4 * Math.sin(this.spin * 2.4 + i * 1.3);

      // A soft halo under each dot, so they read on a light window behind the panel too.
      const glow = ctx.createRadialGradient(x, y, 0, x, y, size * 2.4);
      glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(0.34 * pulse).toFixed(3)})`);
      glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, size * 2.4, 0, TAU);
      ctx.fill();

      ctx.beginPath();
      ctx.arc(x, y, size * (0.8 + pulse * 0.3), 0, TAU);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${(0.55 + pulse * 0.45).toFixed(3)})`;
      ctx.fill();
    }
  }
}
