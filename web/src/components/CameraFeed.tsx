"use client";

/* One camera tile. It shows one of two kinds of picture and never confuses them.
 *
 * RECORDED - real street footage recorded in Bengaluru (lib/footage.ts) standing in for
 * the camera, with real object detections (YOLO11s + ByteTrack, run over that footage)
 * replayed in step with it. Badged RECORDED, and the corner shows the year it was filmed
 * rather than a running clock, because a burned-in clock over a replay is a claim that
 * it is happening now.
 *
 * LIVE - a stream an administrator attached to this camera (lib/streams.ts): an NVR's
 * HLS output, a WebRTC (WHEP) endpoint, an MJPEG camera, a YouTube Live broadcast, or
 * the viewing officer's own device camera. The red LIVE badge is earned, not asserted:
 * it shows only while the browser is receiving frames, and a source that turns out to
 * be a finished recording (an HLS VOD playlist, an MP4 file, a YouTube upload) is badged
 * as one. A link that fails or stalls is retried with backoff, then the recorded clip is
 * shown under a notice saying exactly that, and the live link is re-tried every minute.
 * No boxes are drawn over a live picture - no detector runs on it - and the HUD says so.
 *
 * Registry matches come from the simulated sighting stream. Over recorded footage they
 * are an alert strip, never pinned on a passer-by (nobody in these clips is a registry
 * person). Over a live picture of a real public place they are not shown at all: a
 * "WANTED" banner above real people walking past would be an accusation the matcher
 * never made about any of them. The alert rail and "Verify target" still carry them. */

import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { RotateCw, Webcam } from "lucide-react";
import { CLASS_STYLE, clipFor, footageUrl, hashUnit, loadTracks, type Clip, type Tracks } from "@/lib/footage";
import { KIND_LABEL, sourceKey, youtubeEmbedUrl, type FeedState, type LiveSource, type StreamKind } from "@/lib/streams";

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

type StatusFn = (cameraId: string, state: FeedState | null) => void;

interface FeedProps {
  cam: FeedCamera;
  detections?: Detection[];
  /** A live source attached by an administrator. Absent: the recorded clip. */
  live?: LiveSource | null;
  large?: boolean;
  clip?: Clip;
  paused?: boolean;
  /** Told what this tile is actually showing whenever that changes, and null on unmount. */
  onStatus?: StatusFn;
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

const typeName = (t: string) =>
  t === "ANPR" ? "Optical ANPR" : t === "CCTV" ? "Surveillance" : t === "PTZ" ? "PTZ · Face" : "Biometrics";

function subscribeVisibility(f: () => void) {
  document.addEventListener("visibilitychange", f);
  return () => document.removeEventListener("visibilitychange", f);
}
/** A hidden tab paints no frames; without this every live tile would "stall" in the
 *  background and be found fallen back to its recording when the officer returns. */
function useDocumentVisible() {
  return useSyncExternalStore(subscribeVisibility, () => document.visibilityState !== "hidden", () => true);
}

function CameraFeedInner({ cam, detections, live, large = false, clip, paused = false, onStatus }: FeedProps) {
  const box = useRef<HTMLDivElement>(null);
  const [onScreen, setOnScreen] = useState(false);

  // Decode only what is on screen; a wall of feeds otherwise plays behind the fold. For a
  // live source this also means the connection is closed while the tile is out of view.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => setOnScreen(e.isIntersecting), { threshold: 0.05 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => () => onStatus?.(cam.camera_id, null), [cam.camera_id, onStatus]);

  const shared = { cam, detections, large, clip, paused, onScreen, onStatus };
  return (
    <div ref={box} className={`relative overflow-hidden bg-[#0b0e14] ${large ? "aspect-video" : "aspect-[16/10]"}`}>
      {live ? <LiveFeed key={sourceKey(live)} live={live} {...shared} /> : <RecordedFeed {...shared} />}
    </div>
  );
}

export default memo(CameraFeedInner);

// ===================================================================== HUD pieces
type Tone = "live" | "rec" | "wait" | "warn" | "idle" | "off";
const BADGE: Record<Tone, string> = {
  live: "bg-[#ff3b30] text-white",
  rec: "bg-black/70 text-white/90 ring-1 ring-inset ring-white/25",
  wait: "bg-[#ff9f0a] text-black",
  warn: "bg-[#ffd60a] text-black",
  idle: "bg-white/20 text-white/85",
  off: "bg-white/20 text-white/70",
};

function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[9.5px] font-bold ${BADGE[tone]}`}>
      {tone === "live" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />}
      {tone === "rec" && <span className="h-1.5 w-1.5 rounded-[1px] bg-white/75" />}
      {tone === "wait" && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-black/70" />}
      {children}
    </span>
  );
}

function TopBar({ cam, badge, right, degraded = false, large = false }:
  { cam: FeedCamera; badge: ReactNode; right?: ReactNode; degraded?: boolean; large?: boolean }) {
  // In the dialog the close button sits over the top-right corner; keep the clock clear of it.
  return (
    <div className={`pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-2 ${large ? "pr-14" : ""}`}>
      <div className="flex min-w-0 items-center gap-1.5">
        {badge}
        <span className="truncate rounded bg-black/60 px-1.5 py-0.5 font-mono text-[9.5px] text-white/90">{cam.camera_id}</span>
        {degraded && <span className="shrink-0 rounded bg-[#ff9f0a] px-1.5 py-0.5 font-mono text-[9px] font-bold text-black">DEGRADED</span>}
      </div>
      {right && <span className="shrink-0 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[9.5px] text-white/90">{right}</span>}
    </div>
  );
}

function BottomBar({ left, center, right }: { left: ReactNode; center: ReactNode; right: ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-1 p-2 font-mono text-[9px] text-white/85">
      <span className="shrink-0 rounded bg-black/60 px-1.5 py-0.5">{left}</span>
      <span className="min-w-0 truncate rounded bg-black/60 px-1.5 py-0.5">{center}</span>
      <span className="hidden max-w-[35%] truncate rounded bg-black/60 px-1.5 py-0.5 sm:inline">{right}</span>
    </div>
  );
}

/** Grain and scanlines are a look, and are kept off a real live picture. */
function Sensor({ grain, lines }: { grain: boolean; lines: boolean }) {
  return (
    <>
      {grain && <div className="cam-grain pointer-events-none absolute inset-0 opacity-60" />}
      {lines && <div className="pointer-events-none absolute inset-0" style={{ background: "repeating-linear-gradient(0deg, rgba(0,0,0,0.10) 0 1px, transparent 1px 3px)" }} />}
      <div className="pointer-events-none absolute inset-0" style={{ boxShadow: "inset 0 0 50px rgba(0,0,0,0.55)" }} />
    </>
  );
}

function Strip({ children, top = "top-8" }: { children: ReactNode; top?: string }) {
  return (
    <div className={`pointer-events-none absolute inset-x-2 ${top} flex items-center gap-2 rounded-md bg-black/75 px-2 py-1 font-mono text-white shadow-lg backdrop-blur-sm`}>
      {children}
    </div>
  );
}

// ===================================================================== recorded
interface RecordedProps {
  cam: FeedCamera;
  clip?: Clip;
  detections?: Detection[];
  large: boolean;
  paused: boolean;
  onScreen: boolean;
  onStatus?: StatusFn;
  /** Set when the clip stands in for a live source that is down; says so on the picture. */
  notice?: ReactNode;
}

function RecordedFeed({ cam, clip: chosen, detections = [], large, paused, onScreen, onStatus, notice }: RecordedProps) {
  const clip = chosen || clipFor(cam);
  const video = useRef<HTMLVideoElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [tracks, setTracks] = useState<Tracks | null>(null);
  const [counts, setCounts] = useState({ vehicles: 0, people: 0 });
  const standIn = !!notice;
  // The health flag belongs to the simulated camera. When the clip stands in for a real
  // source that is down, the notice says what is wrong and the flag is beside the point.
  const offline = !standIn && cam.status === "offline";
  const degraded = !standIn && cam.status === "degraded";

  useEffect(() => {
    if (!standIn) onStatus?.(cam.camera_id, offline ? "offline" : "recorded");
  }, [cam.camera_id, offline, standIn, onStatus]);

  useEffect(() => {
    if (offline) return;
    let alive = true;
    loadTracks(clip.slug).then((t) => alive && setTracks(t));
    return () => {
      alive = false;
    };
  }, [clip.slug, offline]);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    if (onScreen && !paused) v.play().catch(() => {});
    else v.pause();
  }, [onScreen, paused, clip.slug, offline]);

  // Each camera starts at its own moment, so two tiles on one clip never move in lockstep.
  const seek = () => {
    const v = video.current;
    if (v?.duration) v.currentTime = hashUnit(cam.camera_id) * v.duration * 0.85;
  };

  // Boxes, redrawn every frame from the stored tracks at the video's current time.
  useEffect(() => {
    if (!tracks || !onScreen || offline) return;
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
  }, [tracks, onScreen, offline, large]);

  const hot = detections[0];
  const year = clip.title.match(/\b(20\d{2})\b/)?.[1];

  return (
    <>
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
            key={clip.slug}
            ref={video}
            src={footageUrl(clip)}
            muted
            loop
            playsInline
            preload="metadata"
            onLoadedMetadata={seek}
            className="absolute inset-0 h-full w-full object-cover"
            style={{ filter: degraded ? "contrast(0.92) saturate(0.7) blur(0.6px)" : "contrast(1.06) saturate(0.9)" }}
          />
          <canvas ref={canvas} className="pointer-events-none absolute inset-0 h-full w-full" />
        </>
      )}

      <Sensor grain={!offline} lines />
      <TopBar cam={cam} degraded={degraded} large={large}
              badge={offline ? <Badge tone="off">OFFLINE</Badge> : <Badge tone="rec">RECORDED</Badge>}
              right={offline ? null : year ? `FILMED ${year}` : "REPLAY"} />
      {notice}

      {/* matcher alert for this camera — a strip, not a box on someone in the frame */}
      {hot && !offline && (
        <button onClick={hot.onClick}
                className={`absolute inset-x-2 ${notice ? "top-[3.6rem]" : "top-8"} flex items-center gap-2 rounded-md px-2 py-1 text-left font-mono text-white shadow-lg backdrop-blur-sm`}
                style={{ background: `${TONE[hot.tone]}e6` }}>
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-white" />
          <span className="min-w-0 flex-1 truncate text-[10px] font-bold">{hot.label} · {(hot.score * 100).toFixed(1)}%</span>
          {detections.length > 1 && <span className="shrink-0 rounded bg-black/30 px-1 text-[9px] font-bold">+{detections.length - 1}</span>}
          <span className="shrink-0 text-[9px] font-semibold opacity-85">REVIEW →</span>
        </button>
      )}

      <BottomBar
        left={`${cam.fps || 25}FPS · ${cam.resolution || "1080p"}`}
        center={!offline && tracks
          ? `AI · ${counts.vehicles} vehicle${counts.vehicles === 1 ? "" : "s"} · ${counts.people} ${counts.people === 1 ? "person" : "people"}`
          : `${cam.lat.toFixed(4)}°N ${cam.lng.toFixed(4)}°E`}
        right={typeName(cam.type)} />
    </>
  );
}

// ===================================================================== live
const CONNECT_TIMEOUT_MS = 20_000;   // not live by then: the attempt has failed
const QUIET_MS = 3_000;              // no frame this long: no longer "LIVE", but buffering
const STALL_MS = 10_000;             // no frame this long: reconnect
const HEALTHY_MS = 30_000;           // live this long: the retry budget is earned back
const BACKOFF_MS = [2_000, 5_000, 12_000];
const REPROBE_MS = 60_000;           // after giving up, try the live link again this often

type Phase = "connecting" | "live" | "not-live" | "stalled" | "unverified" | "retrying" | "down";

/** What a player tells the tile. Every signal is about what the browser observed. */
interface PlayerHooks {
  /** A frame was presented (or the embedded player's clock advanced while playing). */
  frame: () => void;
  /** The source is a finished recording, not a live stream. */
  notLive: () => void;
  fail: (why: string, fatal?: boolean) => void;
  meta: (w: number, h: number) => void;
  /** The embedded player never reported its state; frames cannot be confirmed. */
  unverified: () => void;
  /** Whether this source emits a per-frame signal, so that a stall is detectable. */
  stallable: (on: boolean) => void;
  /** Hold the connect timeout while waiting on the viewer (a permission prompt). */
  hold: (on: boolean) => void;
  /** Withhold LIVE until the source proves it is advancing: an encoder that died leaves
   *  its last playlist behind, and replaying those seconds is not live. */
  gate: (on: boolean) => void;
}

interface LiveProps extends Omit<RecordedProps, "notice"> {
  live: LiveSource;
}

function LiveFeed({ cam, live, clip, detections, large, paused, onScreen, onStatus }: LiveProps) {
  const device = live.kind === "device";
  const docVisible = useDocumentVisible();
  // The viewer's own camera starts only on their click: a page must not switch on a
  // camera because someone scrolled past a tile.
  const [started, setStarted] = useState(!device);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [attempt, setAttempt] = useState(0);
  const [reason, setReason] = useState("");
  const [fatal, setFatal] = useState(false);
  const [size, setSize] = useState("");
  const [watched, setWatched] = useState(live.kind !== "mjpeg" && live.kind !== "youtube");
  const phaseRef = useRef<Phase>("connecting");
  const fails = useRef(0);
  const probes = useRef(0);
  const lastFrame = useRef(0);
  const since = useRef(0);
  const notLiveRef = useRef(false);
  const stallRef = useRef(true);
  const holdRef = useRef(false);
  const gateRef = useRef(false);
  const liveSince = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const go = useCallback((p: Phase) => {
    phaseRef.current = p;
    setPhase(p);
  }, []);
  const cancelTimer = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => cancelTimer, [cancelTimer]);

  const visible = onScreen && docVisible && !paused;
  const connected = phase === "connecting" || phase === "live" || phase === "not-live" || phase === "stalled" || phase === "unverified";
  const mounted = started && visible && connected;

  const perFrame = live.kind !== "mjpeg" && live.kind !== "youtube";
  const reconnect = useCallback(() => {
    // A new connection has to show again that it reports frames before stalls count.
    setWatched(perFrame);
    go("connecting");
    setAttempt((a) => a + 1);
  }, [go, perFrame]);

  const fail = useCallback((why: string, isFatal = false) => {
    const p = phaseRef.current;
    if (p === "retrying" || p === "down") return;
    cancelTimer();
    setReason(why);
    const n = fails.current++;
    if (!isFatal && n < BACKOFF_MS.length) {
      go("retrying");
      timer.current = setTimeout(reconnect, BACKOFF_MS[n]);
    } else {
      setFatal(isFatal);
      go("down");
      // A refused permission or a broadcast that forbids embedding will not fix itself;
      // anything else might, so it is probed again while the tile is in use.
      // The probe is ONE attempt (fails stays exhausted, so a miss drops straight back
      // here) and the interval doubles up to 10 minutes: a dead link must leave the
      // recorded footage on screen, not cycle through "Reconnecting…" every minute.
      if (!isFatal) {
        const wait = Math.min(REPROBE_MS * 2 ** probes.current, 10 * REPROBE_MS);
        probes.current += 1;
        timer.current = setTimeout(() => { fails.current = BACKOFF_MS.length; reconnect(); }, wait);
      }
    }
  }, [cancelTimer, go, reconnect]);

  const retryNow = useCallback(() => {
    cancelTimer();
    fails.current = 0;
    probes.current = 0;
    setFatal(false);
    setReason("");
    reconnect();
  }, [cancelTimer, reconnect]);

  const frame = useCallback(() => {
    lastFrame.current = performance.now();
    const p = phaseRef.current;
    if (gateRef.current || (p !== "connecting" && p !== "unverified" && p !== "stalled")) return;
    // The retry budget is not reset here but after HEALTHY_MS of playing: a source that
    // shows a few seconds and dies again must still end on the honest fallback.
    if (p !== "stalled") liveSince.current = lastFrame.current;
    go(notLiveRef.current ? "not-live" : "live");
  }, [go]);
  const notLive = useCallback(() => {
    notLiveRef.current = true;
    if (phaseRef.current === "live") go("not-live");
  }, [go]);
  const unverified = useCallback(() => {
    if (phaseRef.current === "connecting") go("unverified");
  }, [go]);
  const meta = useCallback((w: number, h: number) => setSize(w > 0 && h > 0 ? `${w}×${h}` : ""), []);
  const stallable = useCallback((on: boolean) => {
    stallRef.current = on;
    setWatched(on);
  }, []);
  const hold = useCallback((on: boolean) => {
    holdRef.current = on;
    if (!on) since.current = performance.now();
  }, []);
  const gate = useCallback((on: boolean) => {
    gateRef.current = on;
  }, []);
  const hooks = useMemo<PlayerHooks>(() => ({ frame, notLive, fail, meta, unverified, stallable, hold, gate }),
    [frame, notLive, fail, meta, unverified, stallable, hold, gate]);

  // A fresh connection starts with no frames. This also covers coming back on screen
  // after the player was torn down, when the phase still says what it was before.
  // A layout effect, not a passive one: a child's passive effects run before its
  // parent's, so a reset here in useEffect would land after the player had already
  // opened - wiping the hold the device player sets while its permission prompt is up.
  useLayoutEffect(() => {
    if (!mounted) return;
    since.current = performance.now();
    lastFrame.current = 0;
    holdRef.current = false;
    gateRef.current = false;
    liveSince.current = 0;
    stallRef.current = perFrame;
    if (phaseRef.current !== "connecting") go("connecting");
  }, [mounted, attempt, perFrame, go]);

  // Watchdog: a first frame within the connect timeout, then frames that keep coming.
  useEffect(() => {
    if (!mounted) return;
    const t = setInterval(() => {
      if (document.visibilityState === "hidden") return;
      const now = performance.now();
      const p = phaseRef.current;
      if (p === "connecting") {
        if (!holdRef.current && now - since.current > CONNECT_TIMEOUT_MS)
          fail(gateRef.current && lastFrame.current
            ? "The stream replays old segments but nothing new is being published."
            : `No picture within ${CONNECT_TIMEOUT_MS / 1000} s.`);
        return;
      }
      if (p !== "live" && p !== "not-live" && p !== "stalled") return;
      if (liveSince.current && now - liveSince.current > HEALTHY_MS) {
        fails.current = 0;
        probes.current = 0;
      }
      if (!stallRef.current) return;
      const gap = now - lastFrame.current;
      if (gap > STALL_MS) fail(`No new frames for ${STALL_MS / 1000} s.`);
      else if (gap > QUIET_MS && p !== "stalled") go("stalled");
    }, 1000);
    return () => clearInterval(t);
  }, [mounted, fail, go]);

  const feed: FeedState = !started ? "awaiting-start" : phase === "down" ? "down" : !visible ? "paused"
    : phase === "retrying" ? "connecting" : phase;
  useEffect(() => {
    onStatus?.(cam.camera_id, feed);
  }, [cam.camera_id, feed, onStatus]);

  if (phase === "down") {
    return (
      <RecordedFeed cam={cam} clip={clip} detections={detections} large={large} paused={paused} onScreen={onScreen}
        notice={
          <Strip>
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#ff9f0a]" />
            <span className="min-w-0 flex-1 truncate text-[10px] font-bold" title={reason}>
              {device ? "Device camera unavailable" : "Live link down"} — showing recorded footage
              {large && reason ? <span className="font-normal text-white/70"> · {reason}{fatal ? "" : " Checking the link again periodically."}</span> : null}
            </span>
            <button onClick={retryNow} className="pointer-events-auto flex shrink-0 items-center gap-1 rounded bg-white/15 px-1.5 py-0.5 text-[9px] font-semibold hover:bg-white/25">
              <RotateCw className="h-2.5 w-2.5" /> {fatal ? "Try again" : "Retry"}
            </button>
          </Strip>
        } />
    );
  }

  const kind = KIND_LABEL[live.kind];
  const badge = !started ? <Badge tone="idle">DEVICE CAMERA</Badge>
    : !visible ? <Badge tone="idle">LIVE SOURCE · PAUSED</Badge>
    : phase === "live" ? <Badge tone="live">{device ? "LIVE · THIS DEVICE" : "LIVE"}</Badge>
    : phase === "not-live" ? <Badge tone="warn">NOT LIVE · REMOTE RECORDING</Badge>
    : phase === "unverified" ? <Badge tone="idle">EMBED · STATUS UNKNOWN</Badge>
    : phase === "stalled" ? <Badge tone="wait">BUFFERING…</Badge>
    : <Badge tone="wait">{phase === "retrying" ? "RECONNECTING…" : "CONNECTING…"}</Badge>;
  const waiting = started && visible && (phase === "connecting" || phase === "retrying");
  const analytics = device ? "Live · this device's camera · no analytics" : "Live · no on-device analytics";

  return (
    <>
      {mounted && <LivePlayer key={attempt} kind={live.kind} url={live.url} videoId={live.video_id} hooks={hooks} />}

      {!started && (
        <div className="absolute inset-0 grid place-items-center p-4 text-center">
          <div>
            <button onClick={() => setStarted(true)}
                    className="inline-flex items-center gap-1.5 rounded-full bg-white px-3.5 py-1.5 text-[12px] font-semibold text-[#1d1d1f] shadow-lg hover:bg-white/90">
              <Webcam className="h-4 w-4" /> Start this device&apos;s camera
            </button>
            <p className="mx-auto mt-2 max-w-[28ch] font-mono text-[10px] leading-snug text-white/60">
              Plays the camera of the device you are using — not the CCTV camera at {cam.name}.
            </p>
          </div>
        </div>
      )}
      {waiting && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center p-6 text-center">
          <div className={`font-mono text-white/70 ${large ? "text-[12px]" : "text-[10px]"}`}>
            {phase === "retrying" ? `Reconnecting to the ${kind} source…` : `Connecting to the ${kind} source…`}
            {large && phase === "retrying" && reason && <div className="mt-1 text-white/45">{reason}</div>}
          </div>
        </div>
      )}

      <Sensor grain={false} lines={false} />
      <TopBar cam={cam} badge={badge} large={large} right={phase === "live" && visible ? <Clock /> : null} />
      {device && started && (
        <Strip>
          <span className="min-w-0 flex-1 truncate text-[10px]">This device&apos;s camera — not the CCTV camera at {cam.name}</span>
        </Strip>
      )}
      <BottomBar
        left={size || kind}
        center={phase === "not-live" ? "Recording, not a live stream · no analytics"
          : large && phase === "live" && !watched ? `${analytics} · stalls not detectable for this source` : analytics}
        right={live.label || typeName(cam.type)} />
    </>
  );
}

// ===================================================================== players
class PlayerError extends Error {
  fatal: boolean;
  constructor(message: string, fatal = false) {
    super(message);
    this.fatal = fatal;
  }
}

function LivePlayer({ kind, url, videoId, hooks }: { kind: StreamKind; url: string | null; videoId?: string | null; hooks: PlayerHooks }) {
  if (kind === "youtube") return <YouTubePlayer videoId={videoId || ""} hooks={hooks} />;
  if (kind === "mjpeg") return <MjpegPlayer url={url || ""} hooks={hooks} />;
  return <VideoPlayer kind={kind} url={url || ""} hooks={hooks} />;
}

function mediaErrorText(e: MediaError | null) {
  if (!e) return "The stream could not be played.";
  if (e.code === 2) return "The stream's server stopped answering.";
  if (e.code === 3) return "The stream's video could not be decoded.";
  if (e.code === 4) return "This browser cannot play the stream, or its server refused it.";
  return "The stream could not be played.";
}

/** <video> for HLS, MP4, WHEP and the device camera. Frames are counted with
 *  requestVideoFrameCallback where the browser has it (one call per presented frame),
 *  and otherwise by currentTime advancing. */
function VideoPlayer({ kind, url, hooks }: { kind: StreamKind; url: string; hooks: PlayerHooks }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    let disposed = false;
    let teardown: (() => void) | undefined;
    let handle = 0;
    let lastT = -1;
    const perFrame = typeof v.requestVideoFrameCallback === "function";
    const onFrame = () => {
      if (disposed) return;
      hooks.frame();
      handle = v.requestVideoFrameCallback(onFrame);
    };
    const onTime = () => {
      if (v.currentTime !== lastT) {
        lastT = v.currentTime;
        hooks.frame();
      }
    };
    const onMeta = () => hooks.meta(v.videoWidth, v.videoHeight);
    const onError = () => hooks.fail(mediaErrorText(v.error));
    const onEnded = () => hooks.fail("The stream ended.");
    if (perFrame) handle = v.requestVideoFrameCallback(onFrame);
    else v.addEventListener("timeupdate", onTime);
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("resize", onMeta);
    v.addEventListener("error", onError);
    v.addEventListener("ended", onEnded);

    openVideo(kind, url, v, hooks, () => disposed)
      .then((t) => {
        if (disposed) t?.();
        else teardown = t;
      })
      .catch((e) => {
        if (!disposed) hooks.fail(e instanceof PlayerError ? e.message : "The stream could not be opened.", e instanceof PlayerError && e.fatal);
      });

    return () => {
      disposed = true;
      if (perFrame && handle) v.cancelVideoFrameCallback(handle);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("resize", onMeta);
      v.removeEventListener("error", onError);
      v.removeEventListener("ended", onEnded);
      teardown?.();
      v.pause();
      v.srcObject = null;
      v.removeAttribute("src");
      v.load();
    };
  }, [kind, url, hooks]);
  return <video ref={ref} muted playsInline autoPlay className="absolute inset-0 h-full w-full object-cover" />;
}

function openVideo(kind: StreamKind, url: string, v: HTMLVideoElement, hooks: PlayerHooks, gone: () => boolean): Promise<(() => void) | undefined> {
  if (kind === "hls") return openHls(url, v, hooks, gone);
  if (kind === "mp4") return Promise.resolve(openSrc(url, v, hooks));
  if (kind === "webrtc-whep") return openWhep(url, v, hooks, gone);
  if (kind === "device") return openDevice(v, hooks, gone);
  return Promise.reject(new PlayerError("This kind of source cannot be played here.", true));
}

/** A plain src: an MP4 stream, or HLS where the browser plays it natively. A live
 *  stream has no end, so the browser reports its duration as infinite; a finite one
 *  means a file - real footage, but a recording, and it is labelled as one. */
function openSrc(url: string, v: HTMLVideoElement, hooks: PlayerHooks) {
  const onMeta = () => {
    if (Number.isFinite(v.duration) && v.duration > 0) {
      v.loop = true;
      hooks.notLive();
    }
  };
  v.addEventListener("loadedmetadata", onMeta);
  v.src = url;
  v.play().catch(() => {});
  return () => v.removeEventListener("loadedmetadata", onMeta);
}

const HLS_MIME = "application/vnd.apple.mpegurl";

/** Safari (and every iOS browser, all WebKit) plays HLS natively, which is lighter and
 *  keeps AirPlay. Chosen by engine rather than by canPlayType alone, because Android
 *  Chrome also answers "maybe" for HLS and then plays it badly. */
function prefersNativeHls(v: HTMLVideoElement) {
  if (!v.canPlayType(HLS_MIME)) return false;
  const ua = navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ||
    (/Safari\//.test(ua) && !/Chrome|Chromium|CriOS|Android|Edg/.test(ua));
}

async function openHls(url: string, v: HTMLVideoElement, hooks: PlayerHooks, gone: () => boolean) {
  if (prefersNativeHls(v)) return openSrc(url, v, hooks);
  const Hls = (await import("hls.js")).default;
  if (gone()) return undefined;
  if (!Hls.isSupported()) {
    if (v.canPlayType(HLS_MIME)) return openSrc(url, v, hooks);
    throw new PlayerError("This browser cannot play HLS.", true);
  }
  const hls = new Hls({ lowLatencyMode: true, liveDurationInfinity: true, backBufferLength: 30 });
  let recovered = false;
  // Frames from the first playlist prove nothing about now: it may be the last one a
  // dead encoder wrote. LIVE waits for a refresh that brings new segments.
  hooks.gate(true);
  let loads = 0;
  hls.on(Hls.Events.LEVEL_LOADED, (_e, d) => {
    const det = d.details;
    if (!det) return;
    if (!det.live) {
      v.loop = true;
      hooks.gate(false);
      hooks.notLive();
      return;
    }
    if (++loads > 1 && det.advanced) hooks.gate(false);
    if (det.misses >= 4) hooks.fail("The live playlist stopped advancing: nothing new is being published.");
  });
  hls.on(Hls.Events.MANIFEST_PARSED, () => {
    v.play().catch(() => {});
  });
  hls.on(Hls.Events.ERROR, (_e, d) => {
    if (!d.fatal) return;
    // hls.js can often resume after a decode error by re-creating the media buffers.
    if (d.type === Hls.ErrorTypes.MEDIA_ERROR && !recovered) {
      recovered = true;
      hls.recoverMediaError();
      return;
    }
    const code = d.response?.code;
    if (d.details === Hls.ErrorDetails.MANIFEST_LOAD_ERROR || d.details === Hls.ErrorDetails.MANIFEST_LOAD_TIMEOUT)
      hooks.fail(code ? `The playlist answered HTTP ${code}.`
        : "The playlist could not be fetched: the server is unreachable, or sends no CORS headers for this site.");
    else if (d.type === Hls.ErrorTypes.NETWORK_ERROR)
      hooks.fail(code ? `A segment answered HTTP ${code}.` : "The stream's server stopped answering.");
    else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hooks.fail("The stream's video could not be decoded in this browser.");
    else hooks.fail(`HLS error: ${d.details}.`);
  });
  hls.loadSource(url);
  hls.attachMedia(v);
  return () => hls.destroy();
}

function iceGathered(pc: RTCPeerConnection, ms: number) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise<void>((resolve) => {
    const done = () => {
      clearTimeout(t);
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    };
    const check = () => {
      if (pc.iceGatheringState === "complete") done();
    };
    const t = setTimeout(done, ms);
    pc.addEventListener("icegatheringstatechange", check);
  });
}

/** A minimal WHEP client (RFC 9725): recvonly transceivers, the offer POSTed as
 *  application/sdp, the answer applied, the session DELETEd on teardown.
 *
 *  No ICE servers are configured, on purpose: a third-party STUN server would learn
 *  which officer is watching which camera. A media server on a public address (MediaMTX,
 *  Cloudflare Stream, Janus) is reachable without one; one behind NAT needs a TURN relay
 *  on the server side. */
async function openWhep(url: string, v: HTMLVideoElement, hooks: PlayerHooks, gone: () => boolean) {
  if (typeof RTCPeerConnection === "undefined") throw new PlayerError("This browser has no WebRTC.", true);
  const pc = new RTCPeerConnection();
  const stream = new MediaStream();
  let resource: string | null = null;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    pc.close();
    // Best effort: if the media server's CORS policy refuses DELETE, its session simply
    // times out on the server.
    if (resource) fetch(resource, { method: "DELETE" }).catch(() => {});
  };
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  pc.ontrack = (e) => {
    stream.addTrack(e.track);
    if (v.srcObject !== stream) v.srcObject = stream;
    v.play().catch(() => {});
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === "failed") hooks.fail("The WebRTC connection failed: the media server is not reachable from this network.");
  };
  try {
    await pc.setLocalDescription(await pc.createOffer());
    await iceGathered(pc, 2500);
    if (gone()) {
      close();
      return undefined;
    }
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/sdp" }, body: pc.localDescription?.sdp ?? "" });
    if (res.status !== 201 && res.status !== 200)
      throw new PlayerError(`The WHEP endpoint answered HTTP ${res.status}.`, res.status === 401 || res.status === 403);
    const loc = res.headers.get("Location");
    if (loc) resource = new URL(loc, url).toString();
    const sdp = await res.text();
    if (gone()) {
      close();
      return undefined;
    }
    await pc.setRemoteDescription({ type: "answer", sdp });
  } catch (e) {
    close();
    if (e instanceof PlayerError) throw e;
    throw new PlayerError(e instanceof TypeError
      ? "The WHEP endpoint could not be reached (network, or no CORS headers for this site)."
      : "The media server's WebRTC answer could not be used.");
  }
  return close;
}

async function openDevice(v: HTMLVideoElement, hooks: PlayerHooks, gone: () => boolean) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
    throw new PlayerError("Camera access needs the app on https (or localhost).", true);
  let stream: MediaStream;
  hooks.hold(true);
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
  } catch (e) {
    const name = (e as DOMException | undefined)?.name;
    throw new PlayerError(
      name === "NotAllowedError" ? "Camera permission was refused."
        : name === "NotFoundError" || name === "OverconstrainedError" ? "This device has no usable camera."
        : name === "NotReadableError" ? "The camera is in use by another app."
        : "The camera could not be started.", true);
  } finally {
    hooks.hold(false);
  }
  const stop = () => stream.getTracks().forEach((t) => t.stop());
  if (gone()) {
    stop();
    return undefined;
  }
  stream.getVideoTracks().forEach((t) => t.addEventListener("ended", () => hooks.fail("The device camera stopped.", true)));
  v.srcObject = stream;
  v.play().catch(() => {});
  return stop;
}

const YT_ORIGIN = /^https:\/\/www\.youtube(-nocookie)?\.com$/;
/** The iframe API's message envelope, as far as this player reads it. */
type YtMessage = { event?: string; info?: unknown };
type YtInfo = { playerState?: unknown; currentTime?: unknown; videoData?: { isLive?: unknown } };
// Errors that retrying cannot fix: bad id, removed/private video, embedding forbidden.
const YT_FATAL = new Set([2, 100, 101, 150, 153]);
const ytError = (c: number) =>
  c === 2 ? "YouTube rejected the video id."
    : c === 5 ? "YouTube's player could not play this broadcast."
    : c === 100 ? "The video is private or was removed."
    : c === 101 || c === 150 ? "The broadcaster does not allow this video to be embedded."
    : c === 153 ? "YouTube refused the embed (no referrer was sent)."
    : `YouTube player error ${c}.`;

/** The privacy-enhanced YouTube embed. A cross-origin iframe exposes no frames, so the
 *  only evidence is the player's own state, posted back through the iframe API's
 *  message protocol (enablejsapi=1): "playing" and its clock advancing count as frames.
 *  If the player never answers, the tile says the status is unknown instead of LIVE. */
function YouTubePlayer({ videoId, hooks }: { videoId: string; hooks: PlayerHooks }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [src] = useState(() => youtubeEmbedUrl(videoId, typeof window === "undefined" ? "" : window.location.origin));
  useEffect(() => {
    const f = ref.current;
    if (!f) return;
    let heard = false;
    let tries = 0;
    let state = -1;
    let lastT = -1;
    let ticks = 0;
    let firstTick = 0;
    const post = (m: object) => f.contentWindow?.postMessage(JSON.stringify(m), "https://www.youtube-nocookie.com");
    const hello = () => {
      post({ event: "listening", id: 1, channel: "widget" });
      for (const ev of ["onReady", "onStateChange", "onError"])
        post({ event: "command", func: "addEventListener", args: [ev], id: 1, channel: "widget" });
    };
    const ping = setInterval(() => {
      if (heard) return clearInterval(ping);
      if (++tries > 12) {
        clearInterval(ping);
        hooks.unverified();
        return;
      }
      hello();
    }, 1000);
    const onMsg = (e: MessageEvent) => {
      if (e.source !== f.contentWindow || !YT_ORIGIN.test(e.origin)) return;
      let d: YtMessage | null;
      try {
        d = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      } catch {
        return;
      }
      if (!d || typeof d !== "object") return;
      heard = true;
      if (d.event === "onError") {
        const c = Number(d.info);
        hooks.fail(ytError(c), YT_FATAL.has(c));
        return;
      }
      const info = (d.event === "onStateChange" ? { playerState: d.info }
        : d.event === "infoDelivery" || d.event === "initialDelivery" ? d.info : null) as YtInfo | null;
      if (!info || typeof info !== "object") return;
      if (info.videoData?.isLive === false) hooks.notLive();
      if (typeof info.playerState === "number") {
        state = info.playerState;
        if (state === 0) hooks.fail("The broadcast ended.");
        else if (state === 1) hooks.frame();
      }
      if (typeof info.currentTime === "number" && info.currentTime !== lastT) {
        lastT = info.currentTime;
        if (state !== 1) return;
        hooks.frame();
        // Stall detection only once the player has shown it reports its clock steadily
        // (five updates inside eight seconds); otherwise silence would read as a stall.
        const now = performance.now();
        if (!firstTick) firstTick = now;
        if (++ticks === 5 && now - firstTick < 8000) hooks.stallable(true);
      }
    };
    window.addEventListener("message", onMsg);
    f.addEventListener("load", hello);
    return () => {
      clearInterval(ping);
      window.removeEventListener("message", onMsg);
      f.removeEventListener("load", hello);
    };
  }, [hooks]);
  return (
    <iframe ref={ref} src={src} title="Live YouTube broadcast" allow="autoplay; encrypted-media; picture-in-picture"
            referrerPolicy="strict-origin-when-cross-origin" className="pointer-events-none absolute inset-0 h-full w-full border-0" />
  );
}

const BLANK_GIF = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";

/** MJPEG in an <img>. Firefox fires `load` for every part of the multipart stream, so
 *  there a stall is detectable. Chrome fires it at most once and a cross-origin image
 *  cannot be read back, so there the badge can only mean "a frame arrived and the
 *  connection has not errored" - and the large view says stalls are not detectable. */
function MjpegPlayer({ url, hooks }: { url: string; hooks: PlayerHooks }) {
  const ref = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const img = ref.current;
    if (!img) return;
    let loads = 0;
    let seen = false;
    const first = () => {
      if (seen || img.naturalWidth === 0) return;
      seen = true;
      hooks.meta(img.naturalWidth, img.naturalHeight);
      hooks.frame();
    };
    const onLoad = () => {
      if (++loads === 2) hooks.stallable(true);
      first();
      hooks.frame();
    };
    const onError = () => hooks.fail("The MJPEG stream could not be loaded.");
    const poll = setInterval(first, 500);
    img.addEventListener("load", onLoad);
    img.addEventListener("error", onError);
    img.src = url;
    return () => {
      clearInterval(poll);
      img.removeEventListener("load", onLoad);
      img.removeEventListener("error", onError);
      img.src = BLANK_GIF;   // replacing src is what closes the multipart connection
    };
  }, [url, hooks]);
  // eslint-disable-next-line @next/next/no-img-element
  return <img ref={ref} alt="" className="absolute inset-0 h-full w-full object-cover" />;
}

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
