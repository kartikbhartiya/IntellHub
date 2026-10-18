"use client";

/**
 * The front door: sign in, with the platform's live state beside the form.
 *
 * The dashboard used to be the root, which meant the first thing anyone saw was a
 * state-wide command overview with no indication of who they were or what they
 * were allowed to see. Person-level surfaces then refused them one page at a time.
 * Asking first — and offering a clearly labelled aggregates-only way in — states
 * the access model before it is enforced.
 *
 * Everything numeric on this page is read from GET /stats when the page loads.
 * A figure the API did not return is hidden, not estimated, and the map draws only
 * what the GeoJSON contains. The arcs on the map are decoration and say so.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { animate, motion, useReducedMotion } from "framer-motion";
import {
  ArrowRight,
  ArrowUpRight,
  Building2,
  Eye,
  EyeOff,
  Info,
  Landmark,
  LoaderCircle,
  Lock,
  MapPin,
} from "lucide-react";
import { ApiError, LoginError, demoLogin, getJSON, login, type DemoInfo, type DemoPersona } from "@/lib/api";
import { LANGUAGES, useI18n } from "@/lib/i18n";
import { safeNext, useDemoInfo, useHasToken, useIdentity } from "@/lib/session";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
const EASE = [0.32, 0.72, 0, 1] as const;
const NUMBER = new Intl.NumberFormat("en-IN");

type T = (key: string, fallback: string) => string;

/* ------------------------------------------------------------------ small hooks */

const WIDE = "(min-width: 768px)";

function subscribeWide(cb: () => void) {
  const m = window.matchMedia(WIDE);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
}

/** Whether the map is worth drawing (and its GeoJSON worth fetching): md and up.
 *  On a phone the hero is compact and the map would sit under the fold anyway. */
function useWide() {
  return useSyncExternalStore(subscribeWide, () => window.matchMedia(WIDE).matches, () => false);
}

function subscribePopstate(cb: () => void) {
  window.addEventListener("popstate", cb);
  return () => window.removeEventListener("popstate", cb);
}

/** `location.search`, read without useSearchParams — which would force a Suspense
 *  boundary around the whole page in a static export for one query parameter. */
function useSearch() {
  return useSyncExternalStore(subscribePopstate, () => window.location.search, () => "");
}

/* ------------------------------------------------------------------ live figures */

interface LandingStats {
  total_cases?: number;
  tracked_suspects?: number;
  districts?: string[];
  graph_edges?: number;
  trend_alerts?: { district?: string; crime_type?: string; spike_ratio?: number }[];
}

const FIGURES: { key: string; en: string; pick: (s: LandingStats) => unknown }[] = [
  { key: "landing.stat.firs", en: "FIRs on record", pick: (s) => s.total_cases },
  { key: "landing.stat.offenders", en: "Offenders tracked", pick: (s) => s.tracked_suspects },
  { key: "landing.stat.districts", en: "Police districts", pick: (s) => s.districts?.length },
  { key: "landing.stat.links", en: "Graph relationships", pick: (s) => s.graph_edges },
];

function CountUp({ value }: { value: number }) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (reduce) return;
    const c = animate(0, value, { duration: 1.4, ease: EASE, onUpdate: (v) => setShown(v) });
    return () => c.stop();
  }, [value, reduce]);
  return (
    <>
      {/* The tween is for sighted users; a screen reader gets the final value once. */}
      <span aria-hidden="true">{NUMBER.format(Math.round(reduce ? value : shown))}</span>
      <span className="sr-only">{NUMBER.format(value)}</span>
    </>
  );
}

function LiveFigures({ stats, failed, t }: { stats: LandingStats | null; failed: boolean; t: T }) {
  if (failed) {
    return (
      <p className="text-[13px] text-muted">
        {t("landing.statsDown", "Live figures unavailable — the intelligence core did not answer.")}
      </p>
    );
  }
  const shown = FIGURES.map((f) => ({ ...f, value: stats ? f.pick(stats) : undefined })).filter(
    (f) => !stats || (typeof f.value === "number" && Number.isFinite(f.value)),
  );
  return (
    <dl className="grid max-w-[600px] grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4" aria-busy={!stats}>
      {shown.map((f, i) => (
        <motion.div
          key={f.key}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.25 + i * 0.07, ease: EASE }}
          className="flex flex-col-reverse gap-1 border-l border-brand/25 pl-3.5"
        >
          <dt className="text-[11.5px] font-medium leading-snug text-muted">{t(f.key, f.en)}</dt>
          <dd className="text-display tabular text-[26px] font-bold leading-none text-ink sm:text-[30px]">
            {stats ? <CountUp value={f.value as number} /> : <span className="shimmer block h-[26px] w-20 rounded-md sm:h-[30px]" />}
          </dd>
        </motion.div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ Karnataka map */

type Ring = number[][];
interface GeoFeature {
  properties: { district: string; label_lng: number; label_lat: number };
  geometry: { type: "Polygon"; coordinates: Ring[] } | { type: "MultiPolygon"; coordinates: Ring[][] };
}

const MAP_W = 420;
// Equirectangular, scaled by cos(15°N) — the state's middle latitude — so the
// outline is not stretched east-west. At this extent (11.6°–18.5°N) the error at
// the edges is a few percent, invisible at this size; a real projection library
// would be ~50 KB for a decorative backdrop.
const COS_LAT0 = Math.cos((15 * Math.PI) / 180);

// SCRB sits in Bengaluru; the arcs fan out to a spread of district headquarters.
// They are decorative — no data travels along them — and the legend says so.
const HUB = "Bengaluru Urban";
const SPOKES = ["Belagavi", "Vijayapura", "Kalaburagi", "Ballari", "Uttara Kannada", "Shivamogga", "Dakshina Kannada", "Mysuru"];

function rings(g: GeoFeature["geometry"]): Ring[] {
  return g.type === "Polygon" ? g.coordinates : g.coordinates.flat();
}

function projectMap(features: GeoFeature[]) {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const f of features) {
    for (const ring of rings(f.geometry)) {
      for (const [lng, lat] of ring) {
        const x = lng * COS_LAT0, y = -lat;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  const s = MAP_W / (maxX - minX);
  const pt = (lng: number, lat: number): [number, number] => [
    Math.round((lng * COS_LAT0 - minX) * s * 10) / 10,
    Math.round((-lat - minY) * s * 10) / 10,
  ];
  const districts = features.map((f) => ({
    name: f.properties.district,
    d: rings(f.geometry)
      .map((ring) => "M" + ring.map(([lng, lat]) => pt(lng, lat).join(",")).join("L") + "Z")
      .join(""),
    hq: pt(f.properties.label_lng, f.properties.label_lat),
  }));
  return { width: MAP_W, height: Math.ceil((maxY - minY) * s), districts };
}

function arcPath([x1, y1]: [number, number], [x2, y2]: [number, number]) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  // A gentle, consistent bow: the fan stays inside the state outline.
  const bow = len * 0.16;
  const cx = (x1 + x2) / 2 - (dy / len) * bow;
  const cy = (y1 + y2) / 2 + (dx / len) * bow;
  return `M${x1},${y1} Q${cx.toFixed(1)},${cy.toFixed(1)} ${x2},${y2}`;
}

function KarnatakaMap({ alertDistricts, onReady, t }: { alertDistricts: Set<string>; onReady: () => void; t: T }) {
  const reduce = useReducedMotion();
  const [features, setFeatures] = useState<GeoFeature[] | null>(null);
  const titleId = useId();

  useEffect(() => {
    let alive = true;
    fetch(`${BASE}/geo/karnataka-districts.geojson`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        if (alive && Array.isArray(d?.features) && d.features.length) setFeatures(d.features);
      })
      // No outline is better than an invented one: on failure the hero simply has no map.
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const map = useMemo(() => (features ? projectMap(features) : null), [features]);
  useEffect(() => {
    if (map) onReady();
  }, [map, onReady]);
  if (!map) return null;

  const byName = new Map(map.districts.map((d) => [d.name, d]));
  const hub = byName.get(HUB);
  const arcs = hub
    ? SPOKES.map((n) => byName.get(n)).filter((d): d is NonNullable<typeof d> => !!d).map((d) => arcPath(hub.hq, d.hq))
    : [];

  return (
    <motion.svg
      viewBox={`-12 -12 ${map.width + 24} ${map.height + 24}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-labelledby={titleId}
      className="h-full w-full overflow-visible"
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 1.1, ease: EASE }}
    >
      <title id={titleId}>
        {t("landing.mapTitle", "Karnataka's police districts")}
        {alertDistricts.size ? ` — ${[...alertDistricts].join(", ")}` : ""}
      </title>
      <defs>
        <linearGradient id="ksp-arc" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#e7b43c" stopOpacity="0.95" />
          <stop offset="100%" stopColor="#b07d16" stopOpacity="0.55" />
        </linearGradient>
      </defs>

      <g>
        {map.districts.map((d) => {
          const hot = alertDistricts.has(d.name);
          return (
            <path
              key={d.name}
              d={d.d}
              fill={hot ? "rgba(220,43,37,0.10)" : d.name === HUB ? "rgba(231,180,60,0.16)" : "rgba(255,255,255,0.78)"}
              stroke={hot ? "rgba(220,43,37,0.5)" : "rgba(125,87,15,0.26)"}
              strokeWidth={hot ? 0.9 : 0.6}
              strokeLinejoin="round"
            >
              <title>{d.name}</title>
            </path>
          );
        })}
      </g>

      {/* Arc skeletons stay visible; the travelling stroke is the only motion. */}
      <g fill="none" strokeLinecap="round">
        {arcs.map((p, i) => (
          <path key={`s${i}`} d={p} stroke="rgba(176,125,22,0.22)" strokeWidth={0.9} strokeDasharray="2 3" />
        ))}
        {!reduce &&
          arcs.map((p, i) => (
            <motion.path
              key={`a${i}`}
              d={p}
              stroke="url(#ksp-arc)"
              strokeWidth={1.6}
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: [0, 1, 1], opacity: [0, 1, 0] }}
              transition={{ duration: 3.6, times: [0, 0.6, 1], repeat: Infinity, repeatDelay: 2.2, delay: 0.6 + i * 0.55, ease: "easeOut" }}
            />
          ))}
      </g>

      <g>
        {map.districts.map((d) =>
          alertDistricts.has(d.name) ? null : (
            <circle key={d.name} cx={d.hq[0]} cy={d.hq[1]} r={1.5} fill="rgba(125,87,15,0.45)" />
          ),
        )}
        {map.districts
          .filter((d) => alertDistricts.has(d.name))
          .map((d, i) => (
            <g key={d.name}>
              {!reduce && (
                <motion.circle
                  cx={d.hq[0]}
                  cy={d.hq[1]}
                  fill="rgba(220,43,37,0.35)"
                  initial={{ r: 3, opacity: 0.6 }}
                  animate={{ r: [3, 13], opacity: [0.6, 0] }}
                  transition={{ duration: 2.2, repeat: Infinity, delay: i * 0.4, ease: "easeOut" }}
                />
              )}
              <circle cx={d.hq[0]} cy={d.hq[1]} r={3} fill="#dc2b25" stroke="#fff" strokeWidth={1} />
            </g>
          ))}
        {hub && (
          <g>
            {!reduce && (
              <motion.circle
                cx={hub.hq[0]}
                cy={hub.hq[1]}
                fill="rgba(231,180,60,0.4)"
                initial={{ r: 4, opacity: 0.7 }}
                animate={{ r: [4, 18], opacity: [0.7, 0] }}
                transition={{ duration: 2.6, repeat: Infinity, ease: "easeOut" }}
              />
            )}
            <circle cx={hub.hq[0]} cy={hub.hq[1]} r={4.5} fill="#e7b43c" stroke="#7d570f" strokeWidth={1.2} />
          </g>
        )}
      </g>
    </motion.svg>
  );
}

/* ------------------------------------------------------------------ sign-in card */

type FormError =
  | { kind: "invalid" }
  | { kind: "locked"; seconds: number | null }
  | { kind: "network" }
  | { kind: "server"; status: number }
  | { kind: "demo"; status: number };

function errorText(e: FormError, t: T) {
  switch (e.kind) {
    case "invalid":
      return t("landing.err.invalid", "Those credentials were not recognised. Check the address and password and try again.");
    case "locked":
      return t("landing.err.locked", "Too many failed attempts — sign-in for this account is paused.");
    case "network":
      return t("landing.err.network", "Could not reach the intelligence core. Check that the backend is running, then retry.");
    case "demo":
      return e.status === 404
        ? t("landing.err.demoOff", "Demo access is switched off on this deployment.")
        : `${t("landing.err.demo", "That demo persona could not be opened")} (HTTP ${e.status}).`;
    default:
      return `${t("landing.err.server", "Sign-in failed on the server")} (HTTP ${e.status}).`;
  }
}

const TILES: { id: string; key: string; en: string; icon: typeof Landmark }[] = [
  { id: "dgp", key: "landing.tile.state", en: "State", icon: Landmark },
  { id: "sp", key: "landing.tile.district", en: "District", icon: Building2 },
  { id: "sho", key: "landing.tile.station", en: "Station", icon: MapPin },
];

function personaScope(p: DemoPersona, district: string | null, t: T) {
  if (p.scope_level === "state") return t("profile.allDistricts", "All 31 districts");
  if (p.scope_level === "station") return [p.station, p.district].filter(Boolean).join(", ");
  return district || p.district || "";
}

function DemoTiles({
  info,
  busy,
  onEnter,
  t,
}: {
  info: DemoInfo;
  busy: string | null;
  onEnter: (p: DemoPersona, district?: string) => void;
  t: T;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const baseId = useId();
  const tiles = TILES.map((tile) => ({ ...tile, persona: info.personas.find((p) => p.id === tile.id) })).filter(
    (x): x is typeof x & { persona: DemoPersona } => !!x.persona,
  );
  if (!tiles.length) return null;

  return (
    <ul className="space-y-2">
      {tiles.map(({ id, key, en, icon: Icon, persona }) => {
        const switchable = persona.switchable_district && info.districts.length > 0;
        const district = switchable ? picked ?? persona.district ?? info.districts[0] : persona.district;
        const working = busy === id;
        const selectId = `${baseId}-${id}`;
        return (
          <li
            key={id}
            className="group flex items-center gap-2 rounded-2xl border border-white/[0.08] bg-white/[0.035] p-1.5 pr-2 transition-colors duration-300 hover:border-brand-soft/40 hover:bg-white/[0.06]"
          >
            <button
              type="button"
              disabled={!!busy}
              onClick={() => onEnter(persona, switchable ? district ?? undefined : undefined)}
              className="flex min-h-[48px] min-w-0 flex-1 items-center gap-3 rounded-xl px-2 py-1.5 text-left disabled:cursor-wait"
            >
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft/12 text-brand-soft ring-1 ring-inset ring-brand-soft/20">
                {working ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Icon className="h-4 w-4" strokeWidth={1.8} aria-hidden="true" />}
              </span>
              <span className="min-w-0">
                <span className="block text-[13.5px] font-semibold text-white">{t(key, en)}</span>
                <span className="block truncate text-[11.5px] text-white/55">
                  {persona.role_label}
                  {!switchable && personaScope(persona, district ?? null, t) ? ` · ${personaScope(persona, district ?? null, t)}` : ""}
                </span>
              </span>
              {!switchable && (
                <ArrowRight className="ml-auto h-4 w-4 shrink-0 text-white/35 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:text-brand-soft" aria-hidden="true" />
              )}
            </button>
            {switchable && (
              <>
                <label htmlFor={selectId} className="sr-only">
                  {t("landing.pickDistrict", "District for the")} {persona.role_label}
                </label>
                <select
                  id={selectId}
                  value={district ?? ""}
                  disabled={!!busy}
                  onChange={(e) => setPicked(e.target.value)}
                  className="h-9 w-[112px] shrink-0 cursor-pointer sm:w-[132px] rounded-lg border border-white/10 bg-[#0f1b33] px-2 text-[12px] text-white/85 outline-none transition-colors hover:border-white/20 focus:border-brand-soft/60"
                >
                  {info.districts.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={() => onEnter(persona, district ?? undefined)}
                  aria-label={`${t("landing.enterAs", "Enter as")} ${persona.role_label}, ${district}`}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white/[0.06] text-white/70 transition-colors hover:bg-brand-soft hover:text-black disabled:cursor-wait"
                >
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </button>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function SignInCard({ nextPath, onSignedIn, t }: { nextPath: string; onSignedIn: () => void; t: T }) {
  const { authenticated, loading, error: identityDown } = useIdentity();
  const hasToken = useHasToken();
  const { info: demo } = useDemoInfo();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<FormError | null>(null);
  const [lockedFor, setLockedFor] = useState(0);
  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  // Count the lock down in place, so the officer can see when to try again
  // instead of spending attempts against a lock they cannot see.
  useEffect(() => {
    if (lockedFor <= 0) return;
    const id = setTimeout(() => {
      if (lockedFor <= 1) setError(null);
      setLockedFor(lockedFor - 1);
    }, 1000);
    return () => clearTimeout(id);
  }, [lockedFor]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy || lockedFor > 0) return;
    setBusy("password");
    setError(null);
    try {
      await login(email.trim(), password);
      setPassword("");
      onSignedIn();
    } catch (err) {
      if (err instanceof LoginError && err.isLockedOut) {
        setError({ kind: "locked", seconds: err.retryAfter });
        setLockedFor(err.retryAfter ?? 0);
      } else if (err instanceof ApiError && err.status === 401) {
        setError({ kind: "invalid" });
      } else if (err instanceof ApiError) {
        setError({ kind: "server", status: err.status });
      } else {
        setError({ kind: "network" });
      }
    } finally {
      setBusy(null);
    }
  };

  const enterAs = async (p: DemoPersona, district?: string) => {
    if (busy) return;
    setBusy(p.id);
    setError(null);
    try {
      await demoLogin(p.id, district);
      onSignedIn();
    } catch (err) {
      setError(err instanceof ApiError ? { kind: "demo", status: err.status } : { kind: "network" });
    } finally {
      setBusy(null);
    }
  };

  // A held token is checked before the form is offered, so a returning officer is
  // not asked for credentials they do not need to type.
  const restoring = hasToken && (loading || authenticated);

  return (
    <section
      aria-labelledby="signin-title"
      className="relative overflow-hidden rounded-[1.65rem] bg-[#0b1426] text-white shadow-[0_40px_90px_-30px_rgba(11,20,38,0.65)] ring-1 ring-white/[0.06]"
    >
      {/* Soft light from the top edge, and the emblem as a watermark at the foot. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-48 bg-[radial-gradient(70%_100%_at_50%_0%,rgba(231,180,60,0.16),transparent_70%)]" />
      <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-brand-soft/50 to-transparent" />
      {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image adds nothing for a fixed decorative asset */}
      <img
        src={`${BASE}/brand/karnataka-emblem@2x.png`}
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute -bottom-10 left-1/2 w-[360px] max-w-none -translate-x-1/2 select-none opacity-[0.05] grayscale"
      />

      <div className="relative px-6 pb-7 pt-7 sm:px-9 sm:pb-9 sm:pt-9 lg:px-7 xl:px-9">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-soft/25 bg-brand-soft/10 px-3 py-1 text-[10.5px] font-semibold uppercase tracking-[0.2em] text-brand-soft">
          <Lock className="h-3 w-3" aria-hidden="true" /> {t("landing.secure", "Secure access")}
        </span>
        <h2 id="signin-title" className="text-display mt-4 text-[30px] font-bold leading-tight tracking-tight">
          {t("common.signIn", "Sign in")}
        </h2>
        <p className="mt-1.5 text-[13.5px] text-white/60">
          {t("landing.useOfficial", "Use your official @ksp.gov.in address")}
        </p>
        {nextPath && (
          <p className="mt-2 text-[12px] text-white/45">
            {t("landing.returnTo", "You will return to")} <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-white/70">{nextPath}</code>
          </p>
        )}

        {restoring ? (
          <div role="status" className="mt-8 flex min-h-[220px] flex-col items-center justify-center gap-3 rounded-2xl border border-white/[0.07] bg-white/[0.03] text-center">
            <LoaderCircle className="h-6 w-6 animate-spin text-brand-soft" aria-hidden="true" />
            <p className="text-[13.5px] text-white/75">
              {authenticated
                ? t("landing.opening", "Signed in — opening your workspace…")
                : t("landing.checking", "Restoring your session…")}
            </p>
          </div>
        ) : (
          <form onSubmit={submit} noValidate className="mt-7 space-y-4" aria-describedby={error ? errorId : undefined}>
            <div>
              <label htmlFor={emailId} className="mb-1.5 block text-[12px] font-medium text-white/65">
                {t("landing.email", "Official email")}
              </label>
              {/* type="text", not "email": the legacy demo accounts (analyst, io, sp)
                  are bare usernames, and the browser would refuse them. */}
              <input
                id={emailId}
                type="text"
                inputMode="email"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@ksp.gov.in"
                aria-invalid={error?.kind === "invalid" || undefined}
                className="h-12 w-full rounded-xl border border-white/10 bg-white/[0.045] px-4 text-[14px] text-white outline-none transition-colors duration-300 placeholder:text-white/30 hover:border-white/20 focus:border-brand-soft/60 focus:bg-white/[0.07]"
              />
            </div>
            <div>
              <label htmlFor={passwordId} className="mb-1.5 block text-[12px] font-medium text-white/65">
                {t("landing.password", "Password")}
              </label>
              <div className="relative">
                <input
                  id={passwordId}
                  type={reveal ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={error?.kind === "invalid" || undefined}
                  className="h-12 w-full rounded-xl border border-white/10 bg-white/[0.045] pl-4 pr-12 text-[14px] text-white outline-none transition-colors duration-300 hover:border-white/20 focus:border-brand-soft/60 focus:bg-white/[0.07]"
                />
                <button
                  type="button"
                  onClick={() => setReveal((r) => !r)}
                  aria-pressed={reveal}
                  aria-controls={passwordId}
                  aria-label={reveal ? t("landing.hide", "Hide password") : t("landing.show", "Show password")}
                  className="absolute right-1.5 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-white/50 transition-colors hover:bg-white/[0.06] hover:text-white"
                >
                  {reveal ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
                </button>
              </div>
            </div>

            {error && (
              <div
                id={errorId}
                role="alert"
                className="rounded-xl border border-crimson/35 bg-crimson/[0.12] px-3.5 py-2.5 text-[12.5px] leading-relaxed text-[#ffc1bd]"
              >
                {errorText(error, t)}
                {/* An alert re-announces whenever its content changes, so a ticking
                    number inside it would be read out every second. The wait is
                    announced once; the visible countdown is not announced at all. */}
                {error.kind === "locked" && error.seconds ? (
                  <span className="sr-only">
                    {" "}
                    {t("landing.retryIn", "Try again in")} {error.seconds}s
                  </span>
                ) : null}
                {error.kind === "locked" && lockedFor > 0 && (
                  <span aria-hidden="true" className="mt-0.5 block font-semibold tabular text-white">
                    {t("landing.retryIn", "Try again in")} {lockedFor}s
                  </span>
                )}
              </div>
            )}

            <button
              type="submit"
              disabled={!!busy || lockedFor > 0 || !email.trim() || !password}
              className="group flex h-12 w-full items-center justify-between rounded-full bg-gradient-to-b from-brand-soft to-brand pl-6 pr-1.5 text-[14px] font-semibold text-black shadow-[0_12px_30px_-10px_rgba(231,180,60,0.6)] transition-[filter,transform,opacity] duration-300 hover:brightness-105 active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-45"
            >
              {busy === "password" ? t("landing.signingIn", "Signing in…") : t("common.signIn", "Sign in")}
              <span className="grid h-9 w-9 place-items-center rounded-full bg-black/10 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-px">
                {busy === "password" ? <LoaderCircle className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ArrowUpRight className="h-4 w-4" aria-hidden="true" />}
              </span>
            </button>
          </form>
        )}

        {identityDown && hasToken && (
          <p className="mt-3 text-[12px] text-white/50">
            {t("landing.restoreFailed", "Your saved session could not be checked because the intelligence core did not answer.")}
          </p>
        )}

        {!restoring && demo && demo.personas.length > 0 && (
          <div className="mt-7">
            <div className="mb-3 flex items-center gap-3">
              <span className="h-px flex-1 bg-white/10" />
              <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-white/45">
                {t("landing.explore", "Or explore without an account")}
              </span>
              <span className="h-px flex-1 bg-white/10" />
            </div>
            <DemoTiles info={demo} busy={busy} onEnter={enterAs} t={t} />
            <p className="mt-2.5 text-[11px] leading-relaxed text-white/40">
              {t("landing.demoNote", "Demo mode only: each opens a ready-made persona with that jurisdiction. Every entry is audited.")}
            </p>
          </div>
        )}

        {!restoring && (
          <Link
            prefetch={false}
            href="/overview"
            className="group mt-6 inline-flex items-center gap-1.5 text-[13px] font-medium text-white/70 transition-colors hover:text-brand-soft"
          >
            {t("landing.analyst", "Continue as analyst (aggregates only)")}
            <ArrowRight className="h-3.5 w-3.5 transition-transform duration-300 group-hover:translate-x-0.5" aria-hidden="true" />
          </Link>
        )}

        <p className="mt-8 border-t border-white/[0.07] pt-4 text-[11px] leading-relaxed text-white/35">
          {t("landing.audited", "Every sign-in attempt, successful or not, is written to the audit log.")}
        </p>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ page */

export default function Landing() {
  const { t, lang, setLang } = useI18n();
  const router = useRouter();
  const { authenticated } = useIdentity();
  const hasToken = useHasToken();
  const wide = useWide();
  const search = useSearch();
  const nextPath = useMemo(() => safeNext(new URLSearchParams(search).get("next"), ""), [search]);

  const [stats, setStats] = useState<LandingStats | null>(null);
  const [statsFailed, setStatsFailed] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const onMapReady = useCallback(() => setMapReady(true), []);

  useEffect(() => {
    getJSON<LandingStats>("/stats")
      .then(setStats)
      .catch(() => setStatsFailed(true));
  }, []);

  // `replace`, so Back from the workspace does not land on a sign-in form for a
  // session that is still open. One navigation per visit, whichever path fires.
  const leaving = useRef(false);
  const go = useCallback(() => {
    if (leaving.current) return;
    leaving.current = true;
    router.replace(nextPath || "/overview");
  }, [router, nextPath]);

  useEffect(() => {
    if (authenticated && hasToken) go();
  }, [authenticated, hasToken, go]);

  const alertDistricts = useMemo(
    () => new Set((stats?.trend_alerts ?? []).map((a) => a.district).filter((d): d is string => !!d)),
    [stats],
  );

  return (
    // No background of its own: the layout's fixed field and grid show through, so
    // the front door reads as the same building as the rooms behind it.
    <div className="relative min-h-[100dvh] overflow-x-hidden">
      {/* Airy light field behind the hero: warm at the crest, cool at the far edge. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 bg-[radial-gradient(55%_45%_at_12%_8%,rgba(231,180,60,0.16),transparent_70%),radial-gradient(40%_40%_at_70%_85%,rgba(47,116,192,0.08),transparent_70%)]" />

      <div className="relative mx-auto grid min-h-[100dvh] w-full max-w-[1500px] grid-cols-1 lg:grid-cols-[minmax(0,1.2fr)_minmax(400px,0.8fr)]">
        {/* --------------------------------------------------------------- hero */}
        <div className="relative flex flex-col px-4 pb-8 pt-6 sm:px-8 md:min-h-[600px] md:pb-10 md:pt-8 lg:min-h-[100dvh] lg:px-12 lg:pb-12 xl:px-16">
          {wide && (
            <div aria-hidden={!mapReady} className="pointer-events-none absolute bottom-[10%] right-0 top-[12%] w-[52%] max-w-[520px] lg:right-[-4%] lg:w-[60%] [&_path]:pointer-events-auto">
              <KarnatakaMap alertDistricts={alertDistricts} onReady={onMapReady} t={t} />
            </div>
          )}

          <header className="relative z-10 flex items-start justify-between gap-4">
            <div className="flex items-center gap-3.5">
              {/* eslint-disable-next-line @next/next/no-img-element -- static export; fixed-size brand asset */}
              <img
                src={`${BASE}/brand/karnataka-emblem.png`}
                srcSet={`${BASE}/brand/karnataka-emblem.png 1x, ${BASE}/brand/karnataka-emblem@2x.png 2x`}
                alt={t("landing.emblemAlt", "Emblem of the Government of Karnataka")}
                width={62}
                height={56}
                className="h-12 w-auto sm:h-14"
              />
              <div className="leading-tight">
                <p className="text-[10.5px] font-bold uppercase tracking-[0.16em] text-ink sm:text-[12px] sm:tracking-[0.26em]">
                  {t("landing.ksp", "Karnataka State Police")}
                </p>
                <p className="mt-1 text-[11px] font-medium tracking-wide text-muted sm:text-[11.5px]">
                  {t("landing.scrb", "State Crime Records Bureau")}
                </p>
              </div>
            </div>
            <div role="group" aria-label="Language" className="flex shrink-0 items-center gap-0.5 rounded-full border border-black/[0.07] bg-white/70 p-0.5 backdrop-blur">
              {LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  type="button"
                  onClick={() => setLang(l.code)}
                  aria-pressed={lang === l.code}
                  aria-label={`Switch interface language to ${l.native}`}
                  title={l.native}
                  className={`h-9 min-w-9 rounded-full px-2.5 text-[12px] font-semibold transition-colors duration-300 ${
                    lang === l.code ? "bg-brand text-black" : "text-muted hover:text-ink"
                  }`}
                >
                  {l.label}
                </button>
              ))}
            </div>
          </header>

          <div className="relative z-10 mt-10 max-w-[600px] md:my-auto md:pt-14 lg:pt-10">
            <motion.span
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: EASE }}
              className="inline-flex items-center gap-2 rounded-full border border-brand/20 bg-white/70 px-3 py-1 text-[10.5px] font-semibold uppercase tracking-[0.2em] text-brand-deep backdrop-blur"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-brand" aria-hidden="true" />
              {t("landing.eyebrow", "Crime intelligence platform")}
            </motion.span>
            <motion.h1
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.9, delay: 0.05, ease: EASE }}
              className="text-display mt-5 text-[44px] font-bold leading-[0.95] tracking-[-0.035em] text-ink sm:text-[60px] lg:text-[72px] xl:text-[80px]"
            >
              KSP{" "}
              <span className="bg-gradient-to-br from-brand-soft via-brand to-brand-deep bg-clip-text text-transparent">IntelHub</span>
            </motion.h1>
            <motion.p
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.9, delay: 0.12, ease: EASE }}
              className="mt-5 max-w-[30rem] text-[15.5px] leading-relaxed text-muted sm:text-[17px]"
            >
              {t(
                "landing.tagline",
                "Connected crime intelligence for the State Crime Records Bureau — cases, offenders, networks and live CCTV in one audited workspace.",
              )}
            </motion.p>

            <div className="mt-9 sm:mt-11">
              <LiveFigures stats={stats} failed={statsFailed} t={t} />
            </div>
          </div>

          <div className="relative z-10 mt-8 flex max-w-[600px] flex-col gap-2.5 md:mt-0">
            {wide && mapReady && (
              <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
                <li className="flex items-center gap-1.5">
                  <span className="h-px w-4 border-t border-dashed border-brand" aria-hidden="true" />
                  {t("landing.legendArcs", "SCRB to district HQs — decorative")}
                </li>
                {alertDistricts.size > 0 && (
                  <li className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-crimson" aria-hidden="true" />
                    {t("landing.legendAlerts", "Emerging trend alert, last 30 days")} ({alertDistricts.size})
                  </li>
                )}
              </ul>
            )}
            <p className="flex items-start gap-2 text-[11.5px] leading-relaxed text-faint">
              <Info className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {t(
                "landing.simulated",
                "Figures are computed live from a simulated, CCTNS-shaped corpus over Karnataka's real districts. No production KSP records are used.",
              )}
            </p>
          </div>
        </div>

        {/* ---------------------------------------------------------- sign-in */}
        <div className="relative flex items-start justify-center px-4 pb-10 sm:px-8 lg:items-center lg:py-10 lg:pl-0 lg:pr-10 xl:pr-14">
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.9, delay: 0.1, ease: EASE }}
            className="w-full max-w-[460px] rounded-[2rem] bg-black/[0.035] p-1.5 ring-1 ring-black/[0.05]"
          >
            <SignInCard nextPath={nextPath} onSignedIn={go} t={t} />
          </motion.div>
        </div>
      </div>
    </div>
  );
}
