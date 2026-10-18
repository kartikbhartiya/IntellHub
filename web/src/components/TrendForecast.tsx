"use client";

/* Annual crime-trend forecast — one number per district per year, and how far to
 * trust it.
 *
 * Reads three aggregate endpoints (see appsail/trend_forecast.py):
 *   /forecast/annual/meta     units, years, crime types and the model card
 *   /forecast/annual          one forecast, with history, interval and explanation
 *   /forecast/annual/outlook  every district for the same year, ranked by change
 *
 * Choosing a year that is already in the corpus runs a backtest: the model is
 * refitted on the years before it and the recorded count is drawn next to what it
 * would have said. That is the honest way to show a forecaster — the reader can
 * pick any past year and see it miss.
 *
 * Nothing here is computed in the browser except layout; every number is the
 * server's, and the corpus note is shown under the chart rather than hidden, because
 * a forecast drawn on simulated data must say so where it is read.
 */

import { useEffect, useMemo, useState } from "react";
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  LabelList,
  ReferenceLine,
} from "recharts";
import {
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  Info,
  Loader2,
  Minus,
  XCircle,
} from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";

/* ------------------------------------------------------------------ types */
type Scores = { mae: number; wape_pct: number | null; r2_levels: number | null; r2_yoy_change: number | null };
type Baseline = Scores & { strategy: string; label: string };
type Horizon = {
  folds: number;
  points: number;
  target_years: number[];
  model: Scores;
  baselines: Baseline[];
  best_baseline: string;
  skill_vs_last_year: number | null;
  skill_vs_best_baseline: number | null;
  significance_vs_best_baseline: { mae_gap: number; ci95: [number, number]; verdict: string } | null;
  interval: { level: number; realised_coverage: number | null; coverage_points: number };
};
type Meta = {
  units: string[];
  crime_types: string[];
  years: {
    min: number;
    max: number;
    data_years: number[];
    backtest_years: number[];
    forecast_years: number[];
    partial_years: number[];
    default: number;
  };
  model: {
    name: string;
    rows: number;
    districts: number;
    backtest: Record<string, Horizon>;
    statewide_band_check?: { inside: number; of: number; assumption: string };
    finding: string;
    r2_caveat: string;
  };
  note: string;
};
type HistoryRow = { year: number; count: number; in_training: boolean };
type Forecast = {
  unit: string;
  year: number;
  crime_type: string | null;
  kind: "forecast" | "backtest";
  horizon: number;
  predicted: number;
  interval: { low: number; high: number; level: number } | null;
  last_year: number;
  last_count: number;
  change_vs_last_pct: number | null;
  change_exceeds_band: boolean;
  history: HistoryRow[];
  actual: number | null;
  error: number | null;
  inside_interval: boolean | null;
  per_lakh: number | null;
  trained_on: { years: number[]; rows: number; districts: number };
  explanation: string;
  by_crime_type?: { crime_type: string; predicted: number; last_count: number; change_vs_last_pct: number | null }[];
  note: string;
};
type OutlookRow = {
  unit: string;
  predicted: number;
  last_count: number;
  change_vs_last_pct: number | null;
  interval: { low: number; high: number } | null;
  change_exceeds_band: boolean;
  per_lakh: number;
  actual?: number;
};
type Outlook = { year: number; kind: string; last_year: number; districts: OutlookRow[] };

const STATE = "Karnataka";

const tooltipStyle = {
  backgroundColor: "rgba(255,255,255,0.98)",
  border: "1px solid rgba(0,0,0,0.08)",
  borderRadius: "12px",
  boxShadow: "0 12px 34px -12px rgba(17,20,32,0.22)",
  fontSize: "12px",
  color: "#1d1d1f",
};

const selectCls =
  "w-full rounded-xl border border-black/10 bg-black/[0.02] px-3.5 py-2.5 text-[13px] text-ink focus:border-brand/40 focus:outline-none";
const labelCls = "mb-1 block text-[11px] font-bold uppercase tracking-wider text-faint";

const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString("en-IN"));
const pct = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;

function errorText(e: unknown) {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : "Could not load the forecast.";
}

/* ------------------------------------------------------------------ chart */
function FanChart({ f }: { f: Forecast }) {
  const data = useMemo(() => {
    const rows: any[] = f.history
      .filter((h) => h.year <= f.year)
      .map((h) => ({
        year: String(h.year),
        actual: h.in_training ? h.count : undefined,
        heldout: !h.in_training && h.year === f.year ? h.count : undefined,
      }));
    const bridge = rows.find((r) => r.year === String(f.last_year));
    if (bridge) {
      bridge.forecast = f.last_count;
      bridge.range = [f.last_count, f.last_count];
    }
    let target = rows.find((r) => r.year === String(f.year));
    if (!target) {
      for (let y = f.last_year + 1; y <= f.year; y++) {
        if (!rows.find((r) => r.year === String(y))) rows.push({ year: String(y) });
      }
      target = rows.find((r) => r.year === String(f.year));
    }
    target.forecast = f.predicted;
    if (f.interval) target.range = [f.interval.low, f.interval.high];
    return rows;
  }, [f]);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 22, right: 18, left: -8, bottom: 4 }}>
        <defs>
          <linearGradient id="tfBand" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#7c5cff" stopOpacity={0.22} />
            <stop offset="100%" stopColor="#7c5cff" stopOpacity={0.08} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="rgba(0,0,0,0.06)" vertical={false} />
        <XAxis dataKey="year" tick={{ fontSize: 11, fill: "#97979f" }} axisLine={false} tickLine={false} />
        <YAxis
          tick={{ fontSize: 11, fill: "#97979f" }}
          axisLine={false}
          tickLine={false}
          width={52}
          domain={[(min: number) => Math.max(0, Math.floor(min * 0.85)), (max: number) => Math.ceil(max * 1.08)]}
          tickFormatter={(v) => Number(v).toLocaleString("en-IN")}
        />
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v: any, name: any) => {
            if (name === "range" && Array.isArray(v)) return [`${n(v[0])} – ${n(v[1])}`, "80% range"];
            const label =
              name === "actual" ? "Recorded" : name === "heldout" ? "Recorded (held out)" : "Forecast";
            return [n(Number(v)), label];
          }}
        />
        <Area
          type="linear"
          dataKey="range"
          stroke="none"
          fill="url(#tfBand)"
          connectNulls
          isAnimationActive={false}
          activeDot={false}
        />
        <Line
          type="linear"
          dataKey="actual"
          stroke="#2f74c0"
          strokeWidth={2.2}
          dot={{ r: 4, fill: "#2f74c0", strokeWidth: 0 }}
          activeDot={{ r: 5 }}
          isAnimationActive={false}
        >
          <LabelList dataKey="actual" position="top" offset={10} fontSize={11} fill="#6b6b73" formatter={(v: any) => n(v)} />
        </Line>
        <Line
          type="linear"
          dataKey="forecast"
          stroke="#7c5cff"
          strokeWidth={2.2}
          strokeDasharray="5 4"
          connectNulls
          dot={(p: any) =>
            p.payload.year === String(f.year) ? (
              <circle key={`fc-${p.index}`} cx={p.cx} cy={p.cy} r={6} fill="#7c5cff" stroke="#fff" strokeWidth={2} />
            ) : (
              <g key={`fc-${p.index}`} />
            )
          }
          isAnimationActive={false}
        />
        <Line
          type="linear"
          dataKey="heldout"
          stroke="none"
          dot={{ r: 6, fill: "#fff", stroke: "#0a9d57", strokeWidth: 2.5 }}
          isAnimationActive={false}
          legendType="none"
        >
          <LabelList dataKey="heldout" position="right" offset={10} fontSize={11} fill="#0a9d57" formatter={(v: any) => n(v)} />
        </Line>
        <ReferenceLine x={String(f.last_year)} stroke="rgba(0,0,0,0.12)" strokeDasharray="2 3" />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ pieces */
function ChangeChip({ v, flagged }: { v: number | null; flagged?: boolean }) {
  if (v == null) return <span className="text-[12px] text-faint">—</span>;
  const up = v > 0.5;
  const down = v < -0.5;
  const Icon = up ? ArrowUpRight : down ? ArrowDownRight : Minus;
  const tone = up ? "text-crimson bg-crimson/[0.07]" : down ? "text-emerald bg-emerald/[0.08]" : "text-muted bg-black/[0.04]";
  return (
    <span
      className={`inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[12px] font-semibold tabular ${tone}`}
      title={flagged ? "Larger than the model's own 80% error band" : "Within the model's error band — not a signal on its own"}
    >
      <Icon className="h-3.5 w-3.5" />
      {pct(v)}
      {flagged && <span className="ml-0.5 h-1.5 w-1.5 rounded-full bg-current" />}
    </span>
  );
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-black/[0.06] bg-white/70 px-3 py-2.5">
      <p className="text-[10.5px] font-semibold uppercase tracking-wider text-faint">{label}</p>
      <p className="text-display tabular mt-0.5 text-[17px] font-bold text-ink">{value}</p>
      {sub && <p className="mt-0.5 text-[11px] leading-snug text-muted">{sub}</p>}
    </div>
  );
}

function ModelCard({ meta }: { meta: Meta }) {
  const [open, setOpen] = useState(false);
  const h1 = meta.model.backtest["1"];
  if (!h1) return null;
  const sig = h1.significance_vs_best_baseline;
  const best = h1.baselines.find((b) => b.strategy === h1.best_baseline);
  const verdict =
    sig?.verdict === "better"
      ? "better, beyond noise"
      : sig?.verdict === "worse"
        ? "worse, beyond noise"
        : "within noise of it";
  const sw = meta.model.statewide_band_check;
  return (
    <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between gap-3 text-left"
        aria-expanded={open}
      >
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-faint">How good is it — out of sample</p>
          <p className="mt-0.5 text-[13px] text-ink">
            Next-year error <b className="tabular">±{h1.model.mae.toFixed(1)}</b> FIRs per district
            {h1.model.wape_pct != null && <> ({h1.model.wape_pct.toFixed(1)}%)</>} ·{" "}
            <b className="tabular">{h1.skill_vs_last_year != null ? `${Math.round(h1.skill_vs_last_year * 100)}%` : "—"}</b>{" "}
            more accurate than &ldquo;same as last year&rdquo;
          </p>
        </div>
        <ChevronDown className={`h-4 w-4 shrink-0 text-faint transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <div className="grid gap-2 sm:grid-cols-4">
            <Stat label="R² · levels" value={h1.model.r2_levels ?? "—"} sub="mostly the spread between districts" />
            <Stat label="R² · yearly change" value={h1.model.r2_yoy_change ?? "—"} sub="the harder test" />
            <Stat
              label="80% band covers"
              value={h1.interval.realised_coverage != null ? `${Math.round(h1.interval.realised_coverage * 100)}%` : "—"}
              sub={`measured on ${h1.interval.coverage_points} held-out district-years`}
            />
            <Stat
              label={`vs ${best?.label.toLowerCase() ?? "best baseline"}`}
              value={h1.skill_vs_best_baseline != null ? pct(h1.skill_vs_best_baseline * 100, 0) : "—"}
              sub={verdict}
            />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-[12px]">
              <thead>
                <tr className="text-left text-[10.5px] uppercase tracking-wider text-faint">
                  <th className="py-1.5 font-semibold">Method · backtest {h1.target_years[0]}–{h1.target_years[h1.target_years.length - 1]}</th>
                  <th className="py-1.5 text-right font-semibold">MAE</th>
                  <th className="py-1.5 text-right font-semibold">WAPE</th>
                  <th className="py-1.5 text-right font-semibold">R² levels</th>
                  <th className="py-1.5 text-right font-semibold">R² change</th>
                </tr>
              </thead>
              <tbody className="tabular">
                <tr className="border-t border-black/[0.06] font-semibold text-ink">
                  <td className="py-1.5">This model</td>
                  <td className="py-1.5 text-right">{h1.model.mae.toFixed(2)}</td>
                  <td className="py-1.5 text-right">{h1.model.wape_pct?.toFixed(1)}%</td>
                  <td className="py-1.5 text-right">{h1.model.r2_levels}</td>
                  <td className="py-1.5 text-right">{h1.model.r2_yoy_change}</td>
                </tr>
                {h1.baselines.map((b) => (
                  <tr key={b.strategy} className="border-t border-black/[0.06] text-muted">
                    <td className="py-1.5">{b.label}</td>
                    <td className="py-1.5 text-right">{b.mae.toFixed(2)}</td>
                    <td className="py-1.5 text-right">{b.wape_pct?.toFixed(1)}%</td>
                    <td className="py-1.5 text-right">{b.r2_levels ?? "—"}</td>
                    <td className="py-1.5 text-right">{b.r2_yoy_change ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11.5px] leading-relaxed text-muted">
            {meta.model.finding} {meta.model.r2_caveat}
            {sw && (
              <>
                {" "}
                Statewide, {sw.inside} of {sw.of} backtest years fell inside the 80% band (the band assumes{" "}
                {sw.assumption}).
              </>
            )}{" "}
            {h1.points} district-years scored, each predicted only from the years before it.
          </p>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ main */
export default function TrendForecast() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [unit, setUnit] = useState(STATE);
  const [crime, setCrime] = useState("");
  const [year, setYear] = useState<number | null>(null);
  const [f, setF] = useState<Forecast | null>(null);
  const [out, setOut] = useState<Outlook | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const q = crime ? `?crime_type=${encodeURIComponent(crime)}` : "";
    getJSON<Meta>(`/forecast/annual/meta${q}`)
      .then((m) => {
        setMeta(m);
        setMetaErr(null);
        setYear((y) => y ?? m.years.default);
      })
      .catch((e) => setMetaErr(errorText(e)));
  }, [crime]);

  useEffect(() => {
    if (year == null) return;
    let live = true;
    setLoading(true);
    const ct = crime ? `&crime_type=${encodeURIComponent(crime)}` : "";
    Promise.all([
      getJSON<Forecast>(`/forecast/annual?unit=${encodeURIComponent(unit)}&year=${year}${ct}`),
      getJSON<Outlook>(`/forecast/annual/outlook?year=${year}${ct}`),
    ])
      .then(([fc, ol]) => {
        if (!live) return;
        setF(fc);
        setOut(ol);
        setErr(null);
      })
      .catch((e) => live && setErr(errorText(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [unit, crime, year]);

  const risers = useMemo(() => (out?.districts || []).filter((r) => (r.change_vs_last_pct ?? 0) > 0).slice(0, 5), [out]);
  const fallers = useMemo(
    () =>
      [...(out?.districts || [])]
        .filter((r) => (r.change_vs_last_pct ?? 0) < 0)
        .sort((a, b) => (a.change_vs_last_pct ?? 0) - (b.change_vs_last_pct ?? 0))
        .slice(0, 5),
    [out],
  );

  if (metaErr)
    return (
      <div className="flex items-center gap-2 rounded-xl border border-crimson/20 bg-crimson/[0.04] p-4 text-[13px] text-crimson">
        <AlertCircle className="h-4 w-4" /> {metaErr}
      </div>
    );
  if (!meta) return <div className="h-[420px] animate-pulse rounded-2xl bg-black/[0.03]" />;

  const backtest = f?.kind === "backtest";
  const what = crime ? crime : "IPC/BNS FIRs";

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_minmax(0,0.9fr)]">
        <div>
          <label htmlFor="tf-unit" className={labelCls}>Unit</label>
          <select id="tf-unit" value={unit} onChange={(e) => setUnit(e.target.value)} className={selectCls}>
            {meta.units.map((u) => (
              <option key={u} value={u}>
                {u === STATE ? "Karnataka (statewide)" : u}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="tf-crime" className={labelCls}>Crime type</label>
          <select id="tf-crime" value={crime} onChange={(e) => setCrime(e.target.value)} className={selectCls}>
            <option value="">All crime types</option>
            {meta.crime_types.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="tf-year" className={labelCls}>Year</label>
          <select
            id="tf-year"
            value={year ?? ""}
            onChange={(e) => setYear(Number(e.target.value))}
            className={selectCls}
          >
            {meta.years.forecast_years.map((y) => (
              <option key={y} value={y}>
                {y} · forecast
              </option>
            ))}
            {[...meta.years.backtest_years].reverse().map((y) => (
              <option key={y} value={y}>
                {y} · backtest
              </option>
            ))}
          </select>
        </div>
      </div>

      {err && (
        <div className="flex items-center gap-2 rounded-xl border border-crimson/20 bg-crimson/[0.04] p-3 text-[12.5px] text-crimson">
          <AlertCircle className="h-4 w-4" /> {err}
        </div>
      )}

      {f && (
        <div className={`relative rounded-2xl border border-black/[0.08] bg-black/[0.02] p-5 transition-opacity ${loading ? "opacity-60" : ""}`}>
          {loading && <Loader2 className="absolute right-4 top-4 h-4 w-4 animate-spin text-faint" />}
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="text-[12.5px] text-muted">
                {f.unit === STATE ? "Karnataka (statewide)" : f.unit} — {f.year}
                {backtest && (
                  <span className="ml-2 rounded-full bg-emerald/10 px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wider text-emerald">
                    backtest · trained on {f.trained_on.years[0]}–{f.trained_on.years[f.trained_on.years.length - 1]}
                  </span>
                )}
              </p>
              <div className="mt-1 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                <span className="text-display tabular text-[34px] font-bold leading-none text-[#4b33c9]">{n(f.predicted)}</span>
                <span className="text-[14px] text-ink/80">predicted {what}</span>
                <ChangeChip v={f.change_vs_last_pct} flagged={f.change_exceeds_band} />
                <span className="text-[12px] text-faint">vs {f.last_year} ({n(f.last_count)})</span>
              </div>
              {f.interval && (
                <p className="mt-1.5 text-[12.5px] text-muted">
                  80% range <b className="tabular text-ink">{n(f.interval.low)} – {n(f.interval.high)}</b>
                  {f.per_lakh != null && (
                    <>
                      {" "}· <span className="tabular">{f.per_lakh.toFixed(2)}</span> per lakh population (2011 Census)
                    </>
                  )}
                </p>
              )}
            </div>
            {backtest && f.actual != null && (
              <div
                className={`flex items-center gap-2 rounded-xl border px-3.5 py-2.5 ${
                  f.inside_interval ? "border-emerald/25 bg-emerald/[0.06]" : "border-crimson/20 bg-crimson/[0.05]"
                }`}
              >
                {f.inside_interval ? (
                  <CheckCircle2 className="h-5 w-5 text-emerald" />
                ) : (
                  <XCircle className="h-5 w-5 text-crimson" />
                )}
                <div>
                  <p className="text-[10.5px] font-semibold uppercase tracking-wider text-faint">Recorded in {f.year}</p>
                  <p className="tabular text-[15px] font-bold text-ink">
                    {n(f.actual)}{" "}
                    <span className={`text-[12px] font-semibold ${f.inside_interval ? "text-emerald" : "text-crimson"}`}>
                      ({f.error != null && f.error > 0 ? "+" : ""}
                      {n(f.error)}) · {f.inside_interval ? "inside" : "outside"} the band
                    </span>
                  </p>
                </div>
              </div>
            )}
          </div>

          <p className="mt-4 text-[11px] font-semibold uppercase tracking-wider text-faint">
            Recorded history, same unit{backtest ? " · held-out year in green" : ""}
          </p>
          <div className="mt-1 h-[230px] w-full">
            <FanChart f={f} />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-4 text-[11px] text-faint">
            <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded bg-azure" /> Recorded (training)</span>
            <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed border-[#7c5cff]" /> Forecast</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded bg-[#7c5cff]/20" /> 80% range</span>
            {backtest && (
              <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full border-2 border-emerald bg-white" /> Recorded, never seen by the model</span>
            )}
          </div>
          <p className="mt-3 flex gap-2 text-[12.5px] leading-relaxed text-ink/80">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-azure" />
            {f.explanation}
          </p>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Breakdown by crime type */}
        {f?.by_crime_type && f.by_crime_type.length > 0 && (
          <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
            <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-faint">
              By crime type · {f.unit === STATE ? "statewide" : f.unit} {f.year}
            </p>
            <div className="space-y-1.5">
              {(() => {
                const max = Math.max(...f.by_crime_type!.map((r) => r.predicted), 1);
                return f.by_crime_type!.map((r) => (
                  <button
                    key={r.crime_type}
                    onClick={() => setCrime(r.crime_type)}
                    className="group grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-1.5 py-1 text-left hover:bg-black/[0.03]"
                    title={`Forecast ${r.crime_type} on its own`}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center justify-between gap-2 text-[12.5px]">
                        <span className="truncate text-ink/85 group-hover:text-ink">{r.crime_type}</span>
                        <span className="tabular font-semibold text-ink">{n(r.predicted)}</span>
                      </div>
                      <div className="mt-1 h-1.5 w-full rounded-full bg-black/[0.05]">
                        <div className="h-1.5 rounded-full bg-[#7c5cff]/70" style={{ width: `${(100 * r.predicted) / max}%` }} />
                      </div>
                    </div>
                    <ChangeChip v={r.change_vs_last_pct} />
                  </button>
                ));
              })()}
            </div>
            <p className="mt-2 text-[11px] text-faint">Each type is forecast on its own, so they need not sum exactly to the total.</p>
          </div>
        )}

        {/* District outlook */}
        {out && (
          <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
            <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-faint">
              District outlook · {out.year} vs {out.last_year}
              {crime ? ` · ${crime}` : ""}
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              {[
                { title: "Expected to rise", rows: risers, tone: "text-crimson" },
                { title: "Expected to fall", rows: fallers, tone: "text-emerald" },
              ].map((col) => (
                <div key={col.title}>
                  <p className={`mb-1.5 text-[11.5px] font-semibold ${col.tone}`}>{col.title}</p>
                  <div className="space-y-1">
                    {col.rows.map((r) => (
                      <button
                        key={r.unit}
                        onClick={() => setUnit(r.unit)}
                        className={`flex w-full items-center justify-between gap-2 rounded-lg px-1.5 py-1 text-left text-[12.5px] hover:bg-black/[0.03] ${
                          unit === r.unit ? "bg-black/[0.04]" : ""
                        }`}
                      >
                        <span className="truncate text-ink/85">{r.unit}</span>
                        <span className="flex items-center gap-2">
                          <span className="tabular text-faint">{n(r.predicted)}</span>
                          <ChangeChip v={r.change_vs_last_pct} flagged={r.change_exceeds_band} />
                        </span>
                      </button>
                    ))}
                    {!col.rows.length && <p className="text-[12px] text-faint">None.</p>}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-faint">
              <span className="h-1.5 w-1.5 rounded-full bg-faint" /> marks a change larger than the model&rsquo;s 80% error band — the rest is within normal year-to-year noise.
            </p>
          </div>
        )}
      </div>

      <ModelCard meta={meta} />

      <p className="text-[11.5px] leading-relaxed text-faint">
        {meta.model.name} · {meta.model.rows} (district, year) rows across {meta.model.districts} districts · {meta.note}
      </p>
    </div>
  );
}
