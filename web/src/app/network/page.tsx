"use client";

import { useEffect, useState, useRef, useCallback, useMemo, Suspense } from "react";
import dynamic from "next/dynamic";
import { X, Network, ShieldAlert, ArrowRight, Expand, Search, SlidersHorizontal, Shrink } from "lucide-react";
import { apiFetch, ApiError } from "@/lib/api";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

/**
 * Canvas font family, resolved once.
 *
 * The renderer used to ask for `var(--font-jakarta)`. A CSS variable is not a legal
 * font-family token in the canvas font shorthand, so the browser rejected the whole
 * `ctx.font` assignment and silently kept the previous value — the default
 * `10px sans-serif`. Every computed font size below was therefore discarded, and the
 * label size became whatever 10 graph units happened to be at the current zoom. On a
 * twelve-node graph that meant roughly 30px text, which is why a small graph looked
 * like a wall of words.
 *
 * next/font emits a generated family name behind the variable, so read the variable
 * and use its value — falling back to a plain stack when it is unset.
 */
let cachedFont: string | null = null;
const canvasFont = () => {
  if (cachedFont) return cachedFont;
  const resolved =
    typeof window !== "undefined"
      ? getComputedStyle(document.documentElement).getPropertyValue("--font-jakarta").trim()
      : "";
  cachedFont = `${resolved ? `${resolved}, ` : ""}system-ui, -apple-system, "Segoe UI", sans-serif`;
  return cachedFont;
};

/**
 * Case labels arrive as an 18-digit FIR number followed by the crime type, which is
 * far too long to sit under a node. Keep the tail of the number — the part that
 * distinguishes one FIR from its neighbours — and the crime type.
 */
const shortLabel = (node: any) => {
  const label: string = node?.label ?? "";
  if (node?.group !== 2) return label.length > 24 ? `${label.slice(0, 23)}…` : label;

  const m = label.match(/^(\d{6,})\s*(?:\((.*)\))?$/);
  if (m) {
    const crime = m[2] ? ` · ${m[2]}` : "";
    return `…${m[1].slice(-5)}${crime}`;
  }
  return label.length > 24 ? `${label.slice(0, 23)}…` : label;
};

const getNodeColor = (node: any) => {
  switch (node.group) {
    case 1: return "#e5342b"; // Suspect (red)
    case 2: return "#2f74c0"; // Case (blue)
    case 3: return "#9333ea"; // CyberID (purple)
    case 4: return "#f97316"; // Vehicle (orange)
    case 5: return "#10b981"; // Officer (green)
    default: return "#6b7280";
  }
};

const getLabelColor = (node: any) => {
  switch (node.group) {
    case 1: return "#a11b13"; // Suspect
    case 2: return "#1f5390"; // Case
    case 3: return "#701a75"; // CyberID
    case 4: return "#c2410c"; // Vehicle
    case 5: return "#047857"; // Officer
    default: return "#4b5563";
  }
};

function NetworkInner() {
  const searchParams = useSearchParams();
  const [graphData, setGraphData] = useState<any>({ nodes: [], links: [] });
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const graphRef = useRef<any>(null);
  // Live canvas zoom. linkWidth has no globalScale argument, so it reads this instead
  // of drawing links that thicken as the view scales up.
  const zoomRef = useRef<number>(1);
  
  // UX Overhaul States
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [hoverNode, setHoverNode] = useState<any>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [isFullScreen, setIsFullScreen] = useState(false);

  // Target Option Lists
  const [suspectsList, setSuspectsList] = useState<any[]>([]);
  const [casesList, setCasesList] = useState<any[]>([]);
  const [isLoadingGraph, setIsLoadingGraph] = useState(false);
  
  // Filters & Physics - Spaced out defaults for clean unclustered visualization
  const [visibleGroups, setVisibleGroups] = useState<Set<number>>(new Set([1,2,3,4,5,6]));
  const [chargeStrength, setChargeStrength] = useState(-10000);
  const [linkDistanceMult, setLinkDistanceMult] = useState(4.5);

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
      if (fs) {
        document.documentElement.setAttribute("data-fullscreen", "true");
      } else {
        document.documentElement.removeAttribute("data-fullscreen");
      }
    };
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  useEffect(() => {
    const measure = () => {
      if (containerRef.current) {
        setDimensions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const fetchNetwork = useCallback(async (params: { suspect_id?: string; case_id?: string; q?: string }) => {
    setIsLoadingGraph(true);
    setError(null);
    try {
      const qs = new URLSearchParams();
      if (params.suspect_id) qs.set("suspect_id", params.suspect_id);
      if (params.case_id) qs.set("case_id", params.case_id);
      if (params.q) qs.set("q", params.q);

      const res = await apiFetch(`/network?${qs.toString()}`);
      if (!res.ok) throw new Error("Failed to load network");
      const data = await res.json();
      setGraphData(data);
      setSelectedNode(null);
    } catch (err: any) {
      if (err instanceof ApiError && err.isForbidden) {
        setError("Access Denied. You need investigator privileges to view the network graph");
      } else {
        setError(err.message);
      }
    } finally {
      setIsLoadingGraph(false);
    }
  }, []);

  useEffect(() => {
    // Fetch suspects for target dropdown
    apiFetch("/registry/criminals")
      .then((res) => res.json())
      .then((data) => setSuspectsList(data.rows || []))
      .catch(() => {});

    apiFetch("/cases")
      .then((res) => res.json())
      .then((data) => setCasesList(data.cases || []))
      .catch(() => {});
  }, []);

  // Listen to url params to auto-pivot if redirected from other dossiers
  useEffect(() => {
    const targetSuspectId = searchParams.get("suspect_id");
    const targetCaseId = searchParams.get("case_id");
    if (targetSuspectId) {
      fetchNetwork({ suspect_id: targetSuspectId });
    } else if (targetCaseId) {
      fetchNetwork({ case_id: targetCaseId });
    }
  }, [searchParams, fetchNetwork]);

  // Compute Neighbors for Focus Mode
  const neighbors = useMemo(() => {
    const map = new Map();
    if (!graphData) return map;
    graphData.links.forEach((link: any) => {
      const source = typeof link.source === 'object' ? link.source.id : link.source;
      const target = typeof link.target === 'object' ? link.target.id : link.target;
      if (!map.has(source)) map.set(source, new Set());
      if (!map.has(target)) map.set(target, new Set());
      map.get(source).add(target);
      map.get(target).add(source);
    });
    return map;
  }, [graphData]);

  // Derived Filtered Data
  const filteredData = useMemo(() => {
    if (!graphData) return null;
    const nodes = graphData.nodes.filter((n: any) => visibleGroups.has(n.group));
    const nodeIds = new Set(nodes.map((n: any) => n.id));
    const links = graphData.links.filter((l: any) => {
      const s = typeof l.source === 'object' ? l.source.id : l.source;
      const t = typeof l.target === 'object' ? l.target.id : l.target;
      return nodeIds.has(s) && nodeIds.has(t);
    });
    return { nodes, links };
  }, [graphData, visibleGroups]);

  const handleNodeClick = useCallback((node: any) => {
    setSelectedNode(node);
    if (graphRef.current) {
      // Smoothly center and zoom on the clicked node in 2D space
      graphRef.current.centerAt(node.x, node.y, 1000);
      graphRef.current.zoom(2.5, 1000);
    }
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    fetchNetwork({ q: searchQuery });
    setSearchQuery("");
  };

  useEffect(() => {
    if (graphRef.current && filteredData) {
      graphRef.current.d3Force("charge")?.strength(chargeStrength)?.distanceMax(8000);
      graphRef.current.d3Force("link")?.distance((link: any) => {
        const base = link.strength && link.strength >= 3 ? 180 : link.strength && link.strength >= 2 ? 280 : 420;
        return base * linkDistanceMult;
      });
      graphRef.current.d3ReheatSimulation();

      const timer = setTimeout(() => {
        if (graphRef.current) {
          graphRef.current.zoomToFit(600, 150);
        }
      }, 600);
      return () => clearTimeout(timer);
    }
  }, [filteredData, chargeStrength, linkDistanceMult]);

  const toggleGroup = (grp: number) => {
    setVisibleGroups(prev => {
      const next = new Set(prev);
      if (next.has(grp)) next.delete(grp);
      else next.add(grp);
      return next;
    });
  };

  return (
    <div className={`flex flex-col ${isFullScreen ? 'fixed inset-0 z-[99999] bg-[#eef1f5] m-0 p-0 h-screen w-screen overflow-hidden' : 'relative space-y-4'}`}>
      {!isFullScreen && (
        <header className="relative z-10 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div>
            <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
              <Network className="h-8 w-8 text-brand" strokeWidth={2} /> 3D Link Analysis
            </h1>
            <p className="mt-2 text-[15px] text-muted">
              Holographic network mapping the connections between prime suspects and their cases.
            </p>
          </div>
          <div className="flex flex-wrap gap-4 rounded-3xl border border-black/10 bg-[#ffffff]/90 px-4 py-2 backdrop-blur-md">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-crimson shadow-[0_0_12px_rgba(240,71,63,0.9)]" />
              <span className="text-[13px] font-medium text-muted">Suspects</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-azure shadow-[0_0_12px_rgba(91,155,213,0.9)]" />
              <span className="text-[13px] font-medium text-muted">Cases</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#9333ea] shadow-[0_0_12px_rgba(147,51,234,0.9)]" />
              <span className="text-[13px] font-medium text-muted">Cyber IDs</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#f97316] shadow-[0_0_12px_rgba(249,115,22,0.9)]" />
              <span className="text-[13px] font-medium text-muted">Vehicles</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#10b981] shadow-[0_0_12px_rgba(16,185,129,0.9)]" />
              <span className="text-[13px] font-medium text-muted">Officers</span>
            </div>
          </div>
          <button
            onClick={toggleFullScreen}
            className="flex items-center gap-2 rounded-full border border-black/10 bg-white px-4 py-2 text-sm font-medium transition-all hover:border-brand/40 hover:bg-brand/5 hover:text-brand"
          >
            <Expand className="h-4 w-4" /> Full Screen
          </button>
        </header>
      )}

      <div className={isFullScreen ? 'h-full w-full relative' : 'bezel'}>
        <div
          ref={containerRef}
          className={`relative overflow-hidden !bg-[#eef1f5] ${isFullScreen ? 'h-full w-full !rounded-none' : 'bezel-core h-[calc(100dvh-14rem)] min-h-[550px] !rounded-[1.1rem]'}`}
        >
          {/* Target Search Overlay (Initial Blank State) */}
          {(!graphData || !graphData.nodes || graphData.nodes.length === 0) && (
            <div className="absolute inset-0 z-20 flex items-center justify-center p-6 bg-[#f1f3f7]/80 backdrop-blur-[1px]">
              <div className="w-full max-w-xl rounded-3xl border border-black/10 bg-white p-8 shadow-2xl text-center">
                <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand/12 text-brand">
                  <Network className="h-7 w-7" strokeWidth={2} />
                </div>
                <h2 className="text-display text-2xl font-bold text-ink">Criminal Relationship Link Analysis</h2>
                <p className="mt-2 text-sm text-muted">
                  Target a specific suspect or case number to generate a focused, multi-dimensional relational map.
                </p>

                <div className="mt-8 space-y-4 text-left">
                  {/* Select Suspect */}
                  <div>
                    <label
                      htmlFor="net-suspect"
                      className="text-[11px] font-bold uppercase tracking-wider text-faint block mb-1.5"
                    >
                      Target Accused Suspect
                    </label>
                    <select
                      id="net-suspect"
                      onChange={(e) => {
                        if (e.target.value) {
                          fetchNetwork({ suspect_id: e.target.value });
                        }
                      }}
                      className="w-full rounded-xl border border-black/10 bg-white px-4 py-3 text-sm text-ink focus:border-brand/40 focus:outline-none font-medium"
                      defaultValue=""
                    >
                      <option key="placeholder-suspect" value="" disabled>Choose an accused suspect...</option>
                      {suspectsList.map((s: any, idx: number) => (
                        <option key={s.criminal_id || s.PersonID || idx} value={s.criminal_id || s.PersonID}>
                          {s.name} ({s.criminal_id || s.PersonID}) {s.alias ? `· '${s.alias}'` : ''} · {s.gang_affiliation || 'Independent'}
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Select Case */}
                  <div>
                    <label
                      htmlFor="net-case"
                      className="text-[11px] font-bold uppercase tracking-wider text-faint block mb-1.5"
                    >
                      Target Case ID (FIR Number)
                    </label>
                    <select
                      id="net-case"
                      onChange={(e) => {
                        if (e.target.value) {
                          fetchNetwork({ case_id: e.target.value });
                        }
                      }}
                      className="w-full rounded-xl border border-black/10 bg-white px-4 py-3 text-sm text-ink focus:border-brand/40 focus:outline-none font-medium font-mono"
                      defaultValue=""
                    >
                      <option key="placeholder-case" value="" disabled>Choose a registered case...</option>
                      {casesList.map((c: any, idx: number) => (
                        <option key={c.case_id || idx} value={c.case_id}>
                          {c.case_id} · {c.crime_type} ({c.district})
                        </option>
                      ))}
                    </select>
                  </div>

                  {/* Substring search across the graph. Not semantic — the label says so. */}
                  <div className="relative">
                    <label
                      htmlFor="net-ai-query"
                      className="text-[11px] font-bold uppercase tracking-wider text-faint block mb-1.5"
                    >
                      Search the network
                    </label>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const formData = new FormData(e.currentTarget);
                        const query = formData.get("ai_query") as string;
                        if (query.trim()) {
                          fetchNetwork({ q: query });
                        }
                      }}
                      className="relative flex items-center"
                    >
                      <input
                        id="net-ai-query"
                        name="ai_query"
                        type="text"
                        placeholder="Name, FIR number, gang, crime type or district"
                        className="w-full rounded-xl border border-black/10 bg-white py-3.5 pl-4 pr-24 text-sm text-ink placeholder:text-faint focus:border-brand/40 focus:outline-none font-medium"
                      />
                      <button
                        type="submit"
                        className="absolute right-2 rounded-lg bg-brand px-3 py-1.5 text-xs font-bold text-black hover:bg-brand-hover transition-colors"
                      >
                        Extract Links
                      </button>
                    </form>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Target Network Overlays (Active Graph State) */}
          {graphData && graphData.nodes && graphData.nodes.length > 0 && (
            <>
              {/* Floating Top Control Bar */}
              <div className="absolute right-6 top-6 z-30 flex items-center gap-3">
                {isFullScreen && (
                  <div className="flex items-center gap-3 rounded-full border border-black/10 bg-white/95 px-4 py-2 shadow-lg backdrop-blur-md">
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-crimson" />
                      <span className="text-[11px] font-bold text-muted">Suspects</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-azure" />
                      <span className="text-[11px] font-bold text-muted">Cases</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="h-2.5 w-2.5 rounded-full bg-[#9333ea]" />
                      <span className="text-[11px] font-bold text-muted">Cyber</span>
                    </div>
                  </div>
                )}

                <button
                  onClick={() => setGraphData({ nodes: [], links: [] })}
                  className="rounded-full border border-black/10 bg-white/95 px-4 py-2.5 text-xs font-bold text-ink shadow-lg hover:bg-brand/5 hover:text-brand hover:border-brand/40 transition-all backdrop-blur-md"
                >
                  ← Target New Entity
                </button>

                {isFullScreen && (
                  <button
                    onClick={toggleFullScreen}
                    className="flex items-center gap-2 rounded-full border border-crimson/30 bg-crimson/10 px-4 py-2.5 text-xs font-bold text-crimson shadow-lg hover:bg-crimson hover:text-white transition-all backdrop-blur-md"
                  >
                    <Shrink className="h-4 w-4" /> Exit Full Screen
                  </button>
                )}
              </div>

              {/* Search Bar Overlay */}
              <div className="absolute left-6 top-6 z-20 w-80">
                <form onSubmit={handleSearch} className="relative flex items-center shadow-[0_8px_30px_rgba(17,20,32,0.1)]">
                  <Search className="absolute left-4 h-5 w-5 text-muted" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search suspect name, Case ID..."
                    className="w-full rounded-2xl border border-black/10 bg-white/95 py-3.5 pl-12 pr-4 text-[14px] font-bold text-ink backdrop-blur-xl placeholder:font-medium placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-brand/50"
                  />
                </form>
              </div>

              {/* Controls Toggle */}
              <button
                onClick={() => setShowFilters(!showFilters)}
                className="absolute bottom-6 left-6 z-30 flex h-12 w-12 items-center justify-center rounded-full border border-black/10 bg-white/95 text-ink shadow-lg backdrop-blur-md transition-transform hover:scale-105"
              >
                <SlidersHorizontal className="h-5 w-5" />
              </button>

              {/* Control Center Panel */}
              {showFilters && (
                <div className="absolute bottom-20 left-6 z-30 w-72 rounded-2xl border border-black/10 bg-white/95 p-5 shadow-2xl backdrop-blur-xl animate-in slide-in-from-bottom-4">
                  <h3 className="mb-4 text-sm font-bold uppercase tracking-wider text-faint">Data Filters</h3>
                  <div className="space-y-3 mb-6">
                    {[
                      { id: 1, label: "Suspects", color: "accent-crimson" },
                      { id: 2, label: "Cases", color: "accent-azure" },
                      { id: 3, label: "Cyber IDs", color: "accent-purple-600" },
                      { id: 4, label: "Vehicles", color: "accent-orange-500" },
                      { id: 5, label: "Officers", color: "accent-emerald-500" }
                    ].map(grp => (
                      <label key={grp.id} className="flex cursor-pointer items-center justify-between rounded-lg hover:bg-black/5 p-1 -mx-1 text-[13px] font-bold text-ink transition-colors">
                        <span>{grp.label}</span>
                        <input type="checkbox" checked={visibleGroups.has(grp.id)} onChange={() => toggleGroup(grp.id)} className={`h-4 w-4 rounded border-black/20 ${grp.color}`} />
                      </label>
                    ))}
                  </div>

                  <h3 className="mb-4 mt-6 text-sm font-bold uppercase tracking-wider text-faint">Physics Engine</h3>
                  <div className="space-y-5">
                    <div>
                      <div className="mb-2 flex justify-between text-[11px] font-bold text-faint">
                        <span>Repulsion</span>
                        <span>{chargeStrength}</span>
                      </div>
                      <input type="range" min="-12000" max="-1000" step="200" value={chargeStrength} onChange={(e) => setChargeStrength(Number(e.target.value))} className="w-full accent-brand" />
                    </div>
                    <div>
                      <div className="mb-2 flex justify-between text-[11px] font-bold text-faint">
                        <span>Link Distance</span>
                        <span>{linkDistanceMult.toFixed(1)}x</span>
                      </div>
                      <input type="range" min="1.0" max="6.0" step="0.2" value={linkDistanceMult} onChange={(e) => setLinkDistanceMult(Number(e.target.value))} className="w-full accent-brand" />
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {/* Graph Loading Overlay */}
          {isLoadingGraph && (
            <div className="absolute inset-0 z-40 flex items-center justify-center bg-white/70 backdrop-blur-[2px]">
              <div className="flex flex-col items-center">
                <div className="relative mb-6 h-20 w-20">
                  <div className="absolute inset-0 animate-spin rounded-full border-t-2 border-brand" />
                  <div className="absolute inset-2 animate-spin-reverse rounded-full border-r-2 border-crimson" />
                  <div className="absolute inset-4 animate-spin rounded-full border-b-2 border-azure" />
                  <Network className="absolute left-1/2 top-1/2 h-6 w-6 -translate-x-1/2 -translate-y-1/2 text-muted" />
                </div>
                <p className="shimmer bg-clip-text text-[13px] font-bold uppercase tracking-widest text-transparent">
                  Generating Target Network…
                </p>
              </div>
            </div>
          )}

          {error && (
            <div className="absolute inset-0 z-40 flex items-center justify-center backdrop-blur-md">
              <div className="flex max-w-md flex-col items-center rounded-2xl border border-crimson/30 bg-crimson/[0.06] p-6 text-center">
                <ShieldAlert className="mb-4 h-12 w-12 text-crimson" />
                <p className="mb-2 text-lg font-medium text-crimson">
                  {error.includes("Access Denied") ? "Access Denied" : "Connection Failed"}
                </p>
                <p className="text-[13px] text-muted">
                  {error}{error.includes("Access Denied") ? "" : ". Ensure the intelligence core is running."}
                </p>
              </div>
            </div>
          )}

          {filteredData && filteredData.nodes && filteredData.nodes.length > 0 && (
            <div className="absolute inset-0 cursor-crosshair">
              <div className="sr-only">
                <h2>Link analysis graph, described</h2>
                <p>
                  {filteredData.nodes.length} entities connected by{" "}
                  {filteredData.links.length} links. The visual graph is drawn on a
                  canvas; the same entities are listed below, each linking to its
                  record.
                </p>
                <ul>
                  {filteredData.nodes.map((n: any) => {
                    const degree = filteredData.links.filter((l: any) => {
                      const src = typeof l.source === "object" ? l.source.id : l.source;
                      const tgt = typeof l.target === "object" ? l.target.id : l.target;
                      return src === n.id || tgt === n.id;
                    }).length;
                    return (
                      <li key={n.id}>
                        {n.label} — {n.group}, {degree}{" "}
                        {degree === 1 ? "connection" : "connections"}
                      </li>
                    );
                  })}
                </ul>
              </div>
              <ForceGraph2D
                ref={graphRef}
                width={dimensions.width}
                height={dimensions.height}
                graphData={filteredData}
                nodeLabel={() => ""} // disable default tooltip
                backgroundColor="#eef1f5"
                cooldownTicks={100}
                onEngineStop={() => {
                  // Generous padding: the copilot console floats over the bottom of the viewport,
                  // and a node parked under it loses its label entirely.
                  graphRef.current?.zoomToFit(400, 110);
                }}
                onNodeClick={handleNodeClick}
                onNodeHover={(node) => setHoverNode(node)}
                nodeCanvasObject={(node: any, ctx: CanvasRenderingContext2D, globalScale: number) => {
                  // Everything below is sized in screen pixels and divided by
                  // globalScale, because the canvas is already scaled by it. Sizing in
                  // graph units — which is what this did before — makes nodes and text
                  // grow and shrink with zoom, so a twelve-node graph rendered as
                  // saucer-sized circles under 30px labels.
                  const px = (screenPx: number) => screenPx / globalScale;

                  const activeNode = selectedNode || hoverNode;
                  const isActive = activeNode && activeNode.id === node.id;
                  const isNeighbour =
                    activeNode && neighbors.get(activeNode.id)?.has(node.id);

                  let color = getNodeColor(node);
                  let labelColor = getLabelColor(node);
                  let opacity = 0.95;

                  if (activeNode && !isActive && !isNeighbour) {
                    color = "#cbd5e1";
                    labelColor = "#94a3b8";
                    opacity = 0.18;
                  }

                  const radius = px(
                    Math.max(4.5, Math.min(11, node.val ? Math.sqrt(node.val) * 2.2 : 5.5))
                  );

                  ctx.save();
                  ctx.shadowColor = color;
                  ctx.shadowBlur = isActive ? px(10) : px(4);
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                  ctx.fillStyle = color;
                  ctx.globalAlpha = opacity;
                  ctx.fill();
                  ctx.restore();

                  ctx.beginPath();
                  ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                  ctx.strokeStyle = "#ffffff";
                  ctx.lineWidth = px(1.5);
                  ctx.stroke();

                  if (isActive) {
                    ctx.beginPath();
                    ctx.arc(node.x, node.y, radius + px(3), 0, 2 * Math.PI, false);
                    ctx.strokeStyle = "#0f172a";
                    ctx.lineWidth = px(1.75);
                    ctx.stroke();
                  }

                  // Labels are the thing that made this look dense, so they are
                  // rationed: on a big graph only what the reader is looking at gets
                  // one, unless they have zoomed in far enough for the text to have
                  // room. A small graph is legible fully labelled.
                  const crowded = filteredData.nodes.length > 18;
                  const showLabel =
                    !crowded || isActive || isNeighbour || globalScale > 1.6;
                  if (!showLabel) return;

                  const text = shortLabel(node);
                  const fontSize = px(11);
                  ctx.font = `${node.group === 1 ? "700" : "500"} ${fontSize}px ${canvasFont()}`;
                  ctx.textAlign = "center";
                  ctx.textBaseline = "top";
                  ctx.globalAlpha = opacity;

                  // A translucent plate behind the text: node colours and link lines
                  // both run underneath labels, and unbacked text on top of either is
                  // the other half of why this read as noise.
                  const w = ctx.measureText(text).width;
                  const padX = px(4);
                  const padY = px(2.5);
                  const top = node.y + radius + px(4);
                  ctx.fillStyle = "rgba(255,255,255,0.82)";
                  ctx.beginPath();
                  ctx.roundRect(
                    node.x - w / 2 - padX,
                    top - padY,
                    w + padX * 2,
                    fontSize + padY * 2,
                    px(3)
                  );
                  ctx.fill();

                  ctx.fillStyle = labelColor;
                  ctx.fillText(text, node.x, top);
                  ctx.globalAlpha = 1.0;
                }}
                linkCurvature={0.15}
                nodePointerAreaPaint={(
                  node: any,
                  color: string,
                  ctx: CanvasRenderingContext2D,
                  globalScale: number
                ) => {
                  // Must track the drawn radius, plus a few pixels so small nodes stay
                  // clickable rather than needing a pixel-perfect hit.
                  const radius =
                    (Math.max(4.5, Math.min(11, node.val ? Math.sqrt(node.val) * 2.2 : 5.5)) + 4) /
                    globalScale;
                  ctx.beginPath();
                  ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                  ctx.fillStyle = color;
                  ctx.fill();
                }}
                linkColor={(link: any) => {
                  const s = typeof link.source === 'object' ? link.source.id : link.source;
                  const t = typeof link.target === 'object' ? link.target.id : link.target;
                  
                  const activeNode = selectedNode || hoverNode;
                  if (activeNode) {
                    if (s === activeNode.id || t === activeNode.id) {
                      return link.strength && link.strength >= 3 ? "rgba(220,38,38,0.95)" : "rgba(245,158,11,0.9)";
                    }
                    return "rgba(226,232,240,0.1)"; // Dimmed
                  }
                  return link.strength && link.strength >= 3 ? "rgba(220,38,38,0.75)" : link.strength && link.strength >= 2 ? "rgba(245,158,11,0.65)" : "rgba(100,116,139,0.18)";
                }}
                onZoom={(t: any) => {
                  zoomRef.current = t?.k || 1;
                }}
                linkWidth={(link: any) => {
                  const s = typeof link.source === 'object' ? link.source.id : link.source;
                  const t = typeof link.target === 'object' ? link.target.id : link.target;
                  const activeNode = selectedNode || hoverNode;
                  let width = link.strength ? Math.min(3, 0.9 + link.strength * 0.5) : 1.1;
                  if (activeNode && (s === activeNode.id || t === activeNode.id)) width *= 1.6;
                  // Graph units again — divide by the live zoom so a thin link stays
                  // thin when the view is scaled up.
                  return width / zoomRef.current;
                }}
                linkDirectionalParticles={(link: any) => {
                  const activeNode = selectedNode || hoverNode;
                  if (activeNode) {
                    const s = typeof link.source === 'object' ? link.source.id : link.source;
                    const t = typeof link.target === 'object' ? link.target.id : link.target;
                    return (s === activeNode.id || t === activeNode.id) ? 2 : 0;
                  }
                  return 0;
                }}
                linkDirectionalParticleWidth={2}
                linkDirectionalParticleSpeed={0.006}
              />
            </div>
          )}
          
          {hoverNode && !selectedNode && (
             <div className="pointer-events-none absolute left-1/2 top-8 z-30 -translate-x-1/2 rounded-2xl border border-black/10 bg-black/90 px-6 py-3 text-white shadow-2xl backdrop-blur-md">
               <span className="text-[10px] font-bold uppercase tracking-widest text-white/60 block mb-1">
                  {hoverNode.group === 1 ? "Target Suspect" : hoverNode.group === 2 ? "Linked Case" : hoverNode.group === 3 ? "Cyber ID Trace" : hoverNode.group === 4 ? "Involved Vehicle" : "Investigating Officer"}
               </span>
               <span className="font-bold text-lg">{hoverNode.label}</span>
             </div>
          )}

          {selectedNode && (
            <div className="absolute right-6 top-6 bottom-6 z-30 w-[340px] overflow-y-auto rounded-2xl border border-black/10 bg-[#ffffff]/95 shadow-[0_0_60px_rgba(17,20,32,0.13)] backdrop-blur-2xl">
              <div
                className={`h-1.5 w-full shrink-0 ${
                  selectedNode.group === 1
                    ? "bg-gradient-to-r from-crimson to-brand"
                    : selectedNode.group === 2
                    ? "bg-gradient-to-r from-azure to-blue-600"
                    : selectedNode.group === 3
                    ? "bg-gradient-to-r from-purple-500 to-indigo-600"
                    : selectedNode.group === 4
                    ? "bg-gradient-to-r from-orange-500 to-amber-500"
                    : "bg-gradient-to-r from-emerald to-teal-600"
                }`}
              />
              <div className="p-6">
                <div className="mb-6 flex items-start justify-between">
                  <div>
                    <span
                      className={`rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest ${
                        selectedNode.group === 1
                          ? "border-crimson/20 bg-crimson/10 text-crimson"
                          : selectedNode.group === 2
                          ? "border-azure/20 bg-azure/10 text-azure"
                          : selectedNode.group === 3
                          ? "border-purple-600/20 bg-purple-50 text-purple-600"
                          : selectedNode.group === 4
                          ? "border-orange-500/20 bg-orange-50 text-orange-500"
                          : "border-emerald/20 bg-emerald/10 text-emerald"
                      }`}
                    >
                      {selectedNode.group === 1
                        ? "Target Suspect"
                        : selectedNode.group === 2
                        ? "Linked Case"
                        : selectedNode.group === 3
                        ? "Cyber ID Trace"
                        : selectedNode.group === 4
                        ? "Involved Vehicle"
                        : "Investigating Officer"}
                    </span>
                    <h3 className="mt-3 text-xl font-bold leading-tight text-ink">{selectedNode.label}</h3>
                  </div>
                  <button
                    onClick={() => {
                      setSelectedNode(null);
                      if (graphRef.current) {
                        graphRef.current.centerAt(0, 0, 1000);
                        graphRef.current.zoom(1.0, 1000);
                      }
                    }}
                    className="rounded-full border border-black/10 bg-black/[0.03] p-1.5 text-faint transition-colors hover:text-ink"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                {selectedNode.group === 1 && (
                  <div className="space-y-3">
                    <Field label="Gang Affiliation" value={selectedNode.details?.gang || "Unknown"} tone="text-crimson" />
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Risk Level" value={selectedNode.details?.risk_level || "Unknown"} tone="text-brand" />
                      <Field label="Target ID" value={selectedNode.id} mono />
                      {selectedNode.details?.centrality !== undefined && (
                        <Field label="Centrality" value={(selectedNode.details.centrality * 100).toFixed(1) + "%"} tone="text-purple-600" />
                      )}
                      {selectedNode.details?.degree !== undefined && (
                        <Field label="Connections" value={selectedNode.details.degree.toString()} tone="text-blue-600" />
                      )}
                    </div>
                    <Link prefetch={false}
                      href={`/suspect?id=${selectedNode.id}`}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-crimson to-[#c8281f] py-3 font-bold text-white transition-all hover:brightness-110 shadow-lg shadow-crimson/20"
                    >
                      <Expand className="h-4 w-4" /> Open Suspect Dossier
                    </Link>
                  </div>
                )}

                {selectedNode.group === 2 && (
                  <div className="space-y-3">
                    <Field label="Crime Type" value={selectedNode.details?.crime_type || "Unknown"} tone="text-azure" />
                    <Field label="District" value={selectedNode.details?.district || "Unknown"} />
                    <Link prefetch={false}
                      href={`/case?id=${selectedNode.id}`}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-brand-soft to-brand py-3 font-bold text-black transition-all hover:brightness-105 shadow-lg shadow-brand/20"
                    >
                      Open Dossier <ArrowRight className="h-4 w-4" />
                    </Link>
                  </div>
                )}

                {selectedNode.group === 3 && (
                  <div className="space-y-3">
                    <Field label="Trace Category" value={selectedNode.details?.kind || "Unknown"} tone="text-purple-600" />
                    <Field label="Identifier Value" value={selectedNode.id} mono />
                    <Link prefetch={false}
                      href={`/intel?tab=cybercrime&q=${encodeURIComponent(selectedNode.id)}`}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-purple-500 to-purple-600 py-3 font-bold text-white transition-all hover:brightness-105 shadow-lg shadow-purple-500/20"
                    >
                      Search Cyber Registry <ArrowRight className="h-4 w-4" />
                    </Link>
                  </div>
                )}

                {selectedNode.group === 4 && (
                  <div className="space-y-3">
                    <Field label="Make & Model" value={selectedNode.details?.make || "Unknown"} tone="text-orange-500" />
                    <Field label="Recovery Status" value={selectedNode.details?.recovery || "Unknown"} />
                    <Link prefetch={false}
                      href={`/intel?tab=vehicles&q=${encodeURIComponent(selectedNode.id)}`}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-orange-500 to-orange-600 py-3 font-bold text-white transition-all hover:brightness-105 shadow-lg shadow-orange-500/20"
                    >
                      Search Vehicle Registry <ArrowRight className="h-4 w-4" />
                    </Link>
                  </div>
                )}

                {selectedNode.group === 5 && (
                  <div className="space-y-3">
                    <Field label="Rank / Designation" value={selectedNode.details?.rank || "Unknown"} tone="text-emerald" />
                    <Field label="Posted Station" value={selectedNode.details?.station || "Unknown"} />
                    <Link prefetch={false}
                      href={`/intel?tab=officers&q=${encodeURIComponent(selectedNode.label)}`}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-emerald-500 to-emerald-600 py-3 font-bold text-white transition-all hover:brightness-105 shadow-lg shadow-emerald-500/20"
                    >
                      Search Officer Registry <ArrowRight className="h-4 w-4" />
                    </Link>
                  </div>
                )}

              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, tone = "text-ink", mono = false }: { label: string; value: string; tone?: string; mono?: boolean }) {
  return (
    <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-faint">{label}</p>
      <p className={`mt-1 text-[15px] font-bold ${tone} ${mono ? "font-mono text-[13px]" : ""}`}>{value}</p>
    </div>
  );
}

export default function NetworkPage() {
  return (
    <Suspense fallback={
      <div className="flex h-[60vh] flex-col items-center justify-center space-y-3">
        <Network className="mb-3 h-7 w-7 animate-pulse text-brand" />
        <p className="text-sm text-muted">Initializing holographic networks…</p>
      </div>
    }>
      <NetworkInner />
    </Suspense>
  );
}
