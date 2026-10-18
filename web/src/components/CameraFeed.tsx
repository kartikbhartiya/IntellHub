"use client";

/* One camera tile. The picture is real recorded street footage from Bengaluru (see
 * lib/footage.ts) standing in for the camera's stream, and the boxes on it are real
 * object detections — YOLO11s with ByteTrack, run over that footage and replayed in
 * step with the video. Registry matches come from the matcher and are shown as an alert
 * strip, never pinned on a passer-by: nobody in this footage is the person matched.
 * If an officer configures a real stream URL for the camera, that plays instead. */

import { memo, useEffect, useRef, useState } from "react";
import { CLASS_STYLE, clipFor, footageUrl, hashUnit, loadTracks, type Clip, type Tracks } from "@/lib/footage";

export interface FeedCamera {
  camera_id: string;
  name: string;
  area: string;
  district: string;
  lat: number;
  lng: number;
  type: string;
  status: string;
  resolution?: string;
  fps?: number;
  heading?: number;
}

export interface Detection {
  id: string;
  label: string;
  sub?: string;
  score: number;
  tone: "red" | "amber" | "green";
  onClick?: () => void;
}

function Clock() {
  const [t, setT] = useState("");
  useEffect(() => {
    const f = () =>
      setT(new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
                                                 hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }));
    f();
    const i = setInterval(f, 1000);
    return () => clearInterval(i);
  }, []);
  return <>{t}</>;
}

const TONE = { red: "#ff3b30", amber: "#ff9f0a", green: "#30d158" };

function CameraFeedInner({ cam, detections = [], streamUrl, large = false, clip: chosen, paused = false }:
  { cam: FeedCamera; detections?: Detection[]; streamUrl?: string; large?: boolean; clip?: Clip; paused?: boolean }) {
  const clip = chosen || clipFor(cam);
  const box = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [tracks, setTracks] = useState<Tracks | null>(null);
  const [onScreen, setOnScreen] = useState(false);
  const [counts, setCounts] = useState({ vehicles: 0, people: 0 });
  const offline = cam.status === "offline";
  const degraded = cam.status === "degraded";
  const footage = !offline && !streamUrl;

  useEffect(() => {
    if (!footage) return;
    let live = true;
    loadTracks(clip.slug).then((t) => live && setTracks(t));
    return () => {
      live = false;
    };
  }, [clip.slug, footage]);

  // Decode only what is on screen; a wall of feeds otherwise plays behind the fold.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setOnScreen(e.isIntersecting), { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    if (onScreen && !paused) v.play().catch(() => {});
    else v.pause();
  }, [onScreen, paused, clip.slug, streamUrl]);

  // Each camera starts at its own moment, so two tiles on one clip never move in lockstep.
  const seek = () => {
    const v = video.current;
    if (v?.duration) v.currentTime = hashUnit(cam.camera_id) * v.duration * 0.85;
  };

  // Boxes, redrawn every frame from the stored tracks at the video's current time.
  useEffect(() => {
    if (!tracks || !onScreen || !footage) return;
    let raf = 0;
    let lastCount = 0;
    const lerp = (a: number, b: number | undefined, t: number) => (b === undefined ? a : a + (b - a) * t);
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      const v = video.current, c = canvas.current;
      if (!v || !c || v.readyState < 2) return;
      const W = c.clientWidth, H = c.clientHeight;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) {
        c.width = Math.round(W * dpr);
        c.height = Math.round(H * dpr);
      }
      const ctx = c.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      // object-cover geometry: where the video frame actually lands in the tile
      const vw = v.videoWidth || 640, vh = v.videoHeight || 360;
      const s = Math.max(W / vw, H / vh);
      const fw = vw * s, fh = vh * s, ox = (W - fw) / 2, oy = (H - fh) / 2;
      const f = v.currentTime * tracks.fps;
      const i0 = Math.min(tracks.frames.length - 1, Math.max(0, Math.floor(f)));
      const t = Math.min(1, f - i0);
      const next = tracks.frames[i0 + 1];
      const nextById = next ? new Map(next.map((b) => [b[0], b])) : null;
      let vehicles = 0, people = 0;
      const fontPx = large ? 11 : 9;
      ctx.font = `700 ${fontPx}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
      ctx.textBaseline = "middle";
      for (const b of tracks.frames[i0] || []) {
        const n = nextById?.get(b[0]);
        const cls = tracks.classes[b[1]];
        if (cls === "person") people++;
        else vehicles++;
        const x = ox + (lerp(b[2], n?.[2], t) / 1000) * fw;
        const y = oy + (lerp(b[3], n?.[3], t) / 1000) * fh;
        const w = (lerp(b[4], n?.[4], t) / 1000) * fw;
        const h = (lerp(b[5], n?.[5], t) / 1000) * fh;
        if (x + w < 0 || x > W || y + h < 0 || y > H || (w < 5 && h < 5)) continue;
        const st = CLASS_STYLE[cls] || CLASS_STYLE.car;
        // thin frame plus heavier corner brackets, the way analytics overlays draw
        ctx.globalAlpha = 0.55;
        ctx.strokeStyle = st.color;
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y, w, h);
        ctx.globalAlpha = 1;
        ctx.lineWidth = large ? 2.5 : 2;
        const k = Math.max(3, Math.min(9, Math.min(w, h) / 3));
        ctx.beginPath();
        ctx.moveTo(x, y + k); ctx.lineTo(x, y); ctx.lineTo(x + k, y);
        ctx.moveTo(x + w - k, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + k);
        ctx.moveTo(x + w, y + h - k); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - k, y + h);
        ctx.moveTo(x + k, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - k);
        ctx.stroke();
        if (large ? w > 14 : w > 22) {
          const label = large ? `${st.label} #${b[0]} ${b[6]}%` : `${st.label} ${b[6]}`;
          const tw = ctx.measureText(label).width + 6;
          const lh = fontPx + 4;
          const ly = y - lh < 0 ? y + h : y - lh;
          ctx.fillStyle = st.color;
          ctx.fillRect(x - 1, ly, tw, lh);
          ctx.fillStyle = "#0b0e14";
          ctx.fillText(label, x + 2, ly + lh / 2 + 0.5);
        }
      }
      if (now - lastCount > 600) {
        lastCount = now;
        setCounts((p) => (p.vehicles === vehicles && p.people === people ? p : { vehicles, people }));
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [tracks, onScreen, footage, large]);

  const hot = detections[0];

  return (
    <div ref={box} className={`relative overflow-hidden bg-[#0b0e14] ${large ? "aspect-video" : "aspect-[16/10]"}`}>
      {offline ? (
        <div className="absolute inset-0 grid place-items-center">
          <div className="absolute inset-0 opacity-30" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")" }} />
          <div className="relative text-center">
            <div className="font-mono text-[13px] font-bold tracking-[0.3em] text-white/70">NO SIGNAL</div>
            <div className="mt-1 font-mono text-[10px] text-white/40">{cam.camera_id} · link down</div>
          </div>
        </div>
      ) : (
        <>
          <video
            key={streamUrl || clip.slug}
            ref={video}
            src={streamUrl || footageUrl(clip)}
            muted
            loop
            playsInline
            preload="metadata"
            onLoadedMetadata={streamUrl ? undefined : seek}
            className="absolute inset-0 h-full w-full object-cover"
            style={{ filter: degraded ? "contrast(0.92) saturate(0.7) blur(0.6px)" : "contrast(1.06) saturate(0.9)" }}
          />
          {footage && <canvas ref={canvas} className="pointer-events-none absolute inset-0 h-full w-full" />}
        </>
      )}

      {/* sensor treatment */}
      {!offline && <div className="cam-grain pointer-events-none absolute inset-0 opacity-60" />}
      <div className="pointer-events-none absolute inset-0" style={{ background: "repeating-linear-gradient(0deg, rgba(0,0,0,0.10) 0 1px, transparent 1px 3px)" }} />
      <div className="pointer-events-none absolute inset-0" style={{ boxShadow: "inset 0 0 50px rgba(0,0,0,0.55)" }} />

      {/* HUD */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-2">
        <div className="flex items-center gap-1.5">
          <span className={`flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[9.5px] font-bold ${offline ? "bg-white/20 text-white/70" : "bg-[#ff3b30] text-white"}`}>
            {!offline && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />} {offline ? "OFFLINE" : "LIVE"}
          </span>
          <span className="rounded bg-black/60 px-1.5 py-0.5 font-mono text-[9.5px] text-white/90">{cam.camera_id}</span>
          {degraded && <span className="rounded bg-[#ff9f0a] px-1.5 py-0.5 font-mono text-[9px] font-bold text-black">DEGRADED</span>}
        </div>
        <span className="rounded bg-black/60 px-1.5 py-0.5 font-mono text-[9.5px] text-white/90"><Clock /></span>
      </div>

      {/* matcher alert for this camera — a strip, not a box on someone in the frame */}
      {hot && !offline && (
        <button onClick={hot.onClick}
                className="absolute inset-x-2 top-8 flex items-center gap-2 rounded-md px-2 py-1 text-left font-mono text-white shadow-lg backdrop-blur-sm"
                style={{ background: `${TONE[hot.tone]}e6` }}>
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-white" />
          <span className="min-w-0 flex-1 truncate text-[10px] font-bold">{hot.label} · {(hot.score * 100).toFixed(1)}%</span>
          {detections.length > 1 && <span className="shrink-0 rounded bg-black/30 px-1 text-[9px] font-bold">+{detections.length - 1}</span>}
          <span className="shrink-0 text-[9px] font-semibold opacity-85">REVIEW →</span>
        </button>
      )}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-1 p-2 font-mono text-[9px] text-white/85">
        <span className="rounded bg-black/60 px-1.5 py-0.5">{cam.fps || 25}FPS · {cam.resolution || "1080p"}</span>
        {footage && tracks ? (
          <span className="truncate rounded bg-black/60 px-1.5 py-0.5">
            AI · {counts.vehicles} vehicle{counts.vehicles === 1 ? "" : "s"} · {counts.people} {counts.people === 1 ? "person" : "people"}
          </span>
        ) : (
          <span className="rounded bg-black/60 px-1.5 py-0.5">{cam.lat.toFixed(4)}°N {cam.lng.toFixed(4)}°E</span>
        )}
        <span className="hidden rounded bg-black/60 px-1.5 py-0.5 sm:inline">{cam.type === "ANPR" ? "Optical ANPR" : cam.type === "CCTV" ? "Surveillance" : cam.type === "PTZ" ? "PTZ · Face" : "Biometrics"}</span>
      </div>
    </div>
  );
}

export default memo(CameraFeedInner);

/** A registry photo that degrades to initials if the image host is unreachable. */
export function Face({ src, name, className = "", capture = false }: { src?: string; name?: string; className?: string; capture?: boolean }) {
  const [ok, setOk] = useState(true);
  const initials = (name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  if (!src || !ok)
    return (
      <span className={`grid place-items-center bg-gradient-to-br from-[#2c3340] to-[#11151b] font-bold text-white/85 ${capture ? "cctv-capture" : ""} ${className}`}>
        {initials}
      </span>
    );
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" onError={() => setOk(false)} className={`object-cover ${capture ? "cctv-capture" : ""} ${className}`} />;
}
