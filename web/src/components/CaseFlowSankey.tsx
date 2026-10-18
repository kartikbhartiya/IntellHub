"use client";

/* Case flow — crime type -> FIR status -> outcome, as a Sankey.
 *
 * Reads GET /lifecycle/flow. The backend guarantees conservation (every FIR in scope
 * is one unit on exactly one path), so band widths here are drawn straight from the
 * counts with one vertical scale shared by all three columns: a band twice as wide
 * is twice as many FIRs, wherever it is.
 *
 * Drawn as an alluvial: each crime type's colour is carried through the status
 * column into the outcome column, using the three-way `paths` the endpoint returns.
 * That is what lets a hover on "Convicted" light up exactly the slice of each crime
 * type's ribbon that ends there, rather than the whole ribbon.
 *
 * Colour: ten crime types is more hues than can be told apart, so hue follows the
 * crime-head group (six, from the CrimeHead master) and crime types within a group
 * are light/dark steps of it. Every column-0 node is labelled, so colour never has
 * to identify a crime type on its own, and a group keeps its hue when the toggle
 * switches between crime types and groups.
 *
 * No chart library: a custom SVG keeps the bundle unchanged and the geometry
 * auditable. Width tracks the card (ResizeObserver); below MIN_WIDTH the chart keeps
 * its width and scrolls sideways inside the card rather than crushing the labels.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Info, Loader2, Table2, Workflow } from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";

/* ------------------------------------------------------------------ types */
type FlowNode = {
  id: string;
  label: string;
  column: 0 | 1 | 2;
  value: number;
  share: number;
  group?: string;
  group_order?: number;
  shade?: number;
  shades?: number;
};
type FlowLink = { source: string; target: string; value: number; stage: 1 | 2 };
type FlowPath = { source: string; via: string; target: string; value: number };
type Flow = {
  filters: { district: string | null; group: "crime" | "head" };
  options: { districts: string[] };
  columns: string[];
  groups: string[];
  nodes: FlowNode[];
  links: FlowLink[];
  paths: FlowPath[];
  totals: {
    firs: number;
    columns: number[];
    by_status: Record<string, number>;
    by_outcome: Record<string, number>;
    court_records_used: number;
    court_records_ignored: number;
  };
  note: string;
  source: string;
};

type Active =
  | { kind: "node"; id: string }
  | { kind: "link"; source: string; via: string }
  | { kind: "path"; key: string };

/* ---------------------------------------------------------------- palette
 * One hue per crime-head group, in CrimeHead master order (which is also the
 * column-0 order, so these are exactly the neighbours that touch). Checked with
 * the dataviz palette validator against white: worst adjacent colour-blind
 * ΔE 16.2, worst adjacent normal-vision ΔE 28.1. Pink and yellow sit below 3:1 on
 * white — every node carries a text label, which is the required relief.
 */
const GROUP_SLOTS = ["#2a78d6", "#eb6834", "#6250d6", "#e87ba4", "#008300", "#eda100"];
const UNGROUPED = "#a1a1aa";
const NEUTRAL_NODE = "#55555c";
const GRID = "#c3c2b7";

const MIN_WIDTH = 680;
const NODE_W = 12;
const NODE_PAD = 10;
const TOP = 30;
const BOTTOM = 10;
const LABEL_GAP = 30; // two-line label block height, for de-collision

const fmt = (n: number) => n.toLocaleString("en-IN");
const pct = (s: number, dp = 1) => (Number.isFinite(s) ? `${(s * 100).toFixed(dp)}%` : "—");

function mix(hex: string, t: number) {
  // t > 0 lightens toward white, t < 0 darkens toward black.
  const n = parseInt(hex.slice(1), 16);
  const target = t > 0 ? 255 : 0;
  const k = Math.abs(t);
  const ch = (v: number) => Math.round(v + (target - v) * k);
  const r = ch((n >> 16) & 255);
  const g = ch((n >> 8) & 255);
  const b = ch(n & 255);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}

function shadeSteps(k: number): number[] {
  if (k <= 1) return [0];
  if (k === 2) return [-0.18, 0.24];
  if (k === 3) return [-0.26, 0, 0.3];
  return Array.from({ length: k }, (_, i) => -0.3 + (0.65 * i) / (k - 1));
}

function nodeColour(n: FlowNode): string {
  if (n.column !== 0) return NEUTRAL_NODE;
  const gi = n.group_order ?? -1;
  const base = gi >= 0 && gi < GROUP_SLOTS.length ? GROUP_SLOTS[gi] : UNGROUPED;
  return mix(base, shadeSteps(n.shades ?? 1)[n.shade ?? 0] ?? 0);
}

/* ---------------------------------------------------------- measurement */
let measureCtx: CanvasRenderingContext2D | null = null;
function textWidth(s: string, font: string) {
  if (typeof document === "undefined") return s.length * 7;
  measureCtx ??= document.createElement("canvas").getContext("2d");
  if (!measureCtx) return s.length * 7;
  measureCtx.font = font;
  return measureCtx.measureText(s).width;
}

/* ---------------------------------------------------------------- layout */
type NodeGeo = { node: FlowNode; x: number; y: number; h: number; colour: string };
type Strand = {
  key: string;
  path: FlowPath;
  t: number;
  y0: number; // leaves the crime node
  y1: number; // enters the status node
  y2: number; // leaves the status node
  y3: number; // enters the outcome node
  colour: string;
};
type Ribbon = { key: string; source: string; via: string; y0: number; y1: number; t: number; colour: string; value: number };
type Label = {
  id: string;
  x: number;
  y: number;
  w: number;
  anchor: "start" | "end";
  name: string;
  sub: string;
  backed: boolean;
  leader?: string;
};

const pathKey = (p: FlowPath) => `${p.source}|${p.via}|${p.target}`;

function declutter(items: { y: number }[], lo: number, hi: number, gap: number) {
  // 1-D label de-collision: push down, then pull back up from the bottom bound.
  const ys = items.map((i) => i.y);
  for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + gap);
  if (ys.length && ys[ys.length - 1] > hi) ys[ys.length - 1] = hi;
  for (let i = ys.length - 2; i >= 0; i--) ys[i] = Math.min(ys[i], ys[i + 1] - gap);
  if (ys.length && ys[0] < lo) {
    ys[0] = lo;
    for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + gap);
  }
  return ys;
}

function layout(flow: Flow, width: number, height: number, family: string) {
  const N = Math.max(1, flow.totals.firs);
  const cols = [0, 1, 2].map((c) => flow.nodes.filter((n) => n.column === c));
  const nameFont = `600 12px ${family}`;
  const subFont = `400 11px ${family}`;
  const sub = (n: FlowNode) => `${fmt(n.value)} · ${pct(n.share)}`;
  const colLabelW = (ns: FlowNode[]) =>
    Math.ceil(Math.max(60, ...ns.map((n) => Math.max(textWidth(n.label, nameFont), textWidth(sub(n), subFont)))));
  const leftW = colLabelW(cols[0]) + 16;
  const rightW = colLabelW(cols[2]) + 16;
  const innerH = height - TOP - BOTTOM;
  const k = Math.min(...cols.map((c) => (innerH - Math.max(0, c.length - 1) * NODE_PAD) / N));
  const colX = [leftW, leftW + (width - leftW - rightW - NODE_W) / 2, width - rightW - NODE_W];

  const nodes = new Map<string, NodeGeo>();
  cols.forEach((ns, c) => {
    const total = flow.totals.firs * k + Math.max(0, ns.length - 1) * NODE_PAD;
    let y = TOP + (innerH - total) / 2;
    for (const n of ns) {
      const h = n.value * k;
      nodes.set(n.id, { node: n, x: colX[c], y, h, colour: nodeColour(n) });
      y += h + NODE_PAD;
    }
  });

  // Strand offsets inside each node. The sort orders are what keep ribbons from
  // crossing more than they must: a (crime, status) ribbon is contiguous at both
  // ends, and so is each (status, outcome) bundle.
  const rank = new Map(flow.nodes.map((n, i) => [n.id, i]));
  const r = (id: string) => rank.get(id) ?? 0;
  const strands = new Map<string, Strand>();
  for (const p of flow.paths) {
    strands.set(pathKey(p), {
      key: pathKey(p),
      path: p,
      t: p.value * k,
      y0: 0,
      y1: 0,
      y2: 0,
      y3: 0,
      colour: nodes.get(p.source)?.colour ?? UNGROUPED,
    });
  }
  const stack = (
    order: (a: FlowPath, b: FlowPath) => number,
    at: (p: FlowPath) => string,
    field: "y0" | "y1" | "y2" | "y3"
  ) => {
    const cursor = new Map<string, number>();
    for (const p of [...flow.paths].sort(order)) {
      const id = at(p);
      const start = cursor.get(id) ?? nodes.get(id)?.y ?? 0;
      const s = strands.get(pathKey(p));
      if (!s) continue;
      s[field] = start;
      cursor.set(id, start + s.t);
    }
  };
  stack((a, b) => r(a.source) - r(b.source) || r(a.via) - r(b.via) || r(a.target) - r(b.target), (p) => p.source, "y0");
  stack((a, b) => r(a.via) - r(b.via) || r(a.source) - r(b.source) || r(a.target) - r(b.target), (p) => p.via, "y1");
  stack((a, b) => r(a.via) - r(b.via) || r(a.target) - r(b.target) || r(a.source) - r(b.source), (p) => p.via, "y2");
  stack((a, b) => r(a.target) - r(b.target) || r(a.via) - r(b.via) || r(a.source) - r(b.source), (p) => p.target, "y3");

  // Stage-1 ribbons, one per (crime, status): the union of its strands, which the
  // ordering above makes contiguous at both ends.
  const ribbons = new Map<string, Ribbon>();
  for (const s of strands.values()) {
    const key = `${s.path.source}|${s.path.via}`;
    const rb = ribbons.get(key);
    if (!rb) {
      ribbons.set(key, { key, source: s.path.source, via: s.path.via, y0: s.y0, y1: s.y1, t: s.t, colour: s.colour, value: s.path.value });
    } else {
      rb.y0 = Math.min(rb.y0, s.y0);
      rb.y1 = Math.min(rb.y1, s.y1);
      rb.t += s.t;
      rb.value += s.path.value;
    }
  }

  // Labels: column 0 to the left of its nodes, columns 1 and 2 to the right.
  const labels: Label[] = [];
  cols.forEach((ns, c) => {
    const want = ns.map((n) => {
      const g = nodes.get(n.id)!;
      return { y: g.y + g.h / 2 };
    });
    const ys = declutter(want, TOP + 12, height - BOTTOM - 12, LABEL_GAP);
    ns.forEach((n, i) => {
      const g = nodes.get(n.id)!;
      const cy = g.y + g.h / 2;
      const x = c === 0 ? g.x - 8 : g.x + NODE_W + 7;
      const moved = Math.abs(ys[i] - cy) > 4;
      labels.push({
        id: n.id,
        x,
        y: ys[i],
        anchor: c === 0 ? "end" : "start",
        w: Math.max(textWidth(n.label, nameFont), textWidth(sub(n), subFont)),
        name: n.label,
        sub: sub(n),
        // The status labels sit over the outgoing ribbons; a translucent card
        // keeps them legible without hiding what is underneath.
        backed: c === 1,
        leader: moved
          ? c === 0
            ? `M${g.x - 1},${cy} L${x + 3},${ys[i]}`
            : `M${g.x + NODE_W + 1},${cy} L${x - 3},${ys[i]}`
          : undefined,
      });
    });
  });

  return { colX, nodes, strands: [...strands.values()], ribbons: [...ribbons.values()], labels, k };
}

function band(x0: number, y0: number, x1: number, y1: number, t: number) {
  const xm = (x0 + x1) / 2;
  const tt = Math.max(t, 0.6); // a 1-FIR band stays visible; the scale is still exact above this
  return (
    `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} ` +
    `L${x1},${y1 + tt} C${xm},${y1 + tt} ${xm},${y0 + tt} ${x0},${y0 + tt} Z`
  );
}

/* --------------------------------------------------------------- tooltip */
function useIndex(flow: Flow | null) {
  return useMemo(() => {
    const node = new Map<string, FlowNode>();
    const link = new Map<string, number>();
    flow?.nodes.forEach((n) => node.set(n.id, n));
    flow?.links.forEach((l) => link.set(`${l.source}|${l.target}`, l.value));
    return { node, link };
  }, [flow]);
}

function involved(a: Active | null, p: FlowPath) {
  if (!a) return false;
  if (a.kind === "path") return pathKey(p) === a.key;
  if (a.kind === "link") return p.source === a.source && p.via === a.via;
  return p.source === a.id || p.via === a.id || p.target === a.id;
}

function Tooltip({ active, flow, idx }: { active: Active; flow: Flow; idx: ReturnType<typeof useIndex> }) {
  const N = flow.totals.firs;
  const lbl = (id: string) => idx.node.get(id)?.label ?? id;
  let value = 0;
  let title = "";
  const rows: [string, string][] = [];

  if (active.kind === "node") {
    const n = idx.node.get(active.id);
    if (!n) return null;
    value = n.value;
    title = n.label;
    rows.push(["Share of FIRs in scope", pct(n.value / N)]);
    if (n.column === 0) {
      flow.links
        .filter((l) => l.source === n.id)
        .forEach((l) => rows.push([`→ ${lbl(l.target)}`, `${fmt(l.value)} · ${pct(l.value / n.value)}`]));
    } else if (n.column === 1) {
      flow.links
        .filter((l) => l.source === n.id)
        .forEach((l) => rows.push([`→ ${lbl(l.target)}`, `${fmt(l.value)} · ${pct(l.value / n.value)}`]));
    } else {
      const feeds = flow.links.filter((l) => l.target === n.id);
      feeds.forEach((l) => {
        const via = idx.node.get(l.source);
        if (via && feeds.length === 1 && via.value !== n.value)
          rows.push([`of ${via.label.toLowerCase()}`, pct(l.value / via.value)]);
      });
      const top = flow.paths
        .filter((p) => p.target === n.id)
        .reduce((m, p) => m.set(p.source, (m.get(p.source) ?? 0) + p.value), new Map<string, number>());
      [...top.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .forEach(([src, v]) => rows.push([lbl(src), `${fmt(v)} · ${pct(v / n.value)}`]));
    }
  } else if (active.kind === "link") {
    value = idx.link.get(`${active.source}|${active.via}`) ?? 0;
    const c = idx.node.get(active.source);
    const s = idx.node.get(active.via);
    title = `${lbl(active.source)} → ${lbl(active.via)}`;
    if (c) rows.push([`of ${c.label}`, pct(value / c.value)]);
    if (s) rows.push([`of ${s.label}`, pct(value / s.value)]);
  } else {
    const p = flow.paths.find((x) => pathKey(x) === active.key);
    if (!p) return null;
    value = p.value;
    title = `${lbl(p.source)} · ${lbl(p.via)} → ${lbl(p.target)}`;
    const cs = idx.link.get(`${p.source}|${p.via}`) ?? 0;
    const so = idx.link.get(`${p.via}|${p.target}`) ?? 0;
    rows.push([`of ${lbl(p.source)} · ${lbl(p.via).toLowerCase()}`, pct(value / cs)]);
    rows.push([`of all ${lbl(p.via).toLowerCase()} → ${lbl(p.target).toLowerCase()}`, `${pct(value / so)} of ${fmt(so)}`]);
  }

  return (
    <div className="w-[260px] rounded-xl border border-black/[0.08] bg-white/[0.98] px-3 py-2.5 shadow-[0_12px_34px_-12px_rgba(17,20,32,0.28)]">
      <div className="flex items-baseline gap-1.5">
        <span className="text-[15px] font-bold text-ink">{fmt(value)}</span>
        <span className="text-[11px] text-faint">FIRs</span>
      </div>
      <p className="mt-0.5 text-[12px] font-medium leading-snug text-muted">{title}</p>
      {rows.length > 0 && (
        <div className="mt-1.5 space-y-0.5 border-t border-black/[0.06] pt-1.5">
          {rows.map(([k, v], i) => (
            <div key={i} className="flex justify-between gap-3 text-[11.5px] leading-snug">
              <span className="min-w-0 text-faint">{k}</span>
              <span className="tabular shrink-0 font-semibold text-ink">{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ table twin */
function FlowTable({ flow }: { flow: Flow }) {
  const c0 = flow.nodes.filter((n) => n.column === 0);
  const c1 = flow.nodes.filter((n) => n.column === 1);
  const c2 = flow.nodes.filter((n) => n.column === 2);
  const cell = new Map<string, number>();
  flow.links.forEach((l) => l.stage === 1 && cell.set(`${l.source}|${l.target}`, l.value));
  flow.paths.forEach((p) => cell.set(`${p.source}|${p.target}`, (cell.get(`${p.source}|${p.target}`) ?? 0) + p.value));
  const th = "px-2 py-2 text-right font-semibold";
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-[12px]">
        <thead>
          <tr className="border-b border-black/[0.08] text-[10px] uppercase tracking-[0.12em] text-faint">
            <th scope="col" className="px-2 py-2 text-left font-semibold">{flow.columns[0]}</th>
            <th scope="col" className={th}>FIRs</th>
            {c1.map((n) => (
              <th key={n.id} scope="col" className={th}>{n.label}</th>
            ))}
            {c2.map((n) => (
              <th key={n.id} scope="col" className={`${th} text-muted`}>{n.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {c0.map((r) => (
            <tr key={r.id} className="border-b border-black/[0.05]">
              <th scope="row" className="px-2 py-2 text-left font-medium text-ink">
                <span className="mr-2 inline-block h-2 w-2 rounded-[2px]" style={{ background: nodeColour(r) }} />
                {r.label}
              </th>
              <td className="tabular px-2 py-2 text-right font-semibold text-ink">{fmt(r.value)}</td>
              {[...c1, ...c2].map((n) => (
                <td key={n.id} className="tabular px-2 py-2 text-right text-muted">
                  {fmt(cell.get(`${r.id}|${n.id}`) ?? 0)}
                </td>
              ))}
            </tr>
          ))}
          <tr className="border-t border-black/10">
            <th scope="row" className="px-2 py-2 text-left font-semibold text-ink">All</th>
            <td className="tabular px-2 py-2 text-right font-bold text-ink">{fmt(flow.totals.firs)}</td>
            {[...c1, ...c2].map((n) => (
              <td key={n.id} className="tabular px-2 py-2 text-right font-semibold text-ink">{fmt(n.value)}</td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------------- component */
type Loaded = { key: string; good: Flow | null; error?: string };

export default function CaseFlowSankey() {
  const [district, setDistrict] = useState("");
  const [group, setGroup] = useState<"crime" | "head">("crime");
  const [showTable, setShowTable] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [active, setActive] = useState<Active | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number } | null>(null);
  const [box, setBox] = useState({ width: 0, family: "sans-serif" });
  const wrapRef = useRef<HTMLDivElement>(null);

  // Measure the card, not the window: the chart lives in a grid that changes width
  // with the sidebar and the breakpoint.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const read = () =>
      setBox({ width: Math.floor(el.clientWidth), family: getComputedStyle(el).fontFamily || "sans-serif" });
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const query = useMemo(() => {
    const q = new URLSearchParams({ group });
    if (district) q.set("district", district);
    return `?${q.toString()}`;
  }, [district, group]);
  const key = `${query}#${nonce}`;

  useEffect(() => {
    let live = true;
    getJSON<Flow>(`/lifecycle/flow${query}`)
      .then((d) => {
        if (live) setLoaded({ key, good: d });
      })
      .catch((e) => {
        if (!live) return;
        setLoaded((prev) => ({
          key,
          good: prev?.good ?? null,
          error:
            e instanceof ApiError && e.status === 400
              ? "That district is not in the corpus."
              : e?.message || "The case flow could not be loaded.",
        }));
      });
    return () => {
      live = false;
    };
  }, [query, key]);

  const loading = loaded?.key !== key;
  const flow = loaded?.good ?? null;
  const error = loading ? undefined : loaded?.error;
  const idx = useIndex(flow);

  const n0 = flow ? flow.nodes.filter((n) => n.column === 0).length : 10;
  const height = Math.max(380, TOP + BOTTOM + n0 * 44);
  const width = Math.max(MIN_WIDTH, box.width);
  const geo = useMemo(
    () => (flow && box.width > 0 && flow.totals.firs > 0 ? layout(flow, width, height, box.family) : null),
    [flow, width, height, box.width, box.family]
  );

  const point = (e: React.PointerEvent | React.MouseEvent) => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    setTip({ x: e.clientX - r.left + el.scrollLeft, y: e.clientY - r.top });
  };
  const clear = () => {
    setActive(null);
    setTip(null);
  };

  const t = flow?.totals;
  const cs = t?.by_status["Charge Sheeted"] ?? 0;
  const convicted = t?.by_outcome["Convicted"] ?? 0;
  const acquitted = t?.by_outcome["Acquitted"] ?? 0;
  const stats = t
    ? [
        { l: "FIRs in scope", v: fmt(t.firs), s: district || "Statewide" },
        {
          l: "Still under investigation",
          v: pct((t.by_outcome["Still under investigation"] ?? 0) / Math.max(1, t.firs)),
          s: `${fmt(t.by_outcome["Still under investigation"] ?? 0)} FIRs`,
        },
        {
          l: "Charge-sheeted, no court record",
          v: cs ? pct((t.by_outcome["Awaiting court record"] ?? 0) / cs) : "—",
          s: `of ${fmt(cs)} charge-sheeted`,
        },
        {
          l: "Conviction rate",
          v: convicted + acquitted ? pct(convicted / (convicted + acquitted)) : "—",
          s: `of ${fmt(convicted + acquitted)} decided trials`,
        },
      ]
    : [];

  const groupLegend = flow?.groups ?? [];
  // Flip the tooltip to the other side of the pointer near the right and bottom
  // edges, so the card's overflow never clips it and it never opens a scrollbar.
  const tipStyle: React.CSSProperties | undefined = tip
    ? {
        left: tip.x > width - 290 ? tip.x - 14 : tip.x + 14,
        top: tip.y > height * 0.55 ? tip.y - 12 : tip.y + 14,
        transform: `translate(${tip.x > width - 290 ? "-100%" : "0"}, ${tip.y > height * 0.55 ? "-100%" : "0"})`,
      }
    : undefined;

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
            <Workflow className="h-4 w-4 shrink-0" /> Case flow — crime type → status → outcome
          </div>
          <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted">
            Where the pendency pipeline leaks, by crime type. Band width is proportional to case
            counts.
          </p>
        </div>
      </div>

      {/* One control row, above everything it scopes. */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <select
          aria-label="Filter by district"
          className="rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] text-ink shadow-sm focus:border-brand/40 focus:outline-none"
          value={district}
          onChange={(e) => setDistrict(e.target.value)}
        >
          <option value="">All districts</option>
          {(flow?.options.districts ?? []).map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <div
          role="radiogroup"
          aria-label="Group the first column by"
          className="flex rounded-full border border-black/10 bg-black/[0.03] p-0.5 text-[12px] font-medium"
        >
          {(
            [
              ["crime", "Crime types"],
              ["head", "Crime-head groups"],
            ] as const
          ).map(([v, l]) => (
            <button
              key={v}
              role="radio"
              aria-checked={group === v}
              onClick={() => setGroup(v)}
              className={`rounded-full px-3 py-1.5 transition-colors ${
                group === v ? "bg-brand text-white shadow-sm" : "text-muted hover:text-ink"
              }`}
            >
              {l}
            </button>
          ))}
        </div>
        <button
          onClick={() => setShowTable((s) => !s)}
          aria-pressed={showTable}
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors ${
            showTable ? "border-brand/40 bg-brand/10 text-brand" : "border-black/10 text-muted hover:text-ink"
          }`}
        >
          <Table2 className="h-3.5 w-3.5" /> Table
        </button>
        {loading && flow && <Loader2 className="h-4 w-4 animate-spin text-faint" aria-label="Updating" />}
      </div>

      {stats.length > 0 && (
        <div className={`mt-4 grid grid-cols-2 gap-3 transition-opacity md:grid-cols-4 ${loading ? "opacity-50" : ""}`}>
          {stats.map((s) => (
            <div key={s.l} className="rounded-xl border border-black/[0.06] bg-black/[0.015] px-3.5 py-2.5">
              <p className="text-[10.5px] uppercase tracking-wider text-faint">{s.l}</p>
              <p className="mt-0.5 text-[20px] font-bold text-ink">{s.v}</p>
              <p className="tabular text-[11px] text-muted">{s.s}</p>
            </div>
          ))}
        </div>
      )}

      {groupLegend.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11.5px] text-muted">
          {groupLegend.map((g, i) => (
            <span key={g} className="flex items-center gap-1.5">
              <span aria-hidden className="h-2.5 w-2.5 rounded-[3px]" style={{ background: GROUP_SLOTS[i] ?? UNGROUPED }} />
              {g}
            </span>
          ))}
          {group === "crime" && (
            <span className="text-faint">· shades separate crime types within a group</span>
          )}
        </div>
      )}

      {error && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-crimson/20 bg-crimson/[0.03] px-4 py-3 text-[12.5px] text-ink">
          <span>{error}</span>
          <button
            onClick={() => setNonce((x) => x + 1)}
            className="rounded-full border border-black/10 bg-white px-3 py-1 text-[12px] font-medium hover:border-brand/40"
          >
            Retry
          </button>
        </div>
      )}

      {/* The wrapper is always mounted so the ResizeObserver has something to watch. */}
      <div
        ref={wrapRef}
        className={`relative mt-3 overflow-x-auto overflow-y-hidden transition-opacity duration-200 ${
          loading && flow ? "opacity-50" : ""
        } ${showTable ? "hidden" : ""}`}
        aria-busy={loading}
      >
        {!flow && loading && <div className="h-[420px] animate-pulse rounded-xl bg-black/[0.03]" />}
        {flow && flow.totals.firs === 0 && (
          <p className="py-16 text-center text-[12.5px] text-faint">No FIRs in this scope.</p>
        )}
        {geo && flow && (
          <svg
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="group"
            aria-label={`Case flow of ${fmt(flow.totals.firs)} FIRs from ${flow.columns[0].toLowerCase()} to FIR status to outcome`}
            className="block select-none"
            onPointerMove={point}
            onPointerLeave={clear}
          >
            {/* column headers */}
            {flow.columns.map((c, i) => (
              <text
                key={c}
                x={i === 0 ? geo.colX[0] + NODE_W : geo.colX[i]}
                y={14}
                textAnchor={i === 0 ? "end" : "start"}
                className="fill-faint text-[10px] font-semibold uppercase"
                style={{ letterSpacing: "0.14em" }}
              >
                {c}
              </text>
            ))}
            <line x1={0} x2={width} y1={TOP - 8} y2={TOP - 8} stroke={GRID} strokeOpacity={0.45} strokeWidth={1} />

            {/* base layer: stage-1 ribbons, then stage-2 strands */}
            <g>
              {geo.ribbons.map((rb) => {
                const on = active?.kind === "link" && active.source === rb.source && active.via === rb.via;
                return (
                  <path
                    key={rb.key}
                    d={band(geo.colX[0] + NODE_W, rb.y0, geo.colX[1], rb.y1, rb.t)}
                    fill={rb.colour}
                    fillOpacity={active ? (on ? 0 : 0.07) : 0.4}
                    style={{ transition: "fill-opacity 150ms ease" }}
                    onPointerEnter={() => setActive({ kind: "link", source: rb.source, via: rb.via })}
                    onPointerLeave={() => setActive(null)}
                  />
                );
              })}
              {geo.strands.map((s) => (
                <path
                  key={s.key}
                  d={band(geo.colX[1] + NODE_W, s.y2, geo.colX[2], s.y3, s.t)}
                  fill={s.colour}
                  fillOpacity={active ? 0.07 : 0.4}
                  style={{ transition: "fill-opacity 150ms ease" }}
                  onPointerEnter={() => setActive({ kind: "path", key: s.key })}
                  onPointerLeave={() => setActive(null)}
                />
              ))}
            </g>

            {/* highlight layer: exactly the strands the hovered thing carries */}
            {active && (
              <g pointerEvents="none">
                {geo.ribbons.map((rb) => {
                  const mine = geo.strands.filter((s) => s.path.source === rb.source && s.path.via === rb.via);
                  const on = mine.filter((s) => involved(active, s.path));
                  if (!on.length) return null;
                  if (on.length === mine.length)
                    return (
                      <path
                        key={rb.key}
                        d={band(geo.colX[0] + NODE_W, rb.y0, geo.colX[1], rb.y1, rb.t)}
                        fill={rb.colour}
                        fillOpacity={0.75}
                      />
                    );
                  return on.map((s) => (
                    <path
                      key={s.key}
                      d={band(geo.colX[0] + NODE_W, s.y0, geo.colX[1], s.y1, s.t)}
                      fill={s.colour}
                      fillOpacity={0.75}
                    />
                  ));
                })}
                {geo.strands
                  .filter((s) => involved(active, s.path))
                  .map((s) => (
                    <path
                      key={s.key}
                      d={band(geo.colX[1] + NODE_W, s.y2, geo.colX[2], s.y3, s.t)}
                      fill={s.colour}
                      fillOpacity={0.75}
                    />
                  ))}
              </g>
            )}

            {/* nodes */}
            {[...geo.nodes.values()].map((g) => {
              const on = active?.kind === "node" && active.id === g.node.id;
              return (
                <g
                  key={g.node.id}
                  tabIndex={0}
                  role="button"
                  aria-label={`${g.node.label}: ${fmt(g.node.value)} FIRs, ${pct(g.node.share)} of scope`}
                  className="cursor-default outline-none"
                  onPointerEnter={() => setActive({ kind: "node", id: g.node.id })}
                  onPointerLeave={() => setActive(null)}
                  onFocus={() => {
                    setActive({ kind: "node", id: g.node.id });
                    setTip({ x: g.x + NODE_W + 4, y: g.y + g.h / 2 });
                  }}
                  onBlur={clear}
                >
                  {/* hit area wider than the 12px bar */}
                  <rect x={g.x - 6} y={g.y - 2} width={NODE_W + 12} height={Math.max(g.h, 1) + 4} fill="transparent" />
                  <rect
                    x={g.x}
                    y={g.y}
                    width={NODE_W}
                    height={Math.max(g.h, 1)}
                    rx={Math.min(3, g.h / 2)}
                    fill={g.colour}
                    stroke={on ? "#1d1d1f" : "none"}
                    strokeWidth={on ? 1.5 : 0}
                  />
                </g>
              );
            })}

            {/* labels (text tokens, never the series colour) */}
            <g pointerEvents="none">
              {geo.labels.map((l) => (
                <g key={l.id}>
                  {l.leader && <path d={l.leader} stroke={GRID} strokeWidth={1} fill="none" />}
                  {l.backed && (
                    <rect
                      x={l.anchor === "end" ? l.x - l.w - 5 : l.x - 5}
                      y={l.y - 15}
                      width={l.w + 10}
                      height={32}
                      rx={6}
                      fill="#ffffff"
                      fillOpacity={0.86}
                    />
                  )}
                  <text x={l.x} y={l.y - 2} textAnchor={l.anchor} className="fill-ink text-[12px] font-semibold">
                    {l.name}
                  </text>
                  <text x={l.x} y={l.y + 12} textAnchor={l.anchor} className="tabular fill-muted text-[11px]">
                    {l.sub}
                  </text>
                </g>
              ))}
            </g>
          </svg>
        )}

        {active && tip && flow && geo && (
          <div
            className="pointer-events-none absolute left-0 top-0 z-10"
            style={tipStyle}
          >
            <Tooltip active={active} flow={flow} idx={idx} />
          </div>
        )}
      </div>

      {showTable && flow && (
        <div className={`mt-3 transition-opacity ${loading ? "opacity-50" : ""}`}>
          <FlowTable flow={flow} />
        </div>
      )}

      {flow && (
        <div className="mt-4 flex items-start gap-2 rounded-xl border border-black/[0.07] bg-black/[0.02] p-4">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-azure" />
          <div className="space-y-1">
            <p className="text-[12px] leading-relaxed text-muted">{flow.note}</p>
            <p className="text-[11px] text-faint">{flow.source}</p>
          </div>
        </div>
      )}
    </div>
  );
}
