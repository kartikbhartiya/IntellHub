"use client";

/** What every mark on the canvas means, in the current colouring. */
import type { ColorBy, EntityType } from "./types";
import { ENTITY_TYPES } from "./types";
import { NEUTRAL, REL_STYLE, RISK_COLOR, TYPE_STYLE } from "./style";
import ShapeGlyph from "./ShapeGlyph";

const EDGE_KEYS = ["Charged", "Co-Offender", "Known Associate", "Cyber Trace", "Vehicle Involved", "Investigating Officer", "Co-accused", "Predicted"];

export default function Legend({ colorBy, present, showPredicted }: { colorBy: ColorBy; present: Set<EntityType>; showPredicted: boolean }) {
  return (
    <div className="space-y-2.5 text-[11px]">
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {ENTITY_TYPES.filter((t) => present.has(t)).map((t) => (
          <span key={t} className="flex items-center gap-1.5 text-ink">
            <ShapeGlyph shape={TYPE_STYLE[t].shape} color={colorBy === "type" ? TYPE_STYLE[t].color : "#6b6b73"} size={13} />
            {TYPE_STYLE[t].plural}
          </span>
        ))}
      </div>
      {colorBy === "risk" && (
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {Object.entries(RISK_COLOR).map(([k, c]) => (
            <span key={k} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: c }} />{k}</span>
          ))}
          <span className="flex items-center gap-1.5 text-muted"><span className="h-2.5 w-2.5 rounded-full" style={{ background: NEUTRAL }} />Not a person</span>
        </div>
      )}
      {colorBy === "community" && (
        <p className="leading-snug text-muted">
          Colour = detected syndicate (Louvain over repeat co-offending). A FIR takes a syndicate&apos;s colour only when two or
          more of its members are accused in it. <span style={{ color: "#8b8f99" }}>Grey</span> = in no syndicate.
        </p>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <span className="flex items-center gap-1.5"><ShapeGlyph shape="circle" color="#cfd2d9" size={14} ring="warrant" />Active warrant</span>
        <span className="flex items-center gap-1.5"><ShapeGlyph shape="circle" color="#cfd2d9" size={14} ring="rowdy" />Rowdy-sheeter</span>
        <span className="text-muted">Size = PageRank in this view</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {EDGE_KEYS.filter((k) => k !== "Predicted" || showPredicted).map((k) => (
          <span key={k} className="flex items-center gap-1.5 text-muted">
            <svg width="22" height="6" aria-hidden className="shrink-0">
              <line x1="0" y1="3" x2="22" y2="3" stroke={`rgb(${REL_STYLE[k].rgb})`} strokeWidth={k === "Investigating Officer" ? 1 : 2}
                    strokeDasharray={REL_STYLE[k].dashed ? "4 3" : undefined} opacity={k === "Investigating Officer" ? 0.6 : 0.9} />
            </svg>
            <span className="truncate">{REL_STYLE[k].label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
