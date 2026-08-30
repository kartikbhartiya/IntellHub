"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Timer,
  AlertTriangle,
  Gauge,
  Layers,
  ArrowUpRight,
  ShieldAlert,
  FileText,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { getJSON } from "@/lib/api";
import ExportButton from "@/components/ExportButton";

type Horizon = "60d" | "90d" | "180d" | "360d";

interface DocketCase {
  case_id: string;
  district: string;
  police_station: string;
  crime_type: string;
  registered: string | null;
  days_open: number;
  severity_score: number;
  expected_days_open: number;
  expected_additional_days: number;
  breach_risk: Record<Horizon, number>;
  horizon_elapsed: Record<Horizon, boolean>;
  risk: number;
  next_horizon_days: number | null;
  already_breached_90d: boolean;
}

// Motion props are hoisted to module scope and reused by reference. Rebuilding
// them inside the component would hand framer-motion a fresh object on every
// render, so the `setData` re-render would restart every entrance animation from
// opacity 0 — leaving the page blank if data arrives mid-transition.
const FADE_FROM = { opacity: 0, y: 16 };
const FADE_TO = { opacity: 1, y: 0 };
const EASE = [0.16, 1, 0.3, 1] as const;

const TRANSITIONS = Array.from({ length: 10 }, (_, i) => ({
  duration: 0.6,
  delay: i * 0.04,
  ease: EASE,
}));

const fade = (step = 0) => ({
  initial: FADE_FROM,
  animate: FADE_TO,
  transition: TRANSITIONS[Math.min(step, TRANSITIONS.length - 1)],
});

function riskTone(v: number) {
  if (v >= 0.85) return { text: "text-crimson", bar: "bg-crimson", chip: "bg-crimson/12 border-crimson/25" };
  if (v >= 0.6) return { text: "text-brand", bar: "bg-brand", chip: "bg-brand/12 border-brand/25" };
  if (v >= 0.35) return { text: "text-azure", bar: "bg-azure", chip: "bg-azure/12 border-azure/25" };
  return { text: "text-emerald", bar: "bg-emerald", chip: "bg-emerald/12 border-emerald/25" };
}

export default function PendencyPage() {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [district, setDistrict] = useState<string>("All");

  useEffect(() => {
    getJSON("/pendency").then(setData).catch((e) => setError(e.message));
  }, []);

  const summary = data?.summary ?? {};
  const backlog = data?.backlog ?? {};
  const metrics = data?.metrics ?? {};

  const districts = useMemo<string[]>(
    () => ["All", ...(data?.by_district ?? []).map((d: any) => d.name)],
    [data]
  );

  const cases = useMemo<DocketCase[]>(() => {
    const all: DocketCase[] = data?.cases ?? [];
    return district === "All" ? all : all.filter((c) => c.district === district);
  }, [data, district]);

  const curve = useMemo(() => {
    const km = data?.km_curve ?? {};
    const times: number[] = km.times ?? [];
    const surv: number[] = km.survival ?? [];
    return times.map((t, i) => ({
      day: Math.round(t),
      open: Math.round(surv[i] * 1000) / 10,
    }));
  }, [data]);

  return (
    <div className="space-y-7">
      <motion.header {...fade()}>
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <Timer className="h-8 w-8 text-brand" strokeWidth={2} /> Case Pendency
        </h1>
        <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-muted">
          Survival analysis over the open docket — which FIRs are going to sit, for
          how much longer, and where pendency is concentrating. Unlike a
          within-180-days classifier, this models{" "}
          <span className="font-semibold text-ink">right-censoring</span>, so a case
          registered days ago can be scored instead of discarded.
        </p>
      </motion.header>

      {error && (
        <div className="bezel">
          <div className="bezel-core p-6 text-crimson">Failed to load pendency: {error}</div>
        </div>
      )}

      {/* ---------------------------------------------------------- KPI row */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          {
            label: "Open cases",
            value: summary.open_cases ?? "—",
            sub: "no chargesheet at extract",
            tone: "text-ink",
            icon: FileText,
          },
          {
            label: "Forecastable",
            value: summary.forecastable ?? "—",
            sub: `inside the ${summary.grid_max_days ?? 360}-day grid`,
            tone: "text-azure",
            icon: Gauge,
          },
          {
            label: "Past 90-day mark",
            value: summary.already_breached_90d ?? "—",
            sub:
              summary.breach_rate_90d != null
                ? `${Math.round(summary.breach_rate_90d * 100)}% of forecastable`
                : "",
            tone: "text-crimson",
            icon: AlertTriangle,
          },
          {
            label: "Median age",
            value: summary.median_days_open != null ? `${summary.median_days_open}d` : "—",
            sub: "of the forecastable docket",
            tone: "text-brand",
            icon: Layers,
          },
        ].map((k, i) => (
          <motion.div key={k.label} {...fade(i)} className="bezel">
            <div className="bezel-core p-5">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
                <k.icon className="h-3.5 w-3.5" /> {k.label}
              </div>
              <p className={`text-display tabular mt-3 text-3xl font-bold ${k.tone}`}>
                {k.value}
              </p>
              <p className="mt-1 text-[12px] text-muted">{k.sub}</p>
            </div>
          </motion.div>
        ))}
      </div>

      {/* ------------------------------------------- curve + refusal card */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <motion.div {...fade(4)} className="bezel lg:col-span-2">
          <div className="bezel-core p-6">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
              <Gauge className="h-4 w-4" /> Kaplan–Meier · share of FIRs still awaiting chargesheet
            </div>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">
              The covariate-free baseline curve, estimated with censoring handled
              correctly. Cases still open simply leave the risk set — they are never
              counted as failures, which a naive “fraction charged so far” statistic
              gets wrong.
            </p>
            <div className="mt-5 h-[260px]">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={curve} margin={{ top: 6, right: 12, bottom: 4, left: -14 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,0,0,0.07)" vertical={false} />
                  <XAxis
                    dataKey="day"
                    tick={{ fontSize: 11, fill: "#97979f" }}
                    tickLine={false}
                    axisLine={{ stroke: "rgba(0,0,0,0.1)" }}
                    label={{ value: "days since registration", position: "insideBottom", offset: -2, fontSize: 10, fill: "#97979f" }}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "#97979f" }}
                    tickLine={false}
                    axisLine={false}
                    domain={[0, 100]}
                    tickFormatter={(v) => `${v}%`}
                  />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 12,
                      border: "1px solid rgba(0,0,0,0.08)",
                      boxShadow: "0 12px 30px -12px rgba(16,18,30,0.25)",
                      fontSize: 12,
                    }}
                    formatter={(v: any) => [`${v}% still open`, ""]}
                    labelFormatter={(l) => `Day ${l}`}
                  />
                  <ReferenceLine x={90} stroke="#dc2b25" strokeDasharray="4 4" strokeOpacity={0.6}
                    label={{ value: "90d", fontSize: 10, fill: "#dc2b25", position: "top" }} />
                  <ReferenceLine x={180} stroke="#b07d16" strokeDasharray="4 4" strokeOpacity={0.6}
                    label={{ value: "180d", fontSize: 10, fill: "#b07d16", position: "top" }} />
                  {/* Animation off: a survival curve is a reference figure, not a
                      reveal. Drawing it immediately also keeps the rendered chart
                      deterministic for screenshots and print exports. */}
                  <Line
                    type="monotone"
                    dataKey="open"
                    stroke="#b07d16"
                    strokeWidth={2.5}
                    dot={{ r: 2.5, fill: "#b07d16" }}
                    activeDot={{ r: 4 }}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </motion.div>

        {/* The refusal — the honesty exhibit */}
        <motion.div {...fade(5)} className="bezel">
          <div className="bezel-core relative h-full overflow-hidden p-6">
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-crimson/70 to-transparent" />
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-crimson">
              <ShieldAlert className="h-4 w-4" /> Not scored
            </div>
            <p className="text-display tabular mt-4 text-4xl font-bold text-ink">
              {backlog.count ?? "—"}
            </p>
            <p className="mt-1 text-[13px] text-muted">
              structural backlog ·{" "}
              {backlog.share_of_open != null
                ? `${Math.round(backlog.share_of_open * 100)}% of all open cases`
                : ""}
            </p>
            <div className="mt-4 space-y-2 border-t border-black/[0.07] pt-4 text-[12px] leading-relaxed text-muted">
              <p>
                These cases have been open longer than the model&apos;s{" "}
                <span className="font-semibold text-ink">{summary.grid_max_days ?? 360}-day</span>{" "}
                observation grid. No comparable training case was still unresolved
                at this age, so any probability shown here would be extrapolation
                rather than a measurement.
              </p>
              <p className="font-medium text-ink">
                The platform refuses to score them. They need administrative
                disposal review, not a forecast.
              </p>
            </div>
            {backlog.median_days_open != null && (
              <div className="mt-4 flex gap-6 border-t border-black/[0.07] pt-4">
                <div>
                  <p className="text-display tabular text-xl font-bold text-crimson">
                    {backlog.median_days_open}d
                  </p>
                  <p className="text-[10px] uppercase tracking-wider text-faint">median age</p>
                </div>
                <div>
                  <p className="text-display tabular text-xl font-bold text-crimson">
                    {backlog.oldest_days_open}d
                  </p>
                  <p className="text-[10px] uppercase tracking-wider text-faint">oldest</p>
                </div>
              </div>
            )}
          </div>
        </motion.div>
      </div>

      {/* ----------------------------------------------- district rollup */}
      <motion.div {...fade(6)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
            <Layers className="h-4 w-4" /> Pendency pressure by district
          </div>
          <div className="mt-5 space-y-2.5">
            {(data?.by_district ?? []).map((d: any) => {
              const tone = riskTone(d.mean_risk);
              return (
                // 160px name + 56 + 64 + 56 of fixed columns and four 16px gaps is a
                // 400px minimum, on a 375px screen. It forced the whole document
                // 76px wide, and every fixed layer stretched to match. Wrapping the
                // name onto its own line at narrow widths keeps all four values
                // rather than hiding one.
                <div key={d.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 md:flex-nowrap md:gap-4">
                  <span className="w-full shrink-0 truncate text-[13px] font-medium text-ink md:w-40">
                    {d.name}
                  </span>
                  {/* Width is set directly and eased with CSS rather than driven by
                      a motion value: the animated object would be rebuilt on every
                      render, restarting the fill from zero each time the data
                      refreshes and leaving the bars empty. */}
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-black/[0.06]">
                    <div
                      style={{ width: `${Math.round(d.mean_risk * 100)}%` }}
                      className={`h-full rounded-full transition-[width] duration-700 ease-out ${tone.bar}`}
                    />
                  </div>
                  <span className="tabular w-14 shrink-0 text-right text-[12px] text-muted">
                    {d.open_cases} open
                  </span>
                  <span className="tabular w-16 shrink-0 text-right text-[12px] text-muted">
                    {d.mean_days_open}d avg
                  </span>
                  <span
                    className={`tabular w-14 shrink-0 text-right text-[13px] font-semibold ${tone.text}`}
                  >
                    {Math.round(d.mean_risk * 100)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </motion.div>

      {/* -------------------------------------------------- at-risk docket */}
      <motion.div {...fade(7)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
                <Timer className="h-4 w-4" /> Triage queue
              </div>
              <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted">
                Ranked by <span className="font-semibold text-ink">expected remaining days</span>,
                not by breach probability. A case at day 359 scores ~100% against its
                360-day horizon simply because one day is left — true, but useless for
                triage. Remaining time surfaces the newly-registered cases that are
                predicted to drag, while intervening still changes the outcome.
              </p>
            </div>
            <div className="flex items-center gap-2">
            <ExportButton
              rows={cases as unknown as Record<string, unknown>[]}
              columns={[
                { key: "case_id", label: "FIR / Crime No" },
                { key: "district", label: "District" },
                { key: "police_station", label: "Station" },
                { key: "crime_type", label: "Crime type" },
                { key: "registered", label: "Registered" },
                { key: "days_open", label: "Days open" },
                { key: "expected_additional_days", label: "Expected days remaining" },
                { key: "severity_score", label: "Severity" },
              ]}
              meta={{
                title: "Pendency triage queue",
                scope: district ? `district=${district}` : "all districts",
                simulated: true,
              }}
            />
            <select
              value={district}
              onChange={(e) => setDistrict(e.target.value)}
              className="rounded-full border border-black/10 bg-black/[0.03] px-4 py-2 text-[13px] font-medium text-ink outline-none transition-colors hover:border-brand/40"
            >
              {districts.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            </div>
          </div>

          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-black/[0.08] text-[10px] uppercase tracking-[0.15em] text-faint">
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">FIR</th>
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">District / station</th>
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">Crime</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Open</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Est. remaining</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Still open @180d</th>
                </tr>
              </thead>
              <tbody>
                {cases.slice(0, 40).map((c) => {
                  const r180 = c.breach_risk["180d"] ?? 0;
                  const elapsed180 = c.horizon_elapsed?.["180d"];
                  const tone = riskTone(r180);
                  return (
                    <tr
                      key={c.case_id}
                      className="border-b border-black/[0.05] transition-colors hover:bg-black/[0.015]"
                    >
                      <td className="px-2 py-3">
                        <Link prefetch={false}
                          href={`/case?q=${encodeURIComponent(c.case_id)}`}
                          className="group inline-flex items-center gap-1 font-medium text-ink hover:text-brand"
                        >
                          …{c.case_id.slice(-8)}
                          <ArrowUpRight className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100" />
                        </Link>
                        <p className="text-[11px] text-faint">{c.registered}</p>
                      </td>
                      <td className="px-2 py-3">
                        <p className="text-ink">{c.district}</p>
                        <p className="text-[11px] text-faint">{c.police_station}</p>
                      </td>
                      <td className="px-2 py-3 text-muted">{c.crime_type}</td>
                      <td className="tabular px-2 py-3 text-right text-ink">{c.days_open}d</td>
                      <td className="tabular px-2 py-3 text-right font-semibold text-brand">
                        +{Math.round(c.expected_additional_days)}d
                      </td>
                      <td className="px-2 py-3 text-right">
                        {elapsed180 ? (
                          <span className="rounded-full border border-black/10 bg-black/[0.04] px-2.5 py-1 text-[11px] font-medium text-muted">
                            already past
                          </span>
                        ) : (
                          <span
                            className={`tabular rounded-full border px-2.5 py-1 text-[12px] font-semibold ${tone.chip} ${tone.text}`}
                          >
                            {Math.round(r180 * 100)}%
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {cases.length === 0 && (
              <p className="py-8 text-center text-[13px] text-muted">
                No forecastable open cases in this selection.
              </p>
            )}
          </div>
        </div>
      </motion.div>

      {/* --------------------------------------------------- provenance */}
      <motion.div {...fade(8)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-faint">
            <Gauge className="h-4 w-4" /> Model provenance
          </div>
          <div className="mt-4 flex flex-wrap gap-8">
            <div>
              <p className="text-display tabular text-2xl font-bold text-ink">
                {metrics.c_index ?? "—"}
              </p>
              <p className="text-[11px] uppercase tracking-wider text-faint">
                C-index (0.5 = coin flip)
              </p>
            </div>
            <div>
              <p className="text-display tabular text-2xl font-bold text-ink">
                {metrics.integrated_brier_score ?? "—"}
              </p>
              <p className="text-[11px] uppercase tracking-wider text-faint">
                Integrated Brier
              </p>
            </div>
            <div>
              <p className="text-display tabular text-2xl font-bold text-muted">
                {metrics.baselines?.[0]?.integrated_brier_score ?? "—"}
              </p>
              <p className="text-[11px] uppercase tracking-wider text-faint">
                Kaplan–Meier baseline IBS
              </p>
            </div>
            <div>
              <p className="text-display tabular text-2xl font-bold text-muted">
                {metrics.censoring_rate != null
                  ? `${Math.round(metrics.censoring_rate * 100)}%`
                  : "—"}
              </p>
              <p className="text-[11px] uppercase tracking-wider text-faint">
                test rows censored
              </p>
            </div>
          </div>
          <p className="mt-4 border-t border-black/[0.07] pt-4 text-[12px] leading-relaxed text-muted">
            {metrics.algorithm}. Split dates, per-horizon metrics and the warnings this
            run raised are published in the committed model registry.
            Pendency risk is a workload triage signal — it reflects how comparable
            past investigations progressed, and is never a judgement of an officer or
            of any accused person.
          </p>
        </div>
      </motion.div>
    </div>
  );
}
