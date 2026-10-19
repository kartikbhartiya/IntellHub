"use client";

/**
 * Link analysis workbench over the knowledge graph.
 *
 * The previous page fetched a fixed slice of /network (first ten cases, first six
 * neighbours of each) and drew it; a hub returned an arbitrary subset and nothing
 * said which. This one works the way link analysis is actually done: find an
 * entity by whatever identifier you hold, expand outward under a visible budget,
 * ask how two entities connect, and read a profile without leaving the chart.
 *
 * Contracts kept from the old page, because the voice assistant navigates with
 * them: `?suspect_id=`, `?case_id=` and `?q=`. A suspect_id or case_id that is not
 * an exact graph id (the assistant sometimes passes a name or a partial FIR
 * number) falls back to a search, as the old endpoint's substring match did.
 * `?syndicate=` and `?seed=` are new.
 */
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Box, Crosshair, Expand, EyeOff, FileDown, ImageDown, LoaderCircle, Maximize2, Minus, Network,
  Plus, RotateCcw, Route, Shrink, SlidersHorizontal, Snowflake, TriangleAlert, Users, Waypoints, X,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { exportCSV } from "@/lib/export";
import { useIdentity } from "@/lib/session";
import AccessRequired from "@/components/AccessRequired";
import SearchBox, { TypeIcon } from "@/components/network/SearchBox";
import FilterPanel, { Segmented } from "@/components/network/FilterPanel";
import PathPanel from "@/components/network/PathPanel";
import SyndicatePanel from "@/components/network/SyndicatePanel";
import EntityPanel from "@/components/network/EntityPanel";
import Legend from "@/components/network/Legend";
import { explore, findPaths, meta as fetchMeta, type ExploreTarget } from "@/components/network/netApi";
import {
  DEFAULT_FILTERS, linkEnd,
  type ColorBy, type EntityType, type Facet, type Filters, type Focus, type GraphControls,
  type NetNode, type NetworkMeta, type PathResponse, type SearchResult, type ViewStats,
} from "@/components/network/types";
import { TYPE_STYLE } from "@/components/network/style";
import {
  EMPTY_GRAPH, clearPins, communityCount, graphFrom, mergeGraph, pairKey, setFrozenPins, visibleGraph,
  type GraphState,
} from "@/components/network/util";

const CanvasLoading = () => (
  <div className="absolute inset-0 grid place-items-center text-[12px] text-muted">
    <span className="flex items-center gap-2"><LoaderCircle className="h-4 w-4 animate-spin" /> Loading renderer…</span>
  </div>
);
const Graph2D = dynamic(() => import("@/components/network/Graph2D"), { ssr: false, loading: CanvasLoading });
const Graph3D = dynamic(() => import("@/components/network/Graph3D"), { ssr: false, loading: CanvasLoading });

type Mode = "explore" | "path" | "syndicates";
type Target = ExploreTarget & { fallbackQ?: string };
type Status = "idle" | "ok" | "forbidden" | "error";

const EXPAND_NODES = 80;
// Merging expansions has no server-side cap, so the page holds one: past this the
// canvas stops being readable (and stops holding 60fps on an ordinary laptop).
const VIEW_CEILING = 600;

function targetFromUrl(sp: URLSearchParams): Target {
  const sid = sp.get("suspect_id")?.trim();
  const cid = sp.get("case_id")?.trim();
  const seed = sp.get("seed")?.trim();
  const syn = sp.get("syndicate")?.trim();
  const q = sp.get("q")?.trim();
  if (sid) return { kind: "seed", ids: [sid], fallbackQ: sid };
  if (cid) return { kind: "seed", ids: [cid], fallbackQ: cid };
  if (seed) return { kind: "seed", ids: seed.split(",").map((s) => s.trim()).filter(Boolean), fallbackQ: seed };
  if (syn) return { kind: "syndicate", id: syn };
  if (q) return { kind: "query", q };
  return { kind: "overview" };
}

function asResult(n: NetNode): SearchResult {
  return { id: n.id, type: n.type, label: n.label, sub: n.sub, risk: n.risk, rowdy: n.rowdy, warrant: n.warrant, matched: null, score: 0 };
}

function NetworkInner() {
  const searchParams = useSearchParams();
  const { epoch } = useIdentity();
  const urlKey = searchParams.toString();
  const urlTarget = useMemo(() => targetFromUrl(new URLSearchParams(urlKey)), [urlKey]);
  // A target the investigator chose wins until the URL changes again (the voice
  // assistant navigating to someone new), without an effect copying URL to state.
  const [picked, setPicked] = useState<{ key: string; target: Target } | null>(null);
  const target = picked && picked.key === urlKey ? picked.target : urlTarget;
  const choose = useCallback((t: Target) => setPicked({ key: urlKey, target: t }), [urlKey]);

  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [graph, setGraph] = useState<GraphState>(EMPTY_GRAPH);
  const graphRef = useRef<GraphState>(EMPTY_GRAPH);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [focus, setFocus] = useState<Focus | null>(null);
  const [stats, setStats] = useState<ViewStats | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const loadSeq = useRef(0);
  const [meta, setMeta] = useState<NetworkMeta | null>(null);

  const [mode, setMode] = useState<Mode>(urlTarget.kind === "syndicate" ? "syndicates" : "explore");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [colorBy, setColorBy] = useState<ColorBy>(urlTarget.kind === "syndicate" ? "community" : "type");
  const [showPredicted, setShowPredicted] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  const [view3D, setView3D] = useState(false);
  const [frozen, setFrozen] = useState(false);
  const [fitToken, setFitToken] = useState(0);
  const [fitIds, setFitIds] = useState<string[] | null>(null);
  const [addToView, setAddToView] = useState(false);
  const [ctxMenu, setCtxMenu] = useState<{ node: NetNode; x: number; y: number } | null>(null);
  const [toast, setToast] = useState<{ text: string; tone?: "warn" } | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [legendOpen, setLegendOpen] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [pathFrom, setPathFrom] = useState<SearchResult | null>(null);
  const [pathTo, setPathTo] = useState<SearchResult | null>(null);
  const [pathK, setPathK] = useState(3);
  const [viaOfficers, setViaOfficers] = useState(false);
  const [pathResult, setPathResult] = useState<PathResponse | null>(null);
  const [pathLoading, setPathLoading] = useState(false);
  const [pathError, setPathError] = useState<string | null>(null);
  const [activePath, setActivePath] = useState<number | null>(null);

  const controlRef = useRef<GraphControls | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 600 });

  const commit = useCallback((next: GraphState) => {
    graphRef.current = next;
    setGraph(next);
  }, []);

  const flash = useCallback((text: string, tone?: "warn") => setToast({ text, tone }), []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4200);
    return () => clearTimeout(t);
  }, [toast]);

  // ------------------------------------------------------------ size
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ width: el.clientWidth, height: el.clientHeight }));
    ro.observe(el);
    setSize({ width: el.clientWidth, height: el.clientHeight });
    return () => ro.disconnect();
  }, [status]);

  // ------------------------------------------------------------ fullscreen
  const toggleFullscreen = useCallback(() => {
    if (!fullscreen) {
      document.documentElement.requestFullscreen?.().catch(() => {});
      document.documentElement.setAttribute("data-fullscreen", "true");
      setFullscreen(true);
    } else {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      document.documentElement.removeAttribute("data-fullscreen");
      setFullscreen(false);
    }
  }, [fullscreen]);
  useEffect(() => {
    const onFs = () => {
      if (!document.fullscreenElement) {
        document.documentElement.removeAttribute("data-fullscreen");
        setFullscreen(false);
      }
    };
    document.addEventListener("fullscreenchange", onFs);
    return () => {
      document.removeEventListener("fullscreenchange", onFs);
      document.documentElement.removeAttribute("data-fullscreen");
    };
  }, []);

  // ------------------------------------------------------------ data
  useEffect(() => {
    let alive = true;
    fetchMeta().then((m) => alive && setMeta(m)).catch(() => alive && setMeta(null));
    return () => {
      alive = false;
    };
  }, [epoch]);

  const load = useCallback((first: Target, f: Filters): Promise<void> => {
    const run = async (t: Target): Promise<void> => {
    const mine = ++loadSeq.current;
    setLoading(true);
    setCtxMenu(null);
    try {
      const res = await explore(t, f);
      if (mine !== loadSeq.current) return;
      commit(graphFrom(res));
      setHidden(new Set());
      setStats(res.stats);
      setNote(res.note);
      setTruncated(res.truncated);
      setFocus(res.focus);
      setStatus("ok");
      setErrorMsg(null);
      setFitIds(null);
      setFitToken((x) => x + 1);
      const ids = res.focus.kind === "seed" || res.focus.kind === "query" ? res.focus.ids : [];
      setSelectedId(ids.length === 1 ? ids[0] : null);
    } catch (e) {
      if (mine !== loadSeq.current) return;
      if (e instanceof ApiError && e.status === 404 && t.fallbackQ) {
        // Not an exact id: search for it instead, as the old ?suspect_id= did.
        return run({ kind: "query", q: t.fallbackQ });
      }
      if (e instanceof ApiError && e.isForbidden) {
        setStatus("forbidden");
      } else {
        setStatus("error");
        setErrorMsg(e instanceof ApiError && e.status === 404
          ? "That entity is not in the knowledge graph."
          : e instanceof Error ? e.message : "The intelligence core did not answer.");
      }
    } finally {
      if (mine === loadSeq.current) setLoading(false);
    }
    };
    return run(first);
  }, [commit]);

  useEffect(() => {
    load(target, filters);
  }, [target, filters, load, epoch]);

  // ------------------------------------------------------------ derived
  const visible = useMemo(() => visibleGraph(graph, hidden), [graph, hidden]);
  const byId = useMemo(() => new Map(visible.nodes.map((n) => [n.id, n])), [visible.nodes]);
  const selected = selectedId ? byId.get(selectedId) || null : null;
  const present = useMemo(() => new Set<EntityType>(visible.nodes.map((n) => n.type)), [visible.nodes]);
  const byType = useMemo(() => {
    const c: Partial<Record<EntityType, number>> = {};
    for (const n of visible.nodes) c[n.type] = (c[n.type] || 0) + 1;
    return c;
  }, [visible.nodes]);
  const communities = useMemo(() => communityCount(visible.nodes), [visible.nodes]);

  const highlight = useMemo(() => {
    if (mode !== "path" || !pathResult || !pathResult.paths.length) return null;
    const paths = activePath === null ? pathResult.paths : [pathResult.paths[activePath]].filter(Boolean);
    const nodes = new Set<string>();
    const pairs = new Set<string>();
    for (const p of paths) {
      p.nodes.forEach((id) => nodes.add(id));
      p.nodes.slice(1).forEach((id, i) => pairs.add(pairKey(p.nodes[i], id)));
    }
    return { nodes, pairs };
  }, [mode, pathResult, activePath]);

  // ------------------------------------------------------------ actions
  const expand = useCallback(async (id: string) => {
    setCtxMenu(null);
    if (graphRef.current.nodes.length >= VIEW_CEILING) {
      flash(`This view already holds ${graphRef.current.nodes.length} entities. Hide some, or reset, before expanding further.`, "warn");
      return;
    }
    setBusyId(id);
    try {
      const before = new Set(graphRef.current.nodes.map((n) => n.id));
      const room = Math.max(10, Math.min(EXPAND_NODES, VIEW_CEILING - graphRef.current.nodes.length));
      const seq = loadSeq.current;
      const res = await explore({ kind: "seed", ids: [id] }, filters, { depth: 1, maxNodes: room });
      // A reload while this was in flight replaced the view; merging into it would
      // mix two investigations.
      if (seq !== loadSeq.current) return;
      // The expanded node keeps its place in the hierarchy rather than becoming a seed.
      const incoming = res.nodes.map((n) => (n.id === id ? { ...n, hop: before.has(id) ? 9 : 0 } : n));
      commit(mergeGraph(graphRef.current, { ...res, nodes: incoming }, { anchorId: id, frozen }));
      setHidden((h) => {
        if (!h.has(id)) return h;
        const next = new Set(h);
        next.delete(id);
        return next;
      });
      const added = res.nodes.filter((n) => !before.has(n.id)).length;
      const label = res.nodes.find((n) => n.id === id)?.label || id;
      if (res.truncated) {
        flash(`+${added} around ${label}. ${res.stats.left_out} more neighbours were left out by the ${room}-node expansion cap.`, "warn");
      } else {
        flash(added ? `+${added} around ${label}.` : `Everything around ${label} is already on screen (with these filters).`);
      }
    } catch (e) {
      flash(e instanceof ApiError && e.isForbidden ? "Your sign-in no longer allows this." : "Expansion failed — the intelligence core did not answer.", "warn");
    } finally {
      setBusyId(null);
    }
  }, [filters, frozen, commit, flash]);

  const hide = useCallback((id: string) => {
    setCtxMenu(null);
    setHidden((h) => new Set(h).add(id));
    setSelectedId((s) => (s === id ? null : s));
  }, []);

  const select = useCallback((n: NetNode | null) => {
    setCtxMenu(null);
    setSelectedId(n ? n.id : null);
  }, []);

  const openEntity = useCallback(async (id: string) => {
    if (byId.has(id)) {
      setSelectedId(id);
      controlRef.current?.focusNode(id);
      return;
    }
    try {
      const seq = loadSeq.current;
      const res = await explore({ kind: "seed", ids: [id] }, filters, { depth: 1, maxNodes: 40 });
      if (seq !== loadSeq.current) return;
      commit(mergeGraph(graphRef.current, res, { anchorId: selectedId, frozen }));
      setHidden((h) => {
        if (!h.has(id)) return h;
        const next = new Set(h);
        next.delete(id);
        return next;
      });
      setSelectedId(id);
    } catch {
      flash("That entity could not be added.", "warn");
    }
  }, [byId, filters, selectedId, frozen, commit, flash]);

  const pickSearch = useCallback((r: SearchResult) => {
    if (addToView && graphRef.current.nodes.length) {
      openEntity(r.id);
      return;
    }
    choose({ kind: "seed", ids: [r.id] });
  }, [addToView, choose, openEntity]);

  const pickFacet = useCallback((f: Facet) => {
    if (f.field === "crime_type") setFilters((x) => ({ ...x, crimeType: f.value }));
    else if (f.field === "district") setFilters((x) => ({ ...x, district: f.value }));
    else choose({ kind: "query", q: f.value });
  }, [choose]);

  const openSyndicate = useCallback((id: string) => {
    setMode("syndicates");
    setColorBy("community");
    choose({ kind: "syndicate", id });
  }, [choose]);

  const pathFromNode = useCallback((n: NetNode) => {
    setCtxMenu(null);
    setMode("path");
    setPathFrom(asResult(n));
    setPathResult(null);
  }, []);

  const runPath = useCallback(async () => {
    if (!pathFrom || !pathTo) return;
    setPathLoading(true);
    setPathError(null);
    try {
      const seq = loadSeq.current;
      const res = await findPaths(pathFrom.id, pathTo.id, pathK, viaOfficers);
      if (seq !== loadSeq.current) return;
      setPathResult(res);
      setActivePath(res.paths.length ? 0 : null);
      if (res.graph.nodes.length) {
        const anchor = graphRef.current.nodes.some((n) => n.id === pathFrom.id) ? pathFrom.id : null;
        commit(mergeGraph(graphRef.current, res.graph, { anchorId: anchor, frozen }));
        const ids = res.graph.nodes.map((n) => n.id);
        setHidden((h) => {
          if (!ids.some((id) => h.has(id))) return h;
          const next = new Set(h);
          ids.forEach((id) => next.delete(id));
          return next;
        });
        // Frame the path once the merged nodes have settled, with the profile
        // panel closed so it does not cover the far end.
        setSelectedId(null);
        setFitIds(ids);
        setFitToken((x) => x + 1);
      }
    } catch (e) {
      setPathResult(null);
      setPathError(e instanceof ApiError && e.isForbidden ? "Sign in as an investigator to trace paths."
        : e instanceof ApiError && e.status === 404 ? "One of the two entities is not in the graph."
          : "Path search failed — the intelligence core did not answer.");
    } finally {
      setPathLoading(false);
    }
  }, [pathFrom, pathTo, pathK, viaOfficers, frozen, commit]);

  const toggleFreeze = useCallback(() => {
    const next = !frozen;
    setFrozenPins(graphRef.current.nodes, next);
    if (!next) controlRef.current?.reheat();
    setFrozen(next);
  }, [frozen]);

  const reset = useCallback(() => {
    clearPins(graphRef.current.nodes);
    setFrozen(false);
    setPathResult(null);
    setSelectedId(null);
    load(target, filters);
  }, [load, target, filters]);

  const exportEdges = useCallback(() => {
    const rows = visible.links.map((l) => {
      const s = byId.get(linkEnd(l.source));
      const t = byId.get(linkEnd(l.target));
      return {
        source: s?.id, source_label: s?.label, source_type: s ? TYPE_STYLE[s.type].label : "",
        target: t?.id, target_label: t?.label, target_type: t ? TYPE_STYLE[t.type].label : "",
        relationship: l.relationship, weight: l.weight,
        provenance: l.derived ? "derived from shared FIRs" : "recorded",
        shared_firs: l.shared_cases ?? "",
      };
    });
    const scope = [
      focusLabel(focus, byId),
      `loaded at depth ${stats?.depth ?? filters.depth}`,
      filters.types.length < 5 ? `types=${filters.types.join("+")}` : "",
      filters.district && `district=${filters.district}`,
      filters.crimeType && `crime_type=${filters.crimeType}`,
      filters.yearFrom && `from=${filters.yearFrom}`,
      filters.yearTo && `to=${filters.yearTo}`,
      filters.rowdy && "rowdy-sheeters only",
      filters.warrant && "active warrants only",
      hidden.size ? `${hidden.size} hidden by the analyst` : "",
      truncated ? "view was capped by the server" : "",
    ].filter(Boolean).join("; ");
    exportCSV(rows, [
      { key: "source", label: "Source id" }, { key: "source_label", label: "Source" }, { key: "source_type", label: "Source type" },
      { key: "target", label: "Target id" }, { key: "target_label", label: "Target" }, { key: "target_type", label: "Target type" },
      { key: "relationship", label: "Relationship" }, { key: "weight", label: "Weight" },
      { key: "provenance", label: "Provenance" }, { key: "shared_firs", label: "Shared FIRs" },
    ], { title: "Network link chart edges", scope, simulated: true });
  }, [visible.links, byId, focus, filters, hidden.size, truncated, stats?.depth]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setCtxMenu(null);
        if (!(e.target instanceof HTMLInputElement)) setSelectedId(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ------------------------------------------------------------ render
  const Graph = view3D ? Graph3D : Graph2D;
  const syndicateId = focus?.kind === "syndicate" ? focus.id : null;

  return (
    <div className={fullscreen ? "fixed inset-0 z-[200] flex flex-col gap-3 bg-base p-3" : "space-y-4"}>
      {!fullscreen && (
        <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.22em] text-brand">
              <Network className="h-3.5 w-3.5" /> Knowledge graph
            </div>
            <h1 className="text-display mt-1 text-4xl font-bold tracking-tight text-ink">Link Analysis</h1>
            <p className="mt-1 max-w-3xl text-[14px] text-muted">
              {meta
                ? `${meta.graph.nodes.toLocaleString()} entities and ${meta.graph.edges.toLocaleString()} recorded links across FIRs, people, officers, vehicles and cyber identifiers. `
                : "FIRs, people, officers, vehicles and cyber identifiers. "}
              Search, expand under a visible cap, trace how two entities connect, and load detected syndicates.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPanelOpen((v) => !v)}
              className="flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] font-semibold text-ink shadow-sm hover:border-brand/40 lg:hidden"
            >
              <SlidersHorizontal className="h-4 w-4" /> {panelOpen ? "Hide panel" : "Show panel"}
            </button>
            <button onClick={toggleFullscreen} className="flex items-center gap-1.5 rounded-full bg-ink px-4 py-2 text-[12.5px] font-semibold text-white shadow-sm hover:bg-black">
              <Expand className="h-4 w-4" /> Full screen
            </button>
          </div>
        </header>
      )}

      {status === "forbidden" ? (
        <div className="bezel"><div className="bezel-core"><AccessRequired what="The criminal knowledge graph" requires="investigator" /></div></div>
      ) : (
        // Sized so the workspace ends above the Kavya console that floats over the
        // bottom of every page (full screen hides the console and takes it all).
        <div className={`grid min-h-0 gap-3 ${fullscreen ? "flex-1 grid-cols-[300px_minmax(0,1fr)]"
          : `lg:h-[calc(100dvh-22rem)] lg:min-h-[540px] ${panelOpen ? "lg:grid-cols-[300px_minmax(0,1fr)]" : "grid-cols-1"}`}`}>
          {/* ------------------------------------------------ left panel */}
          {(panelOpen || fullscreen) && (
            <aside className={`flex min-h-0 flex-col overflow-hidden rounded-[1.25rem] border border-black/10 bg-white shadow-[0_10px_30px_-18px_rgba(16,18,30,0.3)] ${
              fullscreen ? "h-full" : "max-h-[70vh] lg:h-full lg:max-h-none"}`}>
              <div className="border-b border-black/[0.06] p-3">
                <Segmented
                  value={mode}
                  options={[{ v: "explore", label: "Explore" }, { v: "path", label: "Find path" }, { v: "syndicates", label: "Syndicates" }]}
                  onChange={(v) => setMode(v as Mode)}
                />
              </div>
              <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3.5">
                {mode === "explore" && (
                  <>
                    <div className="space-y-2">
                      <SearchBox onPick={pickSearch} onFacet={pickFacet} autoFocus={!target || target.kind === "overview"} />
                      <label className="flex items-center gap-2 text-[11.5px] text-muted">
                        <input type="checkbox" checked={addToView} onChange={(e) => setAddToView(e.target.checked)} className="h-3.5 w-3.5 accent-[#b07d16]" />
                        Add the pick to the current view instead of replacing it
                      </label>
                    </div>
                    <FocusCard focus={focus} byId={byId} loading={loading} onOverview={() => choose({ kind: "overview" })} />
                  </>
                )}
                {mode === "path" && (
                  <>
                    <PathPanel
                      from={pathFrom} to={pathTo}
                      onFrom={(r) => { setPathFrom(r); setPathResult(null); }}
                      onTo={(r) => { setPathTo(r); setPathResult(null); }}
                      k={pathK} onK={setPathK}
                      viaOfficers={viaOfficers} onViaOfficers={setViaOfficers}
                      onRun={runPath} loading={pathLoading} error={pathError}
                      result={pathResult} active={activePath} onActive={setActivePath}
                      onSelectNode={(id) => openEntity(id)}
                    />
                    <p className="text-[10.5px] leading-snug text-faint">Paths search the whole graph; the filters below shape only what Explore loads.</p>
                  </>
                )}
                {mode === "syndicates" && <SyndicatePanel activeId={syndicateId} onLoad={openSyndicate} />}

                <div className="border-t border-black/[0.06] pt-4">
                  <FilterPanel
                    filters={filters} onFilters={setFilters} meta={meta}
                    colorBy={colorBy} onColorBy={setColorBy}
                    showPredicted={showPredicted} onShowPredicted={setShowPredicted}
                    predictedCount={visible.predicted.length}
                    showLabels={showLabels} onShowLabels={setShowLabels}
                  />
                </div>
              </div>
            </aside>
          )}

          {/* ------------------------------------------------ canvas */}
          <div className="flex min-h-0 min-w-0 flex-col gap-2 lg:h-full">
            {/* stats bar — above the canvas, because the Kavya console floats over the
                bottom of the viewport and would cover anything placed below it. */}
            {status === "ok" && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-black/[0.06] bg-white px-3 py-2 text-[11.5px] text-muted">
                <span className="font-semibold text-ink">{visible.nodes.length.toLocaleString()} nodes</span>
                <span className="font-semibold text-ink">{visible.links.length.toLocaleString()} links</span>
                {Object.entries(byType).map(([t, c]) => (
                  <span key={t} className="flex items-center gap-1">
                    <TypeIcon type={t as EntityType} className="h-3 w-3" /> {c} {TYPE_STYLE[t as EntityType].plural.toLowerCase()}
                  </span>
                ))}
                <span className="flex items-center gap-1"><Users className="h-3 w-3" /> {communities} {communities === 1 ? "syndicate" : "syndicates"}</span>
                {stats?.derived_links ? <span>{stats.derived_links} derived co-accused links</span> : null}
                {hidden.size > 0 && (
                  <button onClick={() => setHidden(new Set())} className="flex items-center gap-1 font-semibold text-ink hover:underline">
                    <EyeOff className="h-3 w-3" /> {hidden.size} hidden — show
                  </button>
                )}
                {truncated && (
                  <span className="flex items-center gap-1 rounded-md bg-[#fdf6e3] px-2 py-0.5 font-semibold text-brand-deep" title={note || undefined}>
                    <TriangleAlert className="h-3 w-3" />
                    Truncated{stats?.cap ? ` at ${stats.cap}` : ""}{stats?.left_out ? ` · ${stats.left_out} left out` : ""}
                    {stats?.syndicates_total ? ` · ${stats.syndicates_shown} of ${stats.syndicates_total} syndicates` : ""}
                  </span>
                )}
                {note && <span className="min-w-0 flex-1 basis-full truncate text-[11px] text-faint" title={note}>{note}</span>}
              </div>
            )}

            <div
              ref={canvasRef}
              className={`relative min-h-0 overflow-hidden rounded-[1.25rem] border border-black/10 bg-[#f3f4f7] ${
                fullscreen ? "flex-1" : "h-[70vh] lg:h-auto lg:flex-1"}`}
            >
              {status !== "error" && visible.nodes.length > 0 && (
                <Graph
                  width={size.width}
                  height={size.height}
                  nodes={visible.nodes}
                  links={visible.links}
                  predicted={visible.predicted}
                  showPredicted={showPredicted}
                  colorBy={colorBy}
                  selectedId={selectedId}
                  highlight={highlight}
                  fitToken={fitToken}
                  fitIds={fitIds}
                  insetRight={selected && size.width > 760 ? 352 : 0}
                  showLabels={showLabels}
                  onSelect={select}
                  onExpand={(n) => expand(n.id)}
                  onContext={(node, x, y) => setCtxMenu({ node, x, y })}
                  controlRef={controlRef}
                />
              )}

              {/* toolbar */}
              {visible.nodes.length > 0 && (
                <div className="absolute left-3 top-3 z-20 flex flex-col gap-1.5">
                  <div className="flex overflow-hidden rounded-xl border border-black/10 bg-white/95 shadow-sm backdrop-blur">
                    <ToolButton label="2D" active={!view3D} onClick={() => setView3D(false)}>2D</ToolButton>
                    <ToolButton label="3D" active={view3D} onClick={() => setView3D(true)}><Box className="h-3.5 w-3.5" /></ToolButton>
                  </div>
                  <div className="flex flex-col overflow-hidden rounded-xl border border-black/10 bg-white/95 shadow-sm backdrop-blur">
                    <ToolButton label="Zoom in" onClick={() => controlRef.current?.zoomIn()}><Plus className="h-3.5 w-3.5" /></ToolButton>
                    <ToolButton label="Zoom out" onClick={() => controlRef.current?.zoomOut()}><Minus className="h-3.5 w-3.5" /></ToolButton>
                    <ToolButton label="Fit to view" onClick={() => controlRef.current?.fit()}><Maximize2 className="h-3.5 w-3.5" /></ToolButton>
                    <ToolButton label={frozen ? "Unfreeze layout" : "Freeze layout"} active={frozen} onClick={toggleFreeze}><Snowflake className="h-3.5 w-3.5" /></ToolButton>
                    <ToolButton label="Reset view" onClick={reset}><RotateCcw className="h-3.5 w-3.5" /></ToolButton>
                  </div>
                  <div className="flex flex-col overflow-hidden rounded-xl border border-black/10 bg-white/95 shadow-sm backdrop-blur">
                    <ToolButton label="Export PNG" onClick={() => controlRef.current?.exportPNG(`ksp-link-chart-${new Date().toISOString().slice(0, 10)}.png`)}><ImageDown className="h-3.5 w-3.5" /></ToolButton>
                    <ToolButton label="Export edges (CSV)" onClick={exportEdges}><FileDown className="h-3.5 w-3.5" /></ToolButton>
                    {fullscreen && <ToolButton label="Exit full screen" onClick={toggleFullscreen}><Shrink className="h-3.5 w-3.5" /></ToolButton>}
                  </div>
                </div>
              )}

              {/* legend */}
              {visible.nodes.length > 0 && (
                <div className="absolute bottom-3 left-3 z-20 max-w-[380px] rounded-xl border border-black/10 bg-white/95 shadow-sm backdrop-blur">
                  <button onClick={() => setLegendOpen((v) => !v)} className="flex w-full items-center justify-between px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">
                    Legend <span className="text-[10px] normal-case tracking-normal">{legendOpen ? "hide" : "show"}</span>
                  </button>
                  {legendOpen && <div className="px-3 pb-3"><Legend colorBy={colorBy} present={present} showPredicted={showPredicted} /></div>}
                </div>
              )}

              {/* right panel */}
              {selected && (
                <div className="absolute bottom-3 right-3 top-3 z-30 w-[340px] max-w-[calc(100%-1.5rem)]">
                  <EntityPanel
                    key={selected.id}
                    node={selected}
                    inGraph={(id) => byId.has(id)}
                    onClose={() => setSelectedId(null)}
                    onExpand={expand}
                    onHide={hide}
                    onPathFrom={pathFromNode}
                    onFocus={(id) => controlRef.current?.focusNode(id)}
                    onOpenEntity={openEntity}
                    onOpenSyndicate={openSyndicate}
                  />
                </div>
              )}

              {/* context menu */}
              {ctxMenu && (
                <ContextMenu
                  node={ctxMenu.node} x={ctxMenu.x} y={ctxMenu.y} width={size.width} height={size.height}
                  canPathTo={!!pathFrom && pathFrom.id !== ctxMenu.node.id}
                  onClose={() => setCtxMenu(null)}
                  onExpand={() => expand(ctxMenu.node.id)}
                  onSelect={() => select(ctxMenu.node)}
                  onPathFrom={() => pathFromNode(ctxMenu.node)}
                  onPathTo={() => { setMode("path"); setPathTo(asResult(ctxMenu.node)); setPathResult(null); setCtxMenu(null); }}
                  onHide={() => hide(ctxMenu.node.id)}
                />
              )}

              {toast && (
                <div role="status" className={`absolute left-1/2 top-3 z-40 max-w-[min(560px,80%)] -translate-x-1/2 rounded-full border px-4 py-2 text-[12px] font-medium shadow-lg backdrop-blur ${
                  toast.tone === "warn" ? "border-brand/40 bg-[#fdf6e3]/95 text-brand-deep" : "border-black/10 bg-white/95 text-ink"}`}>
                  {toast.text}
                </div>
              )}

              {(loading || busyId) && (
                <div className={`absolute inset-0 z-40 grid place-items-center ${loading ? "bg-white/55 backdrop-blur-[1px]" : "pointer-events-none"}`}>
                  <div className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-4 py-2 text-[12px] font-semibold text-ink shadow-lg">
                    <LoaderCircle className="h-4 w-4 animate-spin text-brand" />
                    {loading ? "Loading the network…" : "Expanding…"}
                  </div>
                </div>
              )}

              {status === "error" && (
                <div className="absolute inset-0 grid place-items-center p-6">
                  <div className="max-w-md rounded-2xl border border-crimson/25 bg-white p-6 text-center shadow-lg">
                    <TriangleAlert className="mx-auto h-8 w-8 text-crimson" />
                    <p className="mt-2 text-[15px] font-bold text-ink">The network could not be loaded</p>
                    <p className="mt-1 text-[12.5px] text-muted">{errorMsg}</p>
                    <div className="mt-4 flex justify-center gap-2">
                      <button onClick={() => load(target, filters)} className="rounded-full bg-ink px-4 py-2 text-[12px] font-semibold text-white">Retry</button>
                      <button onClick={() => choose({ kind: "overview" })} className="rounded-full border border-black/10 px-4 py-2 text-[12px] font-semibold text-ink">Back to overview</button>
                    </div>
                  </div>
                </div>
              )}

              {status === "ok" && !loading && visible.nodes.length === 0 && (
                <div className="absolute inset-0 grid place-items-center p-6">
                  <div className="max-w-md text-center">
                    <Network className="mx-auto h-8 w-8 text-faint" />
                    <p className="mt-2 text-[15px] font-bold text-ink">Nothing to draw</p>
                    <p className="mt-1 text-[12.5px] text-muted">
                      {graph.nodes.length && hidden.size ? "Every entity in this view is hidden." : note || "No entity matches these filters."}
                    </p>
                    <div className="mt-4 flex justify-center gap-2">
                      {hidden.size > 0 && <button onClick={() => setHidden(new Set())} className="rounded-full border border-black/10 px-4 py-2 text-[12px] font-semibold text-ink">Show hidden</button>}
                      <button onClick={() => setFilters(DEFAULT_FILTERS)} className="rounded-full border border-black/10 px-4 py-2 text-[12px] font-semibold text-ink">Reset filters</button>
                    </div>
                  </div>
                </div>
              )}

              {status === "idle" && !loading && <CanvasLoading />}
            </div>

            {/* The canvas is not readable by a screen reader; the same entities are listed here. */}
            <div className="sr-only">
              <h2>Link analysis graph, described</h2>
              <p>{visible.nodes.length} entities connected by {visible.links.length} links.</p>
              <ul>
                {visible.nodes.slice(0, 200).map((n) => (
                  <li key={n.id}>
                    {TYPE_STYLE[n.type].label} {n.label}{n.sub ? `, ${n.sub}` : ""}{n.warrant ? ", active warrant" : ""}{n.rowdy ? ", rowdy-sheeter" : ""}
                    {n.type === "Suspect" && <> — <Link prefetch={false} href={`/suspect?id=${encodeURIComponent(n.id)}`}>dossier</Link></>}
                    {n.type === "Case" && <> — <Link prefetch={false} href={`/case?id=${encodeURIComponent(n.id)}`}>case file</Link></>}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function focusLabel(focus: Focus | null, byId: Map<string, NetNode>): string {
  if (!focus) return "";
  if (focus.kind === "overview") return "overview of the largest syndicates";
  if (focus.kind === "syndicate") return `syndicate ${focus.id}`;
  const names = focus.ids.map((id) => byId.get(id)?.label || id);
  if (focus.kind === "query") return `search “${focus.q}” → ${names.join(", ")}`;
  return `around ${names.join(", ")}`;
}

function FocusCard({ focus, byId, loading, onOverview }: { focus: Focus | null; byId: Map<string, NetNode>; loading: boolean; onOverview: () => void }) {
  if (!focus) return null;
  const title = focus.kind === "overview" ? "Overview" : focus.kind === "syndicate" ? "Syndicate" : focus.kind === "query" ? "Search" : "Exploring";
  return (
    <div className="rounded-xl border border-black/10 bg-black/[0.02] p-3">
      <div className="flex items-center justify-between">
        <span className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">{title}</span>
        {focus.kind !== "overview" && (
          <button onClick={onOverview} disabled={loading} className="text-[11px] font-semibold text-muted hover:text-ink">Back to overview</button>
        )}
      </div>
      <p className="mt-1 text-[12.5px] leading-snug text-ink">
        {focus.kind === "overview"
          ? "The largest detected syndicates and the FIRs their members share. Search above, or double-click any node to expand it."
          : focusLabel(focus, byId)}
      </p>
      <p className="mt-2 text-[10.5px] leading-snug text-faint">Click selects · double-click expands · right-click for actions · drag to pin.</p>
    </div>
  );
}

function ToolButton({ label, onClick, active, children }: { label: string; onClick: () => void; active?: boolean; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={`grid h-8 min-w-8 place-items-center px-2 text-[11px] font-bold transition-colors ${
        active ? "bg-ink text-white" : "text-ink hover:bg-black/[0.05]"}`}
    >
      {children}
    </button>
  );
}

function ContextMenu({ node, x, y, width, height, canPathTo, onClose, onExpand, onSelect, onPathFrom, onPathTo, onHide }: {
  node: NetNode; x: number; y: number; width: number; height: number; canPathTo: boolean;
  onClose: () => void; onExpand: () => void; onSelect: () => void; onPathFrom: () => void; onPathTo: () => void; onHide: () => void;
}) {
  const left = Math.min(x, width - 210);
  const top = Math.min(y, height - 240);
  const dossier = node.type === "Suspect" ? `/suspect?id=${encodeURIComponent(node.id)}`
    : node.type === "Case" ? `/case?id=${encodeURIComponent(node.id)}` : null;
  const item = "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12px] font-medium text-ink hover:bg-black/[0.05]";
  return (
    <div role="menu" className="absolute z-40 w-[200px] rounded-xl border border-black/10 bg-white p-1 shadow-[0_18px_50px_-18px_rgba(17,20,32,0.45)]" style={{ left, top }}
         onMouseLeave={onClose}>
      <div className="truncate px-2.5 py-1.5 text-[11px] font-semibold text-muted">{node.label}</div>
      <button role="menuitem" className={item} onClick={onExpand}><Waypoints className="h-3.5 w-3.5" /> Expand</button>
      <button role="menuitem" className={item} onClick={onSelect}><Crosshair className="h-3.5 w-3.5" /> Show profile</button>
      <button role="menuitem" className={item} onClick={onPathFrom}><Route className="h-3.5 w-3.5" /> Find path from here</button>
      {canPathTo && <button role="menuitem" className={item} onClick={onPathTo}><Route className="h-3.5 w-3.5" /> Path to here</button>}
      {dossier && <Link prefetch={false} role="menuitem" className={item} href={dossier}><Expand className="h-3.5 w-3.5" /> Open {node.type === "Suspect" ? "dossier" : "case file"}</Link>}
      <button role="menuitem" className={item} onClick={onHide}><EyeOff className="h-3.5 w-3.5" /> Hide</button>
      <button role="menuitem" className={`${item} text-muted`} onClick={onClose}><X className="h-3.5 w-3.5" /> Close</button>
    </div>
  );
}

export default function NetworkPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-[60vh] flex-col items-center justify-center space-y-3">
          <Network className="mb-3 h-7 w-7 animate-pulse text-brand" />
          <p className="text-sm text-muted">Loading the link analysis workbench…</p>
        </div>
      }
    >
      <NetworkInner />
    </Suspense>
  );
}
