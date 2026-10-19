"use client";

import { useEffect, useRef } from "react";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking";

/* Siri-style waveform.
 *
 * Several translucent ribbons, each a sum of travelling Gaussian-windowed sine
 * "bumps", composited additively on a dark field so overlaps glow. Amplitude follows
 * `level` — the live microphone RMS while listening, word-boundary pulses from the
 * speech synthesiser while speaking — so the wave moves with the voice rather than
 * looping a canned animation. Thinking is a slow travelling shimmer; idle is a calm
 * hairline.
 */
const RIBBONS = [
  { color: [231, 180, 60], speed: 0.9 },   // brand gold
  { color: [47, 140, 255], speed: 1.15 },  // azure
  { color: [255, 70, 140], speed: 1.35 },  // rose
  { color: [40, 220, 200], speed: 0.75 },  // teal
  { color: [150, 100, 255], speed: 1.05 }, // violet
];

type Bump = { pos: number; width: number; amp: number; phase: number; drift: number; freq: number };

function seedBumps(n: number): Bump[] {
  return Array.from({ length: n }, () => ({
    pos: Math.random() * 1.6 - 0.8,
    width: 0.18 + Math.random() * 0.35,
    amp: 0.35 + Math.random() * 0.65,
    phase: Math.random() * Math.PI * 2,
    drift: (Math.random() - 0.5) * 0.25,
    freq: 2.2 + Math.random() * 3.2,
  }));
}

export function SiriWave({
  state,
  levelRef,
  height = 96,
}: {
  state: VoiceState;
  levelRef: React.MutableRefObject<number>;
  height?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

    const bumps = RIBBONS.map(() => seedBumps(3));
    let raf = 0;
    let amp = 0; // smoothed amplitude 0..1
    let t = 0;
    let last = performance.now();

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = canvas.clientWidth;
      canvas.width = Math.max(1, Math.floor(w * dpr));
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    ro?.observe(canvas);

    const draw = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      t += dt;
      const w = canvas.clientWidth;
      const h = height;
      const mid = h / 2;
      const st = stateRef.current;

      // Target amplitude per state; the live level rides on top.
      const lvl = Math.min(1, Math.max(0, levelRef.current || 0));
      const target =
        st === "speaking" ? 0.35 + lvl * 0.65 :
        st === "listening" ? 0.08 + lvl * 0.92 :
        st === "thinking" ? 0.28 + 0.12 * Math.sin(t * 3) :
        0.04;
      amp += (target - amp) * Math.min(1, dt * (target > amp ? 14 : 5));
      // Speech pulses decay so each word reads as a breath.
      levelRef.current = lvl * Math.pow(0.04, dt);

      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      RIBBONS.forEach((rb, ri) => {
        const set = bumps[ri];
        ctx.beginPath();
        ctx.moveTo(0, mid);
        const pts: [number, number][] = [];
        const steps = Math.max(60, Math.floor(w / 4));
        for (let i = 0; i <= steps; i++) {
          const x = i / steps;           // 0..1
          const u = x * 2 - 1;           // -1..1
          let y = 0;
          for (const b of set) {
            const centre = st === "thinking" ? Math.sin(t * 0.8 * rb.speed + b.phase) * 0.7 : b.pos;
            const g = Math.exp(-Math.pow((u - centre) / b.width, 2));
            y += g * b.amp * Math.sin(u * b.freq * Math.PI + t * 6 * rb.speed + b.phase);
          }
          // Taper to zero at both ends, like the iOS wave.
          const env = Math.pow(1 - u * u, 2);
          pts.push([x * w, y * env]);
        }
        const scale = (h * 0.42) * amp;
        for (const [px, py] of pts) ctx.lineTo(px, mid - py * scale);
        for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(pts[i][0], mid + pts[i][1] * scale * 0.85);
        ctx.closePath();
        const [r, g, b] = rb.color;
        const grad = ctx.createLinearGradient(0, 0, w, 0);
        grad.addColorStop(0, `rgba(${r},${g},${b},0)`);
        grad.addColorStop(0.5, `rgba(${r},${g},${b},${0.55 + amp * 0.3})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad;
        ctx.fill();
      });

      // Bright core line.
      ctx.globalCompositeOperation = "source-over";
      const core = ctx.createLinearGradient(0, 0, w, 0);
      core.addColorStop(0, "rgba(255,255,255,0)");
      core.addColorStop(0.5, `rgba(255,255,255,${0.35 + amp * 0.5})`);
      core.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = core;
      ctx.fillRect(0, mid - 0.75, w, 1.5);

      // Slowly re-seed bumps so the shape never visibly repeats.
      if (!reduce) {
        bumps.forEach((set) =>
          set.forEach((b) => {
            b.pos += b.drift * dt;
            if (Math.abs(b.pos) > 0.9) b.drift *= -1;
          })
        );
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(raf);
      ro?.disconnect();
    };
  }, [height, levelRef]);

  return <canvas ref={canvasRef} className="block w-full" style={{ height }} aria-hidden="true" />;
}

/* The assistant's orb: blurred colour blobs orbiting inside a glass sphere. It
 * breathes when idle, swirls fast while thinking and swells with the voice. */
export function SiriOrb({ state, size = 44 }: { state: VoiceState; size?: number }) {
  const speed = state === "thinking" ? "1.6s" : state === "idle" ? "9s" : "3.2s";
  const scale = state === "speaking" ? 1.08 : state === "listening" ? 1.04 : 1;
  return (
    <span
      className="siri-orb relative inline-grid shrink-0 place-items-center overflow-hidden rounded-full"
      style={{ width: size, height: size, transform: `scale(${scale})`, ["--orb-speed" as any]: speed }}
      aria-hidden="true"
    >
      <span className="siri-orb-blob siri-orb-a" />
      <span className="siri-orb-blob siri-orb-b" />
      <span className="siri-orb-blob siri-orb-c" />
      <span className="siri-orb-glass" />
      {state !== "idle" && <span className="siri-orb-pulse" />}
    </span>
  );
}

/* Apple-Intelligence-style glow around the screen edge while voice mode is on. */
export function EdgeGlow({ state }: { state: VoiceState }) {
  return <div className={`siri-edge siri-edge-${state}`} aria-hidden="true" />;
}

/* Live microphone level (RMS, 0..1) written into `levelRef` while `active`.
 * Runs alongside SpeechRecognition; if the browser refuses a second capture, the
 * wave simply falls back to its state-driven motion. */
export function useMicLevel(active: boolean, levelRef: React.MutableRefObject<number>) {
  useEffect(() => {
    if (!active || typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let raf = 0;
    let cancelled = false;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true },
        });
        if (cancelled) return;
        const AC = (window as any).AudioContext || (window as any).webkitAudioContext;
        ctx = new AC();
        const src = ctx!.createMediaStreamSource(stream);
        const an = ctx!.createAnalyser();
        an.fftSize = 512;
        src.connect(an);
        const buf = new Uint8Array(an.fftSize);
        const tick = () => {
          an.getByteTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / buf.length);
          const lvl = Math.min(1, rms * 5.5);
          if (lvl > levelRef.current) levelRef.current = lvl;
          raf = requestAnimationFrame(tick);
        };
        tick();
      } catch {
        /* permission or device refused — the wave still animates from state */
      }
    })();
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      try { ctx?.close(); } catch {}
    };
  }, [active, levelRef]);
}
