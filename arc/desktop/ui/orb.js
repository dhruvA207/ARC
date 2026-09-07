/* The orb, as the desktop panel draws it.
 *
 * A point cloud rather than a sphere mesh: the points *are* the interface, so how they
 * move between states is the whole of the design. ARC lives parked in the top-right
 * corner and never moves from there. What changes is how awake it is.
 *
 * States
 *   arriving  points converge from outside the frame into the sphere (first load only)
 *   resting   a slow-breathing sphere, parked
 *
 * Two signals ride on top of whatever state it is in, and both are deliberately unsubtle
 * because they are the only things the orb can say:
 *
 *   mute      unmuted is purple and listening; muted is a darker, quieter blue. The
 *             change is never a swap. A colour front sweeps through the cloud point by
 *             point — outward from the core when it wakes, inward to the core when it
 *             settles — and every circle the front passes over flares as it changes.
 *             That is what makes it read as a thing coming to life rather than a light
 *             being switched.
 *
 *   pointer   when the cursor comes almost close enough to touch it, the circles nearest
 *             the cursor swing aside and hold a gap open around it, so whatever is behind
 *             ARC in the corner stays reachable. It reacts only from very close: this is
 *             a courtesy to the rest of the screen, not a hover target.
 */

const TAU = Math.PI * 2;

/** How long the points take to converge on first load. */
const TRANSITION_SECONDS = 0.34;

/** How long the colour front takes to cross the cloud. Slow enough to watch. */
const MUTE_MORPH_SECONDS = 0.8;

/** Steps the front is quantised into while it sweeps. Enough to look continuous, few
 *  enough that the transition still costs a handful of colour parses per frame rather
 *  than one per point. */
const MORPH_BUCKETS = 6;

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

/** Purple: unmuted, listening, awake. */
const LIVE_RGB = [150, 92, 246];

/** A darker, quieter blue than the live purple: muted, resting. Kept a blue rather than
 *  a grey — grey reads as "switched off", and a resting ARC is still running. */
const RESTING_RGB = [58, 124, 206];

/** The depth ramp for each state: what a point at the back of the cloud is, and what one
 *  at the front is. Green is held below red and blue in both, because under additive
 *  blending overlapping points sum and a climbing green turns the whole cloud white. */
const PALETTES = {
  live: { back: [84, 44, 166], front: [190, 140, 255] },
  resting: { back: [30, 74, 138], front: [120, 172, 226] },
};

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
const clamp01 = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);

/** One band of the depth ramp, in whichever palette is asked for. */
function bandColour(palette, depth, level, muted) {
  const { back, front } = palette;
  const lift = level * (muted ? 10 : 22);
  return [
    Math.min(255, (back[0] + (front[0] - back[0]) * depth + lift) | 0),
    Math.min(255, (back[1] + (front[1] - back[1]) * depth + lift) | 0),
    Math.min(255, (back[2] + (front[2] - back[2]) * depth + lift * 0.5) | 0),
  ];
}

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
    // Which step of the colour front each point is on, while one is sweeping.
    this._sbk = new Uint8Array(count);
    this._morph = new Array(BANDS * MORPH_BUCKETS).fill('rgba(0, 0, 0, 0)');

    this.state = 'resting';
    this.progress = 1;
    this.spin = 0;
    //: One entry per tool currently running, in the order they started.
    this.tools = [];

    //: ARC starts at rest. Waking it is a deliberate act — double-tap ⌘, or the menu bar.
    this.muted = true;
    this._muteFrom = true;
    //: 1 means settled. Anything less is a colour front mid-sweep.
    this._muteAnim = 1;
    this._muteChangedAt = 0;

    //: Cursor position in canvas pixels, and how close it is: 0 is far enough away to
    //: ignore, 1 is close enough to part the cloud. Fed by the native side, which polls
    //: the screen — the panel is click-through while it rests and sees no mouse events
    //: of its own.
    this.pointer = { x: -1e4, y: -1e4, near: 0 };

    //: Raw microphone amplitude, 0..1, and the smoothed value actually drawn. Smoothing
    //: with a fast attack and slow release: the orb should jump when you start talking
    //: and settle gently, not chatter at 30 Hz with the level packets.
    this.level = 0;
    this._level = 0;

    this._raf = null;
    this._last = 0;
    this._clock = 0;
    this._startedAt = performance.now();
  }

  setState(state) {
    if (state === this.state) return;
    // `arriving` is a transition; `resting` is where it lives.
    this.state = state;
    this.progress = state === 'arriving' ? 0 : 1;
    this._startedAt = performance.now();
  }

  /** Mute is its own axis, deliberately *not* folded into setActivity.
   *
   *  Live mode emits SPEAKING and IDLE continuously while you talk, and when mute was
   *  derived from the activity string every one of those events silently un-muted the
   *  orb — so muting appeared to do nothing the moment anyone spoke.
   *
   *  The colour does not change on the spot. A front starts sweeping, and `_drawMorph`
   *  carries it through the cloud over the next `MUTE_MORPH_SECONDS`. */
  setMuted(muted) {
    const next = Boolean(muted);
    if (next !== this.muted) {
      this._muteFrom = this.muted;
      this.muted = next;
      this._muteAnim = 0;
      this._muteChangedAt = performance.now();
    }
    if (this.muted) {
      this.level = 0;
      this._level = 0;
    }
  }

  /** Where the cursor is, in canvas pixels, and how close: 0 ignores it, 1 parts the
   *  cloud around it. Everything below `near > 0` is left alone on purpose — the orb
   *  reacting to a cursor halfway across the screen would be noise, not life. */
  setPointer(x, y, near) {
    const px = Number(x);
    const py = Number(y);
    const n = Number(near);
    this.pointer.x = Number.isFinite(px) ? px : -1e4;
    this.pointer.y = Number.isFinite(py) ? py : -1e4;
    this.pointer.near = Number.isFinite(n) ? clamp01(n) : 0;
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
    this._clock += dt;

    // Speech energy also drives the spin, so a loud sentence visibly stirs the cloud.
    this.spin += dt * (0.22 + this._level * 0.55);
    if (this.spin > TAU * 1e4) this.spin -= TAU * 1e4; // keep the float small and exact

    // Fast attack, slow release.
    const rate = this.level > this._level ? 14 : 4;
    this._level += (this.level - this._level) * Math.min(1, dt * rate);

    // Against the clock, not accumulated deltas: a webview that was throttled while
    // occluded would otherwise resume a colour sweep minutes after it started.
    if (this._muteAnim < 1) {
      const since = (performance.now() - this._muteChangedAt) / 1000;
      this._muteAnim = Math.min(1, since / MUTE_MORPH_SECONDS);
    }

    if (this.state !== 'arriving') return;

    // Same reasoning as above. macOS throttles an occluded webview's rAF to nothing, and
    // with accumulated deltas the orb would resume from wherever it froze. Against the
    // clock, a resumed panel is simply already settled.
    const elapsed = (performance.now() - this._startedAt) / 1000;
    this.progress = Math.min(1, elapsed / TRANSITION_SECONDS);
    if (this.progress >= 1) this.state = 'resting';
  }

  _draw() {
    const { ctx, canvas } = this;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;

    // Mid-resize the panel can be reported at zero size; there is nothing to draw and
    // the gradients below would be degenerate.
    if (!w || !h) return;

    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    // One geometry now: the orb sits in the middle of its own small panel, which is
    // itself parked in the corner of the screen. Slightly above centre leaves room for
    // the satellite stack underneath.
    const radius = Math.min(w, h) * 0.3;
    const cx = w / 2;
    const cy = h * 0.46;

    this._backdrop(cx, cy, radius);
    this._points(cx, cy, radius);
    if (this.tools.length) this._satelliteStack(cx, cy, radius, h);
  }

  /** A tint filling the sphere, in whatever colour the orb currently is.
   *
   *  This is what the points sit on, so they read against something of their own rather
   *  than against whatever window happens to be behind the panel. It is contained
   *  *inside* the cloud and fades to nothing at the edge — an earlier version extended to
   *  2.6× the radius, which clipped to a hard edge along the top of the panel and needed
   *  a stroked ring to hide the seam. Both are gone. */
  _backdrop(cx, cy, radius) {
    const { ctx } = this;
    const [r, g, b] = this.muted ? RESTING_RGB : LIVE_RGB;
    const lift = this._level * 0.1;
    // Dipped while a colour front is crossing, so the tint does not announce the new
    // state before the points have got there.
    const settled = 0.55 + 0.45 * this._muteAnim;
    const core = (0.2 + lift) * settled;

    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius * 1.08);
    glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${core.toFixed(3)})`);
    glow.addColorStop(0.55, `rgba(${r}, ${g}, ${b}, ${(core * 0.52).toFixed(3)})`);
    glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);

    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 1.08, 0, TAU);
    ctx.fill();
  }

  _points(cx, cy, radius) {
    const { base, entry, phase, count, _sx, _sy, _ss, _sb, _sbk } = this;
    const ctx = this.ctx;
    const sin = Math.sin(this.spin);
    const cos = Math.cos(this.spin);
    const level = this._level;

    // Arriving eases in from the entry positions; after that there is nothing to blend.
    const blend = this.state === 'arriving' ? easeOut(this.progress) : 1;

    // A slow breath that never stops, stronger at rest than when it is listening. A
    // perfectly still orb reads as a frozen frame; this is what says it is still there.
    const breath = 0.5 + 0.5 * Math.sin(this._clock * 1.05);
    const rest = this.muted ? 0.018 + breath * 0.03 : 0.01 * breath;
    const swell = 1 + level * 0.16 + rest;
    // Never zero: each circle keeps drifting on its own phase even in silence.
    const wobble = level * 0.13 + (this.muted ? 0.012 : 0.02);
    const now = this.spin * 3.1 + this._clock * 0.6;
    const sizeGain = (this.muted ? 0.86 : 1) * (1 + level * 0.35);

    // The colour front. Waking pushes it outward from the core; settling pulls it inward
    // to the core. The range overshoots both ends so that when the sweep finishes every
    // point — including the ones exactly at the core or the rim — has been crossed.
    const morphing = this._muteAnim < 1;
    const spreading = !this.muted;
    const swept = easeInOut(this._muteAnim);
    const LO = -0.35;
    const HI = 1.4;
    const front = spreading ? LO + swept * (HI - LO) : HI - swept * (HI - LO);
    const FRONT_BAND = 0.3;

    const near = this.pointer.near;
    const pointerX = this.pointer.x;
    const pointerY = this.pointer.y;
    const reach = radius * 1.15;

    // Pass one: project every point, note its depth band, and apply the two things that
    // move individual circles rather than the whole cloud — the colour front, and the
    // cursor.
    for (let i = 0; i < count; i += 1) {
      const x0 = base[i * 3];
      const y0 = base[i * 3 + 1];
      const z0 = base[i * 3 + 2];

      // Spin about Y so the cloud has depth rather than reading as a flat ring.
      const x = x0 * cos - z0 * sin;
      const z = x0 * sin + z0 * cos;

      // Each point breathes on its own phase, so a loud voice ripples the surface.
      const ripple = 1 + Math.sin(now + phase[i]) * wobble;
      const grow = swell * ripple;

      const px = (x * blend + entry[i * 2] * (1 - blend)) * grow;
      const py = (y0 * blend + entry[i * 2 + 1] * (1 - blend)) * grow;

      const depth = (z + 1.6) / 3.2;
      let sx = cx + px * radius;
      let sy = cy - py * radius;
      let size = (1.15 + depth * 2.2) * sizeGain;

      if (morphing) {
        // Measured on the *projected* radius — how far from the middle of the orb this
        // circle looks, not where it sits on the model. Using the model's own axis makes
        // the front sweep sideways through the spin instead of out from the centre.
        const out = Math.sqrt(px * px + py * py) / grow;
        // How far this circle is past the front, as a fraction of the band width: 0 is
        // still the old colour, 1 is fully the new one.
        const past = spreading ? front - out : out - front;
        const crossing = past <= 0 ? 0 : past >= FRONT_BAND ? 1 : past / FRONT_BAND;
        _sbk[i] = Math.min(MORPH_BUCKETS - 1, (crossing * MORPH_BUCKETS) | 0);
        // Circles the front is passing over right now swell. This is the whole reason
        // the change is a sweep and not a swap. Kept under half again their size: any
        // more and the additive blending sums the wavefront to flat white and the colour
        // it is carrying stops being visible at all.
        size *= 1 + Math.sin(crossing * Math.PI) * 0.45;
      }

      if (near > 0) {
        // Shove each circle directly away from the cursor, hardest at the centre of the
        // gap and fading to nothing at `reach`. The ring around the gap thickens as the
        // points it pushed pile up there, which is what makes it look like parting
        // rather than fading out.
        const dx = sx - pointerX;
        const dy = sy - pointerY;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < reach) {
          const force = 1 - distance / reach;
          const shove = force * force * near * radius * 0.6;
          const inverse = distance > 0.001 ? 1 / distance : 0;
          sx += dx * inverse * shove;
          sy += dy * inverse * shove;
          size *= 1 + force * near * 0.9;
        }
      }

      _sx[i] = sx;
      _sy[i] = sy;
      _ss[i] = size;
      _sb[i] = Math.min(BANDS - 1, (depth * BANDS) | 0);
    }

    // Additive blending makes overlapping points build into a genuine glow rather than
    // averaging out to the flat haze they had before.
    ctx.globalCompositeOperation = 'lighter';
    if (morphing) this._drawMorph(blend, level);
    else this._drawSteady(blend, level);
    ctx.globalCompositeOperation = 'source-over';
  }

  /** Pass two, settled: one colour per depth band, then every point in that band.
   *
   *  This is the path the orb is on essentially all the time, so it is the one that has
   *  to stay cheap — twelve colour parses a frame, not fifteen hundred. */
  _drawSteady(blend, level) {
    const { ctx, count, _sx, _sy, _ss, _sb } = this;
    const palette = this.muted ? PALETTES.resting : PALETTES.live;
    // Resting is dimmer as well as bluer. Two signals for one state, on purpose.
    const dim = this.muted ? 0.82 : 1;

    for (let band = 0; band < BANDS; band += 1) {
      const depth = (band + 0.5) / BANDS;
      const alpha = Math.min(
        0.95,
        ((0.44 + depth * 0.52) * blend + 0.16 * (1 - blend)) * dim,
      );
      const [r, g, b] = bandColour(palette, depth, level, this.muted);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;

      for (let i = 0; i < count; i += 1) {
        if (_sb[i] !== band) continue;
        ctx.fillRect(_sx[i], _sy[i], _ss[i], _ss[i]);
      }
    }
  }

  /** Pass two, mid-sweep: each band blended between the old palette and the new one at
   *  `MORPH_BUCKETS` steps, so a circle's colour depends on whether the front has reached
   *  it yet.
   *
   *  This does set `fillStyle` per point, which the settled path is careful not to. It is
   *  the deliberate exception: the strings are built once per band per step — 72 of them,
   *  not one per point — and the whole sweep is over in under a second. */
  _drawMorph(blend, level) {
    const { ctx, count, _sx, _sy, _ss, _sb, _sbk, _morph } = this;
    const from = this._muteFrom ? PALETTES.resting : PALETTES.live;
    const to = this.muted ? PALETTES.resting : PALETTES.live;

    for (let band = 0; band < BANDS; band += 1) {
      const depth = (band + 0.5) / BANDS;
      const alpha = Math.min(0.95, (0.44 + depth * 0.52) * blend + 0.16 * (1 - blend));
      const was = bandColour(from, depth, level, this._muteFrom);
      const becomes = bandColour(to, depth, level, this.muted);

      for (let step = 0; step < MORPH_BUCKETS; step += 1) {
        const t = step / (MORPH_BUCKETS - 1);
        const r = (was[0] + (becomes[0] - was[0]) * t) | 0;
        const g = (was[1] + (becomes[1] - was[1]) * t) | 0;
        const b = (was[2] + (becomes[2] - was[2]) * t) | 0;
        // Brightest halfway through the change, so the front itself is visible as a band
        // of light travelling through the cloud.
        const flare = 1 + Math.sin(t * Math.PI) * 0.12;
        _morph[band * MORPH_BUCKETS + step] =
          `rgba(${r}, ${g}, ${b}, ${Math.min(0.98, alpha * flare).toFixed(3)})`;
      }
    }

    for (let band = 0; band < BANDS; band += 1) {
      for (let i = 0; i < count; i += 1) {
        if (_sb[i] !== band) continue;
        ctx.fillStyle = _morph[band * MORPH_BUCKETS + _sbk[i]];
        ctx.fillRect(_sx[i], _sy[i], _ss[i], _ss[i]);
      }
    }
  }

  /** Coloured satellites, stacked vertically below the orb.
   *
   *  Replaces the constellation of tool cubes the app window used: same idea — one mark
   *  per thing being worked on — but a column reads as a queue, which is what it is. */
  _satelliteStack(cx, cy, radius, height) {
    const { ctx } = this;
    const size = Math.max(1, radius * 0.15);
    const x = cx;
    const top = cy + radius * 1.2;

    // The stack has to fit the panel. At the preferred spacing a fourth tool ran off the
    // bottom edge, so the gap tightens once there are more than the panel can hold —
    // a cramped column is still readable, a clipped one is just missing information.
    const preferred = radius * 0.5;
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
