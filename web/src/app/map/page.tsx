"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Box, Map as MapIcon, Layers as LayersIcon, Satellite, Moon, Flame, Hexagon, MapPin, Radar, Cctv, BellRing,
  Expand, Shrink, Play, Pause, Search, X, Clapperboard, Footprints, Crosshair, TrendingUp, Clock, Building2,
} from "lucide-react";
import { getJSON, ApiError } from "@/lib/api";
import { useNow } from "@/lib/alerts";
import type { Camera, Incident, Layers, MapMode, TrailPoint, ViewStats } from "./map-libre";

// MapLibre touches `window` at import.
const MapLibreMap = dynamic(() => import("./map-libre"), { ssr: false });

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

const MODES: { id: MapMode; label: string; icon: any }[] = [
  { id: "3d", label: "3D city", icon: Box },
  { id: "2d", label: "2D", icon: MapIcon },
  { id: "districts", label: "Districts", icon: LayersIcon },
  { id: "satellite", label: "Satellite", icon: Satellite },
  { id: "night", label: "Night", icon: Moon },
];

const METRICS: { id: string; label: string; unit: string }[] = [
  { id: "cases", label: "Cases", unit: "cases" },
  { id: "per_100k", label: "Per 100k / yr", unit: "per 100k" },
  { id: "open", label: "Open cases", unit: "open" },
  { id: "fatalities", label: "Deaths", unit: "deaths" },
  { id: "last_30d", label: "Last 30 days", unit: "in 30d" },
  { id: "risk_index", label: "Risk index", unit: "risk" },
  { id: "cameras", label: "Cameras", unit: "cameras" },
];

// What each mode shows by default. The officer can still toggle any layer.
const PRESETS: Record<MapMode, Layers> = {
  "3d": { heat: false, hex: true, points: true, zones: false, kde: true, cameras: true, alerts: true },
  "2d": { heat: true, hex: false, points: true, zones: true, kde: true, cameras: true, alerts: true },
  districts: { heat: false, hex: false, points: false, zones: false, kde: false, cameras: false, alerts: false },
  satellite: { heat: true, hex: false, points: true, zones: false, kde: true, cameras: true, alerts: true },
  night: { heat: true, hex: true, points: true, zones: false, kde: true, cameras: true, alerts: true },
};

const fmtHour = (h: number) => `${String(h).padStart(2, "0")}:00`;

function MapInner() {
  const params = useSearchParams();
  const router = useRouter();

  const [mode, setMode] = useState<MapMode>((params.get("mode") as MapMode) || "3d");
  const [layers, setLayers] = useState<Layers>(PRESETS[((params.get("mode") as MapMode) || "3d")] || PRESETS["3d"]);
  const [raw, setRaw] = useState<Incident[]>([]);
  const [meta, setMeta] = useState<{ crime_types: string[]; districts: string[]; statuses: string[] }>({ crime_types: [], districts: [], statuses: [] });
  const [geo, setGeo] = useState<any>(null);
  const [dstats, setDstats] = useState<any[]>([]);
  const [heat, setHeat] = useState<{ kde: any[]; dbscan: any[] }>({ kde: [], dbscan: [] });
  const [trend, setTrend] = useState<any[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [alertsLocked, setAlertsLocked] = useState(false);
  const [trail, setTrail] = useState<TrailPoint[]>([]);
  const [tracking, setTracking] = useState<any>(null);

  const [district, setDistrict] = useState(params.get("district") || "");
  const [crime, setCrime] = useState(params.get("crime_type") || "");
  const [status, setStatus] = useState("");
  const [year, setYear] = useState<number | "">("");
  const [hours, setHours] = useState<[number, number]>(() => {
    const tod = (params.get("time_of_day") || "").toLowerCase();
    if (tod.includes("late night")) return [1, 4];
    if (tod.includes("morning")) return [7, 10];
    if (tod.includes("afternoon")) return [13, 16];
    if (tod.includes("evening")) return [17, 20];
    return [0, 23];
  });
  const [playing, setPlaying] = useState(false);
  const [metric, setMetric] = useState("cases");
  const [view, setView] = useState<ViewStats | null>(null);
  const [flyTo, setFlyTo] = useState<{ lat: number; lng: number; zoom?: number; key: number } | null>(null);
  const [fitDistrict, setFitDistrict] = useState<{ name: string; key: number } | null>(null);
  const [tour, setTour] = useState(0);
  const [search, setSearch] = useState("");
  const [full, setFull] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const key = useRef(0);

  const switchMode = (m: MapMode) => {
    setMode(m);
    setLayers(PRESETS[m]);
  };

  // ------------------------------------------------------------------ data
  useEffect(() => {
    getJSON("/map/incidents")
      .then((d) => {
        const C = d.columns as string[];
        const ix = (n: string) => C.indexOf(n);
        const rows: Incident[] = d.rows.map((r: any[]) => ({
          lat: r[ix("lat")], lng: r[ix("lng")], severity: r[ix("severity")],
          crime: d.crime_types[r[ix("crime")]], district: d.districts[r[ix("district")]],
          station: d.stations[r[ix("station")]], status: d.statuses[r[ix("status")]],
          hour: r[ix("hour")], year: r[ix("year")], month: r[ix("month")], fatalities: r[ix("fatalities")],
          case_id: r[ix("case_id")],
        }));
        setRaw(rows);
        setMeta({ crime_types: d.crime_types, districts: d.districts, statuses: d.statuses });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
    fetch(`${BASE}/geo/karnataka-districts.geojson`).then((r) => r.json()).then(setGeo).catch(() => {});
    getJSON("/map/districts").then((d) => setDstats(d.districts || [])).catch(() => {});
    getJSON("/stats").then((d) => setTrend(d.trend_alerts || [])).catch(() => {});
    getJSON("/surveillance/cameras").then((d) => setCameras(d.cameras || [])).catch(() => {});
  }, []);

  useEffect(() => {
    // KDE hotspots and DBSCAN zones, scoped to the district when one is chosen.
    getJSON(`/heatmap${district ? `?district=${encodeURIComponent(district)}` : ""}`)
      .then((d) => setHeat({ kde: d.clusters || [], dbscan: d.dbscan_clusters || [] }))
      .catch(() => {});
  }, [district]);

  // Live camera alerts (investigator). Polled so the map stays live.
  useEffect(() => {
    let stop = false;
    const tick = () =>
      getJSON("/surveillance/alerts?limit=60")
        .then((d) => !stop && setAlerts(d.alerts || []))
        .catch((e) => {
          if (e instanceof ApiError && e.isForbidden) setAlertsLocked(true);
        });
    tick();
    const t = setInterval(tick, 15000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  // Track a person: /map?track=SUS-1234
  useEffect(() => {
    const sid = params.get("track");
    if (!sid) return;
    getJSON(`/surveillance/track/${encodeURIComponent(sid)}`)
      .then((d) => {
        setTracking(d);
        const pts = (d.sightings || []).map((s: any) => ({ lat: s.lat, lng: s.lng, time: s.time, camera_name: s.camera_name,
                                                          similarity: s.face_match?.similarity }));
        setTrail(pts);
        if (pts.length) setFlyTo({ lat: pts[pts.length - 1].lat, lng: pts[pts.length - 1].lng, zoom: 12.5, key: ++key.current });
      })
      .catch(() => {});
  }, [params]);

  // Deep links: case_id flies to the incident.
  useEffect(() => {
    const cid = params.get("case_id");
    if (!cid || !raw.length) return;
    const hit = raw.find((r) => r.case_id === cid);
    if (hit) setFlyTo({ lat: hit.lat, lng: hit.lng, zoom: 15, key: ++key.current });
  }, [params, raw]);

  useEffect(() => {
    if (district) setFitDistrict({ name: district, key: ++key.current });
  }, [district]);

  // Deep link from the CCTV page: /map?camera=CAM-…
  useEffect(() => {
    const cid = params.get("camera");
    const cam = cid && cameras.find((c) => c.camera_id === cid);
    if (cam) setFlyTo({ lat: cam.lat, lng: cam.lng, zoom: 16.5, key: ++key.current });
  }, [params, cameras]);

  // 24-hour playback: a 3-hour window sweeping the clock.
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setHours(([a]) => {
      const n = (a + 1) % 24;
      return [n, Math.min(23, n + 2)];
    }), 1100);
    return () => clearInterval(t);
  }, [playing]);

  const filtered = useMemo(() => {
    const [h0, h1] = hours;
    return raw.filter((r) =>
      (!district || r.district === district) &&
      (!crime || r.crime === crime) &&
      (!status || r.status === status) &&
      (!year || r.year === year) &&
      (h0 === 0 && h1 === 23 ? true : r.hour >= h0 && r.hour <= h1));
  }, [raw, district, crime, status, year, hours]);

  // District values for the selected metric, honouring crime / year / status filters.
  const districtValues = useMemo(() => {
    const out: Record<string, number> = {};
    const byDistrict = Object.fromEntries(dstats.map((d) => [d.district, d]));
    const filteredByAll = crime || status || year || hours[0] !== 0 || hours[1] !== 23;
    if (["cases", "open", "fatalities", "per_100k"].includes(metric) && filteredByAll) {
      const rows = raw.filter((r) =>
        (!crime || r.crime === crime) && (!status || r.status === status) && (!year || r.year === year) &&
        (hours[0] === 0 && hours[1] === 23 ? true : r.hour >= hours[0] && r.hour <= hours[1]));
      for (const r of rows) {
        const v = metric === "open" ? (r.status === "Under Investigation" ? 1 : 0) : metric === "fatalities" ? r.fatalities : 1;
        out[r.district] = (out[r.district] || 0) + v;
      }
      if (metric === "per_100k")
        for (const d of Object.keys(out)) {
          const pop = byDistrict[d]?.population_lakh;
          out[d] = pop ? +(out[d] / (year ? 1 : 7) / pop).toFixed(1) : 0;
        }
      return out;
    }
    for (const d of dstats) out[d.district] = Number(d[metric] ?? 0);
    return out;
  }, [dstats, raw, metric, crime, status, year, hours]);

  const ranking = useMemo(() => Object.entries(districtValues).sort((a, b) => b[1] - a[1]), [districtValues]);
  const rankMax = ranking[0]?.[1] || 1;

  const nowMs = useNow(30000);
  const alertCameras = useMemo(() => {
    const m: Record<string, "P1" | "P2"> = {};
    const now = nowMs / 1000;
    for (const a of alerts) {
      if (now - a.ts > 3600 || a.status === "rejected") continue;
      if (a.priority === "P1" || !m[a.camera_id]) m[a.camera_id] = a.priority;
    }
    return m;
  }, [alerts, nowMs]);

  const visibleCams = useMemo(() => (district ? cameras.filter((c) => c.district === district) : cameras), [cameras, district]);
  const metricInfo = METRICS.find((m) => m.id === metric)!;

  // ------------------------------------------------------------------ actions
  const selectDistrict = useCallback((d: string) => setDistrict((cur) => (cur === d ? "" : d)), []);
  const onOpenCase = useCallback((id: string) => router.push(`/case?id=${encodeURIComponent(id)}`), [router]);
  const onOpenCamera = useCallback((id: string) => router.push(`/surveillance?camera=${encodeURIComponent(id)}`), [router]);

  const doSearch = (q: string) => {
    const s = q.trim().toLowerCase();
    if (!s) return;
    const d = meta.districts.find((x) => x.toLowerCase().includes(s)) ||
      (/(bangalore|bengaluru)/.test(s) ? "Bengaluru Urban" : /mysore/.test(s) ? "Mysuru" : /mangalore/.test(s) ? "Dakshina Kannada" : "");
    if (d) return setDistrict(d);
    const cam = cameras.find((c) => c.name.toLowerCase().includes(s) || c.area.toLowerCase().includes(s) || c.camera_id.toLowerCase() === s);
    if (cam) return setFlyTo({ lat: cam.lat, lng: cam.lng, zoom: 16, key: ++key.current });
    const inc = raw.find((r) => r.case_id === s || r.station.toLowerCase().includes(s));
    if (inc) return setFlyTo({ lat: inc.lat, lng: inc.lng, zoom: inc.case_id === s ? 16 : 13, key: ++key.current });
  };

  const toggleFull = () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {});
    else document.exitFullscreen?.().catch(() => {});
    setFull((f) => !f);
  };

  const L = (k: keyof Layers, label: string, Icon: any) => (
    <button
      key={k}
      onClick={() => setLayers((l) => ({ ...l, [k]: !l[k] }))}
      className={`flex items-center gap-2 rounded-xl px-2.5 py-1.5 text-[12px] font-medium transition-all ${
        layers[k] ? "bg-ink text-white" : "bg-black/[0.04] text-muted hover:text-ink"
      }`}
    >
      <Icon className="h-3.5 w-3.5" /> {label}
    </button>
  );

  const recentAlerts = alerts.filter((a) => !district || a.district === district).slice(0, 5);

  return (
    <div className={full ? "fixed inset-0 z-[60] overflow-auto bg-[#f4f4f6] p-4" : "space-y-4"}>
      {/* ------------------------------------------------------------ header */}
      {!full && (
        <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-brand">
              <span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-crimson opacity-60" /><span className="relative inline-flex h-2 w-2 rounded-full bg-crimson" /></span>
              Live crime intelligence · Karnataka
            </div>
            <h1 className="text-display mt-1 text-4xl font-bold tracking-tight text-ink">Crime Map</h1>
            <p className="mt-1 text-[14px] text-muted">
              {raw.length ? `${raw.length.toLocaleString()} FIRs` : "Loading FIRs"} across all 31 districts, {cameras.length || "…"} cameras, live
              face and plate alerts — in 3D, 2D, by district or from the air.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-full border border-black/10 bg-white p-1 shadow-sm">
              {MODES.map((m) => (
                <button
                  key={m.id}
                  onClick={() => switchMode(m.id)}
                  className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                    mode === m.id ? "bg-ink text-white shadow" : "text-muted hover:text-ink"
                  }`}
                >
                  <m.icon className="h-3.5 w-3.5" /> {m.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => { switchMode("3d"); setTour((t) => t + 1); }}
              className="flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] font-semibold text-ink shadow-sm hover:border-brand/40"
              title="Fly the busiest districts in 3D"
            >
              <Clapperboard className="h-4 w-4 text-brand" /> Tour
            </button>
            <button onClick={toggleFull} className="grid h-9 w-9 place-items-center rounded-full border border-black/10 bg-white shadow-sm hover:border-brand/40" title="Full screen">
              {full ? <Shrink className="h-4 w-4" /> : <Expand className="h-4 w-4" />}
            </button>
          </div>
        </header>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        {/* ------------------------------------------------------------ map */}
        <div className="relative overflow-hidden rounded-[1.6rem] border border-black/10 bg-[#dfe6ee] shadow-[0_20px_60px_-30px_rgba(17,20,32,0.35)]"
             style={{ height: full ? "calc(100vh - 2rem)" : "min(74vh, 780px)", minHeight: 520 }}>
          <MapLibreMap
            mode={mode}
            incidents={filtered}
            layers={layers}
            districtGeo={geo}
            districtValues={districtValues}
            districtMetricLabel={metricInfo.unit}
            selectedDistrict={district}
            dbscan={heat.dbscan}
            kde={heat.kde}
            trendAlerts={trend}
            cameras={visibleCams}
            alertCameras={alertCameras}
            trail={trail}
            flyTo={flyTo}
            fitDistrict={fitDistrict}
            tour={tour}
            onSelectDistrict={selectDistrict}
            onOpenCase={onOpenCase}
            onOpenCamera={onOpenCamera}
            onViewStats={setView}
          />

          {/* top-left: hotspot chip + search */}
          <div className="pointer-events-none absolute left-3 top-3 flex flex-col items-start gap-2">
            <div className="pointer-events-auto flex items-center gap-2 rounded-2xl bg-[#0d1220]/90 px-3 py-2 text-white shadow-lg backdrop-blur">
              <Flame className="h-4 w-4 text-[#f5b13d]" />
              <span className="text-[12.5px] font-semibold">Hotspot Intel</span>
              <span className="rounded-md bg-white/15 px-1.5 text-[11px] font-bold">{(view?.count ?? filtered.length).toLocaleString()}</span>
              {district && (
                <button onClick={() => setDistrict("")} className="ml-1 flex items-center gap-1 rounded-md bg-[#b07d16] px-1.5 text-[11px] font-semibold">
                  {district} <X className="h-3 w-3" />
                </button>
              )}
            </div>
            <form
              onSubmit={(e) => { e.preventDefault(); doSearch(search); }}
              className="pointer-events-auto flex w-[min(78vw,300px)] items-center gap-2 rounded-2xl border border-black/10 bg-white/95 px-3 py-2 shadow-lg backdrop-blur"
            >
              <Search className="h-4 w-4 text-faint" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="District, station, camera, FIR no…"
                     className="min-w-0 flex-1 bg-transparent text-[13px] text-ink placeholder:text-faint focus:outline-none" />
            </form>
            {tracking && (
              <div className="pointer-events-auto w-[min(78vw,300px)] rounded-2xl border border-crimson/25 bg-white/95 p-3 shadow-lg backdrop-blur">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-crimson"><Footprints className="h-3.5 w-3.5" /> Tracking</div>
                  <button onClick={() => { setTracking(null); setTrail([]); router.replace("/map"); }} className="text-faint hover:text-ink"><X className="h-4 w-4" /></button>
                </div>
                <div className="mt-1 text-[14px] font-bold text-ink">{tracking.person?.name} <span className="font-mono text-[12px] text-muted">{tracking.suspect_id}</span></div>
                <div className="text-[12px] text-muted">{trail.length} camera sighting(s) in 24h · numbered in order</div>
                <ol className="mt-2 max-h-36 space-y-1 overflow-auto text-[11.5px]">
                  {trail.map((t, i) => (
                    <li key={i}>
                      <button className="text-left hover:text-brand" onClick={() => setFlyTo({ lat: t.lat, lng: t.lng, zoom: 16, key: ++key.current })}>
                        <b>{i + 1}.</b> {new Date(t.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {t.camera_name}
                        {t.similarity ? ` · ${Math.round(t.similarity * 100)}%` : ""}
                      </button>
                    </li>
                  ))}
                </ol>
                <p className="mt-1.5 text-[10.5px] text-faint">Matcher output — a lead until verified.</p>
              </div>
            )}
          </div>

          {/* layers */}
          <div className="absolute bottom-3 left-3 flex flex-col items-start gap-2">
            {layersOpen && (
              <div className="grid grid-cols-2 gap-1.5 rounded-2xl border border-black/10 bg-white/95 p-2 shadow-xl backdrop-blur">
                {L("heat", "Heatmap", Flame)}
                {L("hex", "3D columns", Hexagon)}
                {L("points", "FIRs", MapPin)}
                {L("zones", "DBSCAN zones", Radar)}
                {L("kde", "Predicted", Crosshair)}
                {L("cameras", "Cameras", Cctv)}
                {L("alerts", "Live alerts", BellRing)}
              </div>
            )}
            <div className="flex items-center gap-2">
              <button onClick={() => setLayersOpen((o) => !o)}
                      className="flex items-center gap-2 rounded-2xl bg-[#0d1220]/90 px-3 py-2 text-[12.5px] font-semibold text-white shadow-lg backdrop-blur">
                <LayersIcon className="h-4 w-4" /> Layers
              </button>
              <div className="flex items-center gap-2 rounded-2xl bg-white/95 px-3 py-2 text-[11px] text-muted shadow-lg backdrop-blur">
                {mode === "districts" ? (
                  <>
                    <span>{metricInfo.label}</span>
                    <span className="h-2 w-24 rounded-full" style={{ background: "linear-gradient(90deg,#e8eefc,#9db8f2,#5b86e5,#3457c4,#1c2f86)" }} />
                    <span>{Math.round(rankMax).toLocaleString()}</span>
                  </>
                ) : (
                  <>
                    <span>Severity</span>
                    <span className="h-2 w-24 rounded-full" style={{ background: "linear-gradient(90deg,#0a9d57,#e7b43c,#f08a24,#dc2b25,#7a0c2e)" }} />
                    <span className="hidden sm:inline">· <span className="text-[#965fff]">●</span> Face <span className="text-[#2f8cff]">●</span> ANPR <span className="text-[#e7b43c]">●</span> PTZ</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {loading && (
            <div className="absolute inset-0 grid place-items-center bg-white/40 backdrop-blur-[2px]">
              <div className="rounded-2xl bg-white px-4 py-3 text-[13px] font-medium text-muted shadow-lg">Loading 18,000 FIRs…</div>
            </div>
          )}
        </div>

        {/* ------------------------------------------------------------ side panel */}
        <aside className="space-y-3 lg:max-h-[min(74vh,780px)] lg:overflow-y-auto lg:pr-1">
          {/* filters */}
          <section className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
            <h3 className="text-[12px] font-semibold uppercase tracking-[0.16em] text-faint">Filters</h3>
            <div className="mt-2.5 grid grid-cols-2 gap-2">
              <select value={district} onChange={(e) => setDistrict(e.target.value)} className="col-span-2 rounded-xl border border-black/10 bg-white px-3 py-2 text-[13px]">
                <option value="">All Karnataka · 31 districts</option>
                {meta.districts.map((d) => <option key={d}>{d}</option>)}
              </select>
              <select value={crime} onChange={(e) => setCrime(e.target.value)} className="rounded-xl border border-black/10 bg-white px-3 py-2 text-[13px]">
                <option value="">All crimes</option>
                {meta.crime_types.map((c) => <option key={c} value={c}>{c.split(" (")[0]}</option>)}
              </select>
              <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded-xl border border-black/10 bg-white px-3 py-2 text-[13px]">
                <option value="">Any status</option>
                {meta.statuses.map((c) => <option key={c}>{c}</option>)}
              </select>
              <select value={year} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : "")} className="col-span-2 rounded-xl border border-black/10 bg-white px-3 py-2 text-[13px]">
                <option value="">All years</option>
                {[2026, 2025, 2024, 2023, 2022, 2021, 2020].map((y) => <option key={y}>{y}</option>)}
              </select>
            </div>
            <div className="mt-3">
              <div className="flex items-center justify-between text-[12px]">
                <span className="flex items-center gap-1.5 font-medium text-ink"><Clock className="h-3.5 w-3.5 text-brand" /> Time of day</span>
                <span className="font-mono text-muted">{fmtHour(hours[0])}–{fmtHour(hours[1])}</span>
              </div>
              <div className="mt-1.5 flex items-center gap-2">
                <button onClick={() => setPlaying((p) => !p)} className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-ink text-white" title="Play the 24-hour cycle">
                  {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                </button>
                <input type="range" min={0} max={23} value={hours[0]} onChange={(e) => { setPlaying(false); setHours(([, b]) => [Math.min(Number(e.target.value), b), b]); }} className="w-full accent-[#b07d16]" />
                <input type="range" min={0} max={23} value={hours[1]} onChange={(e) => { setPlaying(false); setHours(([a]) => [a, Math.max(Number(e.target.value), a)]); }} className="w-full accent-[#b07d16]" />
              </div>
              {(hours[0] !== 0 || hours[1] !== 23) && (
                <button onClick={() => { setPlaying(false); setHours([0, 23]); }} className="mt-1 text-[11px] font-semibold text-brand">All day</button>
              )}
            </div>
          </section>

          {/* this view */}
          <section className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
            <h3 className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-[0.16em] text-faint"><TrendingUp className="h-3.5 w-3.5" /> This view</h3>
            <div className="mt-2.5 grid grid-cols-2 gap-2">
              <div className="rounded-xl bg-black/[0.03] p-2.5"><div className="text-[10px] uppercase tracking-wider text-faint">Concentration</div><div className="text-xl font-bold text-ink">{view?.concentration ?? 0}%</div></div>
              <div className="rounded-xl bg-black/[0.03] p-2.5"><div className="text-[10px] uppercase tracking-wider text-faint">Peak hour</div><div className="text-xl font-bold text-ink">{view?.peakHour != null ? fmtHour(view.peakHour) : "—"}</div></div>
              <div className="rounded-xl bg-black/[0.03] p-2.5"><div className="text-[10px] uppercase tracking-wider text-faint">Open</div><div className="text-xl font-bold text-ink">{view?.openPct ?? 0}%</div></div>
              <div className="rounded-xl bg-black/[0.03] p-2.5"><div className="text-[10px] uppercase tracking-wider text-faint">Cameras</div><div className="text-xl font-bold text-ink">{view?.cameras ?? 0}</div></div>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-muted">
              {view?.count
                ? <>The busiest {Math.min(4, view.topDistricts.length)} district(s) hold <b className="text-ink">{view.concentration}%</b> of the {view.count.toLocaleString()} incidents in view, led by <b className="text-ink">{view.heaviest}</b>. {view.peakHour != null && <>{fmtHour(view.peakHour)} is the busiest hour ({view.peakHourCount}).</>} Most common: {view.topCrime}.</>
                : "Pan or zoom the map to analyse an area."}
            </p>
          </section>

          {/* districts ranking */}
          <section className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-[0.16em] text-faint"><Building2 className="h-3.5 w-3.5" /> Districts by</h3>
              <select value={metric} onChange={(e) => { setMetric(e.target.value); if (mode !== "districts") switchMode("districts"); }}
                      className="rounded-lg border border-black/10 bg-white px-2 py-1 text-[12px] font-semibold text-ink">
                {METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </div>
            <ol className="mt-2 max-h-64 space-y-1 overflow-y-auto pr-1">
              {ranking.map(([d, v], i) => (
                <li key={d}>
                  <button onClick={() => selectDistrict(d)}
                          className={`grid w-full grid-cols-[18px_1fr_70px_auto] items-center gap-2 rounded-lg px-1.5 py-1 text-left text-[12.5px] transition-colors ${district === d ? "bg-brand/10" : "hover:bg-black/[0.03]"}`}>
                    <span className="text-[11px] text-faint">{i + 1}</span>
                    <span className="truncate font-medium text-ink">{d}</span>
                    <span className="h-1.5 rounded-full bg-black/[0.05]"><span className="block h-1.5 rounded-full bg-[#3457c4]" style={{ width: `${Math.max(3, (v / rankMax) * 100)}%` }} /></span>
                    <span className="w-12 text-right font-mono text-[11.5px] text-muted">{Math.round(v).toLocaleString()}</span>
                  </button>
                </li>
              ))}
            </ol>
          </section>

          {/* emerging hotspots */}
          <section className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
            <h3 className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-[0.16em] text-faint"><Flame className="h-3.5 w-3.5 text-crimson" /> Emerging hotspots</h3>
            <div className="mt-2 space-y-1.5">
              {trend.length === 0 && <p className="text-[12px] text-muted">No category is spiking above its baseline.</p>}
              {trend.slice(0, 5).map((a: any, i: number) => (
                <button key={i} onClick={() => { setDistrict(a.district); setCrime(a.crime_type); }}
                        className="flex w-full items-center justify-between rounded-xl border border-crimson/15 bg-crimson/[0.04] px-3 py-2 text-left hover:border-crimson/35">
                  <span>
                    <span className="block text-[12.5px] font-semibold text-ink">{a.crime_type.split(" (")[0]} · {a.district}</span>
                    <span className="text-[11px] text-muted">{a.recent_count} in 30 days vs ~{a.expected_count} expected</span>
                  </span>
                  <span className="rounded-md bg-crimson/15 px-1.5 py-0.5 text-[11px] font-bold text-crimson">{a.spike_ratio}×</span>
                </button>
              ))}
            </div>
          </section>

          {/* live camera alerts */}
          <section className="rounded-2xl border border-black/10 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <h3 className="flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-[0.16em] text-faint"><Cctv className="h-3.5 w-3.5 text-[#965fff]" /> Live camera alerts</h3>
              <button onClick={() => router.push(`/surveillance${district ? `?district=${encodeURIComponent(district)}` : ""}`)} className="text-[11px] font-semibold text-brand">Open command →</button>
            </div>
            {alertsLocked ? (
              <p className="mt-2 text-[12px] text-muted">Sign in as an investigator to see face and plate matches.</p>
            ) : (
              <div className="mt-2 space-y-1.5">
                {recentAlerts.length === 0 && <p className="text-[12px] text-muted">No alerts in this area.</p>}
                {recentAlerts.map((a) => (
                  <button key={a.event_id} onClick={() => setFlyTo({ lat: a.lat, lng: a.lng, zoom: 16, key: ++key.current })}
                          className="flex w-full items-start gap-2 rounded-xl bg-black/[0.03] px-2.5 py-2 text-left hover:bg-black/[0.06]">
                    <span className={`mt-0.5 rounded px-1 text-[10px] font-bold ${a.priority === "P1" ? "bg-crimson text-white" : "bg-[#f08a24]/20 text-[#b45f0a]"}`}>{a.priority}</span>
                    <span className="min-w-0 text-[12px]">
                      <span className="block truncate font-semibold text-ink">
                        {a.face_match ? `${a.face_match.person?.name} · ${Math.round(a.face_match.similarity * 100)}%` : `Plate ${a.plate_read}`}
                      </span>
                      <span className="block truncate text-[11px] text-muted">{a.camera_name} · {new Date(a.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>

      {/* ------------------------------------------------------------ KPI strip */}
      {!full && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { k: "Incidents in view", v: (view?.count ?? 0).toLocaleString(), s: `${filtered.length.toLocaleString()} after filters` },
            { k: "Busiest window", v: view?.busiestWindow || "—", s: "weight patrol cover toward it" },
            { k: "Heaviest district", v: view?.heaviest || "—", s: view?.topDistricts?.[0] ? `${view.topDistricts[0][1].toLocaleString()} incidents in view` : "" },
            { k: "Live alerts (1h)", v: Object.keys(alertCameras).length.toString(), s: "cameras with a P1/P2 match" },
          ].map((c) => (
            <div key={c.k} className="rounded-2xl border border-black/10 border-t-[3px] border-t-[#3457c4] bg-white p-4 shadow-sm">
              <div className="text-[10.5px] font-semibold uppercase tracking-[0.16em] text-faint">{c.k}</div>
              <div className="mt-1 text-2xl font-bold text-ink">{c.v}</div>
              <div className="text-[12px] text-muted">{c.s}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function MapPage() {
  return (
    <Suspense fallback={null}>
      <MapInner />
    </Suspense>
  );
}
