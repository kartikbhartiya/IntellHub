"use client";

/* One camera tile. There is no video in the corpus, so the default "feed" is overhead
 * reference imagery of the camera's real location treated like a sensor image — grain,
 * scanlines, night vision after dark — with the matcher's detections drawn over it.
 * If an officer configures a real stream URL for the camera, that plays instead. */

import { memo, useEffect, useState } from "react";

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

const Z = 18;
function tileXY(lat: number, lng: number) {
  const n = 2 ** Z;
  const x = ((lng + 180) / 360) * n;
  const r = (lat * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n;
  return { x, y };
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967295;
}

function isNightIST() {
  const h = Number(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata", hour: "2-digit", hour12: false }));
  return h >= 19 || h < 6;
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

function CameraFeedInner({ cam, detections = [], streamUrl, large = false }:
  { cam: FeedCamera; detections?: Detection[]; streamUrl?: string; large?: boolean }) {
  const { x, y } = tileXY(cam.lat, cam.lng);
  const tx = Math.floor(x), ty = Math.floor(y);
  const fx = x - tx, fy = y - ty;
  const night = isNightIST();
  const offline = cam.status === "offline";
  // Each camera looks at a slightly different patch, and drifts slowly like a PTZ head.
  const seed = hash(cam.camera_id);
  const filter = night
    ? "grayscale(1) sepia(1) hue-rotate(58deg) saturate(2.4) brightness(0.72) contrast(1.25)"
    : cam.type === "ANPR" ? "grayscale(0.55) contrast(1.15) brightness(0.92)" : "saturate(0.75) contrast(1.08) brightness(0.95)";

  return (
    <div className={`relative overflow-hidden ${large ? "aspect-video" : "aspect-[16/10]"}`}
         style={{ background: night ? "radial-gradient(120% 90% at 50% 70%, #10241a 0%, #070b09 70%)"
                                    : "radial-gradient(120% 90% at 50% 70%, #3a4148 0%, #14181d 75%)" }}>
      {offline ? (
        <div className="absolute inset-0 grid place-items-center">
          <div className="absolute inset-0 opacity-30" style={{ backgroundImage: "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")" }} />
          <div className="relative text-center">
            <div className="font-mono text-[13px] font-bold tracking-[0.3em] text-white/70">NO SIGNAL</div>
            <div className="mt-1 font-mono text-[10px] text-white/40">{cam.camera_id} · link down</div>
          </div>
        </div>
      ) : streamUrl ? (
        <video src={streamUrl} autoPlay muted loop playsInline className="absolute inset-0 h-full w-full object-cover" />
      ) : (
        <div
          className="cam-drift absolute"
          style={{
            width: 768, height: 768,
            left: `calc(50% - ${(1 + fx) * 256}px)`, top: `calc(50% - ${(1 + fy) * 256}px)`,
            filter,
            transform: `scale(${large ? 1.25 : 1.05}) rotate(${(seed - 0.5) * 8}deg)`,
            animationDelay: `${-seed * 40}s`,
          }}
        >
          {[-1, 0, 1].map((dy) =>
            [-1, 0, 1].map((dx) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                key={`${dx},${dy}`}
                alt=""
                loading="lazy"
                draggable={false}
                src={`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${Z}/${ty + dy}/${tx + dx}`}
                className="absolute h-[256px] w-[256px] select-none"
                onError={(e) => ((e.currentTarget as HTMLImageElement).style.visibility = "hidden")}
                style={{ left: (dx + 1) * 256, top: (dy + 1) * 256 }}
              />
            ))
          )}
        </div>
      )}

      {/* sensor treatment */}
      {!offline && <div className="cam-grain pointer-events-none absolute inset-0" />}
      <div className="pointer-events-none absolute inset-0" style={{ background: "repeating-linear-gradient(0deg, rgba(0,0,0,0.16) 0 1px, transparent 1px 3px)" }} />
      <div className="pointer-events-none absolute inset-0" style={{ boxShadow: "inset 0 0 60px rgba(0,0,0,0.65)" }} />

      {/* detections */}
      {detections.slice(0, 2).map((d, i) => {
        const h = hash(d.id);
        const w = cam.type === "ANPR" ? 34 : 20 + h * 8;
        const left = 14 + h * (64 - w);
        const top = 22 + ((h * 7.3 + i * 0.37) % 1) * 36;
        const col = d.tone === "red" ? "#ff3b30" : d.tone === "amber" ? "#ffb020" : "#30d158";
        return (
          <button
            key={d.id}
            onClick={d.onClick}
            className="cam-box absolute text-left"
            style={{ left: `${left}%`, top: `${top}%`, width: `${w}%`, height: cam.type === "ANPR" ? "16%" : "34%", borderColor: col, boxShadow: `0 0 0 1px rgba(0,0,0,0.4), 0 0 18px ${col}55` }}
          >
            <span className="absolute -top-[18px] left-[-2px] whitespace-nowrap rounded-sm px-1.5 py-px font-mono text-[9.5px] font-bold text-white" style={{ background: col }}>
              {d.label} ({(d.score * 100).toFixed(1)}%)
            </span>
            {d.sub && <span className="absolute -bottom-[16px] left-[-2px] whitespace-nowrap rounded-sm bg-black/70 px-1 font-mono text-[8.5px] text-white/85">{d.sub}</span>}
          </button>
        );
      })}

      {/* HUD */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-2">
        <div className="flex items-center gap-1.5">
          <span className={`flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[9.5px] font-bold ${offline ? "bg-white/20 text-white/70" : "bg-[#ff3b30] text-white"}`}>
            {!offline && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />} {offline ? "OFFLINE" : "LIVE"}
          </span>
          <span className="rounded bg-black/55 px-1.5 py-0.5 font-mono text-[9.5px] text-white/85">{cam.camera_id}</span>
        </div>
        <span className="rounded bg-black/55 px-1.5 py-0.5 font-mono text-[9.5px] text-white/85"><Clock /></span>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between p-2 font-mono text-[9px] text-white/80">
        <span className="rounded bg-black/55 px-1.5 py-0.5">{cam.fps || 25}FPS · {cam.resolution || "1080p"}{night ? " · IR" : ""}</span>
        <span className="rounded bg-black/55 px-1.5 py-0.5">{cam.lat.toFixed(4)}°N {cam.lng.toFixed(4)}°E</span>
        <span className="rounded bg-black/55 px-1.5 py-0.5">{cam.type === "ANPR" ? "Optical ANPR" : cam.type === "CCTV" ? "Surveillance" : "Biometrics"}</span>
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
