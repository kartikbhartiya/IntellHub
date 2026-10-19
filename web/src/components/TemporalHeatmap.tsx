"use client";

/* FIRs by hour of day x day of week.
 *
 * Reads GET /analytics/temporal (appsail/temporal.py). Two choices worth stating:
 *
 * - Colour is relative to an even spread (pale = as expected, warm = above, cool =
 *   below), never min-to-max: on a near-even grid a min-to-max scale paints noise
 *   as a pattern, and a scale from zero paints every cell the same dark colour.
 * - The verdict is on the card, not in a tooltip. The server tests the grid against
 *   an even spread and only outlines a cell as hot when it clears a correction for
 *   168 comparisons; the busiest cell is always shown, and the note says whether it
 *   means anything.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Clock3, Flame, Loader2, Sigma, Sunrise } from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";

type Test = { statistic: number; df: number; p_value: number; pattern: boolean; max_excess_pct: number };
type Cell = { weekday: string; hour: number; label: string; count: number; z: number };
type Temporal = {
  filters: { district: string | null; crime_type: string | null };
  options: { districts: string[]; crime_types: string[] };
  firs: number;
  weekdays: string[];
  grid: number[][];
  top_crime: ({ crime_type: string; count: number } | null)[][];
  by_weekday: { weekday: string; count: number }[];
  by_hour: { hour: number; count: number }[];
  max_cell: number;
  expected_per_cell: number;
  peak_cell: { label: string; count: number; expected: number } | null;
  peak_window: { label: string; count: number; share: number } | null;
  busiest_weekday: string | null;
  busiest_hour: string | null;
  tests: { cells?: Test; hours?: Test; weekdays?: Test };
  hot_cells: Cell[];
  hot_threshold_z: number;
  note: string;
};

// Coloured against an even spread, not against the quietest cell. A count scale from
// zero painted an even grid uniformly dark; a min-to-max scale paints noise as a
// pattern. Relative to "expected": pale is as expected, warm above, cool below.
const BELOW = ["#3f6fb5", "#7fa3d4", "#c3d4ec"];
const EVEN = "#f4efe6";
const ABOVE = ["#f6d49a", "#ee9b4f", "#d4561f", "#a12a17"];
const STEPS = [0.6, 0.8, 0.92, 1.08, 1.2, 1.4, 1.7]; // ratio cut points
const RAMP = [...BELOW, EVEN, ...ABOVE];

function colour(v: number, expected: number) {
  if (expected <= 0) return EVEN;
  const r = v / expected;
  let i = STEPS.findIndex((cut) => r < cut);
  if (i === -1) i = RAMP.length - 1;
  return RAMP[i];
}

const selectCls =
  "rounded-xl border border-black/10 bg-black/[0.02] px-3 py-2 text-[12.5px] text-ink focus:border-brand/40 focus:outline-none";
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

export default function TemporalHeatmap({ initialDistrict = "" }: { initialDistrict?: string }) {
  const [district, setDistrict] = useState(initialDistrict);
  const [crime, setCrime] = useState("");
  const [data, setData] = useState<Temporal | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<{ d: number; h: number } | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    const q = new URLSearchParams();
    if (district) q.set("district", district);
    if (crime) q.set("crime_type", crime);
    const qs = q.toString();
    getJSON<Temporal>(`/analytics/temporal${qs ? `?${qs}` : ""}`)
      .then((d) => {
        if (!live) return;
        setData(d);
        setErr(null);
      })
      .catch((e) => live && setErr(e instanceof ApiError ? e.message : "Could not load the time pattern."))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [district, crime]);

  const hot = useMemo(() => new Set((data?.hot_cells || []).map((c) => `${c.weekday}|${c.hour}`)), [data]);
  const maxDay = useMemo(() => Math.max(1, ...(data?.by_weekday || []).map((d) => d.count)), [data]);
  const maxHour = useMemo(() => Math.max(1, ...(data?.by_hour || []).map((d) => d.count)), [data]);

  if (err && !data)
    return (
      <div className="flex items-center gap-2 rounded-xl border border-crimson/20 bg-crimson/[0.04] p-4 text-[13px] text-crimson">
        <AlertCircle className="h-4 w-4" /> {err}
      </div>
    );
  if (!data) return <div className="h-[340px] animate-pulse rounded-2xl bg-black/[0.03]" />;

  const pattern = !!(data.tests.cells?.pattern || data.tests.hours?.pattern || data.tests.weekdays?.pattern);
  const hv = hover ? { d: hover.d, h: hover.h, n: data.grid[hover.d][hover.h], top: data.top_crime[hover.d][hover.h] } : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="District" value={district} onChange={(e) => setDistrict(e.target.value)} className={selectCls}>
          <option value="">All districts</option>
          {data.options.districts.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <select aria-label="Crime type" value={crime} onChange={(e) => setCrime(e.target.value)} className={selectCls}>
          <option value="">All crime types</option>
          {data.options.crime_types.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <span className="tabular text-[12px] text-faint">{data.firs.toLocaleString("en-IN")} FIRs</span>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-faint" />}
        <span
          className={`ml-auto inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
            pattern ? "bg-crimson/10 text-crimson" : "bg-black/[0.05] text-muted"
          }`}
          title="Chi-square test against an even spread, Holm-corrected across grid, hours and weekdays"
        >
          <Sigma className="h-3.5 w-3.5" />
          {pattern ? "Pattern beyond chance" : "No pattern beyond chance"}
        </span>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_230px]">
        <div className="min-w-0">
          <div className="relative overflow-x-auto pb-1">
            <div className="min-w-[620px]">
              {/* hour axis */}
              <div className="ml-[42px] grid gap-[3px] pb-1" style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}>
                {Array.from({ length: 24 }, (_, h) => (
                  <span key={h} className="text-center text-[9.5px] tabular text-faint">
                    {h % 3 === 0 ? String(h).padStart(2, "0") : ""}
                  </span>
                ))}
              </div>
              {data.grid.map((row, d) => (
                <div key={d} className="flex items-center gap-[3px] py-[1.5px]">
                  <span className="w-[39px] shrink-0 text-[11px] font-medium text-muted">{data.weekdays[d].slice(0, 3)}</span>
                  <div className="grid flex-1 gap-[3px]" style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}>
                    {row.map((v, h) => {
                      const isHot = hot.has(`${data.weekdays[d]}|${h}`);
                      const active = hover?.d === d && hover?.h === h;
                      return (
                        <button
                          key={h}
                          type="button"
                          onMouseEnter={() => setHover({ d, h })}
                          onFocus={() => setHover({ d, h })}
                          onMouseLeave={() => setHover(null)}
                          onBlur={() => setHover(null)}
                          aria-label={`${data.weekdays[d]} ${hh(h)}: ${v} FIRs`}
                          className={`aspect-square w-full rounded-[4px] transition-transform ${
                            active ? "scale-110 ring-2 ring-ink/70" : ""
                          } ${isHot ? "ring-2 ring-crimson" : ""}`}
                          style={{ background: colour(v, data.expected_per_cell) }}
                        />
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-[11px] text-faint">
              <span>below</span>
              <div className="flex">
                {RAMP.map((c) => (
                  <span key={c} className="h-2.5 w-5 first:rounded-l last:rounded-r" style={{ background: c }} />
                ))}
              </div>
              <span>above an even spread ({data.expected_per_cell.toFixed(1)} / slot · max {data.max_cell})</span>
              {data.hot_cells.length > 0 && (
                <span className="ml-2 flex items-center gap-1">
                  <span className="h-2.5 w-2.5 rounded-[3px] ring-2 ring-crimson" /> hot beyond chance
                </span>
              )}
            </div>
            <div className="min-h-[34px] rounded-lg bg-black/[0.03] px-3 py-1.5 text-[12px] text-ink/85">
              {hv ? (
                <>
                  <b>
                    {data.weekdays[hv.d]} {hh(hv.h)}–{hh((hv.h + 1) % 24)}
                  </b>{" "}
                  · <span className="tabular">{hv.n}</span> FIRs (even spread: {data.expected_per_cell.toFixed(1)})
                  {hv.top && (
                    <>
                      {" "}
                      · most often {hv.top.crime_type} ({hv.top.count})
                    </>
                  )}
                </>
              ) : (
                <span className="text-faint">Hover a cell for its count and most common crime</span>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-2.5">
          {data.peak_window && (
            <div className="rounded-xl border border-black/[0.06] bg-white/70 p-3">
              <p className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-faint">
                <Flame className="h-3.5 w-3.5 text-crimson" /> Busiest 3-hour window
              </p>
              <p className="mt-1 text-[13.5px] font-bold text-ink">{data.peak_window.label}</p>
              <p className="text-[11.5px] text-muted">
                {data.peak_window.count} FIRs · {(data.peak_window.share * 100).toFixed(1)}% of the week (even: 1.8%)
              </p>
            </div>
          )}
          <div className="rounded-xl border border-black/[0.06] bg-white/70 p-3">
            <p className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-faint">
              <Sunrise className="h-3.5 w-3.5 text-brand" /> By day
            </p>
            <div className="mt-1.5 space-y-1">
              {data.by_weekday.map((d) => (
                <div key={d.weekday} className="flex items-center gap-2 text-[11.5px]">
                  <span className="w-8 text-muted">{d.weekday.slice(0, 3)}</span>
                  <div className="h-1.5 flex-1 rounded-full bg-black/[0.05]">
                    <div className="h-1.5 rounded-full bg-[#e5812e]" style={{ width: `${(100 * d.count) / maxDay}%` }} />
                  </div>
                  <span className="tabular w-10 text-right text-ink/80">{d.count}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-black/[0.06] bg-white/70 p-3">
            <p className="flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-faint">
              <Clock3 className="h-3.5 w-3.5 text-azure" /> By hour
            </p>
            <div className="mt-2 flex h-12 items-end gap-[2px]">
              {data.by_hour.map((h) => (
                <div
                  key={h.hour}
                  className="flex-1 rounded-t-[2px] bg-azure/70"
                  style={{ height: `${Math.max(4, (100 * h.count) / maxHour)}%` }}
                  title={`${hh(h.hour)} · ${h.count} FIRs`}
                />
              ))}
            </div>
            <div className="mt-1 flex justify-between text-[9.5px] tabular text-faint">
              <span>00</span>
              <span>06</span>
              <span>12</span>
              <span>18</span>
              <span>23</span>
            </div>
            {data.busiest_hour && <p className="mt-1 text-[11.5px] text-muted">Busiest: {data.busiest_hour}</p>}
          </div>
        </div>
      </div>

      <p className="text-[11.5px] leading-relaxed text-muted">{data.note}</p>
    </div>
  );
}
