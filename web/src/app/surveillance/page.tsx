"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import {
  Cctv, ScanFace, Car, Radar, ShieldCheck, ShieldX, AlertTriangle, Maximize2, X, Search, Eye, MapPinned,
  ListPlus, Trash2, Activity, Lock, CheckCircle2, ArrowUpRight, Footprints, Gauge, EyeOff, Radio,
} from "lucide-react";
import { apiFetch, getJSON, ApiError } from "@/lib/api";
import { useIdentity } from "@/lib/session";
import { useAlerts, patchAlert, refreshAlerts, ago, istTime, useNow, type SurvAlert } from "@/lib/alerts";
import CameraFeed, { Face, type Detection, type FeedCamera } from "@/components/CameraFeed";
import { CLIPS, assignClips, type Clip } from "@/lib/footage";
import {
  KIND_LABEL, StreamSaveError, removeStream, saveStream, useStreams,
  type FeedState, type LiveSource, type StreamKind, type StreamsView,
} from "@/lib/streams";

type Cam = FeedCamera & { matches_last_hour?: number; owner?: string; uptime_pct?: number; landmark?: boolean; quality?: number };

const TABS = [
  { id: "all", label: "All feeds" },
  { id: "live", label: "Live only" },
  { id: "online", label: "Online only" },
  { id: "FACE", label: "Face AI" },
  { id: "ANPR", label: "ANPR readers" },
  { id: "PTZ", label: "PTZ" },
  { id: "alerts", label: "With alerts" },
];

/** The tile footer's word for what the feed is showing, from the tile's own report. */
function feedChip(src: LiveSource | undefined, s: FeedState | undefined) {
  if (!src) return { text: "Recorded clip", cls: "bg-black/[0.05] text-muted" };
  const kind = KIND_LABEL[src.kind];
  if (s === "live") return { text: `Live · ${kind}`, cls: "bg-crimson/10 text-crimson" };
  if (s === "down") return { text: `${kind} down · recorded shown`, cls: "bg-[#f08a24]/15 text-[#b45f0a]" };
  if (s === "not-live") return { text: `${kind} · not live`, cls: "bg-[#f08a24]/15 text-[#b45f0a]" };
  if (s === "awaiting-start") return { text: "Device camera · not started", cls: "bg-black/[0.05] text-muted" };
  if (s === "unverified") return { text: `${kind} · status unknown`, cls: "bg-black/[0.05] text-muted" };
  if (s === "stalled") return { text: `${kind} · buffering`, cls: "bg-[#f08a24]/15 text-[#b45f0a]" };
  return { text: `${kind} · connecting`, cls: "bg-black/[0.05] text-muted" };
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
  const [feedLocked, setFeedLocked] = useState(false);
  const live = useStreams();
  const streams = live.streams;
  // What each wall tile reports it is actually showing. Off-screen tiles report
  // "paused", so "receiving frames" counts only what is really playing right now.
  const [feedStates, setFeedStates] = useState<Record<string, FeedState>>({});
  const onFeedStatus = useCallback((id: string, s: FeedState | null) => {
    setFeedStates((m) => {
      if (s === null) {
        if (!(id in m)) return m;
        const next = { ...m };
        delete next[id];
        return next;
      }
      return m[id] === s ? m : { ...m, [id]: s };
    });
  }, []);

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
      .filter((c) => tab === "all" || (tab === "live" ? !!streams[c.camera_id] : tab === "online" ? c.status === "online" : tab === "alerts" ? alertCams.has(c.camera_id) || latestByCam[c.camera_id] : c.type === tab))
      .filter((c) => !s || c.name.toLowerCase().includes(s) || c.area.toLowerCase().includes(s) || c.camera_id.toLowerCase().includes(s))
      // A real live picture leads the wall: it is the one thing on it that is happening now.
      .sort((a, b) =>
        (streams[b.camera_id] ? 3 : 0) + (latestByCam[b.camera_id] ? 2 : 0) + (b.landmark ? 1 : 0) + (b.status === "online" ? 0.5 : 0) -
        ((streams[a.camera_id] ? 3 : 0) + (latestByCam[a.camera_id] ? 2 : 0) + (a.landmark ? 1 : 0) + (a.status === "online" ? 0.5 : 0)));
  }, [cams, district, tab, q, alertCams, latestByCam, streams]);

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
          <Link href="/?next=%2Fsurveillance" className="ml-auto font-semibold text-brand">Sign in →</Link>
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

      <LiveSummary live={live} cams={cams} district={district} feedStates={feedStates}
                   active={tab === "live"} onShow={() => { setTab("live"); setShown(12); }} />

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

          {tab === "live" && filtered.length === 0 && (
            <div className="rounded-2xl border border-dashed border-black/15 bg-white px-5 py-8 text-center text-[13px] text-muted">
              {live.locked
                ? <>Live sources are shown to investigators and above, because a live stream can show people. <Link href="/?next=%2Fsurveillance" className="font-semibold text-brand">Sign in →</Link></>
                : live.status === "error" ? "Live sources could not be loaded. Every tile is showing its recorded clip."
                : live.status !== "ready" ? "Loading live sources…"
                : live.data?.count
                  ? <>No camera {district ? <>in <b className="text-ink">{district}</b> </> : null}has a live source{q ? " matching the search" : ""}. {district ? <button onClick={() => setDistrict("")} className="font-semibold text-brand">Show all Karnataka →</button> : null}</>
                  : <>No camera has a live source attached yet, so every tile is a recorded clip. {live.canEdit ? "Open any camera and use Live source to attach one." : "A supervisor can attach one from a camera's dialog."}</>}
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
            {filtered.slice(0, shown).map((c) => {
              const dets = (latestByCam[c.camera_id] || []).map((e) => detectionFor(e, () => setReview(e)));
              const hot = (latestByCam[c.camera_id] || [])[0];
              const src = streams[c.camera_id];
              const chip = feedChip(src, feedStates[c.camera_id]);
              return (
                <motion.article key={c.camera_id} layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
                                className={`overflow-hidden rounded-2xl border bg-white shadow-sm transition-shadow hover:shadow-lg ${hot?.priority === "P1" ? "border-crimson/50 ring-2 ring-crimson/20" : "border-black/10"}`}>
                  <CameraFeed cam={c} clip={footage[c.camera_id]} detections={dets} live={src} onStatus={onFeedStatus} />
                  <div className="p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <h3 className="truncate text-[14px] font-bold text-ink">{c.name}</h3>
                        <p className="truncate text-[12px] text-muted">{c.area}, {c.district}</p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-bold ${typeBadge(c.type)}`}>{typeName(c.type)}</span>
                        <span title={src?.label || undefined} className={`max-w-[160px] truncate rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${chip.cls}`}>{chip.text}</span>
                      </div>
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
          <CameraModal key={camOpen.camera_id} cam={camOpen} clip={footage[camOpen.camera_id]} live={streams[camOpen.camera_id]} streams={live}
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
  // Buttons follow the permissions the server enforces (it re-checks every action).
  const perms = useIdentity().identity?.permissions ?? [];
  const canDecide = perms.includes("surveillance_decide");
  const canEscalate = perms.includes("surveillance_escalate");
  const noRight = "Your role cannot take this action";
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
            <button disabled={busy || !canDecide} title={canDecide ? undefined : noRight} onClick={() => decide("verify")} className="flex items-center gap-1.5 rounded-xl bg-emerald px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"><ShieldCheck className="h-4 w-4" /> Verify match</button>
            <button disabled={busy || !canDecide} title={canDecide ? undefined : noRight} onClick={() => decide("reject")} className="flex items-center gap-1.5 rounded-xl bg-ink px-3.5 py-2 text-[13px] font-semibold text-white disabled:opacity-50"><ShieldX className="h-4 w-4" /> Reject — not them</button>
            <button disabled={busy || !canEscalate} title={canEscalate ? undefined : noRight} onClick={() => decide("escalate")} className="flex items-center gap-1.5 rounded-xl border border-black/10 px-3.5 py-2 text-[13px] font-semibold disabled:opacity-50"><ArrowUpRight className="h-4 w-4" /> Escalate up the chain</button>
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

function CameraModal({ cam, clip, detections, live, streams, onReview, onClose }:
  { cam: Cam; clip?: Clip; detections: Detection[]; live?: LiveSource; streams: StreamsView; onReview: (e: any) => void; onClose: () => void }) {
  const [detail, setDetail] = useState<any>(null);
  // What the large feed is actually showing, so the caption under it describes that
  // picture: the live source's credit while it plays, the clip's when it fell back.
  const [feed, setFeed] = useState<FeedState | null>(null);
  const onStatus = useCallback((_id: string, s: FeedState | null) => setFeed(s), []);
  const now = useNow();
  useEffect(() => {
    getJSON(`/surveillance/camera/${cam.camera_id}`).then(setDetail).catch(() => {});
  }, [cam.camera_id]);
  const showingClip = !live || feed === "down";
  return (
    <Modal onClose={onClose} wide>
      <CameraFeed cam={cam} clip={clip} detections={detections} live={live} onStatus={onStatus} large />
      {live && !showingClip && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-black/[0.06] bg-black/[0.02] px-6 py-2 text-[11.5px] text-muted">
          <span><b className="text-ink">Live source:</b> {live.label ? `${live.label} · ` : ""}{KIND_LABEL[live.kind]}
            {live.origin === "deployment" ? " · set by the deployment" : live.added_by ? ` · attached by ${live.added_by}` : ""}</span>
          {live.kind === "device"
            ? <span className="font-semibold text-ink">This is the camera of the device you are using, not the CCTV camera at {cam.name}.</span>
            : <span><b className="text-ink">Boxes:</b> none — no detector runs on a live picture</span>}
          {live.attribution && <span><b className="text-ink">Credit:</b> {live.attribution}</span>}
          {live.kind === "youtube" && live.url && (
            <a href={live.url} target="_blank" rel="noreferrer" className="font-semibold text-brand hover:underline">Open on YouTube</a>
          )}
          <span>Matcher alerts for this camera come from the simulated sighting stream, not from this picture.</span>
        </div>
      )}
      {clip && showingClip && (cam.status !== "offline" || live) && (
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
          </div>
          <LiveSourcePanel cam={cam} live={live} streams={streams} />
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

// ===================================================================== live sources
/** How many cameras have a live source, and how many of the tiles on screen are
 *  actually receiving frames - two different numbers, both shown. */
function LiveSummary({ live, cams, district, feedStates, active, onShow }:
  { live: StreamsView; cams: Cam[]; district: string; feedStates: Record<string, FeedState>; active: boolean; onShow: () => void }) {
  const ids = Object.keys(live.streams);
  const districtOf = useMemo(() => new Map(cams.map((c) => [c.camera_id, c.district])), [cams]);
  const here = district ? ids.filter((id) => districtOf.get(id) === district).length : ids.length;
  const states = Object.values(feedStates);
  const receiving = states.filter((s) => s === "live").length;
  const down = states.filter((s) => s === "down").length;
  let body: React.ReactNode;
  if (live.locked)
    body = <>Sign in as an investigator to see which cameras have a live stream attached. Until then every tile plays its recorded clip, badged RECORDED.</>;
  else if (live.status === "error") body = "Live sources could not be loaded, so every tile is playing its recorded clip.";
  else if (live.status !== "ready") body = "Checking for live sources…";
  else if (!ids.length)
    body = <>None attached: every tile is a recorded Bengaluru street clip, badged RECORDED. {live.canEdit
      ? "Attach an NVR (HLS), WebRTC (WHEP), MJPEG or YouTube Live stream from any camera's dialog."
      : "A supervisor can attach a real stream from a camera's dialog."}</>;
  else
    body = <>
      <b className="text-ink">{ids.length}</b> camera{ids.length === 1 ? "" : "s"} with a live source{district ? <> ({here} in {district})</> : null}
      {" · "}<b className="text-ink">{receiving}</b> on screen receiving frames now
      {down ? <>{" · "}<b className="text-[#b45f0a]">{down}</b> down, showing recorded</> : null}.
      {" "}A tile says LIVE only while frames arrive.
    </>;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-black/10 bg-white px-4 py-2.5 text-[12.5px] text-muted shadow-sm">
      <span className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">
        <Radio className={`h-3.5 w-3.5 ${receiving ? "text-crimson" : "text-faint"}`} /> Live sources
      </span>
      <span className="min-w-0 flex-1">{body}</span>
      {ids.length > 0 && !active && (
        <button onClick={onShow} className="rounded-full bg-ink px-3 py-1 text-[12px] font-semibold text-white">Live only →</button>
      )}
    </div>
  );
}

// Greyed hints in an empty field, never stored or played: example.in is a reserved
// documentation domain and 192.168.x is a private address.
const URL_HINT: Record<StreamKind, string> = {
  hls: "https://nvr.example.in/live/cam1/index.m3u8",
  "webrtc-whep": "https://media.example.in:8889/cam1/whep",
  mjpeg: "http://192.168.1.64/video.mjpg",
  mp4: "https://gateway.example.in/api/stream.mp4?src=cam1",
  youtube: "https://www.youtube.com/watch?v=…",
  device: "",
};

const istStamp = (iso?: string | null) =>
  iso ? `${new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })} IST` : "";

/** The camera's live source: read-only for everyone, editable by a supervisor. The
 *  server validates and stores it; this form only relays its answer, field by field. */
function LiveSourcePanel({ cam, live, streams }: { cam: Cam; live?: LiveSource; streams: StreamsView }) {
  const [kind, setKind] = useState<StreamKind>(live?.kind ?? "hls");
  const [url, setUrl] = useState(live?.url ?? "");
  const [label, setLabel] = useState(live?.label ?? "");
  const [attribution, setAttribution] = useState(live?.attribution ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const info = streams.kinds.find((k) => k.kind === kind);
  const needsUrl = info ? info.needs_url : kind !== "device";
  const mixed = needsUrl && typeof window !== "undefined" && window.location.protocol === "https:" && /^http:\/\//i.test(url.trim());
  const history = (streams.data?.history || []).filter((h) => h.camera_id === cam.camera_id).slice(0, 3);
  const ignored = (streams.data?.ignored || []).filter((i) => i.camera_id === cam.camera_id);

  const run = async (op: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setErrors({});
    setMsg(null);
    try {
      await op();
      setMsg({ ok: true, text: done });
    } catch (e) {
      if (e instanceof StreamSaveError) {
        setErrors(e.errors);
        const onField = ["kind", "url", "label", "attribution"].some((f) => e.errors[f]);
        if (!onField) setMsg({ ok: false, text: e.message });
      } else setMsg({ ok: false, text: "Could not save the change." });
    } finally {
      setBusy(false);
    }
  };
  const save = () => run(async () => {
    const d = await saveStream(cam.camera_id, { kind, url: url.trim(), label: label.trim(), attribution: attribution.trim() });
    const s = d.streams[cam.camera_id];
    if (s?.url) setUrl(s.url);   // a YouTube link comes back as its canonical watch URL
  }, "Saved. Every signed-in officer's wall now plays this source.");
  const remove = () => run(async () => {
    await removeStream(cam.camera_id);
    setUrl("");
    setLabel("");
    setAttribution("");
  }, "Removed. This camera shows its recorded clip again.");

  const field = "mt-1 w-full rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[12.5px] focus:border-brand/40 focus:outline-none";
  const cap = "text-[10.5px] font-semibold uppercase tracking-[0.12em] text-faint";

  return (
    <section className="mt-4 rounded-xl border border-black/10 p-3 text-[12px]">
      <h3 className="flex items-center gap-1.5 text-[13px] font-bold text-ink"><Radio className="h-4 w-4 text-crimson" /> Live source</h3>
      {streams.locked ? (
        <p className="mt-1.5 text-muted">
          Shown to investigators and above, because a live stream can show people. <Link href="/?next=%2Fsurveillance" className="font-semibold text-brand">Sign in →</Link>
        </p>
      ) : streams.status === "error" ? (
        <p className="mt-1.5 text-crimson">Live sources could not be loaded.</p>
      ) : streams.status !== "ready" ? (
        <p className="mt-1.5 text-muted">Loading…</p>
      ) : (
        <>
          {live ? (
            <div className="mt-1.5 space-y-0.5">
              <div className="text-ink"><b>{KIND_LABEL[live.kind]}</b>{live.label ? ` · ${live.label}` : ""}</div>
              {live.url && <div className="truncate font-mono text-[11px] text-muted" title={live.url}>{live.url}</div>}
              {live.attribution && <div className="text-muted">Credit: {live.attribution}</div>}
              <div className="text-[11px] text-faint">
                {live.origin === "deployment"
                  ? "Set by the deployment (CAMERA_STREAMS) — change it there."
                  : `Attached by ${live.added_by || "unknown"}${live.added_at ? ` · ${istStamp(live.added_at)}` : ""}.`}
                {live.overrides_default ? " Overrides the deployment's source for this camera; removing it restores that one." : ""}
              </div>
            </div>
          ) : (
            <p className="mt-1.5 text-muted">None attached — this camera shows a recorded clip.</p>
          )}
          {ignored.map((i, n) => (
            <p key={n} className="mt-1.5 text-[11.5px] text-[#b45f0a]">A {i.origin} source for this camera is not being served: {i.reason}</p>
          ))}

          {streams.canEdit ? (
            <div className="mt-3 space-y-2 border-t border-black/[0.06] pt-3">
              <label className="block">
                <span className={cap}>Kind</span>
                <select value={kind} onChange={(e) => { setKind(e.target.value as StreamKind); setErrors({}); }} className={field}>
                  {streams.kinds.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
                </select>
                {info && <span className="mt-1 block text-[11px] leading-snug text-muted">{info.hint}</span>}
                {errors.kind && <span className="mt-1 block text-[11.5px] text-crimson">{errors.kind}</span>}
              </label>
              {needsUrl && (
                <label className="block">
                  <span className={cap}>Stream URL</span>
                  <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={URL_HINT[kind]} spellCheck={false} autoComplete="off"
                         className={`${field} font-mono`} />
                  {errors.url && <span className="mt-1 block text-[11.5px] text-crimson">{errors.url}</span>}
                  {mixed && !errors.url && (
                    <span className="mt-1 block text-[11px] leading-snug text-[#b45f0a]">
                      This page is served over https, and browsers block http:// media on it. A LAN http source plays only where IntelHub itself is opened over http on that network.
                    </span>
                  )}
                </label>
              )}
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="block">
                  <span className={cap}>Label</span>
                  <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} placeholder="e.g. Gate 3 NVR" className={field} />
                  {errors.label && <span className="mt-1 block text-[11.5px] text-crimson">{errors.label}</span>}
                </label>
                <label className="block">
                  <span className={cap}>Attribution</span>
                  <input value={attribution} onChange={(e) => setAttribution(e.target.value)} maxLength={200} placeholder="Who operates or owns the stream" className={field} />
                  {errors.attribution && <span className="mt-1 block text-[11.5px] text-crimson">{errors.attribution}</span>}
                </label>
              </div>
              <div className="flex flex-wrap items-center gap-2 pt-0.5">
                <button disabled={busy} onClick={save} className="rounded-lg bg-ink px-3 py-1.5 text-[12.5px] font-semibold text-white disabled:opacity-50">
                  {busy ? "Saving…" : live?.origin === "operator" ? "Save changes" : "Attach source"}
                </button>
                {live?.origin === "operator" && (
                  <button disabled={busy} onClick={remove} className="rounded-lg border border-black/10 px-3 py-1.5 text-[12.5px] font-semibold text-crimson disabled:opacity-50">Remove</button>
                )}
              </div>
              {msg && <p className={`text-[11.5px] ${msg.ok ? "text-emerald" : "text-crimson"}`}>{msg.text}</p>}
              {history.length > 0 && (
                <ul className="space-y-0.5 text-[11px] text-faint">
                  {history.map((h, n) => (
                    <li key={n} className="truncate" title={h.url || undefined}>
                      {h.action === "remove" ? "Removed" : `Set ${h.kind ? KIND_LABEL[h.kind as StreamKind] || h.kind : ""}`} by {h.by} · {istStamp(h.at)}
                    </li>
                  ))}
                </ul>
              )}
              {streams.data?.storage && <p className="text-[10.5px] leading-snug text-faint">{streams.data.storage.note}</p>}
            </div>
          ) : (
            <p className="mt-2 text-[11.5px] text-faint">Only a supervisor can attach or change a live source.</p>
          )}
        </>
      )}
    </section>
  );
}

function FootageNote() {
  return (
    <details className="mt-4 rounded-2xl border border-black/10 bg-white px-4 py-3 text-[12px] text-muted shadow-sm">
      <summary className="cursor-pointer font-semibold text-ink">About these feeds</summary>
      <p className="mt-2 leading-relaxed">
        KSP&apos;s own Safe City and traffic-police feeds are not public, so a camera plays live only when a supervisor has
        attached a live stream to it — an NVR or camera gateway&apos;s HLS or WebRTC (WHEP) output, an MJPEG camera, a
        YouTube Live broadcast of a public place, or the viewing device&apos;s own camera. Such a tile is badged LIVE only while
        frames are actually arriving; when the link fails it says so and falls back to its recording. No analytics run on
        a live picture.
      </p>
      <p className="mt-2 leading-relaxed">
        Every other feed is badged RECORDED and plays real street footage recorded in Bengaluru in 2024–25 and published
        on Wikimedia Commons, standing in for the camera&apos;s stream; where a camera is named after a place we have footage
        of (Brigade Road, MG Road, Hebbal, Banashankari, Shivajinagar, Mekhri Circle), it shows that place. The boxes are
        real detections from YOLO11s with ByteTrack tracking, computed on that footage. Face and plate matches come from
        the matcher and are never drawn on anyone in the picture — nobody filmed here is a registry person.
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
