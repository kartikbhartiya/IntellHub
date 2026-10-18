"use client";

/**
 * Syndicates mode: the Louvain communities over repeat co-offending, largest
 * first. Selecting one loads the crew, the FIRs they share and one hop around
 * them, coloured by syndicate.
 */
import { useEffect, useMemo, useState } from "react";
import { LoaderCircle, Search, Users } from "lucide-react";
import { ApiError } from "@/lib/api";
import { syndicates } from "./netApi";
import type { Syndicate } from "./types";
import { RISK_COLOR, communityColor } from "./style";

export default function SyndicatePanel({ activeId, onLoad }: { activeId: string | null; onLoad: (id: string) => void }) {
  const [rows, setRows] = useState<Syndicate[] | null>(null);
  const [method, setMethod] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    let alive = true;
    syndicates()
      .then((r) => {
        if (!alive) return;
        setRows(r.syndicates);
        setMethod(r.method);
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError && e.isForbidden ? "Sign in as an investigator to list syndicates." : "Syndicates could not be loaded.");
      });
    return () => {
      alive = false;
    };
  }, []);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!rows) return [];
    if (!needle) return rows;
    return rows.filter((s) =>
      s.syndicate_id.toLowerCase().includes(needle) ||
      s.members.some((m) => m.name.toLowerCase().includes(needle) || m.id.toLowerCase().includes(needle) || (m.gang || "").toLowerCase().includes(needle)) ||
      s.districts.some((d) => d.label.toLowerCase().includes(needle)) ||
      s.crime_mix.some((c) => c.label.toLowerCase().includes(needle)));
  }, [rows, q]);

  if (error) return <p className="rounded-lg border border-crimson/25 bg-crimson/[0.05] p-2.5 text-[12px] text-crimson">{error}</p>;
  if (!rows) return <div className="flex items-center gap-2 text-[12px] text-muted"><LoaderCircle className="h-4 w-4 animate-spin" /> Loading syndicates…</div>;

  return (
    <div className="space-y-2.5">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-faint" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Member, gang, district, crime…"
          aria-label="Filter syndicates"
          className="w-full rounded-lg border border-black/10 bg-white py-2 pl-8 pr-2 text-[12px] focus:border-brand/50 focus:outline-none"
        />
      </div>
      <p className="text-[10.5px] text-faint">{shown.length} of {rows.length} syndicates</p>
      <ul className="space-y-1.5">
        {shown.slice(0, 120).map((s) => {
          const active = s.syndicate_id === activeId;
          return (
            <li key={s.syndicate_id}>
              <button
                onClick={() => onLoad(s.syndicate_id)}
                className={`w-full rounded-xl border p-2.5 text-left transition-colors ${active ? "border-brand/50 bg-brand/[0.05]" : "border-black/10 hover:border-black/20"}`}
              >
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: communityColor(s.syndicate_id) }} />
                  <span className="flex-1 text-[12px] font-bold text-ink">{s.syndicate_id}</span>
                  <span className="text-[10.5px] font-semibold" style={{ color: RISK_COLOR[s.risk_level || ""] || "#6b6b73" }}>{s.risk_level}</span>
                </div>
                <div className="mt-1 flex items-center gap-1 text-[11px] text-muted">
                  <Users className="h-3 w-3" /> {s.size} members · {s.shared_cases} shared FIRs · {s.cases_total} FIRs
                </div>
                <div className="mt-1 truncate text-[11px] text-ink">{s.members.slice(0, 4).map((m) => m.name).join(", ")}{s.members.length > 4 ? "…" : ""}</div>
                <div className="mt-1 truncate text-[10.5px] text-faint">
                  {s.districts.slice(0, 3).map((d) => d.label).join(" · ")}
                  {s.crime_mix[0] ? ` — mostly ${s.crime_mix[0].label}` : ""}
                </div>
                {(s.warrant_members > 0 || s.rowdy_members > 0) && (
                  <div className="mt-1 flex gap-1 text-[10px] font-semibold">
                    {s.warrant_members > 0 && <span className="rounded bg-crimson/10 px-1 text-crimson">{s.warrant_members} with warrants</span>}
                    {s.rowdy_members > 0 && <span className="rounded bg-brand/12 px-1 text-brand-deep">{s.rowdy_members} rowdy-sheeters</span>}
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {method && <p className="text-[10.5px] leading-snug text-faint">{method}</p>}
    </div>
  );
}
