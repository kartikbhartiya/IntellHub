"use client";

import { Cpu, Database } from "lucide-react";
import type { AgentTraceStep } from "@/lib/api";

/* Minimal rich text for copilot replies: **bold**, "- " bullet lists, and every case
 * number / offender id / officer id rendered as a link that opens the record. */
const ENTITY_RE = /(\*\*[^*]+\*\*|\b\d{15,20}\b|\bSUS-\d{3,6}\b|\bKGID\d{4,8}\b)/g;

export function Inline({ text, onEntity, dark }: { text: string; onEntity: (id: string) => void; dark?: boolean }) {
  const parts = text.split(ENTITY_RE);
  return (
    <>
      {parts.map((p, i) => {
        if (!p) return null;
        if (p.startsWith("**") && p.endsWith("**"))
          return (
            <strong key={i} className={dark ? "font-semibold text-white" : "font-semibold text-ink"}>
              {p.slice(2, -2)}
            </strong>
          );
        if (/^(\d{15,20}|SUS-\d{3,6})$/.test(p))
          return (
            <button
              key={i}
              type="button"
              onClick={() => onEntity(p)}
              title={`Open ${p}`}
              className={`rounded px-1 font-mono text-[0.92em] transition-colors ${
                dark ? "bg-white/10 text-[#f5d486] hover:bg-white/20" : "bg-brand/[0.08] text-brand-deep hover:bg-brand/15"
              }`}
            >
              {p}
            </button>
          );
        if (/^KGID\d+$/.test(p))
          return (
            <span key={i} className={`font-mono text-[0.92em] ${dark ? "text-white/80" : "text-ink/80"}`}>
              {p}
            </span>
          );
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

export function RichText({ text, onEntity, dark }: { text: string; onEntity: (id: string) => void; dark?: boolean }) {
  const blocks: { kind: "p" | "ul"; lines: string[] }[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) continue;
    const bullet = /^\s*[-•*]\s+/.test(line);
    const content = bullet ? line.replace(/^\s*[-•*]\s+/, "") : line;
    const last = blocks[blocks.length - 1];
    if (bullet && last?.kind === "ul") last.lines.push(content);
    else blocks.push({ kind: bullet ? "ul" : "p", lines: [content] });
  }
  return (
    <div className="space-y-1.5">
      {blocks.map((b, i) =>
        b.kind === "ul" ? (
          <ul key={i} className="space-y-1">
            {b.lines.map((l, j) => (
              <li key={j} className="flex gap-2">
                <span className={`mt-[0.55em] h-1 w-1 shrink-0 rounded-full ${dark ? "bg-[#f5d486]" : "bg-brand"}`} />
                <span className="min-w-0">
                  <Inline text={l} onEntity={onEntity} dark={dark} />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            <Inline text={b.lines[0]} onEntity={onEntity} dark={dark} />
          </p>
        )
      )}
    </div>
  );
}

export function TraceStrip({ trace, mode, dark }: { trace?: AgentTraceStep[]; mode?: string; dark?: boolean }) {
  if (!trace?.length && !mode) return null;
  const engine = mode?.startsWith("llm:") ? `AI reasoning · ${mode.slice(4)}` : mode === "deterministic" ? "Records engine" : null;
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
      {engine && (
        <span
          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${
            dark ? "bg-white/10 text-white/60" : "bg-black/[0.05] text-faint"
          }`}
        >
          <Cpu className="h-3 w-3" /> {engine}
        </span>
      )}
      {(trace || []).slice(0, 5).map((t, i) => (
        <span
          key={i}
          title={t.detail}
          className={`inline-flex max-w-[16rem] items-center gap-1 truncate rounded-full px-2 py-0.5 text-[10.5px] ${
            dark ? "bg-white/[0.06] text-white/55" : "bg-azure/[0.07] text-azure"
          }`}
        >
          <Database className="h-3 w-3 shrink-0" />
          <span className="truncate">
            {t.label}
            {t.result && t.result !== "ok" ? ` · ${t.result}` : ""}
          </span>
        </span>
      ))}
    </div>
  );
}

