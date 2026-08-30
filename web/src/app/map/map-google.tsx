"use client";

/**
 * Google Maps engine.
 *
 * Runs when NEXT_PUBLIC_GOOGLE_MAPS_API_KEY was present at build time. The key lives
 * in `web/.env.local`, which is gitignored; it must NOT go in `.env.production`,
 * which is tracked, because a key committed there is exactly how the previous one
 * ended up public.
 *
 * This engine can still fail after loading starts — a restricted referrer, billing
 * switched off, a revoked key — so it reports failure upward via `onUnavailable`
 * and the page swaps to Leaflet rather than leaving a dead rectangle on screen.
 *
 * Every layer here has a Leaflet counterpart drawing the same thing with the same
 * colours; the values that decide what a viewer sees live in map-shared.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { importLibrary, setOptions } from "@googlemaps/js-api-loader";

import {
  DEFAULT_ZOOM,
  DensityNotice,
  KARNATAKA_CENTER,
  MapCanvasProps,
  MapDescription,
  riskColor,
  selectVisible,
  sevColor,
} from "./map-shared";

/** Greyscale basemap, so the incident colours are the only saturated thing on screen. */
const MAP_STYLE: google.maps.MapTypeStyle[] = [
  { elementType: "geometry", stylers: [{ color: "#f3f4f6" }] },
  { elementType: "labels.text.fill", stylers: [{ color: "#6b6b73" }] },
  { elementType: "labels.text.stroke", stylers: [{ color: "#ffffff" }] },
  { featureType: "administrative", elementType: "geometry.stroke", stylers: [{ color: "#d4d4d8" }] },
  { featureType: "administrative.land_parcel", stylers: [{ visibility: "off" }] },
  { featureType: "poi", stylers: [{ visibility: "off" }] },
  { featureType: "road", elementType: "geometry", stylers: [{ color: "#e5e7eb" }] },
  { featureType: "road", elementType: "labels", stylers: [{ visibility: "simplified" }] },
  { featureType: "road.highway", elementType: "geometry", stylers: [{ color: "#dcdce0" }] },
  { featureType: "transit", stylers: [{ visibility: "off" }] },
  { featureType: "water", elementType: "geometry", stylers: [{ color: "#dfe6ec" }] },
];

const API_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY || "";

/**
 * How long to wait for a map before giving up on Google and drawing with Leaflet.
 * Generous — a slow connection should not trigger it — but finite, because an
 * indefinite blank rectangle is the one outcome this page must never produce.
 */
const READY_TIMEOUT_MS = 12000;

declare global {
  interface Window {
    /** Google calls this on an authentication failure. See the effect that sets it. */
    gm_authFailure?: () => void;
  }
}

const popupShell = (inner: string) =>
  `<div style="font-family:var(--font-jakarta),system-ui,sans-serif;padding:2px 4px 4px">${inner}</div>`;

export default function GoogleCanvas({
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
  onUnavailable,
}: MapCanvasProps & { onUnavailable: (reason: string) => void }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<google.maps.Map | null>(null);
  const infoRef = useRef<google.maps.InfoWindow | null>(null);

  // Overlays are held per layer so each can be cleared independently. Google Maps has
  // no React reconciliation — anything put on the map has to be taken off by hand, and
  // a missed removal shows up as markers that never disappear when a filter changes.
  const zonesRef = useRef<google.maps.Circle[]>([]);
  const hotspotRef = useRef<google.maps.Marker[]>([]);
  const firRef = useRef<google.maps.Marker[]>([]);
  const patrolRef = useRef<google.maps.Marker[]>([]);

  const [ready, setReady] = useState(false);
  const [density, setDensity] = useState({
    total: 0,
    shown: 0,
    capped: false,
    zoom: DEFAULT_ZOOM,
  });

  // Google's listeners are registered once, so they must read the current props
  // through a ref — otherwise they close over the first render and silently stop
  // reporting after any parent state change. Filled in an effect rather than during
  // render: the `idle` listener only fires from map interaction, which is always
  // after the effect has flushed.
  const cb = useRef({ onZoomChange, onViewportChange, onOpenCase, points, onUnavailable });
  useEffect(() => {
    cb.current = { onZoomChange, onViewportChange, onOpenCase, points, onUnavailable };
  });

  /* ------------------------------------------------------------ failure hooks */
  useEffect(() => {
    // A rejected loader promise catches almost nothing. With a bad key the script
    // loads fine, `importLibrary` resolves, the Map constructs — and only then does
    // Google paint its own "Oops! Something went wrong" panel over the container and
    // report InvalidKeyMapError to the console. The documented way to hear about that
    // is this global, which Google calls on any authentication failure: wrong key,
    // referrer restriction, billing disabled, revoked key.
    const previous = window.gm_authFailure;
    window.gm_authFailure = () => {
      cb.current.onUnavailable("authentication rejected — check the key's referrer restrictions and billing");
    };
    return () => {
      window.gm_authFailure = previous;
    };
  }, []);

  useEffect(() => {
    if (ready) return;
    // Backstop for the failures that announce themselves neither way: a stalled
    // script fetch, a blocked host, an extension eating the request.
    const t = setTimeout(
      () => cb.current.onUnavailable(`no map after ${READY_TIMEOUT_MS / 1000}s`),
      READY_TIMEOUT_MS
    );
    return () => clearTimeout(t);
  }, [ready]);

  /* --------------------------------------------------------------- bootstrap */
  useEffect(() => {
    if (!hostRef.current) return;
    let cancelled = false;

    // v2 of the loader replaced the `Loader` class with this functional API; the
    // class is still exported but deprecated.
    setOptions({ key: API_KEY, v: "weekly" });
    Promise.all([importLibrary("maps"), importLibrary("marker")])
      .then(([{ Map: GMap }]) => {
        if (cancelled || !hostRef.current) return;

        const map = new GMap(hostRef.current, {
          center: KARNATAKA_CENTER,
          zoom: DEFAULT_ZOOM,
          minZoom: 6,
          maxZoom: 18,
          styles: MAP_STYLE,
          mapTypeControl: false,
          streetViewControl: false,
          fullscreenControl: false,
          backgroundColor: "#f3f4f6",
          clickableIcons: false,
        });
        mapRef.current = map;
        infoRef.current = new google.maps.InfoWindow();

        // `idle` rather than `bounds_changed`: it fires once the pan or zoom has
        // settled, so the parent is not re-rendered on every frame of a drag.
        map.addListener("idle", () => {
          const zoom = map.getZoom() ?? DEFAULT_ZOOM;
          cb.current.onZoomChange(zoom);
          const b = map.getBounds();
          if (!b) return;
          const { shown, total, capped } = selectVisible(
            cb.current.points,
            (p) => b.contains({ lat: p.lat, lng: p.lng }),
            zoom
          );
          cb.current.onViewportChange(shown);
          setDensity({ total, shown: shown.length, capped, zoom });
        });

        setReady(true);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // A rejected loader is nearly always a key problem — unreferrered, restricted,
        // or billing not enabled. Hand it upward so the page can fall back to Leaflet
        // instead of showing the dead rectangle this engine used to leave behind.
        cb.current.onUnavailable(e instanceof Error ? e.message : String(e));
      });

    return () => {
      cancelled = true;
    };
  }, []);

  /* ------------------------------------------------------------ resize / focus */
  useEffect(() => {
    if (!mapRef.current) return;
    const c = mapRef.current.getCenter();
    google.maps.event.trigger(mapRef.current, "resize");
    if (c) mapRef.current.setCenter(c);
  }, [resizeKey, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;

    if (focusCaseId) {
      const hit = points.find((p) => p.case_id === focusCaseId);
      if (hit) {
        map.panTo({ lat: hit.lat, lng: hit.lng });
        map.setZoom(15);
        return;
      }
    }
    if (district) {
      const inDistrict = points.filter((p) => p.district === district);
      if (inDistrict.length) {
        const bounds = new google.maps.LatLngBounds();
        inDistrict.forEach((p) => bounds.extend({ lat: p.lat, lng: p.lng }));
        map.fitBounds(bounds, 48);
        return;
      }
      const row = dispatch.find((d: any) => d.district === district);
      if (row?.lat != null) {
        map.setCenter({ lat: row.lat, lng: row.lng });
        map.setZoom(11);
        return;
      }
    }
    map.setCenter(KARNATAKA_CENTER);
    map.setZoom(DEFAULT_ZOOM);
  }, [focusCaseId, district, points, dispatch, ready]);

  /* ------------------------------------------------------------------ layers */
  const clearLayer = useCallback((ref: { current: Array<{ setMap: (m: null) => void }> }) => {
    ref.current.forEach((o) => o.setMap(null));
    ref.current = [];
  }, []);

  const openInfo = useCallback(
    (anchor: google.maps.Marker | google.maps.Circle, html: string, at?: google.maps.LatLngLiteral) => {
      const map = mapRef.current;
      if (!map || !infoRef.current) return;
      infoRef.current.setContent(html);
      if (at) infoRef.current.setPosition(at);
      if (anchor instanceof google.maps.Marker) infoRef.current.open({ map, anchor });
      else infoRef.current.open({ map });
    },
    []
  );

  // DBSCAN zones — metres, not pixels, so the drawn radius is the reported one.
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    clearLayer(zonesRef);
    if (!showClusters) return;

    zonesRef.current = dbscan.map((c: any) => {
      const circle = new google.maps.Circle({
        map: mapRef.current!,
        center: { lat: c.lat, lng: c.lng },
        radius: Math.max(600, (c.radius_km || 1) * 1000),
        strokeColor: "#2f74c0",
        strokeWeight: 1.5,
        strokeOpacity: 0.75,
        fillColor: "#2f74c0",
        fillOpacity: 0.1,
        clickable: true,
      });
      circle.addListener("click", (e: google.maps.MapMouseEvent) => {
        openInfo(
          circle,
          popupShell(
            `<div style="font-weight:700;color:#2f74c0;font-size:11px;text-transform:uppercase;letter-spacing:.4px">DBSCAN cluster</div>
             <div style="color:#1d1d1f;font-weight:700;margin-top:3px;font-size:14px">${c.dominant_crime ?? "Cluster"}</div>
             <div style="color:#6b6b73;font-size:12px;margin-top:3px">${c.cases} incidents · ${c.district}<br>~${c.radius_km} km radius · mean severity ${c.avg_severity}</div>`
          ),
          e.latLng?.toJSON() ?? { lat: c.lat, lng: c.lng }
        );
      });
      return circle;
    });
  }, [dbscan, showClusters, ready, clearLayer, openInfo]);

  // KDE hotspots — pixel radius scaled by intensity.
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    clearLayer(hotspotRef);

    hotspotRef.current = clusters.map((c: any, idx: number) => {
      const marker = new google.maps.Marker({
        map: mapRef.current!,
        position: { lat: c.lat, lng: c.lng },
        zIndex: 20,
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 8 + (c.intensity ?? 1) * 7,
          fillColor: "#b07d16",
          fillOpacity: 0.85,
          strokeColor: "#ffffff",
          strokeWeight: 2,
        },
        title: `Predicted hotspot ${c.id ?? idx}`,
      });
      marker.addListener("click", () =>
        openInfo(
          marker,
          popupShell(
            `<div style="font-weight:700;color:#b07d16;font-size:11px;text-transform:uppercase;letter-spacing:.4px">Predicted hotspot</div>
             <div style="color:#6b6b73;font-size:12px;margin-top:3px">KDE intensity ${c.intensity ?? "n/a"}</div>`
          )
        )
      );
      return marker;
    });
  }, [clusters, ready, clearLayer, openInfo]);

  // Individual FIRs. The parent only holds what survived the zoom gate and the
  // marker budget, so this maps the whole array.
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    clearLayer(firRef);

    firRef.current = visiblePoints.map((p) => {
      const marker = new google.maps.Marker({
        map: mapRef.current!,
        position: { lat: p.lat, lng: p.lng },
        zIndex: 10,
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 4 + (p.severity / 100) * 4,
          fillColor: sevColor(p.severity),
          fillOpacity: 0.9,
          strokeColor: "#ffffff",
          strokeWeight: 1.2,
        },
        title: `${p.case_id} · ${p.crime_type}`,
      });
      marker.addListener("click", () => {
        openInfo(
          marker,
          popupShell(
            `<div style="min-width:190px">
               <div style="font-weight:700;color:#b07d16;font-size:13px">${p.case_id}</div>
               <div style="color:#1d1d1f;font-weight:600;margin-top:2px">${p.crime_type}</div>
               <div style="color:#6b6b73;font-size:12px;margin-top:2px">${p.district} · ${p.police_station || "Unknown PS"} · severity ${p.severity}${p.status ? ` · ${p.status}` : ""}</div>
               <button id="ksp-open-${p.case_id}" style="margin-top:8px;width:100%;background:#b07d16;color:#fff;border:none;border-radius:8px;padding:7px 10px;font-weight:700;font-size:12px;cursor:pointer">Open dossier →</button>
             </div>`
          )
        );
        // The InfoWindow renders its HTML outside React, so the button has to be
        // wired after it exists. `domready` is the only point at which it does.
        google.maps.event.addListenerOnce(infoRef.current!, "domready", () => {
          document
            .getElementById(`ksp-open-${p.case_id}`)
            ?.addEventListener("click", () => cb.current.onOpenCase(p.case_id));
        });
      });
      return marker;
    });
  }, [visiblePoints, ready, clearLayer, openInfo]);

  // Recommended patrol strength per district.
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    clearLayer(patrolRef);
    if (!showPatrol) return;

    patrolRef.current = dispatch.map((d: any) => {
      const marker = new google.maps.Marker({
        map: mapRef.current!,
        position: { lat: d.lat, lng: d.lng },
        zIndex: 30,
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 13,
          fillColor: riskColor(d.risk_level),
          fillOpacity: 0.95,
          strokeColor: "#ffffff",
          strokeWeight: 2,
        },
        title: `${d.district} · ${d.risk_level} risk`,
      });
      marker.addListener("click", () =>
        openInfo(
          marker,
          popupShell(
            `<div style="font-weight:700;color:#1d1d1f;font-size:13px">${d.district}</div>
             <div style="color:#6b6b73;font-size:12px;margin-top:2px">${d.risk_level} risk · <strong>${d.recommended_officers}</strong> officers recommended</div>`
          )
        )
      );
      return marker;
    });
  }, [dispatch, showPatrol, ready, clearLayer, openInfo]);

  return (
    <>
      <MapDescription
        visiblePoints={visiblePoints}
        clusters={clusters}
        district={district}
        showClusters={showClusters}
        zoom={density.zoom}
        engine="Google Maps"
      />

      <div ref={hostRef} style={{ width: "100%", height: "100%", background: "#f3f4f6" }} />

      {!ready && (
        <div
          role="status"
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            background: "#f3f4f6",
          }}
        >
          <p style={{ color: "#6b6b73", fontSize: 13 }}>Loading the basemap…</p>
        </div>
      )}

      <DensityNotice
        zoom={density.zoom}
        total={density.total}
        shown={density.shown}
        capped={density.capped}
      />
    </>
  );
}
