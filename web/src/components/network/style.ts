/**
 * One visual vocabulary for the 2D canvas, the 3D scene, the legend and the
 * panels. A colour or shape defined twice drifts, and a legend that disagrees
 * with the drawing is worse than no legend.
 *
 * Every type has a distinct SHAPE as well as a colour, so the chart still reads
 * for a colour-blind officer and when colouring switches to community or risk.
 */
import type { ColorBy, EntityType, NetLink, NetNode } from "./types";

export type Shape = "circle" | "square" | "hexagon" | "diamond" | "triangle";

export const TYPE_STYLE: Record<EntityType, { color: string; label: string; plural: string; shape: Shape }> = {
  Suspect: { color: "#dc2b25", label: "Person", plural: "People", shape: "circle" },
  Case: { color: "#2f74c0", label: "FIR", plural: "FIRs", shape: "square" },
  Officer: { color: "#0a9d57", label: "Officer", plural: "Officers", shape: "hexagon" },
  Vehicle: { color: "#c96a0c", label: "Vehicle", plural: "Vehicles", shape: "diamond" },
  CyberID: { color: "#7c3aed", label: "Cyber identifier", plural: "Cyber identifiers", shape: "triangle" },
};

export const RISK_COLOR: Record<string, string> = {
  Critical: "#b4161b",
  High: "#e0621a",
  Medium: "#c9a227",
  Low: "#6a9f3a",
};
export const NEUTRAL = "#b9bdc7";

// Twelve hues spaced for adjacency on a light canvas; a syndicate keeps its colour
// across reloads because it is hashed from the id, not assigned in arrival order.
const COMMUNITY_PALETTE = [
  "#2f74c0", "#dc2b25", "#0a9d57", "#b07d16", "#7c3aed", "#0e8a9a",
  "#c2410c", "#be185d", "#4d7c0f", "#1e40af", "#a16207", "#6d28d9",
];

export function communityColor(id: string | null): string {
  if (!id) return NEUTRAL;
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return COMMUNITY_PALETTE[h % COMMUNITY_PALETTE.length];
}

export function nodeColor(n: NetNode, by: ColorBy): string {
  if (by === "community") return communityColor(n.community);
  if (by === "risk") return n.type === "Suspect" ? RISK_COLOR[n.risk || ""] || NEUTRAL : NEUTRAL;
  return TYPE_STYLE[n.type]?.color || NEUTRAL;
}

/** On-screen radius in CSS pixels: size encodes the view's PageRank. */
export function nodeRadius(n: NetNode): number {
  const base = n.type === "Case" ? 4.2 : n.type === "Officer" ? 4.6 : 5;
  const r = base + 8.5 * Math.sqrt(Math.max(0, Math.min(1, n.score || 0)));
  return n.hop === 0 ? Math.max(r, 8) : r;
}

export function drawShape(ctx: CanvasRenderingContext2D, shape: Shape, x: number, y: number, r: number) {
  ctx.beginPath();
  switch (shape) {
    case "circle":
      ctx.arc(x, y, r, 0, Math.PI * 2);
      break;
    case "square": {
      // A plain rect: roundRect is several times dearer to rasterise, and at 500
      // FIRs on screen that is visible in the frame rate.
      const s = r * 0.9;
      ctx.rect(x - s, y - s, s * 2, s * 2);
      break;
    }
    case "diamond":
      ctx.moveTo(x, y - r * 1.15);
      ctx.lineTo(x + r * 1.05, y);
      ctx.lineTo(x, y + r * 1.15);
      ctx.lineTo(x - r * 1.05, y);
      ctx.closePath();
      break;
    case "triangle":
      ctx.moveTo(x, y - r * 1.2);
      ctx.lineTo(x + r * 1.1, y + r * 0.8);
      ctx.lineTo(x - r * 1.1, y + r * 0.8);
      ctx.closePath();
      break;
    case "hexagon":
      for (let i = 0; i < 6; i++) {
        const a = (Math.PI / 3) * i - Math.PI / 6;
        const px = x + r * 1.05 * Math.cos(a);
        const py = y + r * 1.05 * Math.sin(a);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      break;
  }
}

/** Relationship -> stroke. Alpha is applied by the renderer. */
export const REL_STYLE: Record<string, { rgb: string; label: string; dashed?: boolean }> = {
  Charged: { rgb: "220,43,37", label: "Charged in FIR" },
  "Co-Offender": { rgb: "176,125,22", label: "Co-offender in FIR" },
  "Known Associate": { rgb: "29,29,31", label: "Known associate (registry)" },
  "Investigating Officer": { rgb: "10,157,87", label: "Investigating officer" },
  "Cyber Trace": { rgb: "124,58,237", label: "Identifier traced in FIR" },
  "Vehicle Involved": { rgb: "201,106,12", label: "Vehicle in FIR" },
  "Co-accused": { rgb: "176,125,22", label: "Co-accused, derived from shared FIRs", dashed: true },
  Predicted: { rgb: "190,24,93", label: "Predicted lead (Adamic-Adar)", dashed: true },
};

export function linkRgb(l: NetLink): string {
  if (l.kind === "predicted") return REL_STYLE.Predicted.rgb;
  return (REL_STYLE[l.relationship] || { rgb: "107,107,115" }).rgb;
}

/** Screen-pixel stroke, quantised so the renderer can batch strokes. */
export function linkBaseWidth(l: NetLink): number {
  if (l.kind === "predicted") return 1.4;
  if (l.relationship === "Investigating Officer") return 0.6;
  const w = 0.9 + Math.min(3.2, Math.log2(1 + (l.weight || 1)) * 1.1);
  return Math.round(w * 2) / 2;
}

export const CANVAS_BG = "#f3f4f7";
