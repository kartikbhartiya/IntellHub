/**
 * Client for /serverless/api/network/*. Every call goes through getJSON so the
 * bearer token is attached and a 403 surfaces as ApiError.isForbidden — the page
 * renders a sign-in state for that, not a generic failure.
 */
import { getJSON } from "@/lib/api";
import type {
  ExploreResponse,
  EntityProfile,
  Facet,
  Filters,
  NetworkMeta,
  PathResponse,
  SearchResult,
  Syndicate,
} from "./types";
import { ENTITY_TYPES } from "./types";

function filterParams(f: Filters, qs: URLSearchParams) {
  // Sending every type is the same as sending none; leaving it off keeps URLs short.
  if (f.types.length && f.types.length < ENTITY_TYPES.length) qs.set("types", f.types.join(","));
  qs.set("depth", String(f.depth));
  if (f.district) qs.set("district", f.district);
  if (f.crimeType) qs.set("crime_type", f.crimeType);
  if (f.yearFrom) qs.set("year_from", f.yearFrom);
  if (f.yearTo) qs.set("year_to", f.yearTo);
  if (f.rowdy) qs.set("rowdy", "1");
  if (f.warrant) qs.set("warrant", "1");
  if (f.maxNodes) qs.set("max_nodes", String(f.maxNodes));
}

export type ExploreTarget =
  | { kind: "overview" }
  | { kind: "seed"; ids: string[] }
  | { kind: "syndicate"; id: string }
  | { kind: "query"; q: string };

export function explore(
  target: ExploreTarget,
  filters: Filters,
  opts: { maxNodes?: number; depth?: 1 | 2 } = {},
): Promise<ExploreResponse> {
  const qs = new URLSearchParams();
  filterParams(filters, qs);
  if (opts.depth) qs.set("depth", String(opts.depth));
  if (opts.maxNodes) qs.set("max_nodes", String(opts.maxNodes));
  if (target.kind === "seed") qs.set("seed", target.ids.join(","));
  // The depth control is authoritative for every load, syndicates included: a
  // silent override made the UI (and the CSV scope) state a depth that was not used.
  if (target.kind === "syndicate") qs.set("syndicate", target.id);
  if (target.kind === "query") qs.set("q", target.q);
  return getJSON<ExploreResponse>(`/network/explore?${qs}`);
}

export function search(q: string): Promise<{ results: SearchResult[]; facets: Facet[] }> {
  return getJSON(`/network/search?q=${encodeURIComponent(q)}`);
}

export function findPaths(from: string, to: string, k: number, viaOfficers: boolean): Promise<PathResponse> {
  const qs = new URLSearchParams({ from, to, k: String(k) });
  if (viaOfficers) qs.set("via_officers", "1");
  return getJSON<PathResponse>(`/network/path?${qs}`);
}

export function entity(id: string): Promise<EntityProfile> {
  return getJSON<EntityProfile>(`/network/entity/${encodeURIComponent(id)}`);
}

export function syndicates(): Promise<{ syndicates: Syndicate[]; total: number; method: string }> {
  return getJSON(`/network/syndicates`);
}

export function meta(): Promise<NetworkMeta> {
  return getJSON<NetworkMeta>(`/network/meta`);
}
