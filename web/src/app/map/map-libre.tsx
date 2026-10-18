"use client";

/**
 * The map engine: MapLibre GL over OpenFreeMap / CARTO vector tiles (no API key),
 * with Esri imagery for satellite. One engine draws every mode, so a red dot means the
 * same thing in 2D, 3D and on the district map.
 *
 * Style switches wipe custom layers, so every overlay is (re)installed by
 * `installOverlays` on each `style.load`, and data is pushed by `syncData` whenever
 * the inputs change. Both are idempotent.
 */

import { useEffect, useRef } from "react";
import maplibregl, { type Map as MLMap, type StyleSpecification, type GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

export type MapMode = "3d" | "2d" | "districts" | "satellite" | "night";

export interface Incident {
  lat: number;
  lng: number;
  severity: number;
  crime: string;
  district: string;
  station: string;
  status: string;
  hour: number;
  year: number;
  month: number;
  fatalities: number;
  case_id: string;
}

export interface Camera {
  camera_id: string;
  name: string;
  area: string;
  district: string;
  lat: number;
  lng: number;
  type: string;
  status: string;
  matches_last_hour?: number;
}

export interface Layers {
  heat: boolean;
  hex: boolean;
  points: boolean;
  zones: boolean;
  kde: boolean;
  cameras: boolean;
  alerts: boolean;
}

export interface ViewStats {
  count: number;
  peakHour: number | null;
  peakHourCount: number;
  topCrime: string | null;
  openPct: number;
  concentration: number;
  topDistricts: [string, number][];
  heaviest: string | null;
  cameras: number;
  busiestWindow: string | null;
}

export interface TrailPoint {
  lat: number;
  lng: number;
  time: string;
  camera_name: string;
  similarity?: number;
}

interface Props {
  mode: MapMode;
  incidents: Incident[];
  layers: Layers;
  districtGeo: any | null;
  districtValues: Record<string, number>;
  districtMetricLabel: string;
  selectedDistrict: string;
  dbscan: any[];
  kde: any[];
  trendAlerts: any[];
  cameras: Camera[];
  alertCameras: Record<string, "P1" | "P2">;
  trail: TrailPoint[];
  flyTo: { lat: number; lng: number; zoom?: number; key: number } | null;
  fitDistrict: { name: string; key: number } | null;
  tour: number;
  onSelectDistrict: (d: string) => void;
  onOpenCase: (id: string) => void;
  onOpenCamera: (id: string) => void;
  onViewStats: (s: ViewStats) => void;
}

export const KARNATAKA = { lng: 76.1, lat: 14.75 };
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";

const STYLES: Record<MapMode, string | StyleSpecification> = {
  "3d": "https://tiles.openfreemap.org/styles/liberty",
  "2d": "https://tiles.openfreemap.org/styles/positron",
  districts: "https://tiles.openfreemap.org/styles/positron",
  night: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  satellite: {
    version: 8,
    glyphs: GLYPHS,
    sources: {
      imagery: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 19,
        attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
      },
      labels: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 19,
      },
    },
    layers: [
      { id: "imagery", type: "raster", source: "imagery" },
      { id: "labels", type: "raster", source: "labels", paint: { "raster-opacity": 0.85 } },
    ],
  },
};

/** Raster fallback if a vector style cannot be fetched (blocked CDN, offline demo). */
const FALLBACK: StyleSpecification = {
  version: 8,
  glyphs: GLYPHS,
  sources: {
    carto: {
      type: "raster",
      tiles: ["a", "b", "c", "d"].map((s) => `https://${s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png`),
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [{ id: "carto", type: "raster", source: "carto" }],
};

export const SEV_RAMP = ["#0a9d57", "#e7b43c", "#f08a24", "#dc2b25", "#7a0c2e"];
export const CAM_COLORS: Record<string, string> = { FACE: "#965fff", ANPR: "#2f8cff", PTZ: "#e7b43c", CCTV: "#28b9a8" };

// --------------------------------------------------------------------------- geometry
const KM_LAT = 110.57;
const kmLng = (lat: number) => 111.32 * Math.cos((lat * Math.PI) / 180);

function circlePolygon(lat: number, lng: number, radiusKm: number, steps = 48) {
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 2;
    ring.push([lng + (radiusKm * Math.cos(a)) / kmLng(lat), lat + (radiusKm * Math.sin(a)) / KM_LAT]);
  }
  return ring;
}

/** Pointy-top hex binning in a local km plane. Returns extrusion-ready polygons. */
function hexbin(points: Incident[], sizeKm: number) {
  const lat0 = 14.5;
  const kx = kmLng(lat0);
  const bins = new Map<string, { q: number; r: number; n: number; sev: number; fat: number }>();
  for (const p of points) {
    const x = p.lng * kx;
    const y = p.lat * KM_LAT;
    const qf = ((Math.sqrt(3) / 3) * x - y / 3) / sizeKm;
    const rf = ((2 / 3) * y) / sizeKm;
    // cube rounding
    let q = Math.round(qf);
    let r = Math.round(rf);
    const s = Math.round(-qf - rf);
    const dq = Math.abs(q - qf), dr = Math.abs(r - rf), ds = Math.abs(s + qf + rf);
    if (dq > dr && dq > ds) q = -r - s;
    else if (dr > ds) r = -q - s;
    const key = `${q},${r}`;
    const b = bins.get(key) || { q, r, n: 0, sev: 0, fat: 0 };
    b.n += 1;
    b.sev += p.severity;
    b.fat += p.fatalities;
    bins.set(key, b);
  }
  let max = 1;
  bins.forEach((b) => (max = Math.max(max, b.n)));
  const features: any[] = [];
  bins.forEach((b) => {
    const cx = sizeKm * Math.sqrt(3) * (b.q + b.r / 2);
    const cy = sizeKm * 1.5 * b.r;
    const ring: [number, number][] = [];
    for (let i = 0; i <= 6; i++) {
      const a = ((60 * i - 30) * Math.PI) / 180;
      const px = cx + sizeKm * 0.92 * Math.cos(a);
      const py = cy + sizeKm * 0.92 * Math.sin(a);
      ring.push([px / kx, py / KM_LAT]);
    }
    features.push({
      type: "Feature",
      properties: { n: b.n, sev: b.sev / b.n, fat: b.fat, share: b.n / max },
      geometry: { type: "Polygon", coordinates: [ring] },
    });
  });
  return { type: "FeatureCollection", features, max };
}

function hexSizeFor(zoom: number) {
  return zoom < 8 ? 9 : zoom < 9.5 ? 4 : zoom < 11 ? 1.6 : zoom < 12.5 ? 0.7 : 0.3;
}

// --------------------------------------------------------------------------- component
export default function MapLibreMap(props: Props) {
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const propsRef = useRef(props);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const hexSizeRef = useRef<number>(0);
  const modeRef = useRef<MapMode>(props.mode);
  const hoverRef = useRef<string | null>(null);
  const dashRef = useRef<number>(0);
  // True between a style's `style.load` and the next setStyle: the only window in
  // which sources and layers may be touched.
  const readyRef = useRef(false);
  const fallbackRef = useRef(false);

  useEffect(() => {
    propsRef.current = props;
  });

  // ------------------------------------------------------------------ create once
  useEffect(() => {
    if (!el.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: el.current,
      style: STYLES[props.mode],
      center: [KARNATAKA.lng, KARNATAKA.lat],
      zoom: 6.4,
      pitch: props.mode === "3d" ? 55 : 0,
      bearing: props.mode === "3d" ? -12 : 0,
      maxPitch: 75,
      attributionControl: { compact: true },
      canvasContextAttributes: { antialias: true },
    } as any);
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: "metric" }), "bottom-right");

    map.on("error", (e: any) => {
      // A style that cannot be fetched leaves a blank canvas and no overlays; fall back
      // to raster tiles. Checked on every style request, not just the first.
      const msg = String(e?.error?.message || "");
      if (!readyRef.current && !fallbackRef.current && /style|Failed to fetch|NetworkError|404|AJAX/i.test(msg)) {
        fallbackRef.current = true;
        map.setStyle(FALLBACK);
      }
    });
    map.on("style.load", () => {
      readyRef.current = true;
      installOverlays(map);
      syncAll(map);
    });
    map.on("moveend", () => {
      if (!readyRef.current) return;
      maybeRebin(map);
      emitStats(map);
    });
    wireInteractions(map);

    const animate = () => {
      dashRef.current = (dashRef.current + 1) % 1000;
      if (readyRef.current && map.getLayer("trail-line")) {
        const step = Math.floor(dashRef.current / 3) % 14;
        const seq = [
          [0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0],
          [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5],
        ];
        map.setPaintProperty("trail-line", "line-dasharray", seq[step] as any);
      }
      raf = requestAnimationFrame(animate);
    };
    let raf = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(raf);
      markersRef.current.forEach((m) => m.remove());
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------ mode switches
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const prev = modeRef.current;
    modeRef.current = props.mode;
    if (STYLES[prev] !== STYLES[props.mode]) {
      readyRef.current = false;
      fallbackRef.current = false;
      map.setStyle(STYLES[props.mode] as any);
    } else if (readyRef.current) syncAll(map);
    if (props.mode === "3d") map.easeTo({ pitch: 58, bearing: -15, duration: 1200 });
    else if (props.mode === "districts") map.easeTo({ pitch: 35, bearing: 0, duration: 900 });
    else map.easeTo({ pitch: 0, bearing: 0, duration: 900 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.mode]);

  // ------------------------------------------------------------------ data changes
  useEffect(() => {
    const map = mapRef.current;
    if (map && readyRef.current) syncAll(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.incidents, props.layers, props.districtGeo, props.districtValues, props.selectedDistrict,
      props.dbscan, props.kde, props.trendAlerts, props.cameras, props.alertCameras, props.trail]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !props.flyTo) return;
    map.flyTo({ center: [props.flyTo.lng, props.flyTo.lat], zoom: props.flyTo.zoom ?? 13,
                pitch: modeRef.current === "3d" ? 60 : map.getPitch(), speed: 1.2, curve: 1.5, essential: true });
  }, [props.flyTo]);

  useEffect(() => {
    const map = mapRef.current;
    const geo = props.districtGeo;
    if (!map || !props.fitDistrict || !geo) return;
    const f = geo.features.find((x: any) => x.properties.district === props.fitDistrict!.name);
    if (!f) return;
    const b = new maplibregl.LngLatBounds();
    const walk = (c: any) => (typeof c[0] === "number" ? b.extend(c as [number, number]) : c.forEach(walk));
    walk(f.geometry.coordinates);
    map.fitBounds(b, { padding: 60, duration: 1400, pitch: modeRef.current === "3d" ? 50 : map.getPitch() });
  }, [props.fitDistrict, props.districtGeo]);

  // Cinematic tour: fly the busiest districts in 3D.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !props.tour) return;
    const geo = propsRef.current.districtGeo;
    const vals = propsRef.current.districtValues;
    const stops = Object.entries(vals).sort((a, b) => b[1] - a[1]).slice(0, 5)
      .map(([d]) => geo?.features.find((f: any) => f.properties.district === d))
      .filter(Boolean);
    let i = 0;
    let cancelled = false;
    const next = () => {
      if (cancelled || i >= stops.length) {
        if (!cancelled) map.flyTo({ center: [KARNATAKA.lng, KARNATAKA.lat], zoom: 6.4, pitch: 45, bearing: 0, duration: 3000 });
        return;
      }
      const p = stops[i++].properties;
      map.flyTo({ center: [p.label_lng, p.label_lat], zoom: 10.5, pitch: 62, bearing: -30 + i * 25, duration: 3200, essential: true });
      map.once("moveend", () => setTimeout(next, 1400));
    };
    next();
    return () => {
      cancelled = true;
    };
  }, [props.tour]);

  // ================================================================== internals
  function installOverlays(map: MLMap) {
    const add = (id: string, src: any) => {
      if (!map.getSource(id)) map.addSource(id, src);
    };
    const empty = { type: "FeatureCollection", features: [] };
    add("districts", { type: "geojson", data: empty, promoteId: "district" });
    add("district-labels", { type: "geojson", data: empty });
    add("zones", { type: "geojson", data: empty });
    add("hex", { type: "geojson", data: empty });
    add("incidents", { type: "geojson", data: empty });
    add("incidents-c", { type: "geojson", data: empty, cluster: true, clusterRadius: 46, clusterMaxZoom: 12,
                         clusterProperties: { sev: ["+", ["get", "sev"]] } });
    add("cameras", { type: "geojson", data: empty });
    add("trail", { type: "geojson", data: empty, lineMetrics: true });

    const has = (id: string) => !!map.getLayer(id);
    const styleLayers = map.getStyle().layers || [];
    const before = styleLayers.find((l) => l.type === "symbol")?.id;
    // Use fonts the active style's glyph server actually has; styles differ.
    const fonts: string[] = [];
    for (const l of styleLayers as any[]) {
      const f = l.layout?.["text-font"];
      if (Array.isArray(f) && typeof f[0] === "string") fonts.push(...f);
    }
    const bold = [fonts.find((f) => /bold/i.test(f)) || fonts[0] || "Noto Sans Bold"];
    const regular = [fonts.find((f) => /regular/i.test(f)) || fonts[0] || "Noto Sans Regular"];

    if (!has("district-fill"))
      map.addLayer({ id: "district-fill", type: "fill", source: "districts",
        paint: { "fill-color": ["interpolate", ["linear"], ["get", "t"], 0, "#e8eefc", 0.25, "#9db8f2", 0.5, "#5b86e5",
                                0.75, "#3457c4", 1, "#1c2f86"],
                 "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.92, 0.78] } }, before);
    if (!has("district-extrude"))
      map.addLayer({ id: "district-extrude", type: "fill-extrusion", source: "districts",
        paint: { "fill-extrusion-color": ["interpolate", ["linear"], ["get", "t"], 0, "#cfdcfb", 0.5, "#5b86e5", 1, "#1c2f86"],
                 "fill-extrusion-height": ["*", ["get", "t"], 60000], "fill-extrusion-opacity": 0.85 } });
    if (!has("district-line"))
      map.addLayer({ id: "district-line", type: "line", source: "districts",
        paint: { "line-color": ["case", ["==", ["get", "district"], ["get", "selected"]], "#b07d16", "#ffffff"],
                 "line-width": ["case", ["==", ["get", "district"], ["get", "selected"]], 3, 1.1], "line-opacity": 0.95 } });
    if (!has("zones-fill"))
      map.addLayer({ id: "zones-fill", type: "fill", source: "zones",
        paint: { "fill-color": "#2f74c0", "fill-opacity": 0.08 } });
    if (!has("zones-line"))
      map.addLayer({ id: "zones-line", type: "line", source: "zones",
        paint: { "line-color": "#2f74c0", "line-width": 1.6, "line-dasharray": [2, 2], "line-opacity": 0.8 } });
    if (!has("heat"))
      map.addLayer({ id: "heat", type: "heatmap", source: "incidents", maxzoom: 15,
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["get", "sev"], 0, 0.15, 100, 1],
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 5, 0.6, 9, 1.4, 13, 2.6],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 5, 6, 9, 16, 13, 26],
          "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(47,116,192,0)", 0.15, "rgba(47,140,255,0.45)",
                            0.35, "rgba(40,185,168,0.65)", 0.55, "rgba(231,180,60,0.8)", 0.75, "rgba(240,138,36,0.9)", 1, "rgba(220,43,37,0.95)"],
          "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0.85, 15, 0.15],
        } });
    if (!has("hex"))
      map.addLayer({ id: "hex", type: "fill-extrusion", source: "hex",
        paint: {
          // Colour and height both encode density, so the surface reads at a glance.
          "fill-extrusion-color": ["interpolate", ["linear"], ["get", "share"], 0, "#2f8cff", 0.12, "#28b9a8", 0.3, "#e7b43c",
                                   0.55, "#f08a24", 0.8, "#dc2b25", 1, "#7a0c2e"],
          "fill-extrusion-height": ["*", ["get", "share"], ["get", "hmax"]],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.82,
          "fill-extrusion-vertical-gradient": true,
        } });
    if (!has("clusters"))
      map.addLayer({ id: "clusters", type: "circle", source: "incidents-c", filter: ["has", "point_count"], minzoom: 7.5, maxzoom: 12.5,
        paint: {
          "circle-color": ["interpolate", ["linear"], ["/", ["get", "sev"], ["get", "point_count"]], 30, "#2f74c0", 50, "#e7b43c", 65, "#dc2b25"],
          "circle-radius": ["interpolate", ["linear"], ["get", "point_count"], 2, 11, 50, 20, 400, 32],
          "circle-opacity": 0.85, "circle-stroke-color": "#fff", "circle-stroke-width": 2,
        } });
    if (!has("cluster-count"))
      map.addLayer({ id: "cluster-count", type: "symbol", source: "incidents-c", filter: ["has", "point_count"], minzoom: 7.5, maxzoom: 12.5,
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-size": 11, "text-font": bold },
        paint: { "text-color": "#fff" } });
    if (!has("points"))
      map.addLayer({ id: "points", type: "circle", source: "incidents", minzoom: 11.5,
        paint: {
          "circle-color": ["step", ["get", "sev"], "#0a9d57", 25, "#2f74c0", 50, "#e7b43c", 75, "#dc2b25"],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 11.5, 3, 15, 7],
          "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.2, "circle-opacity": 0.95,
        } });
    if (!has("cameras"))
      map.addLayer({ id: "cameras", type: "circle", source: "cameras", minzoom: 8,
        paint: {
          "circle-color": ["match", ["get", "type"], "FACE", CAM_COLORS.FACE, "ANPR", CAM_COLORS.ANPR, "PTZ", CAM_COLORS.PTZ, CAM_COLORS.CCTV],
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3, 14, 7],
          "circle-stroke-color": ["match", ["get", "status"], "offline", "#dc2b25", "degraded", "#f08a24", "#ffffff"],
          "circle-stroke-width": 2, "circle-opacity": ["match", ["get", "status"], "offline", 0.45, 1],
        } });
    if (!has("camera-labels"))
      map.addLayer({ id: "camera-labels", type: "symbol", source: "cameras", minzoom: 13.5,
        layout: { "text-field": ["get", "name"], "text-size": 10.5, "text-offset": [0, 1.2], "text-anchor": "top",
                  "text-font": regular, "text-max-width": 9 },
        paint: { "text-color": "#1d1d1f", "text-halo-color": "#ffffff", "text-halo-width": 1.4 } });
    if (!has("trail-line"))
      map.addLayer({ id: "trail-line", type: "line", source: "trail", filter: ["==", ["geometry-type"], "LineString"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#dc2b25", "line-width": 4, "line-opacity": 0.9, "line-dasharray": [0, 4, 3] } });
    if (!has("trail-stops"))
      map.addLayer({ id: "trail-stops", type: "circle", source: "trail", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-color": "#dc2b25", "circle-radius": 7, "circle-stroke-color": "#fff", "circle-stroke-width": 2.5 } });
    if (!has("trail-n"))
      map.addLayer({ id: "trail-n", type: "symbol", source: "trail", filter: ["==", ["geometry-type"], "Point"],
        layout: { "text-field": ["to-string", ["get", "n"]], "text-size": 10, "text-font": bold, "text-allow-overlap": true },
        paint: { "text-color": "#fff" } });
    if (!has("district-label"))
      map.addLayer({ id: "district-label", type: "symbol", source: "district-labels",
        layout: { "text-field": ["format", ["get", "district"], { "font-scale": 0.95 }, "\n", {},
                                 ["get", "value"], { "font-scale": 0.8 }],
                  "text-size": 11.5, "text-font": bold, "text-max-width": 8 },
        paint: { "text-color": "#10183a", "text-halo-color": "rgba(255,255,255,0.9)", "text-halo-width": 1.6 } });
  }

  function vis(map: MLMap, id: string, on: boolean) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  }

  function syncAll(map: MLMap) {
    if (!map.getSource("incidents")) installOverlays(map);
    const p = propsRef.current;
    const mode = p.mode;
    const isDistricts = mode === "districts";
    const pts = {
      type: "FeatureCollection",
      features: p.incidents.map((i) => ({
        type: "Feature",
        properties: { sev: i.severity, crime: i.crime, case_id: i.case_id, district: i.district, station: i.station,
                      status: i.status, hour: i.hour, year: i.year },
        geometry: { type: "Point", coordinates: [i.lng, i.lat] },
      })),
    };
    (map.getSource("incidents") as GeoJSONSource).setData(pts as any);
    (map.getSource("incidents-c") as GeoJSONSource).setData(pts as any);

    // Districts: the selected metric, normalised for colour and height.
    const geo = p.districtGeo;
    if (geo) {
      const vals = Object.values(p.districtValues);
      const max = Math.max(1, ...vals);
      const min = Math.min(0, ...vals);
      const feats = geo.features.map((f: any) => {
        const v = p.districtValues[f.properties.district] ?? 0;
        return { ...f, properties: { ...f.properties, v, t: (v - min) / (max - min || 1), selected: p.selectedDistrict || "" } };
      });
      (map.getSource("districts") as GeoJSONSource).setData({ type: "FeatureCollection", features: feats } as any);
      (map.getSource("district-labels") as GeoJSONSource).setData({
        type: "FeatureCollection",
        features: geo.features.map((f: any) => ({
          type: "Feature",
          properties: { district: f.properties.district,
                        value: `${Math.round(p.districtValues[f.properties.district] ?? 0).toLocaleString()} ${p.districtMetricLabel}` },
          geometry: { type: "Point", coordinates: [f.properties.label_lng, f.properties.label_lat] },
        })),
      } as any);
    }

    (map.getSource("zones") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: (p.dbscan || []).map((c: any) => ({
        type: "Feature", properties: { ...c },
        geometry: { type: "Polygon", coordinates: [circlePolygon(c.lat, c.lng, Math.min(25, c.radius_km || 2))] },
      })),
    } as any);

    (map.getSource("cameras") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: p.cameras.map((c) => ({ type: "Feature", properties: { ...c }, geometry: { type: "Point", coordinates: [c.lng, c.lat] } })),
    } as any);

    const tr = p.trail || [];
    (map.getSource("trail") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: [
        ...(tr.length > 1 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: tr.map((t) => [t.lng, t.lat]) } }] : []),
        ...tr.map((t, n) => ({ type: "Feature", properties: { n: n + 1, ...t }, geometry: { type: "Point", coordinates: [t.lng, t.lat] } })),
      ],
    } as any);

    hexSizeRef.current = 0;
    maybeRebin(map);

    const L = p.layers;
    vis(map, "district-fill", isDistricts);
    vis(map, "district-extrude", isDistricts);
    vis(map, "district-label", isDistricts);
    vis(map, "district-line", true);
    if (map.getLayer("district-line"))
      map.setPaintProperty("district-line", "line-color",
        ["case", ["==", ["get", "district"], ["get", "selected"]], "#b07d16", isDistricts ? "#ffffff" : mode === "satellite" || mode === "night" ? "rgba(255,255,255,0.55)" : "rgba(28,47,134,0.35)"]);
    vis(map, "heat", L.heat && !isDistricts);
    vis(map, "hex", L.hex && !isDistricts);
    // Flat cluster bubbles fight with 3D columns; show them only without columns.
    vis(map, "clusters", L.points && !isDistricts && !L.hex);
    vis(map, "cluster-count", L.points && !isDistricts && !L.hex);
    vis(map, "points", L.points);
    vis(map, "zones-fill", L.zones && !isDistricts);
    vis(map, "zones-line", L.zones && !isDistricts);
    vis(map, "cameras", L.cameras);
    vis(map, "camera-labels", L.cameras);
    syncMarkers(map);
    emitStats(map);
  }

  function maybeRebin(map: MLMap) {
    const p = propsRef.current;
    if (!map.getSource("hex")) return;
    const size = hexSizeFor(map.getZoom());
    if (size === hexSizeRef.current) return;
    hexSizeRef.current = size;
    const fc = hexbin(p.incidents, size);
    // Column height scales with cell size so a city block is not as tall as a district.
    const hmax = Math.max(1200, size * 5200);
    fc.features.forEach((f: any) => (f.properties.hmax = hmax));
    (map.getSource("hex") as GeoJSONSource).setData(fc as any);
  }

  function syncMarkers(map: MLMap) {
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];
    const p = propsRef.current;
    const mk = (lng: number, lat: number, cls: string, title: string, onClick?: () => void, label?: string) => {
      const e = document.createElement("div");
      e.className = `ksp-pulse ${cls}`;
      e.title = title;
      if (label) {
        const s = document.createElement("span");
        s.className = "ksp-pulse-label";
        s.textContent = label;
        e.appendChild(s);
      }
      if (onClick) e.addEventListener("click", (ev) => { ev.stopPropagation(); onClick(); });
      markersRef.current.push(new maplibregl.Marker({ element: e }).setLngLat([lng, lat]).addTo(map));
    };
    if (p.layers.kde && p.mode !== "districts")
      (p.kde || []).forEach((h: any) => mk(h.lng, h.lat, "ksp-pulse-gold", `Predicted hotspot (KDE), intensity ${h.intensity}`));
    if (p.mode !== "districts" || true) {
      const geo = p.districtGeo;
      (p.trendAlerts || []).forEach((a: any) => {
        const f = geo?.features.find((x: any) => x.properties.district === a.district);
        if (f) mk(f.properties.label_lng, f.properties.label_lat, "ksp-pulse-red",
                  `${a.crime_type} spike in ${a.district}: ${a.recent_count} vs ${a.expected_count} expected`,
                  () => p.onSelectDistrict(a.district), `${a.crime_type.split(" (")[0]} ${a.spike_ratio}×`);
      });
    }
    if (p.layers.alerts && p.layers.cameras)
      p.cameras.forEach((c) => {
        const pr = p.alertCameras[c.camera_id];
        if (pr) mk(c.lng, c.lat, pr === "P1" ? "ksp-pulse-alert" : "ksp-pulse-alert2", `${pr} alert at ${c.name}`, () => p.onOpenCamera(c.camera_id));
      });
  }

  function emitStats(map: MLMap) {
    const p = propsRef.current;
    const b = map.getBounds();
    const inView = p.incidents.filter((i) => b.contains([i.lng, i.lat]));
    const hours = new Array(24).fill(0);
    const crimes = new Map<string, number>();
    const dists = new Map<string, number>();
    let open = 0;
    for (const i of inView) {
      if (i.hour >= 0) hours[i.hour]++;
      crimes.set(i.crime, (crimes.get(i.crime) || 0) + 1);
      dists.set(i.district, (dists.get(i.district) || 0) + 1);
      if (i.status === "Under Investigation") open++;
    }
    const peak = hours.indexOf(Math.max(...hours));
    // Busiest 3-hour window, wrapping midnight.
    let bw = -1, bwN = -1;
    for (let h = 0; h < 24; h++) {
      const n = hours[h] + hours[(h + 1) % 24] + hours[(h + 2) % 24];
      if (n > bwN) { bwN = n; bw = h; }
    }
    const topD = [...dists.entries()].sort((a, c) => c[1] - a[1]);
    const top4 = topD.slice(0, 4).reduce((s, x) => s + x[1], 0);
    const topC = [...crimes.entries()].sort((a, c) => c[1] - a[1])[0];
    const cams = p.cameras.filter((c) => b.contains([c.lng, c.lat])).length;
    p.onViewStats({
      count: inView.length,
      peakHour: inView.length ? peak : null,
      peakHourCount: inView.length ? hours[peak] : 0,
      topCrime: topC ? topC[0] : null,
      openPct: inView.length ? Math.round((open / inView.length) * 100) : 0,
      concentration: inView.length ? Math.round((top4 / inView.length) * 100) : 0,
      topDistricts: topD.slice(0, 4),
      heaviest: topD[0]?.[0] ?? null,
      cameras: cams,
      busiestWindow: inView.length ? `${String(bw).padStart(2, "0")}:00–${String((bw + 3) % 24).padStart(2, "0")}:00` : null,
    });
  }

  function wireInteractions(map: MLMap) {
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px", className: "ksp-popup" });
    map.on("click", "points", (e: any) => {
      const f = e.features?.[0];
      if (!f) return;
      const q = f.properties;
      const div = document.createElement("div");
      div.innerHTML = `<div class="ksp-pop-k">${q.district} · ${q.station}</div>
        <div class="ksp-pop-t">${q.crime}</div>
        <div class="ksp-pop-m">Severity <b>${q.sev}</b> · ${q.status}<br/>${q.year} · ${String(q.hour).padStart(2, "0")}:00</div>
        <button class="ksp-pop-btn">Open FIR ${q.case_id.slice(-6)} →</button>`;
      div.querySelector("button")?.addEventListener("click", () => propsRef.current.onOpenCase(q.case_id));
      popup.setLngLat(e.lngLat).setDOMContent(div).addTo(map);
    });
    map.on("click", "cameras", (e: any) => {
      const q = e.features?.[0]?.properties;
      if (!q) return;
      const div = document.createElement("div");
      div.innerHTML = `<div class="ksp-pop-k">${q.type} · ${q.district}</div>
        <div class="ksp-pop-t">${q.name}</div>
        <div class="ksp-pop-m">${q.camera_id} · <b style="color:${q.status === "online" ? "#0a9d57" : q.status === "degraded" ? "#f08a24" : "#dc2b25"}">${q.status}</b>
        ${q.matches_last_hour ? `<br/>${q.matches_last_hour} match(es) in the last hour` : ""}</div>
        <button class="ksp-pop-btn">Open live feed →</button>`;
      div.querySelector("button")?.addEventListener("click", () => propsRef.current.onOpenCamera(q.camera_id));
      popup.setLngLat(e.lngLat).setDOMContent(div).addTo(map);
    });
    map.on("click", "clusters", (e: any) => {
      const f = e.features?.[0];
      if (!f) return;
      (map.getSource("incidents-c") as GeoJSONSource).getClusterExpansionZoom(f.properties.cluster_id).then((z) =>
        map.easeTo({ center: f.geometry.coordinates, zoom: z + 0.5 }));
    });
    map.on("click", "district-fill", (e: any) => {
      const d = e.features?.[0]?.properties?.district;
      if (d) propsRef.current.onSelectDistrict(d);
    });
    map.on("mousemove", "district-fill", (e: any) => {
      const d = e.features?.[0]?.properties?.district;
      if (!d || hoverRef.current === d) return;
      if (hoverRef.current) map.setFeatureState({ source: "districts", id: hoverRef.current }, { hover: false });
      hoverRef.current = d;
      map.setFeatureState({ source: "districts", id: d }, { hover: true });
      const v = propsRef.current.districtValues[d];
      popup.setLngLat(e.lngLat).setHTML(`<div class="ksp-pop-t">${d}</div><div class="ksp-pop-m">${Math.round(v ?? 0).toLocaleString()} ${propsRef.current.districtMetricLabel}</div>`).addTo(map);
    });
    map.on("mouseleave", "district-fill", () => {
      if (hoverRef.current) map.setFeatureState({ source: "districts", id: hoverRef.current }, { hover: false });
      hoverRef.current = null;
      popup.remove();
    });
    for (const id of ["points", "cameras", "clusters", "district-fill"]) {
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
  }

  // MapLibre sets `position: relative` on its container, so the sizing lives on a wrapper.
  return (
    <div className="absolute inset-0">
      <div ref={el} className="h-full w-full" />
    </div>
  );
}
