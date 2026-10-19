"use client";

/**
 * One answer per tab to "who is signed in, and what may they see?".
 *
 * The nav, the landing page and the account page each used to call /auth/me on
 * their own. After a sign-in on one surface the others kept showing the previous
 * identity until a reload — a nav saying "Sign in" above a page rendering a
 * supervisor's watchlist is the kind of disagreement that makes an operator stop
 * trusting either. So the identity lives in one module-level store: fetched once
 * per tab, shared by every component, and re-fetched only when the token changes
 * (AUTH_EVENT from api.ts in this tab, the `storage` event from another tab).
 *
 * The store reports what the API said. It never upgrades an identity on its own,
 * and every access decision is still made server-side.
 */

import { useEffect, useSyncExternalStore } from "react";
import {
  AUTH_EVENT,
  TOKEN_KEY,
  demoPersonas,
  getToken,
  setToken,
  whoami,
  type DemoInfo,
  type Identity,
  type IdentityCore,
  type ScopeLevel,
} from "./api";

type Status = "idle" | "loading" | "ready" | "error";

interface IdentityState {
  me: Identity | null;
  status: Status;
  /** Increments on every token change seen by this tab. Lets a consumer re-sync
   *  something that is not driven by the identity itself (the alert feed). */
  epoch: number;
}

const SERVER_STATE: IdentityState = { me: null, status: "idle", epoch: 0 };

let state: IdentityState = SERVER_STATE;
let generation = 0;
const subscribers = new Set<() => void>();

function set(patch: Partial<IdentityState>) {
  state = { ...state, ...patch };
  subscribers.forEach((fn) => fn());
}

/** Re-read /auth/me. Concurrent calls collapse onto the newest one. */
export function refreshIdentity(): Promise<Identity | null> {
  const mine = ++generation;
  // Keep showing the previous identity while the new one loads, so the nav does
  // not flash a signed-out state in the middle of a role switch.
  if (!state.me) set({ status: "loading" });
  return whoami()
    .then((me) => {
      if (mine !== generation) return me;
      // A token the server no longer honours — expired, or signed with a secret
      // that was rotated by a restart (AUTH_SECRET unset) — would otherwise sit in
      // storage and be sent with every request, while the nav claimed a sign-in
      // that does not exist. Drop it, quietly: this IS the reaction to a change.
      if (!me?.identity?.authenticated && getToken()) setToken(null, false);
      set({ me, status: "ready" });
      return me;
    })
    .catch(() => {
      if (mine === generation) set({ status: "error" });
      return null;
    });
}

let listening = false;

function listen() {
  if (listening || typeof window === "undefined") return;
  listening = true;
  // The demo catalogue does not depend on who is signed in, so only the identity
  // is re-read here.
  const onChange = () => {
    // Removing the token can only ever reduce access, so a sign-out is reflected
    // at once instead of after the round trip. Without this the landing page,
    // reached straight after "Sign out", saw the previous identity still marked
    // authenticated and bounced the officer back into the workspace. The reverse
    // — showing a sign-in before the server confirms it — is never assumed.
    if (!getToken() && state.me?.identity?.authenticated) {
      set({ me: { ...state.me, identity: { user: null, role: "analyst", authenticated: false } } });
    }
    set({ epoch: state.epoch + 1 });
    refreshIdentity();
  };
  window.addEventListener(AUTH_EVENT, onChange);
  window.addEventListener("storage", (e) => {
    if (e.key === TOKEN_KEY || e.key === null) onChange();
  });
}

function subscribe(fn: () => void) {
  subscribers.add(fn);
  return () => {
    subscribers.delete(fn);
  };
}

export interface IdentityView {
  /** Full /auth/me payload, or null before the first answer / when unreachable. */
  me: Identity | null;
  identity: IdentityCore | null;
  authenticated: boolean;
  /** True until the first answer arrives. */
  loading: boolean;
  /** The last attempt to reach /auth/me failed (backend down). */
  error: boolean;
  epoch: number;
  refresh: () => Promise<Identity | null>;
}

export function useIdentity(): IdentityView {
  const snap = useSyncExternalStore(subscribe, () => state, () => SERVER_STATE);
  useEffect(() => {
    listen();
    // First consumer in the tab fetches; a later one retries only if the last
    // attempt failed (the backend was still starting), never to re-check a
    // known answer.
    if (state.status === "idle" || state.status === "error") refreshIdentity();
  }, []);
  return {
    me: snap.me,
    identity: snap.me?.identity ?? null,
    authenticated: !!snap.me?.identity?.authenticated,
    loading: snap.status === "idle" || snap.status === "loading",
    error: snap.status === "error",
    epoch: snap.epoch,
    refresh: refreshIdentity,
  };
}

/* ------------------------------------------------------------------ token presence
 * Whether this browser holds a token at all, without asking the server. The
 * landing page uses it to decide between "restoring your session" and showing the
 * form straight away; a visitor with no token should not wait on a round trip to
 * be told they are signed out.
 */
function subscribeToken(fn: () => void) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(AUTH_EVENT, fn);
  window.addEventListener("storage", fn);
  return () => {
    window.removeEventListener(AUTH_EVENT, fn);
    window.removeEventListener("storage", fn);
  };
}

export function useHasToken(): boolean {
  return useSyncExternalStore(subscribeToken, () => !!getToken(), () => false);
}

/* ------------------------------------------------------------------ demo personas */

type DemoState = { info: DemoInfo | null; status: Status };
const DEMO_SERVER: DemoState = { info: null, status: "idle" };
let demoState: DemoState = DEMO_SERVER;
let demoCache: Promise<DemoInfo | null> | null = null;
const demoSubscribers = new Set<() => void>();

function setDemo(next: DemoState) {
  demoState = next;
  demoSubscribers.forEach((fn) => fn());
}

function loadDemo() {
  if (demoCache) return demoCache;
  if (demoState.status === "idle") setDemo({ info: null, status: "loading" });
  demoCache = demoPersonas()
    .then((info) => {
      setDemo({ info, status: "ready" });
      return info;
    })
    .catch(() => {
      // Unreachable is not "not demo mode", but either way there is nothing to
      // offer; allow a later mount to try again.
      demoCache = null;
      setDemo({ info: null, status: "error" });
      return null;
    });
  return demoCache;
}

/**
 * The demo persona catalogue (GET /auth/demo), cached per tab. `info` is null
 * outside demo mode (the endpoint 404s) and while loading; callers hide the
 * persona UI in both cases rather than rendering an empty list.
 */
function subscribeDemo(fn: () => void) {
  demoSubscribers.add(fn);
  return () => {
    demoSubscribers.delete(fn);
  };
}

export function useDemoInfo(): { info: DemoInfo | null; loading: boolean } {
  const snap = useSyncExternalStore(subscribeDemo, () => demoState, () => DEMO_SERVER);
  useEffect(() => {
    listen();
    loadDemo();
  }, []);
  return { info: snap.info, loading: snap.status === "idle" || snap.status === "loading" };
}

/* ------------------------------------------------------------------ presentation */

const LEGACY_LABELS: Record<string, string> = {
  analyst: "Analyst",
  investigator: "Investigator",
  supervisor: "Supervisor",
};

/** Human label for the role; the API's own label when it sends one. */
export function roleLabel(identity: IdentityCore | null | undefined): string {
  if (!identity) return "";
  if (identity.role_label) return identity.role_label;
  const r = identity.role || "analyst";
  return LEGACY_LABELS[r] ?? r.charAt(0).toUpperCase() + r.slice(1);
}

export function scopeLevel(identity: IdentityCore | null | undefined): ScopeLevel {
  return identity?.scope?.level ?? "state";
}

/**
 * Where this identity's list views are limited to, in words.
 *
 * A legacy token carries no scope; the legacy roles were state-wide, so that is
 * what it means — not an assumption about a new role.
 */
export function jurisdictionLabel(
  identity: IdentityCore | null | undefined,
  t: (key: string, fallback: string) => string,
): string {
  const scope = identity?.scope;
  if (!scope || scope.level === "state") return t("profile.allDistricts", "All 31 districts");
  if (scope.level === "station") {
    return [scope.station, scope.district].filter(Boolean).join(" · ") || t("profile.station", "Station");
  }
  return scope.district
    ? `${scope.district} ${t("profile.districtSuffix", "district")}`
    : t("common.district", "District");
}

/** "SP" from "sp.bengaluru@ksp.gov.in", "IO" from "io": a glyph for the avatar. */
export function initials(identity: IdentityCore | null | undefined): string {
  const u = (identity?.user || "").split("@")[0];
  const parts = u.split(/[._\-\s]+/).filter(Boolean);
  const first = parts[0] || identity?.role || "?";
  return first.slice(0, 2).toUpperCase();
}

/**
 * A demo persona is "current" when the signed-in identity has its role and, for a
 * persona with a fixed jurisdiction, the same district and station. A switchable
 * persona matches on role alone, since its district is whichever was chosen.
 */
export function isCurrentPersona(
  identity: IdentityCore | null | undefined,
  persona: { role: string; district: string | null; station: string | null; switchable_district: boolean },
): boolean {
  if (!identity?.authenticated || identity.role !== persona.role) return false;
  if (persona.switchable_district) return true;
  const scope = identity.scope;
  if (!scope) return !persona.district && !persona.station;
  return (scope.district ?? null) === (persona.district ?? null) && (scope.station ?? null) === (persona.station ?? null);
}

/**
 * A `?next=` value made safe to navigate to: a same-origin path inside the app,
 * never the landing page itself (that would bounce straight back), never another
 * origin. An open redirect on a police sign-in page is a phishing kit.
 */
export function safeNext(raw: string | null | undefined, fallback = "/overview"): string {
  if (!raw) return fallback;
  let p = raw.trim();
  if (!p.startsWith("/") || p.startsWith("//") || p.startsWith("/\\")) return fallback;
  // Control characters (tab, newline) are stripped by URL parsers and can turn
  // "/\t/evil.example" into a protocol-relative URL.
  if (/[\u0000-\u001f\u007f]/.test(p)) return fallback;
  if (typeof window !== "undefined") {
    try {
      if (new URL(p, window.location.origin).origin !== window.location.origin) return fallback;
    } catch {
      return fallback;
    }
  }
  // Router paths are basePath-relative; a next= copied from the address bar in
  // production carries the /app prefix, which the router would add a second time.
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
  if (base && (p === base || p.startsWith(base + "/") || p.startsWith(base + "?"))) {
    p = p.slice(base.length) || "/";
    if (!p.startsWith("/")) p = "/" + p;
  }
  p = p.replace(/\/index\.html(?=$|[?#])/, "/");
  const path = p.split(/[?#]/)[0];
  if (path === "/" || path === "") return fallback;
  return p;
}
