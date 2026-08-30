"use client";

/**
 * Leaflet map engine — the one that always works.
 *
 * Google Maps needs a key, a billing account and an unrestricted referrer. Any of
 * those three being wrong renders a dead rectangle, and it is the kind of wrong that
 * only shows up in front of an audience. Leaflet over Esri's grey canvas tiles needs
 * none of them, so this is what runs when no key was present at build time and what
 * the page falls back to if Google refuses to load at runtime.
 *
 * It is not a degraded view. Every layer the Google engine draws is drawn here, with
 * the same colours, the same zoom gate and the same marker budget, all imported from
 * map-shared so the two cannot drift:
 *
 *   DBSCAN zones   Circle, radius in metres — the zone stays the size the model
 *                  reported as the map zooms
 *   KDE hotspots   CircleMarker, pixel radius scaled by intensity
 *   FIR markers    CircleMarker, scaled and coloured by severity, with a popup that
 *                  opens the dossier
 *   Patrol         one marker per district, coloured by risk band
 *
 * A metric radius and a pixel radius are different claims about the world, which is
 * why the zones are Circle and the incidents are CircleMarker.
 *
 * Loaded via next/dynamic with ssr:false — Leaflet touches `window` at import.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Circle,
  Popup,
  useMap,
  useMapEvents,
} from "react-leaflet";
import type { LatLngBoundsExpression } from "leaflet";
import "leaflet/dist/leaflet.css";

import {
  DEFAULT_ZOOM,
  DensityNotice,
  KARNATAKA_CENTER,
  MARKER_ZOOM,
  MapCanvasProps,
  MapDescription,
  riskColor,
  selectVisible,
  sevColor,
} from "./map-shared";

const CENTER: [number, number] = [KARNATAKA_CENTER.lat, KARNATAKA_CENTER.lng];

const POPUP_FONT = { fontFamily: "var(--font-jakarta), system-ui, sans-serif" };

/** Reports zoom and the drawable points back to the page whenever the view settles. */
function ViewportReporter({
  points,
  onZoomChange,
  onViewportChange,
  onDensity,
}: Pick<MapCanvasProps, "points" | "onZoomChange" | "onViewportChange"> & {
  onDensity: (d: { total: number; shown: number; capped: boolean; zoom: number }) => void;
}) {
  // The handlers below are registered once by Leaflet, so they must not close over a
  // stale `points`. Reading through a ref keeps them looking at the current array.
  // The ref is filled in an effect rather than during render: a Leaflet event can
  // only arrive from user interaction, which is always after the effect has flushed.
  const latest = useRef({ points, onZoomChange, onViewportChange, onDensity });
  useEffect(() => {
    latest.current = { points, onZoomChange, onViewportChange, onDensity };
  });

  const map = useMapEvents({
    moveend: () => report(),
    zoomend: () => report(),
  });

  const report = useCallback(() => {
    const { points: pts, onZoomChange: oz, onViewportChange: ov, onDensity: od } = latest.current;
    const zoom = map.getZoom();
    const b = map.getBounds();
    const { shown, total, capped } = selectVisible(pts, (p) => b.contains([p.lat, p.lng]), zoom);
    oz(zoom);
    ov(shown);
    od({ total, shown: shown.length, capped, zoom });
  }, [map]);

  // Re-report when the underlying point set changes — a district filter replaces the
  // array without moving the map, so no Leaflet event fires.
  useEffect(() => {
    report();
  }, [points, report]);

  return null;
}

/** Pans and zooms in response to a district filter or a ?case_id= deep link. */
function FocusController({
  points,
  district,
  focusCaseId,
  dispatch,
}: Pick<MapCanvasProps, "points" | "district" | "focusCaseId" | "dispatch">) {
  const map = useMap();
  const lastFocus = useRef<string>("");

  useEffect(() => {
    if (!points.length) return;
    const key = `${district}|${focusCaseId}`;
    if (key === lastFocus.current) return;
    lastFocus.current = key;

    if (focusCaseId) {
      const match = points.find((p) => p.case_id === focusCaseId);
      if (match) {
        // Past MARKER_ZOOM, so the case the link points at is actually drawn.
        map.setView([match.lat, match.lng], 15);
        return;
      }
    }

    if (district) {
      const inDistrict = points.filter((p) => p.district === district);
      if (inDistrict.length) {
        const bounds: LatLngBoundsExpression = inDistrict.map(
          (p) => [p.lat, p.lng] as [number, number]
        );
        map.fitBounds(bounds, { padding: [56, 56] });
        return;
      }
      const row = dispatch.find((d: any) => d.district === district);
      if (row?.lat != null) map.setView([row.lat, row.lng], 11);
      return;
    }

    map.setView(CENTER, DEFAULT_ZOOM);
  }, [map, points, district, focusCaseId, dispatch]);

  return null;
}

/** Keeps Leaflet's internal size in sync when the container resizes (fullscreen). */
function ResizeHandler({ trigger }: { trigger: unknown }) {
  const map = useMap();
  useEffect(() => {
    // The container is mid-transition when the prop flips; 220ms lets it settle.
    const t = setTimeout(() => map.invalidateSize(), 220);
    return () => clearTimeout(t);
  }, [map, trigger]);
  return null;
}

export default function LeafletCanvas({
  points,
  visiblePoints,
  clusters,
  dbscan,
  dispatch,
  showClusters,
  showPatrol,
  district,
  focusCaseId,
  onZoomChange,
  onViewportChange,
  onOpenCase,
  resizeKey,
}: MapCanvasProps) {
  const [density, setDensity] = useState({
    total: 0,
    shown: 0,
    capped: false,
    zoom: DEFAULT_ZOOM,
  });

  // No L.Icon.Default patching here. Leaflet only reaches for its bundler-hostile
  // default marker images when something constructs a plain Marker; every marker on
  // this map is a vector CircleMarker or Circle, so those URLs are never requested.

  return (
    <>
      <MapDescription
        visiblePoints={visiblePoints}
        clusters={clusters}
        district={district}
        showClusters={showClusters}
        zoom={density.zoom}
        engine="Leaflet with Esri grey canvas tiles"
      />

      <MapContainer
        center={CENTER}
        zoom={DEFAULT_ZOOM}
        minZoom={6}
        maxZoom={18}
        scrollWheelZoom
        zoomControl
        style={{ width: "100%", height: "100%", background: "#f3f4f6" }}
      >
        {/*
          Esri's World Light Gray Canvas. Genuinely keyless, and greyscale, so the
          incident colours stay the only saturated thing on screen.

          Not CARTO: `basemaps.cartocdn.com/light_all` used to be keyless and no longer
          is — it still returns HTTP 200 with a tile, but the tile has "API KEY
          REQUIRED" stamped across it. A watermark is not an error, so nothing catches
          it except looking at the map.

          Esri's tile path is {z}/{y}/{x}, not Leaflet's usual {z}/{x}/{y}. The service
          stops at zoom 16 and serves a placeholder above it, so maxNativeZoom pins the
          fetch at 16 and lets Leaflet upscale for 17-18 — blurrier, but continuous.
        */}
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
          attribution='Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Esri, DeLorme, NAVTEQ'
          maxZoom={18}
          maxNativeZoom={16}
        />

        <ViewportReporter
          points={points}
          onZoomChange={onZoomChange}
          onViewportChange={onViewportChange}
          onDensity={setDensity}
        />
        <FocusController
          points={points}
          district={district}
          focusCaseId={focusCaseId}
          dispatch={dispatch}
        />
        <ResizeHandler trigger={resizeKey} />

        {/* DBSCAN density zones — real metric radii, drawn beneath everything else */}
        {showClusters &&
          dbscan.map((c: any, idx: number) => (
            <Circle
              key={`z-${c.id ?? idx}`}
              center={[c.lat, c.lng]}
              radius={Math.max(600, (c.radius_km || 1) * 1000)}
              pathOptions={{
                color: "#2f74c0",
                weight: 1.5,
                opacity: 0.75,
                fillColor: "#2f74c0",
                fillOpacity: 0.1,
              }}
            >
              <Popup>
                <div style={{ minWidth: 200, ...POPUP_FONT }}>
                  <div
                    style={{
                      fontWeight: 700,
                      color: "#2f74c0",
                      fontSize: 11,
                      textTransform: "uppercase",
                      letterSpacing: 0.4,
                    }}
                  >
                    DBSCAN cluster
                  </div>
                  <div style={{ color: "#1d1d1f", fontWeight: 700, marginTop: 3, fontSize: 14 }}>
                    {c.dominant_crime ?? "Cluster"}
                  </div>
                  <div style={{ color: "#6b6b73", fontSize: 12, marginTop: 3 }}>
                    {c.cases} incidents · {c.district}
                    <br />~{c.radius_km} km radius · mean severity {c.avg_severity}
                  </div>
                </div>
              </Popup>
            </Circle>
          ))}

        {/* KDE predicted hotspots */}
        {clusters.map((c: any, idx: number) => (
          <CircleMarker
            key={`k-${c.id ?? idx}`}
            center={[c.lat, c.lng]}
            radius={8 + (c.intensity ?? 1) * 7}
            pathOptions={{
              color: "#ffffff",
              weight: 2,
              fillColor: "#b07d16",
              fillOpacity: 0.85,
            }}
          >
            <Popup>
              <div style={POPUP_FONT}>
                <div
                  style={{
                    fontWeight: 700,
                    color: "#b07d16",
                    fontSize: 11,
                    textTransform: "uppercase",
                    letterSpacing: 0.4,
                  }}
                >
                  Predicted hotspot
                </div>
                <div style={{ color: "#6b6b73", fontSize: 12, marginTop: 3 }}>
                  KDE intensity {c.intensity ?? "n/a"}
                </div>
              </div>
            </Popup>
          </CircleMarker>
        ))}

        {/* Individual FIRs. The parent only ever holds what survived the zoom gate
            and the marker budget, so this maps the whole array. */}
        {visiblePoints.map((p) => (
          <CircleMarker
            key={p.case_id}
            center={[p.lat, p.lng]}
            radius={4 + (p.severity / 100) * 4}
            pathOptions={{
              color: "#ffffff",
              weight: 1.2,
              fillColor: sevColor(p.severity),
              fillOpacity: 0.9,
            }}
          >
            <Popup>
              <div style={{ minWidth: 190, ...POPUP_FONT }}>
                <div style={{ fontWeight: 700, color: "#b07d16", fontSize: 13 }}>{p.case_id}</div>
                <div style={{ color: "#1d1d1f", fontWeight: 600, marginTop: 2 }}>{p.crime_type}</div>
                <div style={{ color: "#6b6b73", fontSize: 12, marginTop: 2 }}>
                  {p.district} · {p.police_station || "Unknown PS"} · severity {p.severity}
                  {p.status ? ` · ${p.status}` : ""}
                </div>
                <button
                  onClick={() => onOpenCase(p.case_id)}
                  style={{
                    marginTop: 8,
                    width: "100%",
                    background: "#b07d16",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: 8,
                    padding: "7px 10px",
                    fontWeight: 700,
                    fontSize: 12,
                    cursor: "pointer",
                  }}
                >
                  Open dossier →
                </button>
              </div>
            </Popup>
          </CircleMarker>
        ))}

        {/* Recommended patrol strength per district */}
        {showPatrol &&
          dispatch.map((d: any) => (
            <CircleMarker
              key={`p-${d.district}`}
              center={[d.lat, d.lng]}
              radius={13}
              pathOptions={{
                color: "#ffffff",
                weight: 2,
                fillColor: riskColor(d.risk_level),
                fillOpacity: 0.95,
              }}
            >
              <Popup>
                <div style={POPUP_FONT}>
                  <div style={{ fontWeight: 700, color: "#1d1d1f", fontSize: 13 }}>{d.district}</div>
                  <div style={{ color: "#6b6b73", fontSize: 12, marginTop: 2 }}>
                    {d.risk_level} risk · <strong>{d.recommended_officers}</strong> officers
                    recommended
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          ))}
      </MapContainer>

      <DensityNotice
        zoom={density.zoom}
        total={density.total}
        shown={density.shown}
        capped={density.capped}
      />
    </>
  );
}

export { MARKER_ZOOM };
