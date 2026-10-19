"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Box, Layers as LayersIcon, Satellite, Moon, Sun, Flame, Hexagon, MapPin, Radar, Cctv, BellRing,
  Expand, Shrink, Play, Pause, Search, X, Clapperboard, Footprints, Crosshair, TrendingUp, Clock, Building2,
  PanelRightClose, PanelRightOpen,
} from "lucide-react";
import { getJSON, ApiError } from "@/lib/api";
import { useNow } from "@/lib/alerts";
import type { Camera, Incident, Layers, MapMode, MapTheme, TrailPoint, ViewStats } from "./map-deck";

// deck.gl and MapLibre touch `window` at import.
const DeckMap = dynamic(() => import("./map-deck"), { ssr: false });

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";

const MODES: { id: MapMode; label: string; icon: any }[] = [
  { id: "3d", label: "3D", icon: Box },
  { id: "2d", label: "Heatmap", icon: Flame },
  { id: "districts", label: "Districts", icon: LayersIcon },
  { id: "satellite", label: "Satellite", icon: Satellite },
];

// Old links still say mode=night; that view is now the dark 3D map.
const parseMode = (m: string | null): MapMode =>
  m === "2d" || m === "districts" || m === "satellite" ? m : "3d";

const THEME_KEY = "ksp.map.theme";
const MAP_H = "max(520px, min(74vh, 780px))";

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
};

const LEGEND = {
  hex: "linear-gradient(90deg,#0198bd,#49e3ce,#d8feb5,#feedb1,#fead54,#d1374e)",
  heat: "linear-gradient(90deg,#225ea8,#1d91c0,#41b6c4,#fed976,#fd8d3c,#e31a1c)",
  dark: "linear-gradient(90deg,#162850,#1e5a96,#2896be,#6ecdb4,#f0e178)",
  light: "linear-gradient(90deg,#e8eefc,#9db8f2,#5b86e5,#3457c4,#1c2f86)",
};

const fmtHour = (h: number) => `${String(h).padStart(2, "0")}:00`;

function MapInner() {
  const params = useSearchParams();
  const router = useRouter();

  const [mode, setMode] = useState<MapMode>(parseMode(params.get("mode")));
  const [theme, setTheme] = useState<MapTheme>("dark");
  const [layers, setLayers] = useState<Layers>(PRESETS[parseMode(params.get("mode"))]);
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
  const [panelOpen, setPanelOpen] = useState(true);
  const [refit, setRefit] = useState(0);
  const [layersOpen, setLayersOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const key = useRef(0);
  const workspace = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    try {
      const t = localStorage.getItem(THEME_KEY);
      if (t === "dark" || t === "light") setTheme(t);
    } catch {}
  }, []);
  const toggleTheme = () => setTheme((t) => {
    const n = t === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, n); } catch {}
    return n;
  });

  // ------------------------------------------------------------------ full screen
  // The map and its panel go full screen together through the Fullscreen API. `full`
  // follows the browser's own state, so Esc, the exit button and F all agree. Where the
  // API is missing (iPhone Safari) the same layout is shown as an in-page overlay.
  const enterFull = useCallback(async () => {
    setFull(true);
    const el = workspace.current;
    if (el && document.fullscreenEnabled && !document.fullscreenElement) {
      try { await el.requestFullscreen({ navigationUI: "hide" }); } catch { /* the overlay still covers the page */ }
    }
  }, []);
  const exitFull = useCallback(() => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    setFull(false);
  }, []);

  useEffect(() => {
    const onChange = () => {
      if (!document.fullscreenElement) setFull(false);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t?.closest?.("input, textarea, select, [contenteditable=true]")) return;
      // Browsers usually swallow Esc to leave native full screen themselves (and we hear
      // it through fullscreenchange); when the key does reach the page, leave anyway.
      if (e.key === "Escape" && full) exitFull();
      if ((e.key === "f" || e.key === "F") && !e.metaKey && !e.ctrlKey && !e.altKey) (full ? exitFull : enterFull)();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full, enterFull, exitFull]);

  // The stage changes size on entering or leaving full screen (and when the panel is
  // hidden); frame the whole state again for whoever is watching.
  const stageKey = `${full}-${panelOpen}`;
  const lastStage = useRef(stageKey);
  useEffect(() => {
    if (lastStage.current === stageKey) return;
    lastStage.current = stageKey;
    setRefit((r) => r + 1);
  }, [stageKey]);

  // The page behind the overlay must not scroll.
  useEffect(() => {
    if (!full) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [full]);

  const L = (k: keyof Layers, label: string, Icon: any) => (
    <button
      key={k}
      onClick={() => setLayers((l) => ({ ...l, [k]: !l[k] }))}
      className={`flex items-center gap-2 rounded-xl px-2.5 py-1.5 text-[12px] font-medium transition-all ${
        layers[k] ? "bg-[#b07d16] text-white"
          : theme === "dark" || mode === "satellite" ? "bg-white/[0.06] text-white/60 hover:text-white" : "bg-black/[0.04] text-muted hover:text-ink"
      }`}
    >
      <Icon className="h-3.5 w-3.5" /> {label}
    </button>
  );

  const recentAlerts = alerts.filter((a) => !district || a.district === district).slice(0, 5);

  const dark = theme === "dark" || mode === "satellite";
  const glass = dark ? "border-white/10 bg-[#0d1220]/85 text-white" : "border-black/10 bg-white/95 text-ink";
  const legend = mode === "districts" ? LEGEND[dark ? "dark" : "light"] : layers.hex && !layers.heat ? LEGEND.hex : LEGEND.heat;

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------------ header */}
      <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-brand">
            <span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-crimson opacity-60" /><span className="relative inline-flex h-2 w-2 rounded-full bg-crimson" /></span>
            Live crime intelligence · Karnataka
          </div>
          <h1 className="text-display mt-1 text-4xl font-bold tracking-tight text-ink">Crime Map</h1>
          <p className="mt-1 text-[14px] text-muted">
            {raw.length ? `${raw.length.toLocaleString()} FIRs` : "Loading FIRs"} across all 31 districts, {cameras.length || "…"} cameras, live
            face and plate alerts — in 3D, as a heatmap, by district or from the air.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => { switchMode("3d"); setTour((t) => t + 1); }}
            className="flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] font-semibold text-ink shadow-sm hover:border-brand/40"
            title="Fly the busiest districts in 3D"
          >
            <Clapperboard className="h-4 w-4 text-brand" /> Tour
          </button>
          <button onClick={enterFull}
                  className="flex items-center gap-1.5 rounded-full bg-ink px-4 py-2 text-[12.5px] font-semibold text-white shadow-sm hover:bg-black"
                  title="Present the map full screen (F)">
            <Expand className="h-4 w-4" /> Full screen
          </button>
        </div>
      </header>

      <div
        ref={workspace}
        style={{ "--map-h": MAP_H } as React.CSSProperties}
        className={full ? "fixed inset-0 z-[200] flex gap-3 bg-[#070a12] p-3" : "grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]"}
      >
        {/* ------------------------------------------------------------ map */}
        <div className={`relative min-w-0 overflow-hidden border shadow-[0_20px_60px_-30px_rgba(17,20,32,0.35)] ${
               full ? "flex-1 rounded-[1.25rem] border-white/10" : "h-[var(--map-h)] rounded-[1.6rem] border-black/10"}`}>
          <DeckMap
            mode={mode}
            theme={theme}
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
            refit={refit}
            onSelectDistrict={selectDistrict}
            onOpenCase={onOpenCase}
            onOpenCamera={onOpenCamera}
            onViewStats={setView}
          />

          {/* top-right: view switcher, theme, full screen */}
          <div className="pointer-events-none absolute inset-x-3 top-3 flex flex-wrap items-start justify-end gap-2 sm:left-auto">
            <div className={`pointer-events-auto flex max-w-full overflow-x-auto rounded-2xl border p-1 shadow-lg backdrop-blur no-scrollbar ${glass}`}>
              {MODES.map((m) => (
                <button
                  key={m.id}
                  onClick={() => switchMode(m.id)}
                  className={`flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-1.5 text-[12.5px] font-semibold transition-all ${
                    mode === m.id ? "bg-[#b07d16] text-white shadow" : dark ? "text-white/70 hover:text-white" : "text-muted hover:text-ink"
                  }`}
                >
                  <m.icon className="h-3.5 w-3.5" /> {m.label}
                </button>
              ))}
            </div>
            {mode !== "satellite" && (
              <button onClick={toggleTheme} title={theme === "dark" ? "Light map" : "Dark map"}
                      className={`pointer-events-auto grid h-[42px] w-[42px] place-items-center rounded-2xl border shadow-lg backdrop-blur ${glass}`}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
            )}
            {full ? (
              <>
                <button onClick={() => { switchMode("3d"); setTour((t) => t + 1); }} title="Fly the busiest districts in 3D"
                        className={`pointer-events-auto flex h-[42px] items-center gap-1.5 rounded-2xl border px-3 text-[12.5px] font-semibold shadow-lg backdrop-blur ${glass}`}>
                  <Clapperboard className="h-4 w-4 text-[#f5be50]" /> Tour
                </button>
                <button onClick={() => setPanelOpen((o) => !o)} title={panelOpen ? "Hide the insight panel" : "Show the insight panel"}
                        className={`pointer-events-auto flex h-[42px] items-center gap-1.5 rounded-2xl border px-3 text-[12.5px] font-semibold shadow-lg backdrop-blur ${glass}`}>
                  {panelOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />} {panelOpen ? "Hide panel" : "Insights"}
                </button>
                <button onClick={exitFull} title="Exit full screen (Esc)"
                        className="pointer-events-auto flex h-[42px] items-center gap-2 rounded-2xl bg-crimson px-3.5 text-[12.5px] font-bold text-white shadow-lg hover:brightness-110">
                  <Shrink className="h-4 w-4" /> Exit full screen
                  <kbd className="rounded bg-white/20 px-1.5 py-px font-mono text-[10px] font-semibold">Esc</kbd>
                </button>
              </>
            ) : (
              <button onClick={enterFull} title="Full screen (F)"
                      className={`pointer-events-auto grid h-[42px] w-[42px] place-items-center rounded-2xl border shadow-lg backdrop-blur ${glass}`}>
                <Expand className="h-4 w-4" />
              </button>
            )}
          </div>

          {/* top-left: hotspot chip + search */}
          <div className="pointer-events-none absolute left-3 top-[68px] flex flex-col items-start gap-2 sm:top-3">
            <div className="pointer-events-auto flex items-center gap-2 rounded-2xl border border-white/10 bg-[#0d1220]/90 px-3 py-2 text-white shadow-lg backdrop-blur">
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
              className={`pointer-events-auto flex w-[min(78vw,300px)] items-center gap-2 rounded-2xl border px-3 py-2 shadow-lg backdrop-blur ${glass}`}
            >
              <Search className={`h-4 w-4 ${dark ? "text-white/45" : "text-faint"}`} />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="District, station, camera, FIR no…"
                     className={`min-w-0 flex-1 bg-transparent text-[13px] focus:outline-none ${dark ? "text-white placeholder:text-white/40" : "text-ink placeholder:text-faint"}`} />
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

          {/* layers + legend */}
          <div className="absolute bottom-3 left-3 flex flex-col items-start gap-2">
            {layersOpen && (
              <div className={`grid grid-cols-2 gap-1.5 rounded-2xl border p-2 shadow-xl backdrop-blur ${glass}`}>
                {L("heat", "Heatmap", Flame)}
                {L("hex", "3D columns", Hexagon)}
                {L("points", "FIRs", MapPin)}
                {L("zones", "DBSCAN zones", Radar)}
                {L("kde", "Predicted", Crosshair)}
                {L("cameras", "Cameras", Cctv)}
                {L("alerts", "Live alerts", BellRing)}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => setLayersOpen((o) => !o)}
                      className="flex items-center gap-2 rounded-2xl border border-white/10 bg-[#0d1220]/90 px-3 py-2 text-[12.5px] font-semibold text-white shadow-lg backdrop-blur">
                <LayersIcon className="h-4 w-4" /> Layers
              </button>
              <div className={`flex items-center gap-2 rounded-2xl border px-3 py-2 text-[11px] shadow-lg backdrop-blur ${glass}`}>
                {mode === "districts" ? (
                  <>
                    <span className="opacity-75">{metricInfo.label}</span>
                    <span className="h-2 w-24 rounded-full" style={{ background: legend }} />
                    <span className="opacity-75">{Math.round(rankMax).toLocaleString()}</span>
                  </>
                ) : (
                  <>
                    <span className="opacity-75">{layers.hex && !layers.heat ? "Density" : "Intensity"}</span>
                    <span className="h-2 w-24 rounded-full" style={{ background: legend }} />
                    <span className="hidden opacity-75 sm:inline">· <span className="text-[#965fff]">●</span> Face <span className="text-[#2f8cff]">●</span> ANPR <span className="text-[#e7b43c]">●</span> PTZ <span className="text-[#28b9a8]">●</span> CCTV</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {loading && (
            <div className={`absolute inset-0 grid place-items-center backdrop-blur-[2px] ${dark ? "bg-black/30" : "bg-white/40"}`}>
              <div className="rounded-2xl bg-white px-4 py-3 text-[13px] font-medium text-muted shadow-lg">Loading 18,000 FIRs…</div>
            </div>
          )}
        </div>

        {/* ------------------------------------------------------------ side panel */}
        {(!full || panelOpen) && (
        <aside className={full
          ? "w-[340px] shrink-0 space-y-3 overflow-y-auto overscroll-contain rounded-[1.25rem] max-md:absolute max-md:inset-y-3 max-md:right-3 max-md:z-20 max-md:w-[min(340px,86vw)]"
          : "space-y-3 lg:h-[var(--map-h)] lg:overflow-y-auto lg:overscroll-contain lg:pr-1"}>
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
        )}
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
