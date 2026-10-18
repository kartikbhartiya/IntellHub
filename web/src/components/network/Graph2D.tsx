"use client";

/**
 * The 2D link chart. Loaded only in the browser (the page imports it through
 * next/dynamic with ssr:false) because force-graph touches `window` at import.
 *
 * Everything is sized in screen pixels and divided by the canvas scale, so a node
 * is the same size at every zoom — sizing in graph units is what once rendered a
 * twelve-node graph as saucers under 30px labels. Labels are rationed by the view's
 * PageRank and the zoom, and hovered/selected neighbourhoods always get theirs.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import type { GraphViewProps, NetLink, NetNode } from "./types";
import { linkEnd } from "./types";
import {
  CANVAS_BG,
  REL_STYLE,
  TYPE_STYLE,
  drawShape,
  linkBaseWidth,
  linkRgb,
  nodeColor,
  nodeRadius,
} from "./style";
import { canvasFont, downloadCanvas, pairKey } from "./util";
import HoverCard from "./HoverCard";

type N = NodeObject<NetNode>;
type L = LinkObject<NetNode, NetLink>;


const DOUBLE_CLICK_MS = 320;

/**
 * A light collision force. Charge alone lets a syndicate's members and their
 * shared FIRs settle on top of one another; this keeps marks from overlapping.
 * Grid-bucketed, so it stays linear in the node count at the 500-node ceiling.
 */
function collideForce(getZoom: () => number, pad = 3, strength = 0.7) {
  let nodes: N[] = [];
  // Nodes are drawn at a fixed pixel size, so their footprint in graph units grows
  // as the view zooms out. Clamped, so a fit cannot feed back into the spacing.
  const scale = () => 1 / Math.min(1.2, Math.max(0.5, getZoom()));
  const cell = 28;
  const force = () => {
    const grid = new Map<string, N[]>();
    for (const n of nodes) {
      if (n.x === undefined || n.y === undefined) continue;
      const k = `${Math.floor(n.x / cell)},${Math.floor(n.y / cell)}`;
      const b = grid.get(k);
      if (b) b.push(n);
      else grid.set(k, [n]);
    }
    for (const a of nodes) {
      if (a.x === undefined || a.y === undefined) continue;
      const cx = Math.floor(a.x / cell);
      const cy = Math.floor(a.y / cell);
      const k = scale();
      const ra = (nodeRadius(a) + pad) * k;
      for (let dx = -1; dx <= 1; dx++) {
        for (let dy = -1; dy <= 1; dy++) {
          const bucket = grid.get(`${cx + dx},${cy + dy}`);
          if (!bucket) continue;
          for (const b of bucket) {
            if (b === a || b.id < a.id || b.x === undefined || b.y === undefined) continue;
            let ex = b.x - a.x;
            let ey = b.y - a.y;
            let d = Math.hypot(ex, ey);
            const min = ra + (nodeRadius(b) + pad) * k;
            if (d >= min) continue;
            if (d === 0) {
              ex = 0.5 - Math.random();
              ey = 0.5 - Math.random();
              d = Math.hypot(ex, ey);
            }
            const push = ((min - d) / d) * 0.5 * strength;
            a.vx = (a.vx || 0) - ex * push;
            a.vy = (a.vy || 0) - ey * push;
            b.vx = (b.vx || 0) + ex * push;
            b.vy = (b.vy || 0) + ey * push;
          }
        }
      }
    }
  };
  force.initialize = (ns: N[]) => {
    nodes = ns;
  };
  return force;
}

export default function Graph2D(props: GraphViewProps) {
  const {
    width, height, nodes, links, predicted, showPredicted, colorBy, selectedId, highlight,
    fitToken, fitIds, insetRight = 0, showLabels, onSelect, onExpand, onContext, controlRef,
  } = props;
  const insetRef = useRef(insetRight);
  useEffect(() => {
    insetRef.current = insetRight;
  }, [insetRight]);
  const fgRef = useRef<ForceGraphMethods<N, L> | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef(1);
  const pendingFit = useRef(true);
  const fitOnly = useRef<Set<string> | null>(null);
  const lastClick = useRef<{ id: string; t: number } | null>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const [hover, setHover] = useState<{ node: NetNode; x: number; y: number } | null>(null);

  const graphData = useMemo(() => ({ nodes: nodes as N[], links: links as L[] }), [nodes, links]);
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

  // Label priority: rank by score once per data change, not per frame.
  const labelRank = useMemo(() => {
    const ranked = [...nodes].sort((a, b) => (b.score || 0) - (a.score || 0) || (a.hop - b.hop));
    return new Map(ranked.map((n, i) => [n.id, i]));
  }, [nodes]);

  const activeId = hover?.node.id ?? selectedId;
  const hovering = !!hover;
  const activeHood = activeId ? hood.get(activeId) : undefined;

  // -------------------------------------------------------------- forces
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const n = nodes.length;
    // Spread scales with size so a 20-node view is not a dust cloud and a 500-node
    // one is not a ball.
    const charge = n > 300 ? -70 : n > 120 ? -110 : -180;
    fg.d3Force("charge")?.strength((d: N) => (d.type === "Officer" ? charge * 1.8 : charge)).distanceMax(560);
    fg.d3Force("link")?.distance((l: L) =>
      l.relationship === "Investigating Officer" ? 64 : l.derived ? 70 : 38);
    if (!fg.d3Force("collide")) fg.d3Force("collide", collideForce(() => zoomRef.current));
  }, [nodes.length]);

  // zoomToFit centres on the whole canvas; with the profile panel open the right
  // of it is covered, so fit into the visible part instead.
  const fitInto = useCallback((only: Set<string> | null, ms = 500) => {
    const fg = fgRef.current;
    if (!fg) return;
    const filter = only ? (n: N) => only.has(n.id) : undefined;
    const inset = insetRef.current;
    if (!inset) {
      fg.zoomToFit(ms, 56, filter);
      return;
    }
    const box = fg.getGraphBbox(filter);
    if (!box) return;
    const pad = 56;
    const w = Math.max(1, box.x[1] - box.x[0]);
    const h = Math.max(1, box.y[1] - box.y[0]);
    const availW = Math.max(120, width - inset - pad * 2);
    const availH = Math.max(120, height - pad * 2);
    const k = Math.min(8, availW / w, availH / h);
    fg.zoom(k, ms);
    fg.centerAt((box.x[0] + box.x[1]) / 2 + inset / 2 / k, (box.y[0] + box.y[1]) / 2, ms);
  }, [width, height]);

  const fitNow = useCallback(() => fitInto(fitOnly.current), [fitInto]);

  useEffect(() => {
    pendingFit.current = true;
    fitOnly.current = fitIds && fitIds.length ? new Set(fitIds) : null;
    // The engine may already be idle (a filter that removed nothing); fit anyway.
    const t = setTimeout(() => {
      if (pendingFit.current) {
        fitNow();
        pendingFit.current = false;
      }
    }, 2200);
    return () => clearTimeout(t);
  }, [fitToken, fitIds, fitNow]);

  // -------------------------------------------------------------- controls
  useEffect(() => {
    controlRef.current = {
      zoomIn: () => {
        const fg = fgRef.current;
        if (fg) fg.zoom(fg.zoom() * 1.35, 250);
      },
      zoomOut: () => {
        const fg = fgRef.current;
        if (fg) fg.zoom(fg.zoom() / 1.35, 250);
      },
      fit: (ids?: string[]) => fitInto(ids && ids.length ? new Set(ids) : null, 450),
      focusNode: (id: string) => {
        const n = byId.get(id);
        if (n && n.x !== undefined && n.y !== undefined) {
          fgRef.current?.centerAt(n.x, n.y, 600);
          fgRef.current?.zoom(Math.max(2.2, zoomRef.current), 600);
        }
      },
      reheat: () => fgRef.current?.d3ReheatSimulation(),
      exportPNG: (filename: string) => {
        const canvas = wrapRef.current?.querySelector("canvas");
        if (canvas) downloadCanvas(canvas, filename, CANVAS_BG);
      },
    };
  }, [byId, controlRef, fitInto]);

  // -------------------------------------------------------------- painting
  const paintNode = useCallback(
    (node: N, ctx: CanvasRenderingContext2D, scale: number) => {
      if (node.x === undefined || node.y === undefined) return;
      const px = (v: number) => v / scale;
      const x = node.x;
      const y = node.y;
      const r = px(nodeRadius(node));
      const isActive = activeId === node.id;
      const inHood = !activeId || isActive || !!activeHood?.has(node.id);
      const onPath = !highlight || highlight.nodes.has(node.id);
      const lit = inHood && onPath;
      // Hover isolates a neighbourhood hard; a standing selection only recedes the
      // rest, so the wider context stays readable while the profile is open.
      ctx.globalAlpha = lit ? 1 : hovering || highlight ? 0.12 : 0.34;

      if (highlight && highlight.nodes.has(node.id)) {
        ctx.beginPath();
        ctx.arc(x, y, r + px(9), 0, Math.PI * 2);
        ctx.fillStyle = "rgba(10,157,87,0.16)";
        ctx.fill();
      }
      if (node.warrant) {
        ctx.beginPath();
        ctx.arc(x, y, r + px(3.2), 0, Math.PI * 2);
        ctx.strokeStyle = "#dc2b25";
        ctx.lineWidth = px(2);
        ctx.stroke();
      }
      if (node.rowdy) {
        ctx.beginPath();
        ctx.setLineDash([px(2.6), px(2)]);
        ctx.arc(x, y, r + px(node.warrant ? 6.4 : 3.2), 0, Math.PI * 2);
        ctx.strokeStyle = "#b07d16";
        ctx.lineWidth = px(1.6);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      drawShape(ctx, TYPE_STYLE[node.type]?.shape || "circle", x, y, r);
      ctx.fillStyle = nodeColor(node, colorBy);
      ctx.fill();
      ctx.lineWidth = px(1.4);
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();

      if (node.hop === 0 && nodeRadius(node) >= 7) {
        ctx.beginPath();
        ctx.arc(x, y, r * 0.3, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
      }
      if (node.id === selectedId) {
        drawShape(ctx, TYPE_STYLE[node.type]?.shape || "circle", x, y, r + px(3.6));
        ctx.strokeStyle = "#1d1d1f";
        ctx.lineWidth = px(1.8);
        ctx.stroke();
      }

      ctx.globalAlpha = 1;
    },
    [activeId, activeHood, hovering, highlight, colorBy, selectedId],
  );

  const paintPointer = useCallback((node: N, color: string, ctx: CanvasRenderingContext2D, scale: number) => {
    if (node.x === undefined || node.y === undefined) return;
    ctx.beginPath();
    ctx.arc(node.x, node.y, (nodeRadius(node) + 4) / scale, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }, []);

  const linkIsLit = useCallback(
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
      const lit = linkIsLit(l);
      if (highlight && lit) return "rgba(10,157,87,0.95)";
      const base = l.relationship === "Investigating Officer" ? 0.3 : l.derived ? 0.6 : 0.5;
      const a = lit === null ? base : lit ? 0.95 : hovering || highlight ? 0.05 : 0.14;
      return `rgba(${linkRgb(l as NetLink)},${a})`;
    },
    [linkIsLit, highlight, hovering],
  );

  const linkWidth = useCallback(
    (l: L) => {
      const lit = linkIsLit(l);
      return linkBaseWidth(l as NetLink) * (lit ? 1.8 : 1);
    },
    [linkIsLit],
  );

  // Dash lengths are in graph units, so divide by the (coarsened) zoom to keep
  // them a constant on-screen length without minting a new stroke batch per frame.
  const linkDash = useCallback((l: L) => {
    if (!l.derived) return null;
    const z = Math.max(0.2, Math.round(zoomRef.current * 4) / 4);
    return [5 / z, 3.5 / z];
  }, []);

  // Label text widths at the base size, measured once per node rather than every
  // frame: width scales linearly with font size, so it is divided by the zoom.
  const labelWidths = useRef(new Map<string, number>());

  /**
   * Labels in one pass after every node is painted, so no node covers a label and
   * the font is set twice per frame instead of once per node. Rationed: everything
   * in a small view; otherwise the top of the PageRank order, widening as the
   * reader zooms in, plus whatever they are looking at.
   */
  const drawLabels = useCallback(
    (ctx: CanvasRenderingContext2D, scale: number) => {
      const budget = nodes.length <= 30 ? 1e9 : Math.min(90, Math.max(8, Math.round(10 * scale * scale)));
      const fs = 10.5 / scale;
      const font = canvasFont();
      let current = "";
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (const node of nodes) {
        if (node.x === undefined || node.y === undefined) continue;
        const isActive = activeId === node.id;
        const inHood = !activeId || isActive || !!activeHood?.has(node.id);
        const onPath = !highlight || highlight.nodes.has(node.id);
        if (!(inHood && onPath)) continue;   // a dimmed node keeps quiet
        const wanted =
          isActive || node.id === selectedId ||
          (activeId && activeHood?.has(node.id)) ||
          (highlight && highlight.nodes.has(node.id)) ||
          (showLabels && (labelRank.get(node.id) ?? 1e9) < budget);
        if (!wanted) continue;
        const weight = node.type === "Suspect" ? 700 : 600;
        const text = node.short || node.label;
        let base = labelWidths.current.get(node.id);
        if (base === undefined) {
          ctx.font = `${weight} 10.5px ${font}`;
          current = "";
          base = ctx.measureText(text).width;
          labelWidths.current.set(node.id, base);
        }
        const want = `${weight} ${fs}px ${font}`;
        if (want !== current) {
          ctx.font = want;
          current = want;
        }
        const w = base / scale;
        const r = nodeRadius(node) / scale;
        const top = node.y + r + (node.warrant || node.rowdy ? 8 : 4) / scale;
        ctx.fillStyle = "rgba(255,255,255,0.86)";
        ctx.fillRect(node.x - w / 2 - 4 / scale, top - 2 / scale, w + 8 / scale, fs + 4.5 / scale);
        ctx.fillStyle = "#1d1d1f";
        ctx.fillText(text, node.x, top);
      }
      ctx.restore();
    },
    [nodes, activeId, activeHood, highlight, selectedId, showLabels, labelRank],
  );

  const drawPredicted = useCallback(
    (ctx: CanvasRenderingContext2D, scale: number) => {
      if (!showPredicted || !predicted.length) return;
      ctx.save();
      ctx.setLineDash([6 / scale, 4 / scale]);
      ctx.lineWidth = 1.6 / scale;
      for (const p of predicted) {
        const a = byId.get(p.source);
        const b = byId.get(p.target);
        if (!a || !b || a.x === undefined || b.x === undefined || a.y === undefined || b.y === undefined) continue;
        const lit = !activeId || p.source === activeId || p.target === activeId;
        ctx.strokeStyle = `rgba(${REL_STYLE.Predicted.rgb},${lit ? 0.85 : 0.1})`;
        // Bowed, so a lead never sits exactly on top of a recorded link.
        const mx = (a.x + b.x) / 2 - (b.y - a.y) * 0.18;
        const my = (a.y + b.y) / 2 + (b.x - a.x) * 0.18;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo(mx, my, b.x, b.y);
        ctx.stroke();
      }
      ctx.restore();
    },
    [showPredicted, predicted, byId, activeId],
  );

  // -------------------------------------------------------------- events
  const handleClick = useCallback(
    (node: N) => {
      const now = performance.now();
      const last = lastClick.current;
      // force-graph has no double-click event; two clicks on one node close
      // together are the gesture.
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

  const handleRightClick = useCallback(
    (node: N, ev: MouseEvent) => {
      ev.preventDefault();
      const rect = wrapRef.current?.getBoundingClientRect();
      onContext(node, ev.clientX - (rect?.left ?? 0), ev.clientY - (rect?.top ?? 0));
    },
    [onContext],
  );

  const handleHover = useCallback((node: N | null) => {
    setHover(node ? { node, x: pointer.current.x, y: pointer.current.y } : null);
    if (wrapRef.current) wrapRef.current.style.cursor = node ? "pointer" : "grab";
  }, []);

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
      <ForceGraph2D<NetNode, NetLink>
        ref={fgRef}
        width={width}
        height={height}
        graphData={graphData}
        backgroundColor={CANVAS_BG}
        nodeId="id"
        nodeLabel={() => ""}
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={paintPointer}
        linkColor={linkColor}
        linkWidth={linkWidth}
        linkLineDash={linkDash}
        linkCurvature={(l: L) => (l.derived ? 0.18 : 0)}
        linkDirectionalParticles={(l: L) => (highlight && linkIsLit(l) ? 2 : 0)}
        linkDirectionalParticleWidth={2.4}
        linkDirectionalParticleSpeed={0.007}
        linkDirectionalParticleColor={() => "#0a9d57"}
        onRenderFramePost={(ctx, scale) => {
          drawPredicted(ctx, scale);
          drawLabels(ctx, scale);
        }}
        warmupTicks={40}
        cooldownTicks={180}
        cooldownTime={4500}
        d3AlphaDecay={0.035}
        d3VelocityDecay={0.32}
        minZoom={0.15}
        maxZoom={14}
        onZoom={(t) => {
          zoomRef.current = t.k;
        }}
        onEngineStop={() => {
          if (pendingFit.current) {
            pendingFit.current = false;
            fitNow();
          }
        }}
        onNodeClick={handleClick}
        onNodeRightClick={handleRightClick}
        onNodeHover={handleHover}
        onNodeDragEnd={(node: N) => {
          // A node the investigator placed stays placed.
          node.fx = node.x;
          node.fy = node.y;
          node.__userPinned = true;
        }}
        onBackgroundClick={() => onSelect(null)}
        onBackgroundRightClick={(e) => e.preventDefault()}
      />
      {hover && <HoverCard node={hover.node} x={hover.x} y={hover.y} width={width} />}
    </div>
  );
}
