"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import {
  Siren,
  MapPin,
  Crosshair,
  MousePointerClick,
  MapPinned,
  Expand,
  Shrink,
} from "lucide-react";
import { getJSON } from "@/lib/api";
// MapCanvas chooses between the Google and Leaflet engines and loads whichever it
// picks through next/dynamic with ssr:false — both reach for `window` at import, so
// neither is pulled in here.
import MapCanvas, { MARKER_ZOOM, type MapPoint } from "./map-canvas";

function MapInner() {
  const params = useSearchParams();
  const router = useRouter();

  const [rawPoints, setRawPoints] = useState<MapPoint[]>([]);
  const [visiblePoints, setVisiblePoints] = useState<MapPoint[]>([]);
  const [clusters, setClusters] = useState<any[]>([]);
  const [dbscan, setDbscan] = useState<any[]>([]);
  const [dispatch, setDispatch] = useState<any[]>([]);
  const [showClusters, setShowClusters] = useState(true);
  const [showPatrol, setShowPatrol] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [districts, setDistricts] = useState<string[]>([]);
  const [timeBands, setTimeBands] = useState<string[]>([]);
  const [district, setDistrict] = useState<string>(params.get("district") || "");
  const [timeOfDay, setTimeOfDay] = useState<string>(params.get("time_of_day") || "");
  const [zoom, setZoom] = useState(7);
  const [isFullScreen, setIsFullScreen] = useState(false);

  const toggleFullScreen = useCallback(() => {
    if (!document.fullscreenElement && !isFullScreen) {
      document.documentElement.requestFullscreen().catch(() => {});
      document.documentElement.setAttribute("data-fullscreen", "true");
      setIsFullScreen(true);
    } else {
      if (document.exitFullscreen && document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
      document.documentElement.removeAttribute("data-fullscreen");
      setIsFullScreen(false);
    }
  }, [isFullScreen]);

  useEffect(() => {
    const onFsChange = () => {
      const fs = !!document.fullscreenElement;
      setIsFullScreen(fs);
      if (fs) document.documentElement.setAttribute("data-fullscreen", "true");
      else document.documentElement.removeAttribute("data-fullscreen");
    };
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  useEffect(() => {
    getJSON("/stats")
      .then((d) => {
        setDistricts(d.districts || []);
        setTimeBands(d.time_bands || []);
      })
      .catch(() => {});
    getJSON("/dispatch")
      .then((d) => setDispatch(d.districts || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchMapData = async () => {
      setIsLoading(true);
      try {
        const qs = new URLSearchParams();
        if (district) qs.set("district", district);
        if (timeOfDay) qs.set("time_of_day", timeOfDay);
        const data = await getJSON(`/heatmap?${qs.toString()}`);
        if (cancelled) return;
        setRawPoints(data.points || []);
        setClusters(data.clusters || []);
        setDbscan(data.dbscan_clusters || []);
      } catch (err) {
        console.error("heatmap", err);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    fetchMapData();
    return () => {
      cancelled = true;
    };
  }, [district, timeOfDay]);

  const onOpenCase = useCallback(
    (caseId: string) => router.push(`/case?id=${encodeURIComponent(caseId)}`),
    [router]
  );

  return (
    <div
      className={`flex flex-col space-y-4 ${
        isFullScreen ? "fixed inset-0 z-[9999] m-0 overflow-auto bg-[#f7f7f9] p-6" : "relative"
      }`}
    >
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
            <MapPin className="h-8 w-8 text-brand" strokeWidth={2} /> Intelligence Map
          </h1>
          <p className="mt-2 text-[15px] text-muted">
            KDE predicted hotspots<span className="text-brand"> (gold)</span>, DBSCAN cluster
            zones<span className="text-azure"> (blue)</span> and clickable FIRs across all 31
            districts.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <select
            value={district}
            onChange={(e) => setDistrict(e.target.value)}
            className="rounded-full border border-black/10 bg-white px-4 py-2 text-sm text-ink focus:border-brand/40 focus:outline-none"
          >
            <option value="">All Districts</option>
            {districts.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <select
            value={timeOfDay}
            onChange={(e) => setTimeOfDay(e.target.value)}
            className="rounded-full border border-black/10 bg-white px-4 py-2 text-sm text-ink focus:border-brand/40 focus:outline-none"
          >
            <option value="">All Times</option>
            {timeBands.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <button
            onClick={() => setShowClusters((s) => !s)}
            className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
              showClusters
                ? "border-azure/50 bg-azure/15 text-azure"
                : "border-black/10 bg-black/[0.03] text-muted hover:text-ink"
            }`}
          >
            <MapPinned className="h-4 w-4" /> Cluster zones
          </button>
          <button
            onClick={() => setShowPatrol((s) => !s)}
            className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition-all ${
              showPatrol
                ? "border-brand/50 bg-brand/15 text-brand"
                : "border-black/10 bg-black/[0.03] text-muted hover:text-ink"
            }`}
          >
            <Siren className="h-4 w-4" /> Patrol plan
          </button>
          <button
            onClick={toggleFullScreen}
            className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-4 py-2 text-sm font-medium transition-all hover:border-brand/40 hover:bg-brand/5 hover:text-brand"
          >
            {isFullScreen ? <Shrink className="h-4 w-4" /> : <Expand className="h-4 w-4" />}
            {isFullScreen ? "Exit Full Screen" : "Full Screen"}
          </button>
        </div>
      </header>

      <div className="bezel">
        <div
          className={`bezel-core relative overflow-hidden !rounded-[1.1rem] ${
            isFullScreen ? "h-[calc(100dvh-9rem)]" : "h-[calc(100dvh-19rem)] min-h-[460px]"
          }`}
        >
          <MapCanvas
            points={rawPoints}
            visiblePoints={visiblePoints}
            clusters={clusters}
            dbscan={dbscan}
            dispatch={dispatch}
            showClusters={showClusters}
            showPatrol={showPatrol}
            district={district}
            focusCaseId={params.get("case_id")}
            onZoomChange={setZoom}
            onViewportChange={setVisiblePoints}
            onOpenCase={onOpenCase}
            resizeKey={isFullScreen}
          />

          {/* System overlay — live context so the map reads as part of the platform */}
          <div className="pointer-events-none absolute left-4 top-4 z-[1000] rounded-2xl border border-black/10 bg-white/92 p-4 backdrop-blur-xl">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-faint">
              Live geospatial intel
            </p>
            <div className="mt-2 flex gap-5">
              <div>
                <p className="text-display tabular text-2xl font-bold text-ink">
                  {isLoading ? "…" : rawPoints.length.toLocaleString()}
                </p>
                <p className="text-[11px] text-muted">incidents</p>
              </div>
              <div>
                <p className="text-display tabular text-2xl font-bold text-brand">
                  {clusters.length}
                </p>
                <p className="text-[11px] text-muted">predicted</p>
              </div>
              <div>
                <p className="text-display tabular text-2xl font-bold text-azure">
                  {dbscan.length}
                </p>
                <p className="text-[11px] text-muted">zones</p>
              </div>
            </div>
            <div className="mt-3 flex items-center gap-1.5 text-[11px] text-brand">
              {zoom < MARKER_ZOOM ? (
                <>
                  <Crosshair className="h-3.5 w-3.5" /> Zoom in to reveal individual FIRs
                </>
              ) : (
                <>
                  <MousePointerClick className="h-3.5 w-3.5" /> Click a marker to open its FIR
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function MapPage() {
  return (
    <Suspense fallback={<div className="h-[60vh] animate-pulse rounded-xl bg-black/[0.03]" />}>
      <MapInner />
    </Suspense>
  );
}
