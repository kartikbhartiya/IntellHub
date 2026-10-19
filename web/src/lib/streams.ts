"use client";

/* Live stream sources attached to cameras, from the server registry (appsail/streams.py).
 *
 * Real KSP Safe City and traffic-police feeds are not public, so a camera plays live
 * only when an administrator has attached a source to it: an NVR's HLS output, a
 * WebRTC (WHEP) endpoint, an MJPEG camera, a YouTube Live broadcast, or "this device's
 * camera". Nothing here invents one. With no source, a tile plays its recorded clip and
 * says RECORDED.
 *
 * One fetch per tab, shared by every tile through a module-level store, and re-fetched
 * when the signed-in identity changes (the map needs an investigator: a live stream can
 * show people). Each read is audited server-side, so the store does not poll; it
 * refreshes when the tab comes back after a minute away, and after a save.
 *
 * This replaces the old per-browser override in localStorage ("ksp.cctv.streams"),
 * which only ever played on the machine that set it. Nothing is migrated: a source has
 * to be attached on the server, by a supervisor, to count. */

import { useEffect, useSyncExternalStore } from "react";
import { AUTH_EVENT, TOKEN_KEY, ApiError, apiFetchRaw, getJSON } from "./api";

export type StreamKind = "hls" | "mp4" | "youtube" | "mjpeg" | "webrtc-whep" | "device";

export interface LiveSource {
  camera_id: string;
  kind: StreamKind;
  /** null for "device". For YouTube, the canonical watch URL; play `video_id`. */
  url: string | null;
  video_id?: string | null;
  label?: string | null;
  attribution?: string | null;
  added_by?: string | null;
  added_at?: string | null;
  /** "deployment" = CAMERA_STREAMS env (read-only here); "operator" = attached in the app. */
  origin: "deployment" | "operator";
  overrides_default?: boolean;
}

export interface StreamKindInfo {
  kind: StreamKind;
  label: string;
  needs_url: boolean;
  hint: string;
}

export interface StreamsPayload {
  streams: Record<string, LiveSource>;
  kinds: StreamKindInfo[];
  count: number;
  can_edit: boolean;
  note?: string;
  storage?: { file: string; file_error?: string | null; deployment_defaults: number; note: string };
  ignored?: { camera_id: string | null; origin: string; reason: string }[];
  history?: { at: string; by: string; camera_id: string; action: "set" | "remove"; kind?: string; url?: string | null }[];
}

/** What a feed tile is actually showing, as the tile itself observed it. */
export type FeedState =
  | "recorded"        // the recorded clip, no live source
  | "offline"         // simulated camera health says offline, no live source
  | "awaiting-start"  // "device" source, waiting for the viewer's click
  | "paused"          // live source, disconnected while off screen / tab hidden
  | "connecting"      // live source, no frame yet (or reconnecting)
  | "live"            // frames are arriving
  | "stalled"         // was live, no frame for a few seconds (buffering); reconnects if it lasts
  | "not-live"        // frames arriving, but the source is a finished recording
  | "unverified"      // embedded player that reports no status (frames unknowable)
  | "down";           // retries exhausted; recorded clip shown with a notice

export const KIND_LABEL: Record<StreamKind, string> = {
  hls: "HLS",
  mp4: "MP4",
  youtube: "YouTube",
  mjpeg: "MJPEG",
  "webrtc-whep": "WebRTC",
  device: "Device camera",
};

/** The privacy-enhanced embed, muted and chromeless so it autoplays like a camera.
 *  enablejsapi + origin let the player post its state back, which is the only way to
 *  know whether a cross-origin embed is actually playing. */
export function youtubeEmbedUrl(videoId: string, origin: string) {
  const q = new URLSearchParams({
    autoplay: "1", mute: "1", controls: "0", playsinline: "1", rel: "0",
    enablejsapi: "1", origin,
  });
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?${q}`;
}

/** Stable identity of a source: a tile reconnects when, and only when, this changes. */
export function sourceKey(s: LiveSource) {
  return `${s.kind}|${s.url ?? ""}|${s.video_id ?? ""}`;
}

// ------------------------------------------------------------------ store
type Status = "idle" | "loading" | "ready" | "locked" | "error";
interface State {
  data: StreamsPayload | null;
  status: Status;
}

const SERVER: State = { data: null, status: "idle" };
const EMPTY: Record<string, LiveSource> = {};
let state: State = SERVER;
let generation = 0;
let lastLoad = 0;
// A failed read (the backend restarting, a gateway hiccup) is retried a few times, so a
// wall opened during an outage does not stay on recordings until the tab is refocused.
const RETRY_MS = [10_000, 30_000, 90_000];
let retries = 0;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
const subs = new Set<() => void>();

function set(next: Partial<State>) {
  state = { ...state, ...next };
  subs.forEach((f) => f());
}

export function refreshStreams(): Promise<void> {
  const mine = ++generation;
  lastLoad = Date.now();
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
  if (!state.data) set({ status: "loading" });
  return getJSON<StreamsPayload>("/surveillance/streams")
    .then((data) => {
      if (mine !== generation) return;
      retries = 0;
      set({ data, status: "ready" });
    })
    .catch((e) => {
      if (mine !== generation) return;
      // Forbidden is a state worth rendering ("sign in to see live sources"), not an
      // outage; either way no source is assumed, so every tile stays on its recording.
      const locked = e instanceof ApiError && e.isForbidden;
      // A transient failure on a re-read keeps the sources already known: one 502
      // must not unmount every live player. Only a refusal clears them.
      if (locked) set({ data: null, status: "locked" });
      else set({ status: state.data ? "ready" : "error" });
      if (!locked && retries < RETRY_MS.length) retryTimer = setTimeout(refreshStreams, RETRY_MS[retries++]);
    });
}

let listening = false;
function listen() {
  if (listening || typeof window === "undefined") return;
  listening = true;
  window.addEventListener(AUTH_EVENT, () => refreshStreams());
  window.addEventListener("storage", (e) => {
    if (e.key === TOKEN_KEY || e.key === null) refreshStreams();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - lastLoad > 60_000) refreshStreams();
  });
  // The retired per-browser override may still hold a URL an officer typed; it is
  // no longer read by anything, so it should not linger in storage either.
  try {
    window.localStorage.removeItem("ksp.cctv.streams");
  } catch {}
}

export interface StreamsView {
  streams: Record<string, LiveSource>;
  kinds: StreamKindInfo[];
  canEdit: boolean;
  status: Status;
  locked: boolean;
  data: StreamsPayload | null;
  refresh: () => Promise<void>;
}

export function useStreams(): StreamsView {
  const snap = useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
    () => state,
    () => SERVER,
  );
  useEffect(() => {
    listen();
    if (state.status === "idle") refreshStreams();
  }, []);
  return {
    streams: snap.data?.streams ?? EMPTY,
    kinds: snap.data?.kinds ?? [],
    canEdit: !!snap.data?.can_edit,
    status: snap.status,
    locked: snap.status === "locked",
    data: snap.data,
    refresh: refreshStreams,
  };
}

// ------------------------------------------------------------------ writes
/** A refused save, with the server's per-field messages kept for the form. */
export class StreamSaveError extends Error {
  status: number;
  code: string;
  errors: Record<string, string>;
  constructor(status: number, code: string, message: string, errors: Record<string, string> = {}) {
    super(message);
    this.name = "StreamSaveError";
    this.status = status;
    this.code = code;
    this.errors = errors;
  }
}

async function post(body: Record<string, unknown>): Promise<StreamsPayload> {
  let res: Response;
  try {
    res = await apiFetchRaw("/surveillance/streams", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Access-Reason": "attach live camera source" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new StreamSaveError(0, "network", "The server could not be reached.");
  }
  let data: (Partial<StreamsPayload> & { error?: string; message?: string; errors?: Record<string, string> }) | null = null;
  try {
    data = await res.json();
  } catch {
    /* empty or HTML body: the status still says what happened */
  }
  if (!res.ok) {
    const fallback =
      res.status === 403
        ? "Only a supervisor can attach or change a live source."
        : `The server refused the change (HTTP ${res.status}).`;
    throw new StreamSaveError(res.status, data?.error || `http_${res.status}`, data?.message || fallback, data?.errors || {});
  }
  // The answer is the whole map as the server now has it; nothing is patched locally.
  ++generation;
  lastLoad = Date.now();
  set({ data: data as StreamsPayload, status: "ready" });
  return data as StreamsPayload;
}

export function saveStream(
  cameraId: string,
  src: { kind: StreamKind; url?: string; label?: string; attribution?: string },
) {
  return post({
    camera_id: cameraId,
    kind: src.kind,
    url: src.kind === "device" ? undefined : src.url,
    label: src.label,
    attribution: src.attribution,
  });
}

export function removeStream(cameraId: string) {
  return post({ camera_id: cameraId, remove: true });
}
