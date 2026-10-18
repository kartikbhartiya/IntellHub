"use client";

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { GitBranch, Gauge, Info, Scale, TrendingUp } from "lucide-react";
import { getJSON } from "@/lib/api";
import CaseFlowSankey from "@/components/CaseFlowSankey";

// Module-scope motion props: rebuilding them per render restarts every entrance
// animation when data lands, which renders the page blank.
const FADE_FROM = { opacity: 0, y: 16 };
const FADE_TO = { opacity: 1, y: 0 };
const EASE = [0.16, 1, 0.3, 1] as const;
const TRANSITIONS = Array.from({ length: 8 }, (_, i) => ({
  duration: 0.6,
  delay: i * 0.05,
  ease: EASE,
}));
const fade = (step = 0) => ({
  initial: FADE_FROM,
  animate: FADE_TO,
  transition: TRANSITIONS[Math.min(step, TRANSITIONS.length - 1)],
});

const BAND_TONE: Record<string, { text: string; bar: string; chip: string }> = {
  Critical: { text: "text-crimson", bar: "bg-crimson", chip: "bg-crimson/12 border-crimson/25" },
  High: { text: "text-brand", bar: "bg-brand", chip: "bg-brand/12 border-brand/25" },
  Moderate: { text: "text-azure", bar: "bg-azure", chip: "bg-azure/12 border-azure/25" },
  Watch: { text: "text-emerald", bar: "bg-emerald", chip: "bg-emerald/12 border-emerald/25" },
};

const COMPONENT_COLOR: Record<string, string> = {
  crime_rate: "#b07d16",
  pendency: "#dc2b25",
  chargesheet_gap: "#2f74c0",
  severity: "#7c5cbf",
  trend: "#0a9d57",
};

const COMPONENT_LABEL: Record<string, string> = {
  crime_rate: "Crime rate",
  pendency: "Pendency",
  chargesheet_gap: "Chargesheet gap",
  severity: "Severity",
  trend: "Trend",
};

export default function LifecyclePage() {
  const [lc, setLc] = useState<any>(null);
  const [ori, setOri] = useState<any>(null);
  const [useCohort, setUseCohort] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    getJSON("/lifecycle").then(setLc).catch(() => {});
    getJSON("/operational-index").then(setOri).catch(() => {});
  }, []);

  const funnel = useMemo(
    () => (useCohort ? lc?.cohort : lc?.all_cases) ?? null,
    [lc, useCohort]
  );

  return (
    <div className="space-y-7">
      <motion.header {...fade()}>
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <GitBranch className="h-8 w-8 text-brand" strokeWidth={2} /> Case Lifecycle
        </h1>
        <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-muted">
          Where cases stop moving between FIR and verdict, and which districts carry the
          most operational pressure. Pendency is the metric the state actually holds SCRB
          to; a funnel is how you see <span className="font-semibold text-ink">where</span>{" "}
          the loss happens rather than only that it does.
        </p>
      </motion.header>

      {/* ------------------------------------------------------------- funnel */}
      <motion.div {...fade(1)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
                <Scale className="h-4 w-4" /> FIR → arrest → chargesheet → verdict
              </div>
              <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-muted">
                {useCohort ? (
                  <>
                    Measured on a <span className="font-semibold text-ink">closed cohort</span> —
                    cases registered at least {lc?.cohort_days ?? 730} days before the extract,
                    so files still legitimately in progress are not counted as failures.
                  </>
                ) : (
                  <>
                    Measured on <span className="font-semibold text-crimson">every registered case</span>,
                    including ones filed last month that have had no chance to progress. This
                    view always looks worse, and reading it as if it were the cohort is the
                    standard way a funnel chart misleads.
                  </>
                )}
              </p>
            </div>
            <div className="flex rounded-full border border-black/10 bg-black/[0.03] p-1 text-[12px] font-medium">
              <button
                onClick={() => setUseCohort(true)}
                className={`rounded-full px-3.5 py-1.5 transition-colors ${
                  useCohort ? "bg-brand text-white" : "text-muted hover:text-ink"
                }`}
              >
                Closed cohort
              </button>
              <button
                onClick={() => setUseCohort(false)}
                className={`rounded-full px-3.5 py-1.5 transition-colors ${
                  !useCohort ? "bg-crimson text-white" : "text-muted hover:text-ink"
                }`}
              >
                All cases
              </button>
            </div>
          </div>

          <div className="mt-6 space-y-3">
            {(funnel?.stages ?? []).map((s: any, i: number) => (
              <div key={s.stage} className="flex items-center gap-4">
                <span className="w-40 shrink-0 text-[13px] font-medium text-ink">{s.stage}</span>
                <div className="relative h-9 flex-1 overflow-hidden rounded-lg bg-black/[0.05]">
                  <div
                    style={{ width: `${Math.max(2, s.share * 100)}%` }}
                    className={`h-full rounded-lg transition-[width] duration-700 ease-out ${
                      i === 0 ? "bg-ink/80" : useCohort ? "bg-brand" : "bg-crimson"
                    }`}
                  />
                  {/* Plain white on the filled bar. mix-blend-difference turns
                      illegible against the gold fill. */}
                  <span
                    className="absolute inset-y-0 left-3 flex items-center text-[12px] font-semibold text-white"
                    style={{ textShadow: "0 1px 2px rgba(0,0,0,0.35)" }}
                  >
                    {s.count?.toLocaleString()}
                  </span>
                </div>
                <span className="tabular w-14 shrink-0 text-right text-[13px] font-semibold text-ink">
                  {(s.share * 100).toFixed(1)}%
                </span>
                <span className="tabular w-24 shrink-0 text-right text-[12px] text-muted">
                  {i === 0 ? "—" : `${(s.step_conversion * 100).toFixed(0)}% of prev`}
                </span>
                <span className="tabular w-24 shrink-0 text-right text-[12px] text-faint">
                  {s.median_days_from_prev != null ? `+${s.median_days_from_prev}d median` : ""}
                </span>
              </div>
            ))}
          </div>

          {funnel && (
            <div className="mt-5 flex flex-wrap gap-8 border-t border-black/[0.07] pt-4">
              <div>
                <p className="text-display tabular text-2xl font-bold text-ink">
                  {funnel.n?.toLocaleString()}
                </p>
                <p className="text-[11px] uppercase tracking-wider text-faint">cases in scope</p>
              </div>
              <div>
                <p className="text-display tabular text-2xl font-bold text-brand">
                  {funnel.conviction_rate_of_concluded != null
                    ? `${Math.round(funnel.conviction_rate_of_concluded * 100)}%`
                    : "—"}
                </p>
                <p className="text-[11px] uppercase tracking-wider text-faint">
                  conviction rate of concluded trials
                </p>
              </div>
              <div className="max-w-md">
                <p className="text-[12px] leading-relaxed text-muted">
                  The narrowest step is the bottleneck. Everything downstream of it is
                  capped by it, however well those later stages perform.
                </p>
              </div>
            </div>
          )}
        </div>
      </motion.div>

      {/* ------------------------------------------------------- case flow
          The funnel says how many cases reach each stage; the flow says which
          crime types are sitting where. Its outcomes are gated on FIR status, so
          its conviction counts are lower than the funnel's — the card says why. */}
      <motion.div {...fade(2)} className="bezel min-w-0">
        <div className="bezel-core p-6">
          <CaseFlowSankey />
        </div>
      </motion.div>

      {/* -------------------------------------------- operational risk index */}
      <motion.div {...fade(3)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
            <Gauge className="h-4 w-4" /> District Operational Risk Index
          </div>

          <div className="mt-3 flex items-start gap-2 rounded-xl border border-black/[0.07] bg-black/[0.02] p-4">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-azure" />
            <p className="text-[12px] leading-relaxed text-muted">
              <span className="font-semibold text-ink">This is an index, not a model.</span>{" "}
              {ori?.caveat}
            </p>
          </div>

          {/* Weight legend — the policy choice, stated */}
          {ori?.weights && (
            <div className="mt-4 flex flex-wrap gap-3">
              {Object.entries(ori.weights).map(([k, v]) => (
                <span
                  key={k}
                  className="flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3 py-1.5 text-[11px] text-muted"
                >
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ background: COMPONENT_COLOR[k] }}
                  />
                  {COMPONENT_LABEL[k] ?? k}
                  <span className="font-semibold text-ink">{Math.round((v as number) * 100)}%</span>
                </span>
              ))}
            </div>
          )}

          <div className="mt-5 space-y-2">
            {(ori?.districts ?? []).map((d: any) => {
              const tone = BAND_TONE[d.band] ?? BAND_TONE.Watch;
              const open = expanded === d.district;
              return (
                <div key={d.district} className="rounded-xl border border-black/[0.06]">
                  <button
                    onClick={() => setExpanded(open ? null : d.district)}
                    className="flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-black/[0.015]"
                  >
                    <span className="w-40 shrink-0 truncate text-[13px] font-medium text-ink">
                      {d.district}
                    </span>
                    {/* Stacked contribution bar — the score literally is these segments */}
                    <div className="flex h-2.5 flex-1 overflow-hidden rounded-full bg-black/[0.06]">
                      {Object.entries(d.contribution).map(([k, v]) => (
                        <div
                          key={k}
                          style={{ width: `${v as number}%`, background: COMPONENT_COLOR[k] }}
                          title={`${COMPONENT_LABEL[k] ?? k}: ${v} pts`}
                        />
                      ))}
                    </div>
                    <span className="tabular w-16 shrink-0 text-right text-[13px] font-bold text-ink">
                      {d.index}
                    </span>
                    <span
                      className={`w-20 shrink-0 rounded-full border px-2 py-0.5 text-center text-[11px] font-semibold ${tone.chip} ${tone.text}`}
                    >
                      {d.band}
                    </span>
                  </button>

                  {open && (
                    <div className="border-t border-black/[0.06] bg-black/[0.015] px-4 py-3">
                      <table className="w-full text-[12px]">
                        <thead>
                          <tr className="text-[10px] uppercase tracking-wider text-faint">
                            <th scope="col" className="py-1 text-left font-semibold">Component</th>
                            <th scope="col" className="py-1 text-right font-semibold">Value</th>
                            <th scope="col" className="py-1 text-right font-semibold">Percentile</th>
                            <th scope="col" className="py-1 text-right font-semibold">Weight</th>
                            <th scope="col" className="py-1 text-right font-semibold">Points</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(d.contribution).map(([k, v]) => (
                            <tr key={k} className="border-t border-black/[0.05]">
                              <td className="py-1.5">
                                <span className="flex items-center gap-2">
                                  <span
                                    className="h-2 w-2 rounded-full"
                                    style={{ background: COMPONENT_COLOR[k] }}
                                  />
                                  <span className="text-ink">{COMPONENT_LABEL[k] ?? k}</span>
                                </span>
                                <span className="ml-4 text-[11px] text-faint">
                                  {ori?.components?.[k]}
                                </span>
                              </td>
                              <td className="tabular py-1.5 text-right text-ink">{d.raw[k]}</td>
                              <td className="tabular py-1.5 text-right text-muted">
                                {Math.round(d.percentile[k] * 100)}
                              </td>
                              <td className="tabular py-1.5 text-right text-muted">
                                {Math.round(ori.weights[k] * 100)}%
                              </td>
                              <td className="tabular py-1.5 text-right font-semibold text-ink">
                                {v as number}
                              </td>
                            </tr>
                          ))}
                          <tr className="border-t border-black/10">
                            <td className="py-1.5 font-semibold text-ink" colSpan={4}>
                              Index ({d.cases.toLocaleString()} cases · {d.population_lakh} lakh
                              population)
                            </td>
                            <td className="tabular py-1.5 text-right font-bold text-ink">
                              {d.index}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {ori?.method && (
            <p className="mt-4 border-t border-black/[0.07] pt-4 text-[12px] leading-relaxed text-muted">
              {ori.method}
            </p>
          )}
        </div>
      </motion.div>

      {/* --------------------------------------------- district conversion */}
      <motion.div {...fade(4)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
            <TrendingUp className="h-4 w-4" /> Conversion by district — weakest first
          </div>
          <p className="mt-2 text-[13px] text-muted">
            Closed cohort only. Ranking districts on a mixture of mature and brand-new cases
            would sort them by how recently they were busy.
          </p>
          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-black/[0.08] text-[10px] uppercase tracking-[0.15em] text-faint">
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">District</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Cases</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Chargesheet rate</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Concluded</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Conviction rate</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Median days to CS</th>
                </tr>
              </thead>
              <tbody>
                {(lc?.by_district ?? []).map((d: any) => (
                  <tr
                    key={d.district}
                    className="border-b border-black/[0.05] transition-colors hover:bg-black/[0.015]"
                  >
                    <td className="px-2 py-2.5 font-medium text-ink">{d.district}</td>
                    <td className="tabular px-2 py-2.5 text-right text-muted">{d.cases}</td>
                    <td className="tabular px-2 py-2.5 text-right font-semibold text-brand">
                      {Math.round(d.chargesheet_rate * 100)}%
                    </td>
                    <td className="tabular px-2 py-2.5 text-right text-muted">
                      {Math.round(d.conclusion_rate * 100)}%
                    </td>
                    <td className="tabular px-2 py-2.5 text-right text-muted">
                      {d.conviction_rate != null ? `${Math.round(d.conviction_rate * 100)}%` : "—"}
                    </td>
                    <td className="tabular px-2 py-2.5 text-right text-muted">
                      {d.median_days_to_chargesheet ?? "—"}d
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
