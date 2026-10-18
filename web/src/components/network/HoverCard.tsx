"use client";

/** The hover card both renderers show beside the pointer. */
import type { NetNode } from "./types";
import { TYPE_STYLE } from "./style";

export default function HoverCard({ node, x, y, width }: { node: NetNode; x: number; y: number; width: number }) {
  const left = Math.min(Math.max(8, x + 14), Math.max(8, width - 260));
  const ts = TYPE_STYLE[node.type];
  return (
    <div
      className="pointer-events-none absolute z-30 w-[248px] rounded-xl border border-black/10 bg-white/95 px-3 py-2.5 shadow-[0_12px_40px_-12px_rgba(17,20,32,0.35)] backdrop-blur"
      style={{ left, top: Math.max(8, y + 14) }}
    >
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: ts?.color }}>
        {ts?.label || node.type}
        {node.hop === 0 && <span className="rounded bg-black/[0.06] px-1 text-[9px] text-muted">seed</span>}
      </div>
      <div className="mt-0.5 truncate text-[13.5px] font-bold text-ink">{node.label}</div>
      {node.sub && <div className="truncate text-[11.5px] text-muted">{node.sub}</div>}
      <div className="mt-1.5 flex flex-wrap gap-1 text-[10.5px]">
        {node.warrant && <span className="rounded-md bg-crimson/10 px-1.5 py-0.5 font-semibold text-crimson">Active warrant</span>}
        {node.rowdy && <span className="rounded-md bg-brand/12 px-1.5 py-0.5 font-semibold text-brand">Rowdy-sheeter</span>}
        {node.risk && <span className="rounded-md bg-black/[0.05] px-1.5 py-0.5 text-muted">{node.risk} risk</span>}
        {node.community && <span className="rounded-md bg-black/[0.05] px-1.5 py-0.5 text-muted">{node.community}</span>}
        <span className="rounded-md bg-black/[0.05] px-1.5 py-0.5 text-muted">{node.degree} links in graph</span>
      </div>
      <div className="mt-1.5 text-[10px] text-faint">Click to select · double-click to expand · right-click for actions</div>
    </div>
  );
}
