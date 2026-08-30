"use client";

/**
 * Everything the two map engines have in common.
 *
 * The page can render on Google Maps or on Leaflet depending on whether a Maps API
 * key was present at build time. Two engines drawing "the same" map is an obvious
 * way to end up with two subtly different maps, so every value that decides what a
 * viewer sees — the colour bands, the zoom at which individual FIRs appear, the
 * marker budget, the spoken description — lives here and is imported by both.
 * If a rule needs changing, changing it here changes both or neither.
 */

import { useMemo } from "react";

export const KARNATAKA_CENTER = { lat: 14.5204, lng: 75.7224 };
export const DEFAULT_ZOOM = 7;

/**
 * Individual FIRs only appear from this zoom in. The overlay on the page has always
 * told the viewer "zoom in to reveal individual FIRs", but nothing implemented it:
 * both engines drew every in-bounds point at every zoom, which at state level meant
 * asking the browser for twelve thousand markers.
 */
export const MARKER_ZOOM = 12;

/**
 * Hard ceiling on individually drawn incidents, applied after the zoom gate. A dense
 * city centre can still hold thousands inside the viewport at zoom 12. When the cap
 * bites, the map says so rather than quietly dropping the remainder.
 */
export const MAX_MARKERS = 800;

/** Severity bands. Shared so a red dot means the same thing on both engines. */
export const sevColor = (s: number) =>
  s >= 75 ? "#dc2b25" : s >= 50 ? "#b07d16" : s >= 25 ? "#2f74c0" : "#0a9d57";

export const riskColor = (r: string) =>
  r === "Critical" ? "#dc2b25" : r === "High" ? "#b07d16" : r === "Moderate" ? "#2f74c0" : "#0a9d57";

export interface MapPoint {
  case_id: string;
  lat: number;
  lng: number;
  severity: number;
  crime_type: string;
  district: string;
  police_station?: string;
  status?: string;
}

export interface MapCanvasProps {
  points: MapPoint[];
  visiblePoints: MapPoint[];
  clusters: any[];
  dbscan: any[];
  dispatch: any[];
  showClusters: boolean;
  showPatrol: boolean;
  district: string;
  focusCaseId: string | null;
  onZoomChange: (z: number) => void;
  onViewportChange: (visible: MapPoint[]) => void;
  onOpenCase: (caseId: string) => void;
  resizeKey: unknown;
}

/**
 * Selects what actually gets drawn, and reports honestly on what it left out.
 *
 * `total` is the true number of incidents inside the bounds; `shown` is what
 * survived the zoom gate and the marker budget. The two differ often, and a map
 * that shows 800 of 4,000 dots without saying so is a map that misleads about
 * density — the one thing this page exists to communicate.
 */
export function selectVisible(points: MapPoint[], inBounds: (p: MapPoint) => boolean, zoom: number) {
  let total = 0;
  const shown: MapPoint[] = [];
  for (const p of points) {
    if (!inBounds(p)) continue;
    total += 1;
    if (zoom >= MARKER_ZOOM && shown.length < MAX_MARKERS) shown.push(p);
  }
  return { shown, total, capped: zoom >= MARKER_ZOOM && total > shown.length };
}

/**
 * The map in words, for screen readers. Built from the same array the markers are
 * drawn from, so it cannot drift away from the visual.
 */
export function MapDescription({
  visiblePoints,
  clusters,
  district,
  showClusters,
  zoom,
  engine,
}: {
  visiblePoints: MapPoint[];
  clusters: any[];
  district: string;
  showClusters: boolean;
  zoom: number;
  engine: string;
}) {
  const described = useMemo(() => {
    const byDistrict = new Map<string, { count: number; critical: number }>();
    for (const p of visiblePoints) {
      const row = byDistrict.get(p.district) ?? { count: 0, critical: 0 };
      row.count += 1;
      if (p.severity >= 75) row.critical += 1;
      byDistrict.set(p.district, row);
    }
    return [...byDistrict.entries()].sort((a, b) => b[1].count - a[1].count);
  }, [visiblePoints]);

  return (
    <div className="sr-only" aria-live="polite">
      <h2>Incident map, described</h2>
      <p>Rendered with {engine}.</p>
      {zoom < MARKER_ZOOM ? (
        <p>
          Showing {clusters.length} predicted hotspots and cluster zones across
          {district ? ` ${district}` : " Karnataka"}. Individual incidents appear when you zoom in
          closer.
        </p>
      ) : (
        <p>
          {visiblePoints.length} incidents drawn in the current view
          {district ? `, filtered to ${district}` : ""}
          {showClusters ? `, with ${clusters.length} predicted hotspots shown` : ""}.
        </p>
      )}
      <ul>
        {described.map(([name, row]) => (
          <li key={name}>
            {name}: {row.count} {row.count === 1 ? "incident" : "incidents"}
            {row.critical > 0 ? `, ${row.critical} at critical severity` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Bottom-centre status strip: why individual dots are missing, or how many were dropped. */
export function DensityNotice({
  zoom,
  total,
  shown,
  capped,
}: {
  zoom: number;
  total: number;
  shown: number;
  capped: boolean;
}) {
  const message =
    zoom < MARKER_ZOOM
      ? total > 0
        ? `${total.toLocaleString()} incidents in view — zoom in to plot them individually`
        : null
      : capped
        ? `Showing ${shown.toLocaleString()} of ${total.toLocaleString()} in view — zoom in for the rest`
        : null;

  if (!message) return null;

  // Top-centre, not bottom: the Kavya console floats over the bottom of the viewport
  // and clipped this strip in half. Top-centre is clear in both normal and fullscreen
  // layouts, and sits beside the stats panel rather than under anything.
  return (
    <div
      className="pointer-events-none absolute left-1/2 top-4 z-[1000] -translate-x-1/2 rounded-full border border-black/10 bg-white/92 px-4 py-1.5 text-[11px] font-medium text-muted backdrop-blur-xl"
      role="status"
    >
      {message}
    </div>
  );
}
