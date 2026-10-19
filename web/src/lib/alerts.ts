"use client";

/* Live surveillance alerts shared by the nav bell, toasts and the CCTV page.
 *
 * One poller per tab. Unread state is "newer than the last time the officer opened the
 * bell", kept in localStorage so a reload does not replay every alert as new. Settings
 * (sound, desktop notifications, minimum priority, district) are per-officer
 * conveniences and live in localStorage too; every read and write is guarded because
 * storage can be unavailable. */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { getJSON, ApiError } from "@/lib/api";

export interface SurvAlert {
  event_id: string;
  kind: "face" | "plate";
  ts: number;
  time: string;
  camera_id: string;
  camera_name: string;
  district: string;
  area: string;
  lat: number;
  lng: number;
  quality: number;
  priority: "P1" | "P2" | "P3" | null;
  reasons: string[];
  status: string;
  face_match?: any;
  plate_read?: string;
  plate_match?: any;
  decision?: any;
  trail_id?: string;
}

export interface AlertSettings {
  sound: boolean;
  desktop: boolean;
  minPriority: "P1" | "P2";
  district: string;
}

const SETTINGS_KEY = "ksp.alerts.settings";
const SEEN_KEY = "ksp.alerts.seen";
const DEFAULTS: AlertSettings = { sound: true, desktop: false, minPriority: "P2", district: "" };

function read<T>(k: string, d: T): T {
  try {
    const v = localStorage.getItem(k);
    return v ? { ...d, ...JSON.parse(v) } : d;
  } catch {
    return d;
  }
}
function write(k: string, v: unknown) {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {}
}

// ------------------------------------------------------------------ store
type State = { alerts: SurvAlert[]; locked: boolean; seen: number; settings: AlertSettings; fresh: SurvAlert[] };
// The server (and the hydration pass) always see this empty snapshot; live data
// arrives on the client after mount. Returning live state during hydration
// mismatched the server HTML whenever the bell had already fetched alerts.
const SERVER: State = { alerts: [], locked: false, seen: 0, settings: DEFAULTS, fresh: [] };
let state: State = SERVER;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
const set = (p: Partial<State>) => {
  state = { ...state, ...p };
  emit();
};
let started = false;
let timer: any = null;
let lastTs = 0;

function passes(a: SurvAlert, s: AlertSettings) {
  if (s.minPriority === "P1" && a.priority !== "P1") return false;
  if (s.district && a.district !== s.district) return false;
  return a.priority === "P1" || a.priority === "P2";
}

export function beep(priority: string | null) {
  try {
    const AC = (window as any).AudioContext || (window as any).webkitAudioContext;
    const ctx = new AC();
    const tones = priority === "P1" ? [880, 660, 880] : [660, 880];
    tones.forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.16);
      g.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + i * 0.16 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.16 + 0.14);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + i * 0.16);
      o.stop(ctx.currentTime + i * 0.16 + 0.15);
    });
    setTimeout(() => ctx.close(), 900);
  } catch {}
}

function describe(a: SurvAlert) {
  if (a.kind === "face" && a.face_match)
    return `${a.face_match.person?.name || a.face_match.suspect_id} · ${Math.round(a.face_match.similarity * 100)}% face match`;
  return `Plate ${a.plate_read} → ${a.plate_match?.make_model || "stolen vehicle"}`;
}

async function poll() {
  try {
    const d = await getJSON<any>(`/surveillance/alerts?limit=120${lastTs ? `&since=${lastTs}` : ""}`);
    const incoming: SurvAlert[] = d.alerts || [];
    if (!lastTs) {
      set({ alerts: incoming, locked: false });
    } else if (incoming.length) {
      const known = new Set(state.alerts.map((a) => a.event_id));
      const add = incoming.filter((a) => !known.has(a.event_id));
      const notify = add.filter((a) => passes(a, state.settings));
      set({ alerts: [...add, ...state.alerts].slice(0, 400), fresh: notify.slice(0, 3) });
      if (notify.length) {
        const top = notify.find((a) => a.priority === "P1") || notify[0];
        if (state.settings.sound) beep(top.priority);
        if (state.settings.desktop && typeof Notification !== "undefined" && Notification.permission === "granted") {
          try {
            new Notification(`${top.priority} · ${top.camera_name}`, { body: describe(top), tag: top.event_id });
          } catch {}
        }
      }
    }
    lastTs = Math.max(lastTs, ...(state.alerts.map((a) => a.ts)), d.now ? d.now - 1 : 0);
  } catch (e) {
    // Signed out (or a role without CCTV access): the previous officer's named
    // matches must not stay on screen.
    if (e instanceof ApiError && e.isForbidden) {
      lastTs = 0;
      set({ locked: true, alerts: [], fresh: [] });
    }
  }
}

function start() {
  if (started || typeof window === "undefined") return;
  started = true;
  // First visit: only the last hour counts as unread, not the whole day.
  set({ seen: read<{ ts: number }>(SEEN_KEY, { ts: Date.now() / 1000 - 3600 }).ts, settings: read(SETTINGS_KEY, DEFAULTS) });
  poll();
  timer = setInterval(poll, 10000);
}

/** Re-fetch from scratch, e.g. after signing in or deciding an alert. */
export function refreshAlerts() {
  lastTs = 0;
  return poll();
}

export function markAllSeen() {
  const ts = Math.max(0, ...state.alerts.map((a) => a.ts));
  write(SEEN_KEY, { ts });
  set({ seen: ts });
}

export function updateSettings(p: Partial<AlertSettings>) {
  const s = { ...state.settings, ...p };
  write(SETTINGS_KEY, s);
  set({ settings: s });
}

export function patchAlert(a: SurvAlert) {
  set({ alerts: state.alerts.map((x) => (x.event_id === a.event_id ? { ...x, ...a } : x)) });
}

export function clearFresh() {
  set({ fresh: [] });
}

export function useAlerts() {
  useEffect(() => {
    start();
  }, []);
  const snap = useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => state,
    () => SERVER
  );
  const unread = snap.alerts.filter((a) => a.ts > snap.seen && passes(a, snap.settings) && a.status === "new").length;
  return { ...snap, unread, describe };
}

/** "3 min ago" that re-renders itself. */
export function useNow(ms = 15000) {
  const [now, setNow] = useState(() => Date.now());
  const r = useRef<any>(null);
  useEffect(() => {
    r.current = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(r.current);
  }, [ms]);
  return now;
}

export function ago(ts: number, now: number) {
  const s = Math.max(0, Math.round(now / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

export function istTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
