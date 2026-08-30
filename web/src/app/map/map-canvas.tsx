"use client";

/**
 * Picks the map engine.
 *
 * Google Maps when a key exists, Leaflet when it does not — and Leaflet also when
 * Google was supposed to work but did not, which is the case that actually bites.
 * A key can be present and still fail: restricted to the wrong referrer, billing
 * switched off, quota exhausted, revoked. Previously that left a dead grey rectangle
 * where the platform's flagship page should be, and it is a failure that only shows
 * up in front of an audience.
 *
 * So the rule is: the map always draws. The engine may differ; the page does not
 * break. Both engines render the same layers with the same colours, the same zoom
 * gate and the same marker budget, all defined once in map-shared.
 *
 * The key is read from NEXT_PUBLIC_GOOGLE_MAPS_API_KEY, which belongs in
 * `web/.env.local` — gitignored. It must NOT go in `.env.production`, which is
 * tracked; a key committed there is exactly how the previous one ended up public.
 * `set_keys.py --maps <key>` at the repo root writes it correctly.
 *
 * Both engines are loaded through next/dynamic so the unused one is never fetched,
 * and because Leaflet and the Maps loader both reach for `window` at import.
 */

import { useCallback, useState } from "react";
import dynamic from "next/dynamic";
import { Layers } from "lucide-react";

import type { MapCanvasProps } from "./map-shared";

export type { MapPoint } from "./map-shared";
export { MARKER_ZOOM } from "./map-shared";

const Loading = () => (
  <div className="flex h-full w-full flex-col items-center justify-center bg-[#f3f4f6]">
    <Layers className="mb-3 h-7 w-7 animate-pulse text-brand" />
    <p className="text-sm text-muted">Loading geospatial engine…</p>
  </div>
);

const GoogleCanvas = dynamic(() => import("./map-google"), { ssr: false, loading: Loading });
const LeafletCanvas = dynamic(() => import("./map-leaflet"), { ssr: false, loading: Loading });

/**
 * Inlined at build time by Next, so this is a constant in the bundle rather than a
 * runtime lookup — which is what lets the unused engine's chunk go unfetched.
 */
const HAS_KEY = Boolean(process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY);

export default function MapCanvas(props: MapCanvasProps) {
  const [googleFailed, setGoogleFailed] = useState<string | null>(null);

  const onUnavailable = useCallback((reason: string) => {
    // Worth a console line: falling back silently would hide a broken key from
    // whoever configured it, and the page would look fine while costing nothing.
    console.warn(`Google Maps unavailable, falling back to Leaflet: ${reason}`);
    setGoogleFailed(reason);
  }, []);

  if (HAS_KEY && !googleFailed) {
    return <GoogleCanvas {...props} onUnavailable={onUnavailable} />;
  }

  return (
    <>
      <LeafletCanvas {...props} />
      {googleFailed && (
        <div
          className="pointer-events-none absolute right-4 top-4 z-[1000] max-w-[15rem] rounded-2xl border border-black/10 bg-white/92 px-3 py-2 text-[11px] leading-snug text-muted backdrop-blur-xl"
          role="status"
        >
          <span className="font-semibold text-ink">Using the keyless basemap.</span> The Google Maps key
          was rejected — check its referrer restrictions and billing.
        </div>
      )}
    </>
  );
}
