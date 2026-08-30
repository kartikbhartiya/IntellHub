"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  TrendingUp,
  ScanSearch,
  ArrowRight,
  ArrowUpRight,
  Folders,
  Users,
  GitBranch,
  Activity,
  Car,
  Network,
  UserSearch,
  Gavel,
  ShieldCheck,
} from "lucide-react";
import { CrimeTrendsChart, GangActivityChart } from "./dashboard-charts";
import { getJSON } from "@/lib/api";

const rise = {
  hidden: { opacity: 0, y: 18, scale: 0.985 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: "spring" as const, stiffness: 300, damping: 24, delay: i * 0.06 },
  }),
};

function Metric({
  i,
  label,
  value,
  sub,
  icon: Icon,
  tone = "brand",
}: {
  i: number;
  label: string;
  value: string;
  sub: string;
  icon: any;
  tone?: "brand" | "emerald" | "crimson" | "azure";
}) {
  const tones: Record<string, string> = {
    brand: "text-brand",
    emerald: "text-emerald",
    crimson: "text-crimson",
    azure: "text-azure",
  };
  return (
    <motion.div
      variants={rise}
      custom={i}
      initial="hidden"
      animate="show"
      whileHover={{ y: -4 }}
      transition={{ type: "spring", stiffness: 400, damping: 22 }}
      className="bezel"
    >
      <div className="bezel-core group relative overflow-hidden p-5">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-faint">
            {label}
          </span>
          <Icon className={`h-4 w-4 ${tones[tone]}`} strokeWidth={2} />
        </div>
        {/* text-4xl in a two-up grid on a 390px screen leaves ~127px of content
            width, and "₹13.43Cr" needs about 160px — the card's overflow-hidden
            clipped it mid-character rather than overflowing the page, so it read as
            a truncated number. Smaller on narrow screens, and allowed to wrap
            instead of being cut. */}
        <p className="text-display tabular mt-3 break-words text-3xl font-bold leading-tight text-ink sm:text-4xl">{value}</p>
        <p className="mt-1.5 text-[12.5px] text-muted">{sub}</p>
        <div className="pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full bg-current opacity-[0.04] blur-2xl transition-opacity group-hover:opacity-[0.08]" />
      </div>
    </motion.div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState<any>(null);

  useEffect(() => {
    getJSON("/stats").then(setStats).catch((e) => console.error("stats", e));
  }, []);

  return (
    <div className="space-y-7">
      <motion.header
        variants={rise}
        custom={0}
        initial="hidden"
        animate="show"
        className="flex flex-col justify-between gap-4 md:flex-row md:items-end"
      >
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-emerald/25 bg-emerald/10 px-3 py-1">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald" />
            </span>
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.2em] text-emerald">
              Intelligence Core Online
            </span>
          </div>
          <h1 className="text-display text-4xl font-bold tracking-tight text-ink">
            Command Overview
          </h1>
          <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-muted">
            Deep relational intelligence, predictive hotspots and behavioural
            anomaly detection across Karnataka.
          </p>
        </div>
        <Link prefetch={false}
          href="/dispatch"
          className="group flex w-fit items-center gap-2 rounded-full border border-black/10 bg-black/[0.03] py-2.5 pl-5 pr-2.5 text-sm font-medium text-ink transition-all hover:border-brand/40"
        >
          Patrol dispatch plan
          <span className="grid h-7 w-7 place-items-center rounded-full bg-brand text-black transition-transform group-hover:translate-x-0.5">
            <ArrowUpRight className="h-4 w-4" />
          </span>
        </Link>
      </motion.header>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Metric
          i={1}
          label="Total Cases"
          value={stats?.total_cases?.toLocaleString() ?? "—"}
          sub="In knowledge graph"
          icon={Folders}
          tone="brand"
        />
        <Metric
          i={2}
          label="Tracked Suspects"
          value={stats?.tracked_suspects?.toLocaleString() ?? "—"}
          sub="Identified in network"
          icon={Users}
          tone="emerald"
        />
        <Metric
          i={3}
          label="Graph Density"
          value={stats ? `${stats.graph_density}` : "—"}
          sub="Network interconnectivity"
          icon={GitBranch}
          tone="azure"
        />
        <Metric
          i={4}
          label="Live Alerts"
          value={stats ? `${stats.trend_alerts?.length ?? 0}` : "—"}
          sub="Emerging trend spikes"
          icon={Activity}
          tone="crimson"
        />
      </div>

      {/* CCTNS depth — cross-entity intelligence */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Link prefetch={false} href="/intel?tab=vehicles">
          <Metric
            i={5}
            label="Vehicle Recovery"
            value={stats?.extended ? `${stats.extended.vehicle_recovery_rate ?? 0}%` : "—"}
            sub={`${stats?.extended?.vehicles_recovered ?? 0} of ${stats?.extended?.vehicles_total ?? 0} recovered`}
            icon={Car}
            tone="emerald"
          />
        </Link>
        <Link prefetch={false} href="/intel?tab=cybercrime">
          <Metric
            i={6}
            label="Cyber Fraud"
            value={
              stats?.extended
                ? `₹${((stats.extended.cyber_total_fraud ?? 0) / 10000000).toFixed(2)}Cr`
                : "—"
            }
            sub={`${stats?.extended?.cyber_rings?.length ?? 0} fraud rings detected`}
            icon={Network}
            tone="crimson"
          />
        </Link>
        <Link prefetch={false} href="/intel?tab=missing">
          <Metric
            i={7}
            label="Missing Persons"
            value={stats?.extended ? `${stats.extended.missing_open ?? 0}` : "—"}
            sub={`${stats?.extended?.missing_found_rate ?? 0}% traced & found`}
            icon={UserSearch}
            tone="azure"
          />
        </Link>
        <Link prefetch={false} href="/intel?tab=courts">
          <Metric
            i={8}
            label="Conviction Rate"
            value={stats?.extended ? `${stats.extended.conviction_rate ?? 0}%` : "—"}
            sub={`${stats?.extended?.chargesheets_filed ?? 0} chargesheets filed`}
            icon={Gavel}
            tone="brand"
          />
        </Link>
      </div>

      {/* Emerging Trend Alerts */}
      {stats?.trend_alerts?.length > 0 && (
        <motion.section variants={rise} custom={5} initial="hidden" animate="show" className="bezel">
          <div className="bezel-core relative overflow-hidden p-6">
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-crimson/60 to-transparent" />
            <div className="mb-5 flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-crimson/12 text-crimson">
                <TrendingUp className="h-[18px] w-[18px]" />
              </span>
              <div>
                <h3 className="text-[15px] font-bold text-ink">Emerging Trend Alerts</h3>
                <p className="text-[12px] text-faint">Last 30 days vs historical baseline</p>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {stats.trend_alerts.map((a: any, idx: number) => (
                <motion.div
                  key={idx}
                  className="relative overflow-hidden rounded-xl border border-crimson/15 bg-crimson/[0.04] p-4 transition-colors hover:border-crimson/35"
                  animate={{
                    boxShadow: [
                      "0 0 0px rgba(240, 71, 63, 0)",
                      "0 0 10px rgba(240, 71, 63, 0.2)",
                      "0 0 0px rgba(240, 71, 63, 0)",
                    ],
                  }}
                  transition={{
                    duration: 2 + idx * 0.4,
                    repeat: Infinity,
                    ease: "easeInOut",
                  }}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="relative flex h-2 w-2 shrink-0">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-crimson opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-crimson" />
                      </span>
                      <p className="font-semibold text-ink leading-tight">{a.crime_type}</p>
                    </div>
                    <span className="tabular rounded-md bg-crimson/15 px-2 py-0.5 text-[12px] font-bold text-crimson">
                      {a.spike_ratio}×
                    </span>
                  </div>
                  <p className="mt-1 text-[13px] text-muted">{a.district}</p>
                  <p className="tabular mt-2 text-[12.5px] text-faint">
                    {a.recent_count} cases · ~{a.expected_count} expected
                  </p>
                </motion.div>
              ))}
            </div>
          </div>
        </motion.section>
      )}

      {/* Anomalies */}
      {stats?.anomalies?.length > 0 && (
        <motion.section id="anomalies" variants={rise} custom={6} initial="hidden" animate="show" className="scroll-mt-28 bezel">
          <div className="bezel-core p-6">
            <div className="mb-5 flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-azure/12 text-azure">
                <ScanSearch className="h-[18px] w-[18px]" />
              </span>
              <div>
                <h3 className="text-[15px] font-bold text-ink">Behavioural Anomalies</h3>
                <p className="text-[12px] text-faint">
                  {stats.anomalies.length} cases deviate from learned patterns · Isolation Forest
                </p>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {stats.anomalies.slice(0, 6).map((a: any, idx: number) => (
                <Link prefetch={false}
                  key={idx}
                  href={`/case?id=${a.case_id}`}
                  className="group flex items-center justify-between gap-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-4 transition-all hover:border-azure/35 hover:bg-black/[0.04]"
                >
                  <div className="min-w-0">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="text-[12px] font-bold text-azure">{a.case_id}</span>
                      <span className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] text-muted">
                        {a.crime_type}
                      </span>
                    </div>
                    <p className="truncate text-[12px] text-faint">{a.reasons.join(" · ")}</p>
                  </div>
                  <ArrowRight className="h-4 w-4 shrink-0 text-faint transition-all group-hover:translate-x-1 group-hover:text-azure" />
                </Link>
              ))}
            </div>
          </div>
        </motion.section>
      )}

      {/* Charts */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <motion.div variants={rise} custom={7} initial="hidden" animate="show" className="bezel">
          <div className="bezel-core p-6">
            <h3 className="mb-4 text-[15px] font-bold text-ink">Crime Trends by Category</h3>
            <div className="h-[300px] w-full">
              {stats?.crime_trends ? (
                <CrimeTrendsChart data={stats.crime_trends} />
              ) : (
                <div className="h-full w-full animate-pulse rounded-xl bg-black/[0.03]" />
              )}
            </div>
          </div>
        </motion.div>

        <motion.div variants={rise} custom={8} initial="hidden" animate="show" className="bezel">
          <div className="bezel-core p-6">
            <h3 className="mb-4 text-[15px] font-bold text-ink">Gang Affiliation & Offender Radar</h3>
            <div className="h-[300px] w-full">
              {stats?.gang_activity ? (
                <GangActivityChart data={stats.gang_activity} />
              ) : (
                <div className="h-full w-full animate-pulse rounded-xl bg-black/[0.03]" />
              )}
            </div>
          </div>
        </motion.div>
      </div>

      {/* Governance — the constraints this system operates under, stated up front */}
      <motion.section variants={rise} custom={9} initial="hidden" animate="show" className="bezel">
        <div className="bezel-core p-6">
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
            <ShieldCheck className="h-4 w-4" /> Governance &amp; limits
          </div>
          <p className="mt-3 max-w-3xl text-[15px] font-semibold leading-relaxed text-ink">
            KSP IntelHub does not infer guilt. It surfaces relationships and priorities in
            recorded data for authorised operational review.
          </p>
          <div className="mt-5 grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-2">
            {[
              {
                h: "No output is evidence",
                b: "Risk scores, conviction outlooks and link predictions are decision support for allocating attention. None of it is evidence, and none of it may be presented to a court as such.",
              },
              {
                h: "Nothing is stated as certainty",
                b: "Person-level probabilities are calibrated and never display 0% or 100%. Where a model is outside its supported range — a case older than its observation grid — the platform refuses to score it rather than extrapolating.",
              },
              {
                h: "Every number is traceable",
                b: "Each model publishes its split dates, baselines, calibration curve and the warnings its training run raised, in the committed model registry, served at /api/models. The composite Operational Risk Index publishes its weights and per-component arithmetic.",
              },
              {
                h: "Measured against doing nothing",
                b: "Every model is reported next to the trivial baseline it has to beat, including where it fails to: the crime forecast still loses to a 4-week moving average, and the registry says so.",
              },
              {
                h: "Subgroup performance is checked",
                b: "Trial-outcome accuracy is broken out by age band and gender, so a model that works overall but fails a subgroup cannot pass unnoticed.",
              },
              {
                h: "The data is simulated",
                b: "No production KSP records were used. The corpus is CCTNS-schema-shaped synthetic data over the real 31 districts. Scores demonstrate that the pipeline recovers structure deliberately injected into it — they are not claims of field performance.",
              },
            ].map((c) => (
              <div key={c.h}>
                <p className="text-[13px] font-semibold text-ink">{c.h}</p>
                <p className="mt-1 text-[12.5px] leading-relaxed text-muted">{c.b}</p>
              </div>
            ))}
          </div>
          <p className="mt-5 border-t border-black/[0.07] pt-4 text-[12px] leading-relaxed text-faint">
            Predictive policing systems carry a documented risk of entrenching the patterns in
            their own training records: enforcement concentrates where past enforcement was
            recorded, which generates the very data that justifies concentrating there. Nothing
            in this platform is exempt from that. It is built to be audited — which is why the
            registry publishes what each model failed at alongside what it achieved.
          </p>
        </div>
      </motion.section>
    </div>
  );
}
