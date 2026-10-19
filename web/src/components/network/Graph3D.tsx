"use client";

/**
 * The 3D view of the same graph. Same data, same selection and the same actions
 * as the 2D chart; what it cannot draw the same way it draws plainly rather than
 * pretending — shapes become spheres (type stays legible through colour and the
 * hover card), and labels are limited to the most central nodes and whatever the
 * reader is looking at, as sprites.
 *
 * Predicted leads are real links here (3D has no overlay pass) but carry zero
 * spring strength, so a lead never pulls two people together in the layout.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-3d";
import SpriteText from "three-spritetext";
import type { Object3D } from "three";
import type { GraphViewProps, NetLink, NetNode } from "./types";
import { linkEnd } from "./types";
import { CANVAS_BG, linkRgb, nodeColor } from "./style";
import { downloadCanvas, pairKey } from "./util";
import HoverCard from "./HoverCard";

type N = NodeObject<NetNode>;
type L = LinkObject<NetNode, NetLink>;

const DOUBLE_CLICK_MS = 320;
const LABELS_3D = 28;

export default function Graph3D(props: GraphViewProps) {
  const {
    width, height, nodes, links, predicted, showPredicted, colorBy, selectedId, highlight,
    fitToken, fitIds, onSelect, onExpand, onContext, controlRef, showLabels,
  } = props;
  const fgRef = useRef<ForceGraphMethods<N, L> | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement>(null);
  const pendingFit = useRef(true);
  const fitOnly = useRef<Set<string> | null>(null);
  const lastClick = useRef<{ id: string; t: number } | null>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const sprites = useRef(new Map<string, SpriteText>());
  const [hover, setHover] = useState<{ node: NetNode; x: number; y: number } | null>(null);

  const graphData = useMemo(() => {
    const extra: NetLink[] = showPredicted
      ? predicted.map((p) => ({
          source: p.source, target: p.target, relationship: "Predicted", weight: p.score,
          derived: false, kind: "predicted" as const, score: p.score,
          common_associates: p.common_associates, in_global_top: p.in_global_top,
        }))
      : [];
    return { nodes: nodes as N[], links: [...links, ...extra] as L[] };
  }, [nodes, links, predicted, showPredicted]);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const hood = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of links) {
      const s = linkEnd(l.source);
      const t = linkEnd(l.target);
      if (!m.has(s)) m.set(s, new Set());
      if (!m.has(t)) m.set(t, new Set());
      m.get(s)!.add(t);
      m.get(t)!.add(s);
    }
    return m;
  }, [links]);

  const activeId = hover?.node.id ?? selectedId;
  const activeHood = activeId ? hood.get(activeId) : undefined;

  const labelled = useMemo(() => {
    const out = new Set<string>();
    if (showLabels) {
      [...nodes].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, LABELS_3D).forEach((n) => out.add(n.id));
    }
    if (selectedId) out.add(selectedId);
    highlight?.nodes.forEach((id) => out.add(id));
    return out;
  }, [nodes, selectedId, highlight, showLabels]);

  // -------------------------------------------------------------- forces
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const count = new Map<string, number>();
    for (const l of links) {
      count.set(linkEnd(l.source), (count.get(linkEnd(l.source)) || 0) + 1);
      count.set(linkEnd(l.target), (count.get(linkEnd(l.target)) || 0) + 1);
    }
    fg.d3Force("charge")?.strength(nodes.length > 300 ? -45 : -80);
    fg.d3Force("link")
      ?.distance((l: L) => (l.relationship === "Investigating Officer" ? 60 : 34))
      // d3's default strength, except leads, which must not shape the layout.
      .strength((l: L) =>
        l.kind === "predicted"
          ? 0
          : 1 / Math.max(1, Math.min(count.get(linkEnd(l.source as string | NetNode)) || 1,
                                     count.get(linkEnd(l.target as string | NetNode)) || 1)));
  }, [nodes.length, links]);

  const fitNow = useCallback(() => {
    const only = fitOnly.current;
    fgRef.current?.zoomToFit(600, 40, only ? (n: N) => only.has(n.id) : undefined);
  }, []);

  useEffect(() => {
    pendingFit.current = true;
    fitOnly.current = fitIds && fitIds.length ? new Set(fitIds) : null;
    const t = setTimeout(() => {
      if (pendingFit.current) {
        fitNow();
        pendingFit.current = false;
      }
    }, 2400);
    return () => clearTimeout(t);
  }, [fitToken, fitIds, fitNow]);

  useEffect(() => {
    controlRef.current = {
      zoomIn: () => dolly(fgRef.current, 0.72),
      zoomOut: () => dolly(fgRef.current, 1.38),
      fit: (ids?: string[]) => {
        const only = ids && ids.length ? new Set(ids) : null;
        fgRef.current?.zoomToFit(500, 40, only ? (n: N) => only.has(n.id) : undefined);
      },
      focusNode: (id: string) => {
        const n = byId.get(id);
        const fg = fgRef.current;
        if (!n || !fg || n.x === undefined || n.y === undefined) return;
        const z = n.z || 0;
        const d = 1 + 110 / Math.max(1, Math.hypot(n.x, n.y, z));
        fg.cameraPosition({ x: n.x * d, y: n.y * d, z: z * d }, { x: n.x, y: n.y, z }, 800);
      },
      reheat: () => fgRef.current?.d3ReheatSimulation(),
      exportPNG: (filename: string) => {
        const canvas = fgRef.current?.renderer().domElement;
        if (canvas) downloadCanvas(canvas, filename, CANVAS_BG);
      },
    };
  }, [byId, controlRef]);

  // -------------------------------------------------------------- styling
  const isLit = useCallback(
    (id: string) => {
      const inHood = !activeId || id === activeId || !!activeHood?.has(id);
      const onPath = !highlight || highlight.nodes.has(id);
      return inHood && onPath;
    },
    [activeId, activeHood, highlight],
  );

  const colorOf = useCallback(
    (n: N) => (isLit(n.id) ? (n.id === selectedId ? "#1d1d1f" : nodeColor(n, colorBy)) : "#d9dce2"),
    [isLit, colorBy, selectedId],
  );

  const linkLit = useCallback(
    (l: L) => {
      const s = linkEnd(l.source as string | NetNode);
      const t = linkEnd(l.target as string | NetNode);
      if (highlight) return highlight.pairs.has(pairKey(s, t));
      if (activeId) return s === activeId || t === activeId;
      return null;
    },
    [highlight, activeId],
  );

  const linkColor = useCallback(
    (l: L) => {
      const lit = linkLit(l);
      if (highlight && lit) return "rgba(10,157,87,1)";
      const base = l.kind === "predicted" ? 0.8 : l.relationship === "Investigating Officer" ? 0.25 : 0.55;
      return `rgba(${linkRgb(l as NetLink)},${lit === null ? base : lit ? 1 : 0.06})`;
    },
    [linkLit, highlight],
  );

  const threeObject = useCallback(
    (n: N): Object3D => {
      // A falsy object means "default sphere only" to three-forcegraph; the typings
      // do not admit it, hence the cast.
      if (!labelled.has(n.id) && n.id !== activeId) return null as unknown as Object3D;
      let s = sprites.current.get(n.id);
      if (!s) {
        s = new SpriteText(n.short || n.label, 5, "#1d1d1f");
        s.backgroundColor = "rgba(255,255,255,0.82)";
        s.padding = [2, 1];
        s.borderRadius = 2;
        s.fontWeight = n.type === "Suspect" ? "700" : "600";
        sprites.current.set(n.id, s);
      }
      s.position.set(0, -(Math.cbrt(1 + 12 * (n.score || 0)) * 4 + 5), 0);
      return s;
    },
    [labelled, activeId],
  );

  // -------------------------------------------------------------- events
  const handleClick = useCallback(
    (node: N) => {
      const now = performance.now();
      const last = lastClick.current;
      if (last && last.id === node.id && now - last.t < DOUBLE_CLICK_MS) {
        lastClick.current = null;
        onExpand(node);
        return;
      }
      lastClick.current = { id: node.id, t: now };
      onSelect(node);
    },
    [onExpand, onSelect],
  );

  return (
    <div
      ref={wrapRef}
      className="absolute inset-0"
      onMouseMove={(e) => {
        const rect = wrapRef.current?.getBoundingClientRect();
        pointer.current = { x: e.clientX - (rect?.left ?? 0), y: e.clientY - (rect?.top ?? 0) };
      }}
      onContextMenu={(e) => e.preventDefault()}
      // The engine does not always report hover-out when the pointer leaves the
      // canvas; a stale hover kept the chart dimmed (and exported it dimmed).
      onMouseLeave={() => setHover(null)}
    >
      <ForceGraph3D<NetNode, NetLink>
        ref={fgRef}
        width={width}
        height={height}
        graphData={graphData}
        backgroundColor={CANVAS_BG}
        showNavInfo={false}
        rendererConfig={{ preserveDrawingBuffer: true, antialias: true }}
        nodeId="id"
        nodeLabel={() => ""}
        nodeRelSize={4}
        nodeVal={(n: N) => 1 + 12 * (n.score || 0)}
        nodeColor={colorOf}
        nodeOpacity={0.95}
        nodeResolution={10}
        nodeThreeObject={threeObject}
        nodeThreeObjectExtend
        linkColor={linkColor}
        linkWidth={(l: L) => (linkLit(l) ? 1.1 : 0)}
        linkOpacity={0.9}
        linkCurvature={(l: L) => (l.kind === "predicted" ? 0.3 : l.derived ? 0.15 : 0)}
        linkDirectionalParticles={(l: L) => (highlight && linkLit(l) ? 2 : 0)}
        linkDirectionalParticleWidth={2}
        linkDirectionalParticleColor={() => "#0a9d57"}
        warmupTicks={30}
        cooldownTicks={160}
        onEngineStop={() => {
          if (pendingFit.current) {
            pendingFit.current = false;
            fitNow();
          }
        }}
        onNodeClick={handleClick}
        onNodeRightClick={(node: N, ev: MouseEvent) => {
          ev.preventDefault();
          const rect = wrapRef.current?.getBoundingClientRect();
          onContext(node, ev.clientX - (rect?.left ?? 0), ev.clientY - (rect?.top ?? 0));
        }}
        onNodeHover={(node: N | null) => {
          setHover(node ? { node, x: pointer.current.x, y: pointer.current.y } : null);
        }}
        onNodeDragEnd={(node: N) => {
          node.fx = node.x;
          node.fy = node.y;
          node.fz = node.z;
          node.__userPinned = true;
        }}
        onBackgroundClick={() => onSelect(null)}
      />
      {hover && <HoverCard node={hover.node} x={hover.x} y={hover.y} width={width} />}
    </div>
  );
}

function dolly(fg: ForceGraphMethods<N, L> | undefined, factor: number) {
  if (!fg) return;
  const p = fg.camera().position;
  fg.cameraPosition({ x: p.x * factor, y: p.y * factor, z: p.z * factor }, undefined, 300);
}
