/**
 * Small helpers shared by the canvas, the 3D scene and the page.
 */
import type { NetLink, NetNode, Predicted } from "./types";
import { linkEnd, linkKey } from "./types";

/**
 * Canvas font family, resolved once.
 *
 * A CSS variable is not a legal token in the canvas `font` shorthand: assigning
 * `var(--font-jakarta)` makes the browser reject the whole assignment and keep the
 * default 10px sans-serif. next/font emits a generated family name behind the
 * variable, so read the variable's value instead.
 */
let cachedFont: string | null = null;
export function canvasFont(): string {
  if (cachedFont) return cachedFont;
  const resolved =
    typeof window !== "undefined"
      ? getComputedStyle(document.documentElement).getPropertyValue("--font-jakarta").trim()
      : "";
  cachedFont = `${resolved ? `${resolved}, ` : ""}system-ui, -apple-system, "Segoe UI", sans-serif`;
  return cachedFont;
}

export const pairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);

/**
 * Copy a canvas onto a solid background with a provenance footer, then download.
 * The footer travels with the image for the same reason CSV exports carry a
 * header: a chart that reaches a meeting without saying it came from simulated
 * data is the artefact the model registry exists to prevent.
 */
export function downloadCanvas(src: HTMLCanvasElement, filename: string, bg: string) {
  const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  const footer = Math.round(26 * dpr);
  const out = document.createElement("canvas");
  out.width = src.width;
  out.height = src.height + footer;
  const ctx = out.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, out.width, out.height);
  ctx.drawImage(src, 0, 0);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, src.height, out.width, footer);
  ctx.fillStyle = "#6b6b73";
  ctx.font = `${Math.round(11 * dpr)}px ${canvasFont()}`;
  ctx.textBaseline = "middle";
  ctx.fillText(
    `KSP IntelHub link analysis · exported ${new Date().toISOString()} · simulated CCTNS-shaped data · decision support, not evidence`,
    Math.round(10 * dpr),
    src.height + footer / 2,
  );
  try {
    out.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 0);
    }, "image/png");
  } catch {
    // A tainted canvas cannot be exported. Nothing here draws cross-origin images
    // (photos stay in the side panel for exactly this reason), so this is defensive.
  }
}

export interface GraphState {
  nodes: NetNode[];
  links: NetLink[];
  predicted: Predicted[];
}

export const EMPTY_GRAPH: GraphState = { nodes: [], links: [], predicted: [] };

type IncomingLink = Omit<NetLink, "kind"> & { kind?: NetLink["kind"] };

function kindOf(l: IncomingLink): NetLink["kind"] {
  return l.kind ?? (l.derived ? "derived" : "recorded");
}

/**
 * Merge an expansion into the graph without a layout explosion.
 *
 * Existing node objects are kept (the force engine has written their positions
 * onto them) and pinned where they stand for a moment, while new nodes are born
 * next to the node they were expanded from instead of at the origin. The pins
 * are released after `settleMs` unless the investigator froze the layout.
 */
export function mergeGraph(
  prev: GraphState,
  incoming: { nodes: NetNode[]; links: IncomingLink[]; predicted?: Predicted[] },
  opts: { anchorId?: string | null; hidden?: Set<string>; frozen?: boolean; settleMs?: number } = {},
): GraphState {
  const byId = new Map(prev.nodes.map((n) => [n.id, n]));
  const fallbackAnchor = opts.anchorId ? byId.get(opts.anchorId) : undefined;
  const added: NetNode[] = [];
  // Where each incoming node attaches, so it can be born beside a neighbour that
  // is already placed rather than at the origin.
  const attach = new Map<string, string[]>();
  for (const l of incoming.links) {
    const s = linkEnd(l.source);
    const t = linkEnd(l.target);
    (attach.get(s) || attach.set(s, []).get(s)!).push(t);
    (attach.get(t) || attach.set(t, []).get(t)!).push(s);
  }

  for (const n of incoming.nodes) {
    const have = byId.get(n.id);
    if (have) {
      // Keep the better-informed score/hop but never move the node.
      have.score = Math.max(have.score || 0, n.score || 0);
      have.hop = Math.min(have.hop ?? 9, n.hop ?? 9);
      continue;
    }
    const fresh: NetNode = { ...n };
    const placed = (attach.get(n.id) || [])
      .map((id) => byId.get(id))
      .find((m) => m && m.x !== undefined && m.y !== undefined);
    const anchor = placed || fallbackAnchor;
    if (anchor && anchor.x !== undefined && anchor.y !== undefined) {
      const a = Math.random() * Math.PI * 2;
      const d = 24 + Math.random() * 30;
      fresh.x = anchor.x + Math.cos(a) * d;
      fresh.y = anchor.y + Math.sin(a) * d;
      if (anchor.z !== undefined) fresh.z = anchor.z + (Math.random() - 0.5) * 30;
    }
    added.push(fresh);
    byId.set(n.id, fresh);
  }

  if (added.length && prev.nodes.length) {
    const settle = opts.settleMs ?? 1400;
    const pinned: NetNode[] = [];
    for (const n of prev.nodes) {
      if (n.x === undefined || n.fx !== undefined) continue;
      n.fx = n.x;
      n.fy = n.y;
      if (n.z !== undefined) n.fz = n.z;
      pinned.push(n);
    }
    if (!opts.frozen) {
      setTimeout(() => {
        for (const n of pinned) {
          if ((n as NetNode & { __userPinned?: boolean }).__userPinned) continue;
          n.fx = undefined;
          n.fy = undefined;
          n.fz = undefined;
        }
      }, settle);
    }
  }

  const links = new Map<string, NetLink>();
  for (const l of prev.links) links.set(linkKey(linkEnd(l.source), linkEnd(l.target), l.kind), l);
  for (const l of incoming.links) {
    const kind = kindOf(l);
    const key = linkKey(linkEnd(l.source), linkEnd(l.target), kind);
    if (!links.has(key)) links.set(key, { ...l, kind } as NetLink);
  }
  const nodes = [...prev.nodes, ...added];
  const ids = new Set(nodes.map((n) => n.id));
  const pred = new Map<string, Predicted>();
  for (const p of [...prev.predicted, ...(incoming.predicted || [])]) {
    if (ids.has(p.source) && ids.has(p.target)) pred.set(linkKey(p.source, p.target, "predicted"), p);
  }
  return { nodes, links: [...links.values()], predicted: [...pred.values()] };
}

/** A fresh graph from one response (no pinning, nothing to merge with). */
export function graphFrom(res: { nodes: NetNode[]; links: IncomingLink[]; predicted?: Predicted[] }): GraphState {
  return mergeGraph(EMPTY_GRAPH, res);
}

/** The drawable subset: hidden nodes and their links removed. */
export function visibleGraph(g: GraphState, hidden: Set<string>): GraphState {
  if (!hidden.size) return g;
  const nodes = g.nodes.filter((n) => !hidden.has(n.id));
  const links = g.links.filter((l) => !hidden.has(linkEnd(l.source)) && !hidden.has(linkEnd(l.target)));
  const predicted = g.predicted.filter((p) => !hidden.has(p.source) && !hidden.has(p.target));
  return { nodes, links, predicted };
}

/** Distinct communities among the visible nodes. */
export function communityCount(nodes: NetNode[]): number {
  return new Set(nodes.map((n) => n.community).filter(Boolean)).size;
}

type Pinnable = NetNode & { __userPinned?: boolean };

/**
 * Freeze pins every node where it stands; unfreezing releases all but the ones the
 * investigator dragged into place. Plain functions rather than component effects:
 * positions live on the node objects (force-graph's model), and this is an action,
 * not a render.
 */
export function setFrozenPins(nodes: NetNode[], frozen: boolean) {
  for (const n of nodes as Pinnable[]) {
    if (frozen) {
      n.fx = n.x;
      n.fy = n.y;
      if (n.z !== undefined) n.fz = n.z;
    } else if (!n.__userPinned) {
      n.fx = undefined;
      n.fy = undefined;
      n.fz = undefined;
    }
  }
}

/** Release every pin, including ones the investigator placed. */
export function clearPins(nodes: NetNode[]) {
  for (const n of nodes as Pinnable[]) {
    n.fx = undefined;
    n.fy = undefined;
    n.fz = undefined;
    delete n.__userPinned;
  }
}
