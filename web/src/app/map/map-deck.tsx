"use client";

/**
 * The map engine: deck.gl layers over keyless CARTO basemaps (dark / light), with Esri
 * imagery for satellite. deck.gl owns the camera and every overlay, so changing the
 * basemap can never wipe a layer, and the same red dot means the same thing in every
 * mode. Outside Karnataka is dimmed so the state reads as the subject of the screen.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { FlyToInterpolator, LinearInterpolator, WebMercatorViewport, type MapViewState, type PickingInfo } from "@deck.gl/core";
import { HeatmapLayer, HexagonLayer } from "@deck.gl/aggregation-layers";
import { ArcLayer, GeoJsonLayer, PathLayer, ScatterplotLayer, SolidPolygonLayer, TextLayer } from "@deck.gl/layers";
import { Map as BaseMap } from "react-map-gl/maplibre";
import type { StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Compass, LocateFixed, Minus, Plus, X } from "lucide-react";

export type MapMode = "3d" | "2d" | "districts" | "satellite";
export type MapTheme = "dark" | "light";

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
  theme: MapTheme;
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
  /** Bump to re-frame the whole state (e.g. after entering or leaving full screen). */
  refit?: number;
  onSelectDistrict: (d: string) => void;
  onOpenCase: (id: string) => void;
  onOpenCamera: (id: string) => void;
  onViewStats: (s: ViewStats) => void;
}

type RGB = [number, number, number];
type RGBA = [number, number, number, number];

const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
const KA_BOUNDS: [[number, number], [number, number]] = [[74.05, 11.55], [78.6, 18.45]];
const HOME: MapViewState = { longitude: 76.2, latitude: 14.95, zoom: 6.2, pitch: 0, bearing: 0 };
const CAMERA: Record<MapMode, { pitch: number; bearing: number }> = {
  "3d": { pitch: 52, bearing: -14 },
  "2d": { pitch: 0, bearing: 0 },
  districts: { pitch: 42, bearing: -8 },
  satellite: { pitch: 0, bearing: 0 },
};

const CARTO = (name: string) => `https://basemaps.cartocdn.com/gl/${name}-gl-style/style.json`;
const SATELLITE: StyleSpecification = {
  version: 8,
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
};
/** Raster tiles if a vector style cannot be fetched (blocked CDN, flaky venue wifi). */
const rasterFallback = (theme: MapTheme): StyleSpecification => ({
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: ["a", "b", "c", "d"].map((s) => `https://${s}.basemaps.cartocdn.com/${theme === "dark" ? "dark_all" : "light_all"}/{z}/{x}/{y}.png`),
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [{ id: "carto", type: "raster", source: "carto" }],
});

// Low density is cool and recedes on a dark map; the hottest cells are warm and loud.
const HEX_RANGE: RGB[] = [[1, 152, 189], [73, 227, 206], [216, 254, 181], [254, 237, 177], [254, 173, 84], [209, 55, 78]];
const HEAT_RANGE: RGB[] = [[34, 94, 168], [29, 145, 192], [65, 182, 196], [254, 217, 118], [253, 141, 60], [227, 26, 28]];
const DIST_DARK: RGB[] = [[22, 40, 80], [30, 90, 150], [40, 150, 190], [110, 205, 180], [240, 225, 120]];
const DIST_LIGHT: RGB[] = [[232, 238, 252], [157, 184, 242], [91, 134, 229], [52, 87, 196], [28, 47, 134]];
const CAM_RGB: Record<string, RGB> = { FACE: [150, 95, 255], ANPR: [47, 140, 255], PTZ: [231, 180, 60], CCTV: [40, 185, 168] };
const GOLD: RGB = [245, 190, 80];

const sevColor = (s: number): RGB => (s >= 75 ? [235, 64, 52] : s >= 50 ? [240, 170, 60] : s >= 25 ? [64, 156, 255] : [32, 190, 120]);

function ramp(stops: RGB[], t: number): RGB {
  const x = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const a = stops[i], b = stops[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

/** Hex size follows zoom, so a city block is never binned like a district. */
const hexRadius = (z: number) => (z < 7 ? 8000 : z < 8.3 ? 4500 : z < 9.6 ? 2000 : z < 11 ? 900 : z < 12.5 ? 380 : 160);
// Square root keeps Bengaluru from flattening every other column into the floor.
const sqrtCount = (pts: unknown[]) => Math.sqrt(pts.length);
const incidentPos = (d: Incident) => [d.lng, d.lat] as [number, number];
const incidentWeight = (d: Incident) => 0.25 + d.severity / 100;

const WORLD_RING: [number, number][] = [[55, -5], [105, -5], [105, 35], [55, 35], [55, -5]];
const FONT = '"Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif';
// Markers and labels are read, not looked through: draw them over the 3D columns.
const ON_TOP = { depthCompare: "always", depthWriteEnabled: false } as any;

/** Room for the toolbar above and the legend below, so the state lands in the clear. */
const framePadding = (w: number, h: number) => {
  const v = Math.min(70, Math.floor(h / 8)), side = Math.min(28, Math.floor(w / 12));
  return { top: v, bottom: v, left: side, right: side };
};

type Selection = { x: number; y: number; kind: "case" | "camera"; obj: any } | null;

// =========================================================================== component
export default function DeckMap(props: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const size = useRef({ width: 0, height: 0 });
  const propsRef = useRef(props);
  const [viewState, setViewState] = useState<MapViewState>({ ...HOME, ...CAMERA[props.mode] });
  const [outline, setOutline] = useState<[number, number][][]>([]);
  const [styleFailed, setStyleFailed] = useState(false);
  const [sel, setSel] = useState<Selection>(null);
  const [phase, setPhase] = useState(0);
  const [grow, setGrow] = useState(0);
  const growFrom = useRef(0);
  const fitted = useRef(false);
  const tourRef = useRef(0);

  useEffect(() => {
    propsRef.current = props;
  });

  useEffect(() => {
    fetch(`${BASE}/geo/karnataka-outline.geojson`).then((r) => r.json())
      .then((g) => setOutline(g.features.flatMap((f: any) =>
        f.geometry.type === "Polygon" ? [f.geometry.coordinates[0]] : f.geometry.coordinates.map((p: any) => p[0]))))
      .catch(() => {});
  }, []);

  // ------------------------------------------------------------------ camera helpers
  const go = useCallback((patch: Partial<MapViewState> & Record<string, any>, duration: number | "auto" = "auto") => {
    setViewState((v) => ({ ...v, ...patch, transitionDuration: duration,
                           transitionInterpolator: new FlyToInterpolator({ speed: 1.4, curve: 1.45 }) }) as MapViewState);
  }, []);

  const fitTo = useCallback((bounds: [[number, number], [number, number]], extra: Partial<MapViewState> = {}) => {
    const { width, height } = size.current;
    if (!width || !height) return;
    const vp = new WebMercatorViewport({ width, height });
    const { longitude, latitude, zoom } = vp.fitBounds(bounds, { padding: framePadding(width, height) });
    // fitBounds frames a flat map; tilting foreshortens it, so close in to fill the frame again.
    const tilt = (extra.pitch ?? 0) > 30 ? 0.3 : 0;
    go({ longitude, latitude, zoom: Math.min(zoom + tilt, 12.5), ...extra }, 1400);
  }, [go]);

  const recenter = useCallback(() => {
    fitTo(KA_BOUNDS, CAMERA[propsRef.current.mode]);
  }, [fitTo]);

  // Track the container. The first measurement frames Karnataka; later ones (entering
  // or leaving full screen) keep whatever the officer is looking at.
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      size.current = { width: e.contentRect.width, height: e.contentRect.height };
      if (!fitted.current && e.contentRect.width > 0) {
        fitted.current = true;
        const { width, height } = e.contentRect;
        const vp = new WebMercatorViewport({ width, height });
        const { longitude, latitude, zoom } = vp.fitBounds(KA_BOUNDS, { padding: framePadding(width, height) });
        setViewState((v) => ({ ...v, longitude, latitude, zoom: zoom + ((v.pitch ?? 0) > 30 ? 0.3 : 0) }));
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ------------------------------------------------------------------ animation clock
  useEffect(() => {
    let raf = 0;
    let last = 0;
    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      if (t - last < 40 || document.hidden) return;
      last = t;
      setPhase((t % 2200) / 2200);
      if (growFrom.current) {
        const g = Math.min(1, (t - growFrom.current) / 1500);
        setGrow(1 - Math.pow(1 - g, 3));
        if (g >= 1) growFrom.current = 0;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Columns rise from the floor whenever they (re)appear.
  const hexOn = props.layers.hex && props.mode !== "districts";
  useEffect(() => {
    if (hexOn && props.incidents.length) {
      setGrow(0);
      growFrom.current = performance.now();
    }
  }, [hexOn, props.incidents.length, props.mode]);

  // ------------------------------------------------------------------ prop-driven moves
  useEffect(() => {
    setViewState((v) => ({ ...v, ...CAMERA[props.mode], transitionDuration: 900,
                           transitionInterpolator: new LinearInterpolator(["pitch", "bearing"]) }) as MapViewState);
  }, [props.mode]);

  useEffect(() => {
    const f = props.flyTo;
    if (!f) return;
    go({ longitude: f.lng, latitude: f.lat, zoom: f.zoom ?? 13, pitch: props.mode === "3d" ? 58 : viewState.pitch });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.flyTo]);

  useEffect(() => {
    const geo = props.districtGeo;
    if (!props.fitDistrict || !geo) return;
    const f = geo.features.find((x: any) => x.properties.district === props.fitDistrict!.name);
    if (!f) return;
    let minX = 180, minY = 90, maxX = -180, maxY = -90;
    const walk = (c: any) => {
      if (typeof c[0] === "number") {
        minX = Math.min(minX, c[0]); maxX = Math.max(maxX, c[0]); minY = Math.min(minY, c[1]); maxY = Math.max(maxY, c[1]);
      } else c.forEach(walk);
    };
    walk(f.geometry.coordinates);
    fitTo([[minX, minY], [maxX, maxY]], props.mode === "3d" ? { pitch: 50 } : {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.fitDistrict, props.districtGeo]);

  // Cinematic tour: fly the busiest districts, then come home.
  useEffect(() => {
    if (!props.tour) return;
    const run = ++tourRef.current;
    const { districtGeo: geo, districtValues: vals } = propsRef.current;
    const stops = Object.entries(vals).sort((a, b) => b[1] - a[1]).slice(0, 5)
      .map(([d]) => geo?.features.find((f: any) => f.properties.district === d)?.properties)
      .filter(Boolean);
    let i = 0;
    const next = () => {
      if (tourRef.current !== run) return;
      if (i >= stops.length) {
        recenter();
        return;
      }
      const p = stops[i++];
      go({ longitude: p.label_lng, latitude: p.label_lat, zoom: 10.2, pitch: 60, bearing: -30 + i * 24,
           onTransitionEnd: () => setTimeout(next, 1300), onTransitionInterrupt: () => (tourRef.current += 1) }, 3000);
    };
    next();
    return () => {
      tourRef.current += 1;
    };
  }, [props.tour, go, recenter]);

  useEffect(() => {
    if (!props.refit) return;
    // Wait for the browser to finish resizing the stage before measuring it.
    const t = setTimeout(recenter, 450);
    return () => clearTimeout(t);
  }, [props.refit, recenter]);

  // ------------------------------------------------------------------ view statistics
  useEffect(() => {
    const { width, height } = size.current;
    if (!width || !height) return;
    const t = setTimeout(() => {
      const p = propsRef.current;
      const vp = new WebMercatorViewport({ width, height, longitude: viewState.longitude, latitude: viewState.latitude,
                                           zoom: viewState.zoom, pitch: Math.min(viewState.pitch ?? 0, 60), bearing: viewState.bearing });
      const [w, s, e, n] = vp.getBounds();
      const inside = (lng: number, lat: number) => lng >= w && lng <= e && lat >= s && lat <= n;
      const inView = p.incidents.filter((i) => inside(i.lng, i.lat));
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
      let bw = -1, bwN = -1;
      for (let h = 0; h < 24; h++) {
        const c = hours[h] + hours[(h + 1) % 24] + hours[(h + 2) % 24];
        if (c > bwN) { bwN = c; bw = h; }
      }
      const topD = [...dists.entries()].sort((a, b) => b[1] - a[1]);
      const top4 = topD.slice(0, 4).reduce((sum, x) => sum + x[1], 0);
      const topC = [...crimes.entries()].sort((a, b) => b[1] - a[1])[0];
      p.onViewStats({
        count: inView.length,
        peakHour: inView.length ? peak : null,
        peakHourCount: inView.length ? hours[peak] : 0,
        topCrime: topC ? topC[0] : null,
        openPct: inView.length ? Math.round((open / inView.length) * 100) : 0,
        concentration: inView.length ? Math.round((top4 / inView.length) * 100) : 0,
        topDistricts: topD.slice(0, 4),
        heaviest: topD[0]?.[0] ?? null,
        cameras: p.cameras.filter((c) => inside(c.lng, c.lat)).length,
        busiestWindow: inView.length ? `${String(bw).padStart(2, "0")}:00–${String((bw + 3) % 24).padStart(2, "0")}:00` : null,
      });
    }, 220);
    return () => clearTimeout(t);
  }, [viewState.longitude, viewState.latitude, viewState.zoom, viewState.pitch, viewState.bearing, props.incidents, props.cameras]);

  // ------------------------------------------------------------------ derived data
  const dark = props.theme === "dark" || props.mode === "satellite";
  const isDistricts = props.mode === "districts";
  const zoom = viewState.zoom;
  const radius = hexRadius(zoom);

  const districtFeatures = useMemo(() => {
    const geo = props.districtGeo;
    if (!geo) return [];
    const vals = Object.values(props.districtValues);
    const max = Math.max(1, ...vals);
    const min = Math.min(0, ...vals);
    return geo.features.map((f: any) => {
      const v = props.districtValues[f.properties.district] ?? 0;
      return { ...f, properties: { ...f.properties, v, t: (v - min) / (max - min || 1) } };
    });
  }, [props.districtGeo, props.districtValues]);

  const districtLabels = useMemo(() => {
    const { width, height } = size.current;
    if (!isDistricts || !width) return [];
    const vp = new WebMercatorViewport({ width, height, longitude: viewState.longitude, latitude: viewState.latitude,
                                         zoom: viewState.zoom, pitch: viewState.pitch, bearing: viewState.bearing });
    const kept: number[][] = [];
    return [...districtFeatures].sort((a: any, b: any) => b.properties.t - a.properties.t).filter((f: any) => {
      const q = f.properties;
      const [x, y] = vp.project([q.label_lng, q.label_lat, 900 + q.t * 46000]);
      const w = Math.max(q.district.length, 11) * 7 + 6, h = 32;
      const box = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
      if (kept.some((k) => box[0] < k[2] && box[2] > k[0] && box[1] < k[3] && box[3] > k[1])) return false;
      kept.push(box);
      return true;
    });
  }, [isDistricts, districtFeatures, viewState.longitude, viewState.latitude, viewState.zoom, viewState.pitch, viewState.bearing]);

  const selectedFeature = useMemo(
    () => districtFeatures.filter((f: any) => f.properties.district === props.selectedDistrict),
    [districtFeatures, props.selectedDistrict]);

  const trendPoints = useMemo(() => {
    const geo = props.districtGeo;
    const seen: Record<string, number> = {};
    return (props.trendAlerts || []).map((a: any) => {
      const f = geo?.features.find((x: any) => x.properties.district === a.district);
      if (!f) return null;
      // Two spikes in one district share a pin; their labels stack instead of overprinting.
      const stack = (seen[a.district] = (seen[a.district] ?? -1) + 1);
      return { ...a, stack, position: [f.properties.label_lng, f.properties.label_lat] };
    }).filter(Boolean);
  }, [props.trendAlerts, props.districtGeo]);

  const alertCams = useMemo(() => props.cameras
    .filter((c) => props.alertCameras[c.camera_id])
    .map((c) => ({ ...c, priority: props.alertCameras[c.camera_id] })), [props.cameras, props.alertCameras]);

  const trailSegments = useMemo(() => props.trail.slice(1).map((t, i) => ({ from: props.trail[i], to: t })), [props.trail]);

  const maskPolygon = useMemo(() => (outline.length ? [{ polygon: [WORLD_RING, ...outline] }] : []), [outline]);

  // ------------------------------------------------------------------ layers
  const L = props.layers;
  const pulse = (offset: number) => (phase + offset) % 1;
  const camRadius = Math.max(2.5, Math.min(8, (zoom - 6.5) * 1.1));

  const layers = [
    // Dim everything that is not Karnataka.
    new SolidPolygonLayer({
      id: "mask", data: maskPolygon, getPolygon: (d: any) => d.polygon,
      getFillColor: dark ? [4, 7, 14, props.mode === "satellite" ? 120 : 150] : [245, 246, 250, 165],
      updateTriggers: { getFillColor: [dark, props.mode] },
    }),
    new GeoJsonLayer({
      id: "districts", data: districtFeatures, visible: isDistricts,
      filled: true, stroked: true, extruded: true, wireframe: false,
      getElevation: (f: any) => 800 + f.properties.t * 46000,
      getFillColor: (f: any) => [...ramp(dark ? DIST_DARK : DIST_LIGHT, f.properties.t), 255] as RGBA,
      getLineColor: dark ? [255, 255, 255, 90] : [255, 255, 255, 220],
      lineWidthMinPixels: 1,
      material: { ambient: 0.55, diffuse: 0.6, shininess: 24 },
      pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 70],
      onClick: (info: PickingInfo) => info.object && propsRef.current.onSelectDistrict(info.object.properties.district),
      updateTriggers: { getFillColor: [dark, props.districtValues], getElevation: [props.districtValues] },
    }),
    new GeoJsonLayer({
      id: "district-lines", data: districtFeatures, visible: !isDistricts,
      filled: true, stroked: true,
      getFillColor: [0, 0, 0, 0],
      getLineColor: dark ? [180, 205, 255, 60] : [28, 47, 134, 70],
      lineWidthMinPixels: 1,
      pickable: props.mode === "2d",
      onClick: (info: PickingInfo) => info.object && propsRef.current.onSelectDistrict(info.object.properties.district),
      updateTriggers: { getLineColor: [dark] },
    }),
    new PathLayer({
      id: "ka-glow", data: outline, getPath: (d: any) => d, widthUnits: "pixels", getWidth: 7,
      getColor: [...GOLD, dark ? 55 : 45] as RGBA, capRounded: true, jointRounded: true,
    }),
    new PathLayer({
      id: "ka-outline", data: outline, getPath: (d: any) => d, widthUnits: "pixels", getWidth: 1.6,
      getColor: dark ? [...GOLD, 230] as RGBA : [176, 125, 22, 230], capRounded: true, jointRounded: true,
      updateTriggers: { getColor: [dark] },
    }),
    new GeoJsonLayer({
      id: "district-selected", data: selectedFeature, filled: true, stroked: true,
      getFillColor: [...GOLD, isDistricts ? 0 : 26] as RGBA, getLineColor: [...GOLD, 255] as RGBA, lineWidthMinPixels: 3,
      updateTriggers: { getFillColor: [isDistricts] },
    }),
    new ScatterplotLayer({
      id: "zones", data: props.dbscan || [], visible: L.zones && !isDistricts,
      getPosition: (c: any) => [c.lng, c.lat], getRadius: (c: any) => Math.min(25, c.radius_km || 2) * 1000, radiusUnits: "meters",
      filled: true, stroked: true, getFillColor: [47, 140, 255, 28], getLineColor: [90, 170, 255, 210], lineWidthMinPixels: 1.5,
    }),
    new HeatmapLayer({
      id: "heat", data: props.incidents, visible: L.heat && !isDistricts,
      getPosition: incidentPos, getWeight: incidentWeight, radiusPixels: zoom < 8 ? 38 : zoom < 11 ? 48 : 60,
      intensity: zoom < 8 ? 2.6 : zoom < 11 ? 1.8 : 1.2, threshold: 0.05, colorRange: HEAT_RANGE, aggregation: "SUM",
    }),
    new HexagonLayer({
      id: "hex", data: props.incidents, visible: hexOn,
      getPosition: incidentPos, radius, coverage: 0.86, extruded: true, gpuAggregation: false,
      getColorValue: sqrtCount, getElevationValue: sqrtCount, colorScaleType: "quantize", colorRange: HEX_RANGE,
      elevationRange: [0, radius * 13], elevationScale: grow,
      material: { ambient: 0.62, diffuse: 0.62, shininess: 40, specularColor: [60, 64, 70] },
      pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 90],
    }),
    new ScatterplotLayer({
      id: "points", data: props.incidents, visible: L.points && zoom >= 10.5,
      getPosition: incidentPos, getFillColor: (d: Incident) => sevColor(d.severity),
      radiusUnits: "pixels", getRadius: 1, radiusScale: zoom < 12 ? 3.2 : zoom < 14 ? 4.6 : 6.5,
      stroked: true, getLineColor: dark ? [10, 14, 24, 220] : [255, 255, 255, 230], lineWidthMinPixels: 1,
      pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 160],
      onClick: (info: PickingInfo) => info.object && setSel({ x: info.x, y: info.y, kind: "case", obj: info.object }),
      updateTriggers: { getLineColor: [dark] },
    }),
    new ScatterplotLayer({
      id: "kde-ring", parameters: ON_TOP, data: L.kde && !isDistricts ? props.kde || [] : [],
      getPosition: (h: any) => [h.lng, h.lat], radiusUnits: "pixels", getRadius: 1, radiusScale: 7 + pulse(0) * 26,
      filled: false, stroked: true, getLineColor: [...GOLD, 255] as RGBA, lineWidthMinPixels: 2, opacity: 1 - pulse(0),
    }),
    new ScatterplotLayer({
      id: "kde", parameters: ON_TOP, data: L.kde && !isDistricts ? props.kde || [] : [],
      getPosition: (h: any) => [h.lng, h.lat], radiusUnits: "pixels", getRadius: 6,
      getFillColor: [...GOLD, 255] as RGBA, stroked: true, getLineColor: [255, 255, 255, 240], lineWidthMinPixels: 2,
      pickable: true,
    }),
    new ScatterplotLayer({
      id: "cameras", data: props.cameras, visible: L.cameras && zoom >= 7.4,
      getPosition: (c: Camera) => [c.lng, c.lat], radiusUnits: "pixels", getRadius: 1, radiusScale: camRadius,
      getFillColor: (c: Camera) => [...(CAM_RGB[c.type] || CAM_RGB.CCTV), c.status === "offline" ? 110 : 255] as RGBA,
      stroked: true, lineWidthMinPixels: 1.5,
      getLineColor: (c: Camera) => (c.status === "offline" ? [220, 43, 37, 255] : c.status === "degraded" ? [240, 138, 36, 255]
        : dark ? [255, 255, 255, 200] : [255, 255, 255, 255]) as RGBA,
      pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 200],
      onClick: (info: PickingInfo) => info.object && setSel({ x: info.x, y: info.y, kind: "camera", obj: info.object }),
      updateTriggers: { getLineColor: [dark] },
    }),
    new TextLayer({
      id: "camera-labels", parameters: ON_TOP, fontFamily: FONT, data: L.cameras && zoom >= 13 ? props.cameras : [],
      getPosition: (c: Camera) => [c.lng, c.lat], getText: (c: Camera) => c.name, getSize: 11, getPixelOffset: [0, 14],
      getColor: dark ? [235, 240, 255, 255] : [29, 29, 31, 255], characterSet: "auto", fontWeight: 600,
      fontSettings: { sdf: true }, outlineWidth: 3, outlineColor: dark ? [5, 8, 15, 230] : [255, 255, 255, 240],
      getTextAnchor: "middle", getAlignmentBaseline: "top", maxWidth: 14, wordBreak: "break-word",
      updateTriggers: { getColor: [dark] },
    }),
    new ScatterplotLayer({
      id: "alert-ring-a", parameters: ON_TOP, data: L.alerts && L.cameras ? alertCams : [],
      getPosition: (c: any) => [c.lng, c.lat], radiusUnits: "pixels", getRadius: 1, radiusScale: 6 + pulse(0) * 22,
      filled: false, stroked: true, lineWidthMinPixels: 2, opacity: 1 - pulse(0),
      getLineColor: (c: any) => (c.priority === "P1" ? [255, 45, 85, 255] : [240, 138, 36, 255]) as RGBA,
    }),
    new ScatterplotLayer({
      id: "alert-ring-b", parameters: ON_TOP, data: L.alerts && L.cameras ? alertCams : [],
      getPosition: (c: any) => [c.lng, c.lat], radiusUnits: "pixels", getRadius: 1, radiusScale: 6 + pulse(0.5) * 22,
      filled: false, stroked: true, lineWidthMinPixels: 2, opacity: 1 - pulse(0.5),
      getLineColor: (c: any) => (c.priority === "P1" ? [255, 45, 85, 255] : [240, 138, 36, 255]) as RGBA,
    }),
    new ScatterplotLayer({
      id: "alert-core", parameters: ON_TOP, data: L.alerts && L.cameras ? alertCams : [],
      getPosition: (c: any) => [c.lng, c.lat], radiusUnits: "pixels", getRadius: (c: any) => (c.priority === "P1" ? 6 : 5),
      getFillColor: (c: any) => (c.priority === "P1" ? [255, 45, 85, 255] : [240, 138, 36, 255]) as RGBA,
      stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2, pickable: true,
      onClick: (info: PickingInfo) => info.object && propsRef.current.onOpenCamera(info.object.camera_id),
    }),
    new ScatterplotLayer({
      id: "trend-ring", parameters: ON_TOP, data: isDistricts ? [] : trendPoints, getPosition: (a: any) => a.position, radiusUnits: "pixels", getRadius: 1,
      radiusScale: 9 + pulse(0.25) * 30, filled: false, stroked: true, getLineColor: [235, 50, 45, 255], lineWidthMinPixels: 2.5,
      opacity: 1 - pulse(0.25),
    }),
    new ScatterplotLayer({
      id: "trend-core", parameters: ON_TOP, data: isDistricts ? [] : trendPoints, getPosition: (a: any) => a.position, radiusUnits: "pixels", getRadius: 8,
      getFillColor: [220, 43, 37, 255], stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2.5, pickable: true,
      onClick: (info: PickingInfo) => info.object && propsRef.current.onSelectDistrict(info.object.district),
    }),
    new TextLayer({
      id: "trend-label", data: isDistricts ? [] : trendPoints, getPosition: (a: any) => a.position,
      getText: (a: any) => `${a.crime_type.split(" (")[0]} ${a.spike_ratio}×`, getSize: 12,
      getPixelOffset: (a: any) => [0, -24 - a.stack * 26], fontFamily: FONT, parameters: ON_TOP,
      getColor: [255, 255, 255, 255], characterSet: "auto", fontWeight: 700, background: true,
      getBackgroundColor: [13, 18, 32, 235], backgroundPadding: [8, 5, 8, 5], getBorderColor: [235, 50, 45, 220], getBorderWidth: 1,
    }),
    new TextLayer({
      id: "district-labels", parameters: ON_TOP, fontFamily: FONT, data: districtLabels,
      getPosition: (f: any) => [f.properties.label_lng, f.properties.label_lat, 900 + f.properties.t * 46000],
      getText: (f: any) => `${f.properties.district}\n${Math.round(f.properties.v).toLocaleString()} ${propsRef.current.districtMetricLabel}`,
      getSize: 12, getColor: dark ? [240, 244, 255, 255] : [16, 24, 58, 255], characterSet: "auto", fontWeight: 700, lineHeight: 1.15,
      fontSettings: { sdf: true }, outlineWidth: 3, outlineColor: dark ? [5, 8, 15, 220] : [255, 255, 255, 230],
      updateTriggers: { getText: [props.districtValues, props.districtMetricLabel], getPosition: [props.districtValues], getColor: [dark] },
    }),
    new ArcLayer({
      id: "trail-arcs", data: trailSegments, getSourcePosition: (s: any) => [s.from.lng, s.from.lat],
      getTargetPosition: (s: any) => [s.to.lng, s.to.lat], getSourceColor: [255, 59, 48, 255], getTargetColor: [255, 190, 60, 255],
      getWidth: 4, getHeight: 0.6, widthUnits: "pixels",
    }),
    new ScatterplotLayer({
      id: "trail-stops", parameters: ON_TOP, data: props.trail, getPosition: (t: TrailPoint) => [t.lng, t.lat], radiusUnits: "pixels", getRadius: 9,
      getFillColor: [220, 43, 37, 255], stroked: true, getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2.5,
    }),
    new TextLayer({
      id: "trail-n", parameters: ON_TOP, fontFamily: FONT, data: props.trail.map((t, i) => ({ ...t, n: i + 1 })), getPosition: (t: any) => [t.lng, t.lat],
      getText: (t: any) => String(t.n), getSize: 11, getColor: [255, 255, 255, 255], fontWeight: 800,
    }),
  ];

  // ------------------------------------------------------------------ tooltips
  const getTooltip = useCallback((info: PickingInfo) => {
    const o: any = info.object;
    if (!o) return null;
    const id = info.layer?.id;
    const box = (k: string, t: string, m: string) => ({
      html: `<div class="ksp-pop-k">${k}</div><div class="ksp-pop-t">${t}</div>${m ? `<div class="ksp-pop-m">${m}</div>` : ""}`,
      style: { background: "rgba(255,255,255,0.97)", color: "#1d1d1f", borderRadius: "12px", padding: "8px 11px", maxWidth: "260px",
               boxShadow: "0 14px 40px -14px rgba(17,20,32,0.5)", border: "1px solid rgba(0,0,0,0.08)", fontFamily: "var(--font-sans)" },
    });
    if (id === "hex") return box("Hotspot cell", `${(o.count ?? o.points?.length ?? 0).toLocaleString()} incidents`, "Height and colour both encode density");
    if (id === "districts" || id === "district-lines")
      return box("District", o.properties.district,
        `${Math.round(propsRef.current.districtValues[o.properties.district] ?? 0).toLocaleString()} ${propsRef.current.districtMetricLabel} · click to focus`);
    if (id === "points") return box(`${o.district} · ${o.station}`, o.crime, `Severity ${o.severity} · ${o.status} · click for FIR`);
    if (id === "cameras" || id === "alert-core") return box(`${o.type} · ${o.district}`, o.name, `${o.camera_id} · ${o.status}`);
    if (id === "kde") return box("Predicted hotspot", "Kernel density peak", `Intensity ${o.intensity}`);
    if (id === "trend-core") return box("Emerging spike", `${o.crime_type.split(" (")[0]} · ${o.district}`, `${o.recent_count} in 30 days vs ~${o.expected_count} expected`);
    return null;
  }, []);

  const styleUrl = props.mode === "satellite" ? SATELLITE : styleFailed ? rasterFallback(props.theme) : CARTO(props.theme === "dark" ? "dark-matter" : "positron");

  // ------------------------------------------------------------------ render
  const btn = `grid h-9 w-9 place-items-center rounded-xl border shadow-lg backdrop-blur transition-colors ${
    dark ? "border-white/10 bg-[#0d1220]/85 text-white/85 hover:text-white" : "border-black/10 bg-white/95 text-ink/75 hover:text-ink"}`;

  return (
    <div ref={wrap} className="absolute inset-0" style={{ background: dark ? "#0b0f19" : "#eef1f6" }}>
      <DeckGL
        viewState={viewState}
        onViewStateChange={({ viewState: v, interactionState }: any) => {
          setViewState(v as MapViewState);
          if (interactionState?.isDragging || interactionState?.isZooming || interactionState?.isRotating) setSel(null);
        }}
        controller={{ dragRotate: true, touchRotate: true, keyboard: true, inertia: 250 }}
        layers={layers}
        getTooltip={getTooltip as any}
        getCursor={({ isHovering, isDragging }: any) => (isDragging ? "grabbing" : isHovering ? "pointer" : "grab")}
        onClick={(info: PickingInfo) => !info.object && setSel(null)}
      >
        <BaseMap
          mapStyle={styleUrl as any}
          attributionControl={{ compact: true }}
          onError={(e: any) => {
            const msg = String(e?.error?.message || "");
            if (!styleFailed && props.mode !== "satellite" && /style|Failed to fetch|NetworkError|404|AJAX/i.test(msg)) setStyleFailed(true);
          }}
          reuseMaps
        />
      </DeckGL>

      {/* click card for an FIR or a camera */}
      {sel && (
        <div className="absolute z-10 w-[250px] -translate-x-1/2 rounded-2xl border border-black/10 bg-white p-3 shadow-[0_18px_50px_-18px_rgba(17,20,32,0.55)]"
             style={{ left: Math.min(Math.max(sel.x, 135), (size.current.width || 400) - 135), top: Math.max(sel.y - 14, 10), transform: "translate(-50%, -100%)" }}>
          <button onClick={() => setSel(null)} className="absolute right-2 top-2 text-faint hover:text-ink"><X className="h-3.5 w-3.5" /></button>
          {sel.kind === "case" ? (
            <>
              <div className="ksp-pop-k">{sel.obj.district} · {sel.obj.station}</div>
              <div className="ksp-pop-t pr-4">{sel.obj.crime}</div>
              <div className="ksp-pop-m">Severity <b>{sel.obj.severity}</b> · {sel.obj.status}<br />{sel.obj.year} · {String(sel.obj.hour).padStart(2, "0")}:00</div>
              <button className="ksp-pop-btn" onClick={() => propsRef.current.onOpenCase(sel.obj.case_id)}>Open FIR {String(sel.obj.case_id).slice(-6)} →</button>
            </>
          ) : (
            <>
              <div className="ksp-pop-k">{sel.obj.type} · {sel.obj.district}</div>
              <div className="ksp-pop-t pr-4">{sel.obj.name}</div>
              <div className="ksp-pop-m">{sel.obj.camera_id} · <b style={{ color: sel.obj.status === "online" ? "#0a9d57" : sel.obj.status === "degraded" ? "#f08a24" : "#dc2b25" }}>{sel.obj.status}</b>
                {sel.obj.matches_last_hour ? <><br />{sel.obj.matches_last_hour} match(es) in the last hour</> : null}</div>
              <button className="ksp-pop-btn" onClick={() => propsRef.current.onOpenCamera(sel.obj.camera_id)}>Open live feed →</button>
            </>
          )}
        </div>
      )}

      {/* camera controls */}
      <div className="absolute bottom-12 right-3 flex flex-col gap-1.5">
        <button className={btn} title="Zoom in" onClick={() => go({ zoom: Math.min(18, viewState.zoom + 1) }, 350)}><Plus className="h-4 w-4" /></button>
        <button className={btn} title="Zoom out" onClick={() => go({ zoom: Math.max(4.5, viewState.zoom - 1) }, 350)}><Minus className="h-4 w-4" /></button>
        <button className={btn} title="Reset tilt and north" onClick={() => go({ pitch: CAMERA[props.mode].pitch, bearing: 0 }, 600)}>
          <Compass className="h-4 w-4" style={{ transform: `rotate(${-(viewState.bearing || 0)}deg)` }} />
        </button>
        <button className={btn} title="Show all of Karnataka" onClick={recenter}><LocateFixed className="h-4 w-4" /></button>
      </div>
    </div>
  );
}
