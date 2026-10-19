"use client";

/**
 * Find path mode: two entities in, the k shortest simple paths out, each hop in
 * plain English. The note under the results says why the search stopped, so "no
 * path" is never confused with "stopped looking".
 */
import { LoaderCircle, Route } from "lucide-react";
import SearchBox, { TypeIcon } from "./SearchBox";
import type { PathResponse, SearchResult } from "./types";

interface Props {
  from: SearchResult | null;
  to: SearchResult | null;
  onFrom: (r: SearchResult | null) => void;
  onTo: (r: SearchResult | null) => void;
  k: number;
  onK: (k: number) => void;
  viaOfficers: boolean;
  onViaOfficers: (v: boolean) => void;
  onRun: () => void;
  loading: boolean;
  error: string | null;
  result: PathResponse | null;
  active: number | null;
  onActive: (i: number | null) => void;
  onSelectNode: (id: string) => void;
}

export default function PathPanel(p: Props) {
  const typeOf = (id: string) => p.result?.graph.nodes.find((n) => n.id === id)?.type || "Case";
  // An 18-digit FIR number does not fit a hop line; its tail distinguishes it.
  const short = (id: string, label: string) => (typeOf(id) === "Case" && id.length > 8 ? `FIR …${id.slice(-6)}` : label);
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">From</div>
        <SearchBox label="Path start" placeholder="Person, FIR, plate, phone…" value={p.from} onPick={p.onFrom} onClear={() => p.onFrom(null)} />
        <div className="text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">To</div>
        <SearchBox label="Path end" placeholder="Person, FIR, plate, phone…" value={p.to} onPick={p.onTo} onClear={() => p.onTo(null)} />
      </div>
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-1.5 text-[12px] text-muted">
          Paths
          <select value={p.k} onChange={(e) => p.onK(Number(e.target.value))} className="rounded-md border border-black/10 bg-white px-1.5 py-1 text-[12px] text-ink">
            {[1, 2, 3, 4, 5].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[12px] text-muted" title="Shared caseload is not association, so officers are excluded by default.">
          <input type="checkbox" checked={p.viaOfficers} onChange={(e) => p.onViaOfficers(e.target.checked)} className="h-3.5 w-3.5 accent-[#b07d16]" />
          through officers
        </label>
      </div>
      <button
        onClick={p.onRun}
        disabled={!p.from || !p.to || p.from.id === p.to.id || p.loading}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-ink py-2.5 text-[12.5px] font-semibold text-white transition-colors hover:bg-black disabled:cursor-not-allowed disabled:bg-black/25"
      >
        {p.loading ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Route className="h-4 w-4" />} Find connection
      </button>
      {p.from && p.to && p.from.id === p.to.id && <p className="text-[11.5px] text-muted">Pick two different entities.</p>}
      {p.error && <p className="rounded-lg border border-crimson/25 bg-crimson/[0.05] p-2.5 text-[12px] text-crimson">{p.error}</p>}

      {p.result && (
        <div className="space-y-2">
          {p.result.paths.length === 0 && <p className="text-[12px] text-muted">{p.result.note}</p>}
          {p.result.paths.length > 1 && (
            <button onClick={() => p.onActive(null)} className={`text-[11.5px] font-semibold ${p.active === null ? "text-ink" : "text-muted hover:text-ink"}`}>
              Highlight all {p.result.paths.length} paths
            </button>
          )}
          {p.result.paths.map((path, i) => (
            <div key={i} className={`rounded-xl border p-2.5 transition-colors ${p.active === i ? "border-emerald/50 bg-emerald/[0.04]" : "border-black/10"}`}>
              <button onClick={() => p.onActive(p.active === i ? null : i)} className="flex w-full items-center justify-between text-left">
                <span className="text-[12px] font-bold text-ink">Path {i + 1} · {path.length} {path.length === 1 ? "hop" : "hops"}</span>
                <span className="text-[10.5px] font-semibold text-emerald">{p.active === i ? "highlighted" : "highlight"}</span>
              </button>
              <p className="mt-0.5 text-[11px] text-muted">{path.summary}</p>
              <ol className="mt-2 space-y-1.5">
                {path.hops.map((h, j) => (
                  <li key={j} className="flex gap-2">
                    <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-black/[0.06] text-[9px] font-bold text-muted">{j + 1}</span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1 text-[11.5px] font-semibold text-ink">
                        <button onClick={() => p.onSelectNode(h.from)} className="flex items-center gap-1 hover:underline">
                          <TypeIcon type={typeOf(h.from)} id={h.from} className="h-3 w-3" />
                          <span className="max-w-[120px] truncate">{short(h.from, h.from_label)}</span>
                        </button>
                        <span className="text-faint">→</span>
                        <button onClick={() => p.onSelectNode(h.to)} className="flex items-center gap-1 hover:underline">
                          <TypeIcon type={typeOf(h.to)} id={h.to} className="h-3 w-3" />
                          <span className="max-w-[120px] truncate">{short(h.to, h.to_label)}</span>
                        </button>
                      </div>
                      <p className="text-[11px] leading-snug text-muted">{h.explanation}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          ))}
          {p.result.paths.length > 0 && p.result.note && <p className="text-[10.5px] leading-snug text-faint">{p.result.note}</p>}
        </div>
      )}
    </div>
  );
}
