"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { Siren, ShieldCheck, MapPin, Users, Crosshair, ArrowUpRight } from "lucide-react";
import { getJSON } from "@/lib/api";

const RISK_TONE: Record<string, { text: string; bg: string; bar: string }> = {
  Critical: { text: "text-crimson", bg: "bg-crimson/12 border-crimson/25", bar: "bg-crimson" },
  High: { text: "text-brand", bg: "bg-brand/12 border-brand/25", bar: "bg-brand" },
  Moderate: { text: "text-azure", bg: "bg-azure/12 border-azure/25", bar: "bg-azure" },
  Low: { text: "text-emerald", bg: "bg-emerald/12 border-emerald/25", bar: "bg-emerald" },
};

function DispatchInner() {
  const params = useSearchParams();
  const focusDistrict = params.get("district");
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON("/dispatch").then(setData).catch((e) => setError(e.message));
  }, []);

  const districts = useMemo(() => data?.districts ?? [], [data]);
  const next = data?.next_predicted_zone;

  return (
    <div className="space-y-7">
      <motion.header
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      >
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <Siren className="h-8 w-8 text-brand" strokeWidth={2} /> Predictive Dispatch
        </h1>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">
          AI-ranked district risk from open caseload, recent severity and trend
          spikes — with recommended patrol strength for proactive deployment.
        </p>
      </motion.header>

      {error && (
        <div className="bezel">
          <div className="bezel-core p-6 text-crimson">Failed to load dispatch: {error}</div>
        </div>
      )}

      {/* Next predicted zone + totals */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.05, ease: [0.16, 1, 0.3, 1] }}
          className="bezel lg:col-span-2"
        >
          <div className="bezel-core relative overflow-hidden p-6">
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-crimson/70 to-transparent" />
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-crimson">
              <Crosshair className="h-4 w-4" /> Next Predicted Hotspot Zone
            </div>
            {next ? (
              <div className="mt-4 flex flex-wrap items-end justify-between gap-6">
                <div>
                  <p className="text-display text-3xl font-bold text-ink">{next.district}</p>
                  <p className="mt-1 text-[13px] text-muted">
                    Highest composite risk across the state
                  </p>
                  <Link prefetch={false}
                    href={`/map?district=${encodeURIComponent(next.district)}`}
                    className="group mt-4 inline-flex items-center gap-2 rounded-full border border-black/10 bg-black/[0.03] py-2 pl-4 pr-2 text-[13px] font-medium text-ink transition-all hover:border-brand/40"
                  >
                    View on map
                    <span className="grid h-6 w-6 place-items-center rounded-full bg-brand text-black transition-transform group-hover:translate-x-0.5">
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </span>
                  </Link>
                </div>
                <div className="flex gap-8">
                  <div>
                    <p className="text-display tabular text-4xl font-bold text-crimson">
                      {next.risk_score}
                    </p>
                    <p className="text-[11px] uppercase tracking-wider text-faint">Risk index</p>
                  </div>
                  <div>
                    <p className="text-display tabular text-4xl font-bold text-brand">
                      {next.recommended_officers}
                    </p>
                    <p className="text-[11px] uppercase tracking-wider text-faint">Officers</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-4 h-24 animate-pulse rounded-xl bg-black/[0.03]" />
            )}
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
          className="bezel"
        >
          <div className="bezel-core flex h-full flex-col justify-center p-6">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-brand/12 text-brand">
              <Users className="h-5 w-5" />
            </span>
            <p className="text-display tabular mt-4 text-4xl font-bold text-ink">
              {data ? data.total_recommended_officers?.toLocaleString() : "—"}
            </p>
            <p className="mt-1 text-[13px] text-muted">
              Total recommended officers state-wide
            </p>
          </div>
        </motion.div>
      </div>

      {/* District ranking */}
      <motion.section
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, delay: 0.15, ease: [0.16, 1, 0.3, 1] }}
        className="bezel"
      >
        <div className="bezel-core p-6">
          <div className="mb-5 flex items-center gap-3">
            <ShieldCheck className="h-5 w-5 text-brand" />
            <h3 className="text-[15px] font-bold text-ink">District Risk & Deployment Ranking</h3>
          </div>

          {!data ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-14 animate-pulse rounded-xl bg-black/[0.03]" />
              ))}
            </div>
          ) : (
            <div className="space-y-2">
              {districts.map((d: any, i: number) => {
                const tone = RISK_TONE[d.risk_level] ?? RISK_TONE.Low;
                const focused = focusDistrict && d.district === focusDistrict;
                return (
                  <motion.div
                    key={d.district}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: 0.03 * i, duration: 0.4 }}
                    className={`flex items-center gap-4 rounded-xl border p-3.5 transition-colors ${
                      focused
                        ? "border-brand/50 bg-brand/[0.06]"
                        : "border-black/[0.06] bg-black/[0.02] hover:border-black/15"
                    }`}
                  >
                    <span className="tabular w-6 text-center text-[13px] font-bold text-faint">
                      {i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <MapPin className="h-3.5 w-3.5 text-faint" />
                        <p className="truncate font-semibold text-ink">{d.district}</p>
                        <span
                          className={`rounded-md border px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wide ${tone.bg} ${tone.text}`}
                        >
                          {d.risk_level}
                        </span>
                      </div>
                      <div className="tabular mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11.5px] text-faint">
                        <span>{d.open_cases} open</span>
                        <span>avg sev {d.avg_severity}</span>
                        {d.spike_ratio > 1 && (
                          <span className="text-crimson">{d.spike_ratio}× spike</span>
                        )}
                      </div>
                    </div>

                    {/* Risk bar */}
                    <div className="hidden w-40 sm:block">
                      <div className="h-2 overflow-hidden rounded-full bg-black/[0.05]">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${d.risk_score}%` }}
                          transition={{ delay: 0.2 + 0.03 * i, duration: 0.8, ease: "easeOut" }}
                          className={`h-full rounded-full ${tone.bar}`}
                        />
                      </div>
                      <p className="tabular mt-1 text-right text-[10.5px] text-faint">
                        risk {d.risk_score}
                      </p>
                    </div>

                    <div className="w-16 text-right">
                      <p className={`text-display tabular text-2xl font-bold ${tone.text}`}>
                        {d.recommended_officers}
                      </p>
                      <p className="text-[10px] uppercase tracking-wider text-faint">officers</p>
                    </div>
                  </motion.div>
                );
              })}
            </div>
          )}
        </div>
      </motion.section>
    </div>
  );
}

export default function DispatchPage() {
  return (
    <Suspense fallback={<div className="h-40 animate-pulse rounded-xl bg-black/[0.03]" />}>
      <DispatchInner />
    </Suspense>
  );
}
