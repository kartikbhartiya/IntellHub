"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import {
  Cctv, ScanFace, Car, Radar, ShieldCheck, ShieldX, AlertTriangle, Maximize2, X, Search, Eye, MapPinned,
  ListPlus, Trash2, Activity, Lock, Settings2, CheckCircle2, ArrowUpRight, Footprints, Gauge, EyeOff,
} from "lucide-react";
import { apiFetch, getJSON, ApiError } from "@/lib/api";
import { useAlerts, patchAlert, refreshAlerts, ago, istTime, useNow, type SurvAlert } from "@/lib/alerts";
import CameraFeed, { Face, type Detection, type FeedCamera } from "@/components/CameraFeed";
import { CLIPS, assignClips, type Clip } from "@/lib/footage";

type Cam = FeedCamera & { matches_last_hour?: number; owner?: string; uptime_pct?: number; landmark?: boolean; quality?: number };

const TABS = [
  { id: "all", label: "All feeds" },
  { id: "online", label: "Online only" },
  { id: "FACE", label: "Face AI" },
  { id: "ANPR", label: "ANPR readers" },
  { id: "PTZ", label: "PTZ" },
  { id: "alerts", label: "With alerts" },
];

const STREAMS_KEY = "ksp.cctv.streams";
function readStreams(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(STREAMS_KEY) || "{}"); } catch { return {}; }
}
function writeStreams(v: Record<string, string>) {
  try { localStorage.setItem(STREAMS_KEY, JSON.stringify(v)); } catch {}
}

const typeBadge = (t: string) =>
  ({ FACE: "bg-[#965fff]/12 text-[#6d3fe0]", ANPR: "bg-[#2f8cff]/12 text-[#1d64c2]", PTZ: "bg-[#e7b43c]/18 text-[#8a5f0a]", CCTV: "bg-[#28b9a8]/14 text-[#167a6e]" } as any)[t];
const typeName = (t: string) => ({ FACE: "FACE AI", ANPR: "ANPR", PTZ: "PTZ · FACE", CCTV: "CCTV" } as any)[t] || t;

function detectionFor(e: SurvAlert | any, onClick: () => void): Detection {
  if (e.kind === "face" && e.face_match) {
    const p = e.face_match.person || {};
    return { id: e.event_id, label: `${(p.name || e.face_match.suspect_id).toUpperCase()} (${p.active_warrant ? "WANTED" : "SUSPECT"})`,
             sub: `${e.face_match.suspect_id} · ${e.face_match.band}`, score: e.face_match.similarity,
             tone: e.priority === "P1" ? "red" : e.priority === "P2" ? "amber" : "green", onClick };
  }
  const m = e.plate_match || {};
  return { id: e.event_id, label: `${e.plate_read} (${m.recovery_status === "Recovered" ? "RECOVERED" : "STOLEN"} ${String(m.make_model || "").toUpperCase()})`,
           sub: m.kind === "fuzzy" ? `read ≈ ${m.vehicle_number}` : undefined, score: e.read_confidence || 0.9,
           tone: e.priority === "P1" ? "red" : "amber", onClick };
}

// ===================================================================== page
function SurveillanceInner() {
  const params = useSearchParams();
  const router = useRouter();
  const now = useNow(5000);
  const { alerts, locked } = useAlerts();

  const [cams, setCams] = useState<Cam[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [matches, setMatches] = useState<any[]>([]);
  const [counts, setCounts] = useState<any>(null);
  const [model, setModel] = useState<any>(null);
  const [tab, setTab] = useState("all");
  const [district, setDistrict] = useState(params.get("district") || "Bengaluru Urban");
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(12);
  const [review, setReview] = useState<any>(null);
  const [camOpen, setCamOpen] = useState<Cam | null>(null);
  const [railP, setRailP] = useState<"all" | "P1" | "P2">("all");
  const [railS, setRailS] = useState<"new" | "all">("new");
  const [tracking, setTracking] = useState(true);
  const [streams, setStreams] = useState<Record<string, string>>({});
  const [feedLocked, setFeedLocked] = useState(false);

  useEffect(() => setStreams(readStreams()), []);
  useEffect(() => {
    getJSON("/surveillance/cameras").then((d) => { setCams(d.cameras || []); setSummary(d.summary); }).catch(() => {});
    getJSON("/surveillance/model").then(setModel).catch(() => {});
  }, []);

  // Live matches for the detection boxes. Incremental after the first fetch.
  useEffect(() => {
    if (!tracking) return;
    let since = 0;
    let stop = false;
    const tick = () =>
      getJSON(`/surveillance/feed?matches_only=1&limit=300${since ? `&since=${since}` : ""}`)
        .then((d) => {
          if (stop) return;
          setFeedLocked(false);
          if (!since) setCounts(d.counts);
          setMatches((m) => {
            const known = new Set(m.map((x) => x.event_id));
            return [...d.events.filter((x: any) => !known.has(x.event_id)), ...m].slice(0, 600);
          });
          since = d.now;
        })
        .catch((e) => { if (e instanceof ApiError && e.isForbidden) setFeedLocked(true); });
    tick();
    const t = setInterval(tick, 8000);
    const c = setInterval(() => getJSON("/surveillance/feed?limit=1").then((d) => !stop && setCounts(d.counts)).catch(() => {}), 30000);
    return () => { stop = true; clearInterval(t); clearInterval(c); };
  }, [tracking]);

  // Deep links: ?alert=ID opens review, ?camera=ID opens the camera.
  useEffect(() => {
    const aid = params.get("alert");
    if (aid) {
      const a = alerts.find((x) => x.event_id === aid) || matches.find((x) => x.event_id === aid);
      if (a) setReview(a);
    }
  }, [params, alerts, matches]);
  useEffect(() => {
    const cid = params.get("camera");
    if (cid && cams.length) {
      const c = cams.find((x) => x.camera_id === cid);
      if (c) { setCamOpen(c); setDistrict(c.district); }
    }
  }, [params, cams]);

  const latestByCam = useMemo(() => {
    const m: Record<string, any[]> = {};
    const cutoff = now / 1000 - 30 * 60;
    for (const e of [...alerts, ...matches]) {
      if (e.ts < cutoff || e.status === "rejected") continue;
      (m[e.camera_id] ||= []).some((x) => x.event_id === e.event_id) || m[e.camera_id].push(e);
    }
    Object.values(m).forEach((l) => l.sort((a, b) => b.ts - a.ts));
    return m;
  }, [alerts, matches, now]);

  const alertCams = useMemo(() => new Set(alerts.filter((a) => now / 1000 - a.ts < 3600).map((a) => a.camera_id)), [alerts, now]);
  const districts = useMemo(() => [...new Set(cams.map((c) => c.district))].sort(), [cams]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    return cams
      .filter((c) => !district || c.district === district)
      .filter((c) => tab === "all" || (tab === "online" ? c.status === "online" : tab === "alerts" ? alertCams.has(c.camera_id) || latestByCam[c.camera_id] : c.type === tab))
      .filter((c) => !s || c.name.toLowerCase().includes(s) || c.area.toLowerCase().includes(s) || c.camera_id.toLowerCase().includes(s))
      .sort((a, b) =>
        (latestByCam[b.camera_id] ? 2 : 0) + (b.landmark ? 1 : 0) + (b.status === "online" ? 0.5 : 0) -
        ((latestByCam[a.camera_id] ? 2 : 0) + (a.landmark ? 1 : 0) + (a.status === "online" ? 0.5 : 0)));
  }, [cams, district, tab, q, alertCams, latestByCam]);

  const rail = useMemo(() => alerts
    .filter((a) => !district || a.district === district)
    .filter((a) => railP === "all" || a.priority === railP)
    .filter((a) => railS === "all" || a.status === "new"), [alerts, district, railP, railS]);

  const inDistrict = cams.filter((c) => !district || c.district === district);

  // Footage per camera, assigned in a fixed order (landmarks first) so a tile keeps its
  // clip when new alerts re-sort the wall, and the first screenful rarely repeats one.
  const footage = useMemo(() => assignClips(
    cams.filter((c) => !district || c.district === district)
      .sort((a, b) => (Number(!!b.landmark) - Number(!!a.landmark)) || a.camera_id.localeCompare(b.camera_id))),
  [cams, district]);
  const reviewCam = review ? cams.find((c) => c.camera_id === review.camera_id) : undefined;
  const p1New = alerts.filter((a) => a.priority === "P1" && a.status === "new" && (!district || a.district === district)).length;
  const ev = model?.evaluation?.face;

  const setStream = (id: string, url: string) => {
    const s = { ...streams };
    if (url) s[id] = url; else delete s[id];
    setStreams(s);
    writeStreams(s);
  };

  return (
    <div className="space-y-5">
      {/* ----------------------------------------------------------- header */}
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-display text-3xl font-bold tracking-tight text-ink md:text-4xl">CCTV & ANPR Surveillance Command</h1>
            <span className="rounded-full border border-emerald/25 bg-emerald/10 px-2.5 py-0.5 text-[11.5px] font-semibold text-emerald">
              {inDistrict.filter((c) => c.status === "online").length} feeds online
            </span>
          </div>
          <p className="mt-1 text-[14px] text-muted">
            Face recognition against the IntelHub offender registry and plate reading against the stolen-vehicle registry,
            across {summary?.total ?? "…"} cameras in all 31 districts. Every match is a lead until an officer verifies it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={district} onChange={(e) => { setDistrict(e.target.value); setShown(12); }}
                  className="rounded-full border border-black/10 bg-white px-4 py-2 text-[13px] font-medium shadow-sm">
            <option value="">All Karnataka</option>
            {districts.map((d) => <option key={d}>{d}</option>)}
          </select>
          <Link href={`/map?mode=2d${district ? `&district=${encodeURIComponent(district)}` : ""}`}
                className="flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3.5 py-2 text-[13px] font-semibold shadow-sm hover:border-brand/40">
            <MapPinned className="h-4 w-4 text-brand" /> Camera map
          </Link>
          <button onClick={() => setTracking((t) => !t)}
                  className={`flex items-center gap-2 rounded-full px-4 py-2 text-[13px] font-semibold shadow-sm ${tracking ? "bg-[#2f6bff] text-white" : "border border-black/10 bg-white text-muted"}`}>
            <span className={`h-2 w-2 rounded-full ${tracking ? "animate-pulse bg-white" : "bg-faint"}`} />
            AI Tracking: {tracking ? "ACTIVE" : "PAUSED"}
          </button>
        </div>
      </header>

      {(locked || feedLocked) && (
        <div className="flex items-center gap-3 rounded-2xl border border-brand/30 bg-brand/[0.06] px-4 py-3 text-[13px] text-ink">
          <Lock className="h-4 w-4 text-brand" />
          Camera positions are public. Face and plate matches name people, so they need an investigator sign-in.
          <Link href="/access" className="ml-auto font-semibold text-brand">Sign in →</Link>
        </div>
      )}

      {/* ----------------------------------------------------------- KPIs */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {[
          { k: "Cameras online", v: `${inDistrict.filter((c) => c.status === "online").length}/${inDistrict.length}`, s: `${inDistrict.filter((c) => c.status !== "online").length} degraded or offline`, i: Cctv },
          { k: "Detections · 1h", v: counts?.detections?.toLocaleString() ?? "—", s: `${counts?.faces ?? 0} faces · ${counts?.plates ?? 0} plates`, i: Activity },
          { k: "P1 awaiting review", v: String(p1New), s: "warrants, stolen vehicles, watchlist", i: AlertTriangle, hot: p1New > 0 },
          { k: "Verified today", v: String(alerts.filter((a) => a.status === "verified").length), s: `${alerts.filter((a) => a.status === "rejected").length} rejected as false matches`, i: ShieldCheck },
          { k: "Faces discarded", v: counts?.faces_discarded_no_match?.toLocaleString() ?? "—", s: "no registry match — not retained", i: EyeOff },
          { k: "Matcher accuracy", v: ev ? `${Math.round(ev.true_identification_rate * 100)}%` : "—", s: ev ? `${(ev.false_positive_identification_rate * 100).toFixed(2)}% false matches` : "", i: Gauge },
        ].map((c) => (
          <div key={c.k} className={`rounded-2xl border bg-white p-3.5 shadow-sm ${c.hot ? "border-crimson/35" : "border-black/10"}`}>
            <div className="flex items-center justify-between text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">
              {c.k} <c.i className={`h-3.5 w-3.5 ${c.hot ? "text-crimson" : "text-faint"}`} />
            </div>
            <div className={`mt-1 text-2xl font-bold ${c.hot ? "text-crimson" : "text-ink"}`}>{c.v}</div>
            <div className="truncate text-[11.5px] text-muted">{c.s}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        {/* ----------------------------------------------------------- wall */}
        <section>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap rounded-full border border-black/10 bg-white p-1 shadow-sm">
              {TABS.map((t) => (
                <button key={t.id} onClick={() => { setTab(t.id); setShown(12); }}
                        className={`rounded-full px-3 py-1.5 text-[12.5px] font-semibold transition-all ${tab === t.id ? "bg-ink text-white" : "text-muted hover:text-ink"}`}>
                  {t.label}
                </button>
              ))}
            </div>
            <div className="flex min-w-[200px] flex-1 items-center gap-2 rounded-full border border-black/10 bg-white px-3.5 py-2 shadow-sm">
              <Search className="h-4 w-4 text-faint" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Junction, area or camera id…"
                     className="min-w-0 flex-1 bg-transparent text-[13px] focus:outline-none" />
            </div>
            <span className="text-[12px] text-faint">Showing {Math.min(shown, filtered.length)} of {filtered.length} streams</span>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
            {filtered.slice(0, shown).map((c) => {
              const dets = (latestByCam[c.camera_id] || []).map((e) => detectionFor(e, () => setReview(e)));
              const hot = (latestByCam[c.camera_id] || [])[0];
              return (
                <motion.article key={c.camera_id} layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
                                className={`overflow-hidden rounded-2xl border bg-white shadow-sm transition-shadow hover:shadow-lg ${hot?.priority === "P1" ? "border-crimson/50 ring-2 ring-crimson/20" : "border-black/10"}`}>
                  <CameraFeed cam={c} clip={footage[c.camera_id]} detections={dets} streamUrl={streams[c.camera_id]} />
                  <div className="p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <h3 className="truncate text-[14px] font-bold text-ink">{c.name}</h3>
                        <p className="truncate text-[12px] text-muted">{c.area}, {c.district}</p>
                      </div>
                      <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-bold ${typeBadge(c.type)}`}>{typeName(c.type)}</span>
                    </div>
                    <div className="mt-2.5 flex items-center gap-2">
                      <button disabled={!hot} onClick={() => hot && setReview(hot)}
                              className="flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-black/[0.04] py-2 text-[12.5px] font-semibold text-ink transition-colors hover:bg-[#2f6bff] hover:text-white disabled:opacity-40 disabled:hover:bg-black/[0.04] disabled:hover:text-ink">
                        <ScanFace className="h-4 w-4" /> {hot ? "Verify target" : "No active target"}
                      </button>
                      <button onClick={() => setCamOpen(c)} title="Expand" className="grid h-9 w-9 place-items-center rounded-xl bg-black/[0.04] text-muted hover:text-ink">
                        <Maximize2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </motion.article>
              );
            })}
          </div>
          {shown < filtered.length && (
            <button onClick={() => setShown((s) => s + 12)} className="mx-auto mt-4 block rounded-full border border-black/10 bg-white px-5 py-2 text-[13px] font-semibold shadow-sm hover:border-brand/40">
              Load 12 more feeds
            </button>
          )}
          <FootageNote />

          {/* ----------------------------------------------------------- tools */}
          <div className="mt-6 grid gap-4 lg:grid-cols-3">
            <AnprLookup />
            <Watchlist />
            <MatcherCard model={model} />
          </div>
        </section>

        {/* ----------------------------------------------------------- alert rail */}
        <aside className="xl:sticky xl:top-28 xl:max-h-[calc(100vh-8rem)] xl:overflow-hidden">
          <div className="flex h-full flex-col rounded-2xl border border-black/10 bg-white shadow-sm">
            <div className="border-b border-black/[0.06] p-4">
              <div className="flex items-center justify-between">
                <h2 className="flex items-center gap-2 text-[15px] font-bold text-ink">
                  <span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-crimson opacity-60" /><span className="relative h-2.5 w-2.5 rounded-full bg-crimson" /></span>
                  Live alerts
                </h2>
                <span className="text-[11.5px] text-faint">{rail.length} {railS === "new" ? "awaiting review" : "in 24h"}</span>
              </div>
              <div className="mt-2.5 flex gap-1.5">
                {(["all", "P1", "P2"] as const).map((p) => (
                  <button key={p} onClick={() => setRailP(p)} className={`rounded-full px-2.5 py-1 text-[11.5px] font-semibold ${railP === p ? "bg-ink text-white" : "bg-black/[0.04] text-muted"}`}>{p === "all" ? "All" : p}</button>
                ))}
                <span className="mx-1 w-px bg-black/10" />
                {(["new", "all"] as const).map((s) => (
                  <button key={s} onClick={() => setRailS(s)} className={`rounded-full px-2.5 py-1 text-[11.5px] font-semibold ${railS === s ? "bg-brand text-black" : "bg-black/[0.04] text-muted"}`}>{s === "new" ? "Needs review" : "Everything"}</button>
                ))}
              </div>
            </div>
            <div className="flex-1 space-y-2 overflow-y-auto p-3">
              {locked && <p className="p-4 text-center text-[12.5px] text-muted">Sign in as an investigator to see matches.</p>}
              {!locked && rail.length === 0 && <p className="p-4 text-center text-[12.5px] text-muted">Nothing waiting. New matches appear here within seconds.</p>}
              <AnimatePresence initial={false}>
                {rail.slice(0, 60).map((a) => (
                  <motion.button key={a.event_id} layout initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
                                 onClick={() => setReview(a)}
                                 className={`w-full rounded-xl border p-2.5 text-left transition-colors hover:bg-black/[0.02] ${a.priority === "P1" ? "border-crimson/30" : "border-black/10"}`}>
                    <AlertCard a={a} now={now} />
                  </motion.button>
                ))}
              </AnimatePresence>
            </div>
          </div>
        </aside>
      </div>

      <AnimatePresence>
        {review && <ReviewModal key={review.event_id} a={review} cam={reviewCam} clip={reviewCam && footage[reviewCam.camera_id]}
                                onClose={() => { setReview(null); if (params.get("alert")) router.replace("/surveillance"); }} />}
        {camOpen && (
          <CameraModal key={camOpen.camera_id} cam={camOpen} clip={footage[camOpen.camera_id]} stream={streams[camOpen.camera_id]} onStream={(u) => setStream(camOpen.camera_id, u)}
                       detections={(latestByCam[camOpen.camera_id] || []).map((e) => detectionFor(e, () => setReview(e)))}
                       onReview={setReview} onClose={() => { setCamOpen(null); if (params.get("camera")) router.replace("/surveillance"); }} />
        )}
      </AnimatePresence>
    </div>
  );
}

// ===================================================================== pieces
function AlertCard({ a, now }: { a: any; now: number }) {
  const face = a.kind === "face" && a.face_match;
  return (
    <div className="flex items-start gap-2.5">
      {face ? (
        <div className="relative shrink-0">
          <Face src={a.face_match.person?.photo_url} name={a.face_match.person?.name} capture className="h-12 w-12 rounded-lg text-[13px]" />
          <span className="absolute -bottom-1 -right-1 rounded bg-black/80 px-1 font-mono text-[9px] font-bold text-white">{Math.round(a.face_match.similarity * 100)}%</span>
        </div>
      ) : (
        <div className="grid h-12 w-12 shrink-0 place-items-center rounded-lg bg-azure/10 text-azure"><Car className="h-5 w-5" /></div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className={`rounded px-1.5 text-[10px] font-bold ${a.priority === "P1" ? "bg-crimson text-white" : "bg-[#f08a24]/20 text-[#b45f0a]"}`}>{a.priority}</span>
          {face ? <span className="truncate text-[13px] font-bold text-ink">{a.face_match.person?.name}</span>
                : <span className="plate text-[11px]">{a.plate_read}</span>}
          {a.status !== "new" && <span className={`ml-auto rounded px-1 text-[9.5px] font-bold uppercase ${a.status === "verified" ? "bg-emerald/15 text-emerald" : a.status === "rejected" ? "bg-black/10 text-muted" : "bg-azure/15 text-azure"}`}>{a.status}</span>}
        </div>
        <div className="truncate text-[11.5px] text-muted">{face ? `${a.face_match.suspect_id} · ` : `${a.plate_match?.make_model} · `}{a.reasons.slice(0, 2).join(" · ")}</div>
        <div className="truncate text-[11px] text-faint">{a.camera_name} · {ago(a.ts, now)}</div>
      </div>
    </div>
  );
}

function Modal({ children, onClose, wide }: { children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  // Portalled to <body>: inside <main> the dialog shares main's stacking context and the
  // fixed nav and Kavya bar would sit on top of it.
  return createPortal(
    <motion.div className="fixed inset-0 z-[80] grid place-items-center bg-[#0b0e14]/55 p-4 backdrop-blur-sm" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose}>
      <motion.div onClick={(e) => e.stopPropagation()} initial={{ y: 20, scale: 0.97 }} animate={{ y: 0, scale: 1 }} exit={{ y: 10, scale: 0.98 }}
                  transition={{ type: "spring", stiffness: 360, damping: 30 }}
                  className={`relative max-h-[92vh] w-full overflow-y-auto rounded-3xl bg-white shadow-2xl ${wide ? "max-w-5xl" : "max-w-3xl"}`}>
        <button onClick={onClose} className="absolute right-4 top-4 z-10 grid h-9 w-9 place-items-center rounded-full bg-black/[0.06] text-muted hover:text-ink"><X className="h-4 w-4" /></button>
        {children}
      </motion.div>
    </motion.div>,
    document.body,
  );
}

function ReviewModal({ a: initial, cam, clip, onClose }: { a: any; cam?: Cam; clip?: Clip; onClose: () => void }) {
  const [a, setA] = useState(initial);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [trail, setTrail] = useState<any[]>([]);
  const [watched, setWatched] = useState(false);
  const face = a.kind === "face" && a.face_match;
  const p = face ? a.face_match.person || {} : null;
  const now = useNow();

  useEffect(() => {
    if (face) getJSON(`/surveillance/track/${a.face_match.suspect_id}`).then((d) => setTrail(d.sightings || [])).catch(() => {});
  }, [a.event_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const decide = async (action: string) => {
    setBusy(true);
    setErr("");
    try {
      const r = await apiFetch(`/surveillance/alerts/${a.event_id}`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Access-Reason": `alert review: ${action}` },
        body: JSON.stringify({ action, note }),
      });
      const d = await r.json();
      setA(d.alert);
      patchAlert(d.alert);
    } catch (e: any) {
      setErr(e?.status === 400 ? "Write what you checked before verifying or rejecting." : "Could not record the decision.");
    } finally {
      setBusy(false);
    }
  };
  const watch = async () => {
    const item = face ? a.face_match.suspect_id : a.plate_match?.vehicle_number;
    await apiFetch("/surveillance/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ add: item }) }).catch(() => {});
    setWatched(true);
    refreshAlerts();
  };

  return (
    <Modal onClose={onClose} wide>
      <div className="p-6">
        <div className="flex flex-wrap items-center gap-2 pr-10">
          <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold ${a.priority === "P1" ? "bg-crimson text-white" : "bg-[#f08a24]/20 text-[#b45f0a]"}`}>{a.priority}</span>
          <h2 className="text-xl font-bold text-ink">{face ? "Face match review" : "Plate match review"}</h2>
          <span className="text-[12.5px] text-muted">· {a.camera_name}, {a.district} · {istTime(a.ts)} IST ({ago(a.ts, now)})</span>
        </div>
        <div className="mt-3 flex items-start gap-2 rounded-xl border border-brand/25 bg-brand/[0.06] px-3 py-2 text-[12.5px] text-ink">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          A match is a lead, not an identification. Compare the capture with the dossier yourself before any action. Look-alikes produce false matches; low light and low resolution make them likelier.
        </div>

        {face ? (
          <div className="mt-5 grid gap-5 md:grid-cols-[1.5fr_0.8fr_1.2fr]">
            <figure>
              <div className="relative overflow-hidden rounded-2xl bg-black">
                {cam ? <CameraFeed cam={cam} clip={clip} /> : (
                  <Face src={p.photo_url} name={p.name} capture className="aspect-square w-full text-4xl opacity-90" />
                )}
              </div>
              <figcaption className="mt-1.5 text-center text-[11.5px] text-muted">{a.camera_id} · capture quality {a.quality}</figcaption>
            </figure>
            <figure>
              <div className="relative overflow-hidden rounded-2xl">
                <Face src={p.photo_url} name={p.name} className="aspect-square w-full text-4xl" />
                <span className="absolute left-2 top-2 rounded bg-ink px-1.5 font-mono text-[10px] font-bold text-white">REGISTRY</span>
              </div>
              <figcaption className="mt-1.5 text-center text-[11.5px] text-muted">IntelHub record {a.face_match.suspect_id}</figcaption>
            </figure>
            <div>
              <div className="text-[18px] font-bold text-ink">{p.name}{p.alias ? <span className="ml-1 text-[13px] font-medium text-muted">“{p.alias}”</span> : null}</div>
              <div className="font-mono text-[12px] text-muted">{a.face_match.suspect_id}</div>
              <div className="mt-3">
                <div className="flex justify-between text-[12px]"><span className="text-muted">Similarity</span><b className="text-ink">{(a.face_match.similarity * 100).toFixed(1)}%</b></div>
                <div className="mt-1 h-2 rounded-full bg-black/[0.06]"><div className="h-2 rounded-full" style={{ width: `${a.face_match.similarity * 100}%`, background: a.face_match.band === "strong" ? "#0a9d57" : "#f08a24" }} /></div>
                <div className="mt-1 text-[11.5px] text-muted">{a.face_match.band} band · margin {(a.face_match.margin * 100).toFixed(1)} pts over the runner-up</div>
              </div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {a.reasons.map((r: string) => <span key={r} className="rounded-full bg-crimson/10 px-2 py-0.5 text-[11px] font-semibold text-crimson">{r}</span>)}
                {p.gang && <span className="rounded-full bg-black/[0.05] px-2 py-0.5 text-[11px] font-semibold text-ink">{p.gang}</span>}
                <span className="rounded-full bg-black/[0.05] px-2 py-0.5 text-[11px] font-semibold text-ink">{p.prior_firs} prior FIRs</span>
              </div>
              <div className="mt-3 text-[11.5px] text-muted">Other candidates</div>
              <ul className="mt-1 space-y-1">
                {a.face_match.candidates.slice(1).map((c: any) => (
                  <li key={c.suspect_id} className="flex items-center justify-between text-[12px]"><span>{c.name} <span className="font-mono text-faint">{c.suspect_id}</span></span><span className="font-mono text-muted">{(c.similarity * 100).toFixed(1)}%</span></li>
                ))}
              </ul>
            </div>
          </div>
        ) : (
          <div className="mt-5 grid gap-5 md:grid-cols-2">
            <div className="rounded-2xl bg-[#0b0e14] p-6 text-center">
              <div className="text-[11px] uppercase tracking-[0.2em] text-white/50">Read by ANPR</div>
              <div className="plate mx-auto mt-3 inline-block text-2xl">{a.plate_read}</div>
              <div className="mt-2 font-mono text-[11px] text-white/60">confidence {Math.round((a.read_confidence || 0) * 100)}% · {a.camera_id}</div>
            </div>
            <div>
              <div className="text-[12px] text-muted">Matched registry entry ({a.plate_match?.kind}{a.plate_match?.kind === "fuzzy" ? `, distance ${a.plate_match.distance}` : ""})</div>
              <div className="plate mt-1 inline-block text-xl">{a.plate_match?.vehicle_number}</div>
              <dl className="mt-3 grid grid-cols-2 gap-2 text-[12.5px]">
                <dt className="text-muted">Vehicle</dt><dd className="font-semibold">{a.plate_match?.make_model}</dd>
                <dt className="text-muted">Stolen</dt><dd className="font-semibold">{a.plate_match?.theft_date} · {a.plate_match?.theft_district}</dd>
                <dt className="text-muted">Status</dt><dd className="font-semibold text-crimson">{a.plate_match?.recovery_status}</dd>
                {a.plate_match?.linked_case_id && <><dt className="text-muted">Case</dt><dd><Link className="font-semibold text-brand" href={`/case?id=${a.plate_match.linked_case_id}`}>{a.plate_match.linked_case_id}</Link></dd></>}
              </dl>
            </div>
          </div>
        )}

        {face && trail.length > 0 && (
          <div className="mt-5">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-[13px] font-bold text-ink"><Footprints className="h-4 w-4 text-crimson" /> Sightings in the last 24 hours ({trail.length})</h3>
              <Link href={`/map?mode=3d&track=${a.face_match.suspect_id}`} className="text-[12px] font-semibold text-brand">Track on map →</Link>
            </div>
            <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
              {trail.map((s: any) => (
                <div key={s.event_id} className={`min-w-[170px] rounded-xl border px-2.5 py-2 text-[11.5px] ${s.event_id === a.event_id ? "border-brand bg-brand/5" : "border-black/10"}`}>
                  <div className="font-semibold text-ink">{istTime(s.ts).slice(0, 5)} · {Math.round(s.face_match.similarity * 100)}%</div>
                  <div className="truncate text-muted">{s.camera_name}</div>
                  <div className="text-[10.5px] text-faint">{s.status}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-5 rounded-2xl border border-black/10 p-4">
          {a.decision ? (
            <div className="mb-3 flex items-center gap-2 text-[12.5px]">
              <CheckCircle2 className="h-4 w-4 text-emerald" /> <b className="capitalize">{a.status}</b> by {a.decision.actor} — “{a.decision.note}”
            </div>
          ) : null}
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
                    placeholder="What did you check? e.g. compared with dossier photo, scar on left cheek matches; plate font consistent"
                    className="w-full resize-none rounded-xl border border-black/10 bg-black/[0.02] px-3 py-2 text-[13px] focus:border-brand/40 focus:outline-none" />
          {err && <p className="mt-1 text-[12px] text-crimson">{err}</p>}
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <button disabled={busy} onClick={() => decide("verify")} className="flex items-center gap-1.5 rounded-xl bg-emerald px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"><ShieldCheck className="h-4 w-4" /> Verify match</button>
            <button disabled={busy} onClick={() => decide("reject")} className="flex items-center gap-1.5 rounded-xl bg-ink px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"><ShieldX className="h-4 w-4" /> Reject — not them</button>
            <button disabled={busy} onClick={() => decide("escalate")} className="flex items-center gap-1.5 rounded-xl border border-black/10 px-3.5 py-2 text-[13px] font-semibold disabled:opacity-50"><ArrowUpRight className="h-4 w-4" /> Escalate to supervisor</button>
            <button disabled={busy} onClick={() => decide("acknowledge")} className="rounded-xl border border-black/10 px-3.5 py-2 text-[13px] font-semibold disabled:opacity-50">Acknowledge</button>
            <span className="flex-1" />
            {face && <Link href={`/suspect?id=${a.face_match.suspect_id}`} className="flex items-center gap-1 text-[12.5px] font-semibold text-brand"><Eye className="h-4 w-4" /> Dossier</Link>}
            <button onClick={watch} disabled={watched} className="flex items-center gap-1 text-[12.5px] font-semibold text-brand disabled:text-emerald"><ListPlus className="h-4 w-4" /> {watched ? "On watchlist" : "Watchlist"}</button>
          </div>
          <p className="mt-2 text-[11px] text-faint">Decisions are logged with your name, the time and your note, and cannot be edited.</p>
        </div>
      </div>
    </Modal>
  );
}

function CameraModal({ cam, clip, detections, stream, onStream, onReview, onClose }:
  { cam: Cam; clip?: Clip; detections: Detection[]; stream?: string; onStream: (u: string) => void; onReview: (e: any) => void; onClose: () => void }) {
  const [detail, setDetail] = useState<any>(null);
  const [url, setUrl] = useState(stream || "");
  const [showCfg, setShowCfg] = useState(false);
  const now = useNow();
  useEffect(() => {
    getJSON(`/surveillance/camera/${cam.camera_id}`).then(setDetail).catch(() => {});
  }, [cam.camera_id]);
  return (
    <Modal onClose={onClose} wide>
      <CameraFeed cam={cam} clip={clip} detections={detections} streamUrl={stream} large />
      {clip && !stream && cam.status !== "offline" && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-black/[0.06] bg-black/[0.02] px-6 py-2 text-[11.5px] text-muted">
          <span><b className="text-ink">Footage:</b> {clip.place}, Bengaluru — recorded {clip.light === "night" ? "at night" : clip.light === "dusk" ? "at dusk" : "by day"}</span>
          <span><b className="text-ink">Boxes:</b> YOLO11s object detection + ByteTrack, run on this footage</span>
          <a href={clip.source} target="_blank" rel="noreferrer" className="font-semibold text-brand hover:underline">
            “{clip.title}” · {clip.author} · {clip.license}
          </a>
        </div>
      )}
      <div className="grid gap-5 p-6 md:grid-cols-[1fr_1.3fr]">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-bold text-ink">{cam.name}</h2>
            <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-bold ${typeBadge(cam.type)}`}>{typeName(cam.type)}</span>
          </div>
          <p className="text-[13px] text-muted">{cam.area}, {cam.district} · {cam.camera_id}</p>
          <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt className="text-muted">Status</dt><dd className={`font-semibold ${cam.status === "online" ? "text-emerald" : cam.status === "degraded" ? "text-[#b45f0a]" : "text-crimson"}`}>{cam.status}</dd>
            <dt className="text-muted">Uptime</dt><dd className="font-semibold">{cam.uptime_pct}%</dd>
            <dt className="text-muted">Optics</dt><dd className="font-semibold">{cam.resolution} · {cam.fps} fps · quality {cam.quality}</dd>
            <dt className="text-muted">Operator</dt><dd className="font-semibold">{cam.owner}</dd>
            <dt className="text-muted">Detections (6h)</dt><dd className="font-semibold">{detail?.detections_6h ?? "—"}</dd>
            <dt className="text-muted">Faces discarded</dt><dd className="font-semibold">{detail?.faces_discarded_no_match ?? "—"}</dd>
          </dl>
          <div className="mt-4 flex gap-2">
            <Link href={`/map?mode=3d&camera=${cam.camera_id}`} onClick={onClose} className="flex items-center gap-1.5 rounded-xl bg-black/[0.05] px-3 py-2 text-[12.5px] font-semibold"><MapPinned className="h-4 w-4" /> On the map</Link>
            <button onClick={() => setShowCfg((s) => !s)} className="flex items-center gap-1.5 rounded-xl bg-black/[0.05] px-3 py-2 text-[12.5px] font-semibold"><Settings2 className="h-4 w-4" /> Feed source</button>
          </div>
          {showCfg && (
            <div className="mt-3 rounded-xl border border-black/10 p-3 text-[12px]">
              <p className="text-muted">Play a real stream for this camera (MP4, or HLS on Safari). Stored in this browser only.</p>
              <div className="mt-2 flex gap-2">
                <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/stream.mp4" className="min-w-0 flex-1 rounded-lg border border-black/10 px-2 py-1.5" />
                <button onClick={() => onStream(url.trim())} className="rounded-lg bg-ink px-3 font-semibold text-white">Save</button>
                {stream && <button onClick={() => { setUrl(""); onStream(""); }} className="rounded-lg border border-black/10 px-2">Reset</button>}
              </div>
            </div>
          )}
        </div>
        <div>
          <h3 className="text-[13px] font-bold text-ink">Registry matches at this camera (6h)</h3>
          <div className="mt-2 max-h-72 space-y-2 overflow-y-auto">
            {!detail && <p className="text-[12.5px] text-muted">Loading…</p>}
            {detail?.matches?.length === 0 && <p className="text-[12.5px] text-muted">No registry matches in six hours.</p>}
            {detail?.matches?.map((m: any) => (
              <button key={m.event_id} onClick={() => onReview(m)} className="w-full rounded-xl border border-black/10 p-2.5 text-left hover:bg-black/[0.02]">
                <AlertCard a={{ ...m, priority: m.priority || "P3", reasons: m.reasons || [] }} now={now} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function FootageNote() {
  return (
    <details className="mt-4 rounded-2xl border border-black/10 bg-white px-4 py-3 text-[12px] text-muted shadow-sm">
      <summary className="cursor-pointer font-semibold text-ink">About these feeds</summary>
      <p className="mt-2 leading-relaxed">
        Each feed plays real street footage recorded in Bengaluru in 2024–25 and published on Wikimedia Commons, standing in
        for the camera&apos;s stream; where a camera is named after a place we have footage of (Brigade Road, MG Road, Hebbal,
        Banashankari, Shivajinagar, Mekhri Circle), it shows that place. The boxes are real detections from YOLO11s with
        ByteTrack tracking, computed on that footage. Face and plate matches come from the matcher and are never drawn on
        anyone in the picture — nobody filmed here is a registry person.
      </p>
      <ul className="mt-2 grid gap-x-4 gap-y-0.5 sm:grid-cols-2">
        {CLIPS.map((c) => (
          <li key={c.slug} className="truncate">
            <a href={c.source} target="_blank" rel="noreferrer" className="hover:text-brand">“{c.title}”</a> — {c.author}, {c.license}
          </li>
        ))}
      </ul>
    </details>
  );
}

function AnprLookup() {
  const [q, setQ] = useState("");
  const [r, setR] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try { setR(await getJSON(`/surveillance/plate?q=${encodeURIComponent(q)}`)); } catch { setR({ error: true }); } finally { setBusy(false); }
  };
  const m = r?.registry_match;
  return (
    <div className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
      <h3 className="flex items-center gap-1.5 text-[13px] font-bold text-ink"><Car className="h-4 w-4 text-azure" /> ANPR plate lookup</h3>
      <form onSubmit={(e) => { e.preventDefault(); go(); }} className="mt-2.5 flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} placeholder="KA-05-MN-1234" className="plate min-w-0 flex-1 text-[13px] focus:outline-none" />
        <button disabled={busy} className="rounded-xl bg-ink px-3 text-[12.5px] font-semibold text-white">{busy ? "…" : "Check"}</button>
      </form>
      {r && !r.error && (
        <div className="mt-3 text-[12.5px]">
          {m ? (
            <div className="rounded-xl border border-crimson/25 bg-crimson/[0.05] p-2.5">
              <div className="font-bold text-crimson">{m.kind === "exact" ? "Stolen vehicle" : "Likely stolen vehicle (OCR-tolerant)"}</div>
              <div><span className="plate text-[11px]">{m.vehicle_number}</span> {m.make_model} · stolen {m.theft_date}, {m.theft_district} · {m.recovery_status}</div>
            </div>
          ) : <div className="rounded-xl bg-emerald/10 p-2.5 font-semibold text-emerald">Not in the stolen-vehicle registry.</div>}
          <div className="mt-2 text-muted">{r.recent_reads?.length ? `Seen ${r.recent_reads.length}× in 24h — last at ${r.recent_reads[r.recent_reads.length - 1].camera_name}` : "No ANPR reads in 24 hours."}</div>
        </div>
      )}
      {r?.error && <p className="mt-2 text-[12px] text-crimson">Sign in as an investigator to look up plates.</p>}
    </div>
  );
}

function Watchlist() {
  const [w, setW] = useState<any>({ people: [], plates: [] });
  const [item, setItem] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => getJSON("/surveillance/watchlist").then(setW).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const send = async (body: any) => {
    setErr("");
    try {
      const r = await apiFetch("/surveillance/watchlist", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setW(await r.json());
      refreshAlerts();
    } catch (e: any) { setErr(e?.status === 404 ? "Not in the registry." : "Could not update."); }
  };
  return (
    <div className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
      <h3 className="flex items-center gap-1.5 text-[13px] font-bold text-ink"><Radar className="h-4 w-4 text-crimson" /> My watchlist</h3>
      <p className="mt-0.5 text-[11.5px] text-muted">Any sighting of these is raised to P1 for you.</p>
      <form onSubmit={(e) => { e.preventDefault(); if (item.trim()) send({ add: item.trim() }); setItem(""); }} className="mt-2.5 flex gap-2">
        <input value={item} onChange={(e) => setItem(e.target.value)} placeholder="SUS-8286 or KA-41-QF-9012" className="min-w-0 flex-1 rounded-xl border border-black/10 px-3 py-1.5 text-[13px]" />
        <button className="rounded-xl bg-ink px-3 text-[12.5px] font-semibold text-white">Add</button>
      </form>
      {err && <p className="mt-1 text-[12px] text-crimson">{err}</p>}
      <ul className="mt-2.5 max-h-40 space-y-1.5 overflow-y-auto">
        {w.people.map((p: any) => (
          <li key={p.suspect_id} className="flex items-center gap-2 text-[12.5px]">
            <Face src={p.photo_url} name={p.name} className="h-6 w-6 rounded-full text-[9px]" />
            <Link href={`/suspect?id=${p.suspect_id}`} className="flex-1 truncate font-semibold text-ink hover:text-brand">{p.name} <span className="font-mono text-[11px] text-faint">{p.suspect_id}</span></Link>
            <button onClick={() => send({ remove: p.suspect_id })} className="text-faint hover:text-crimson"><Trash2 className="h-3.5 w-3.5" /></button>
          </li>
        ))}
        {w.plates.map((p: any) => (
          <li key={p.normalized} className="flex items-center gap-2 text-[12.5px]">
            <span className="plate text-[10.5px]">{p.vehicle_number}</span>
            <span className="flex-1 truncate text-muted">{p.make_model || "plate"}</span>
            <button onClick={() => send({ remove: p.normalized })} className="text-faint hover:text-crimson"><Trash2 className="h-3.5 w-3.5" /></button>
          </li>
        ))}
        {!w.people.length && !w.plates.length && <li className="text-[12px] text-faint">Empty.</li>}
      </ul>
    </div>
  );
}

function MatcherCard({ model }: { model: any }) {
  const f = model?.evaluation?.face;
  const p = model?.evaluation?.plate;
  return (
    <div className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
      <h3 className="flex items-center gap-1.5 text-[13px] font-bold text-ink"><Gauge className="h-4 w-4 text-emerald" /> How good is the matcher?</h3>
      {!f ? <p className="mt-2 text-[12px] text-muted">Loading…</p> : (
        <div className="mt-2 space-y-1.5 text-[12.5px]">
          <div className="flex justify-between"><span className="text-muted">Finds a registered face</span><b>{Math.round(f.true_identification_rate * 100)}%</b></div>
          <div className="flex justify-between"><span className="text-muted">…at night / low resolution</span><b>{Math.round(f.tir_low_quality_or_night * 100)}%</b></div>
          <div className="flex justify-between"><span className="text-muted">False match on a stranger</span><b>{(f.false_positive_identification_rate * 100).toFixed(2)}%</b></div>
          <div className="flex justify-between"><span className="text-muted">Mangled plates recovered</span><b>{p?.recovered_by_matcher_pct}%</b></div>
          <p className="pt-1 text-[11px] leading-relaxed text-faint">
            Measured on {f.probes.toLocaleString()} probes against {f.gallery_size.toLocaleString()} registered faces. {model.what_is_simulated}
          </p>
        </div>
      )}
    </div>
  );
}

export default function SurveillancePage() {
  return (
    <Suspense fallback={null}>
      <SurveillanceInner />
    </Suspense>
  );
}
