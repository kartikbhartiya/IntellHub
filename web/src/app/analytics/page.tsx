"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Brain,
  TrendingUp,
  TrendingDown,
  Minus,
  MapPinned,
  GitFork,
  ShieldAlert,
  FileSearch,
  ArrowUpRight,
  Sparkles,
  Loader2,
  Scale,
  Lightbulb,
  ArrowLeftRight,
  CheckCircle2,
  AlertCircle,
  HelpCircle,
  Expand,
  Shrink,
  Users,
} from "lucide-react";
import { getJSON, apiFetch } from "@/lib/api";
import { ForecastChart, ModelMetricsChart, FeatureImportanceChart } from "./analytics-charts";
import { SocioScatter, SocioVulnerabilityBar, SocioRadarChart } from "./socio-charts";
import OccupationChart from "@/components/OccupationChart";
import TrendForecast from "@/components/TrendForecast";

const rise = {
  hidden: { opacity: 0, y: 18, scale: 0.985 },
  show: (i: number) => ({
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: "spring" as const, stiffness: 300, damping: 24, delay: i * 0.05 },
  }),
};

function Section({
  i,
  icon: Icon,
  title,
  sub,
  tone = "brand",
  children,
}: {
  i: number;
  icon: any;
  title: string;
  sub: string;
  tone?: string;
  children: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    brand: "bg-brand/12 text-brand",
    azure: "bg-azure/12 text-azure",
    crimson: "bg-crimson/12 text-crimson",
    emerald: "bg-emerald/12 text-emerald",
  };
  return (
    // `min-w-0` because this is a grid item, and grid items default to
    // `min-width: auto` — they refuse to shrink below their content's intrinsic
    // width. A single long unbroken string inside therefore widened the whole track
    // past the viewport, which is where the analytics page's 5px of horizontal
    // scroll came from.
    <motion.section variants={rise} custom={i} initial="hidden" animate="show" className="bezel min-w-0">
      <div className="bezel-core p-6">
        <div className="mb-5 flex items-center gap-3">
          <span className={`grid h-9 w-9 place-items-center rounded-xl ${tones[tone]}`}>
            <Icon className="h-[18px] w-[18px]" />
          </span>
          <div>
            <h3 className="text-[15px] font-bold text-ink">{title}</h3>
            <p className="text-[12px] text-faint">{sub}</p>
          </div>
        </div>
        {children}
      </div>
    </motion.section>
  );
}

const trendIcon = (t?: string) =>
  t === "rising" ? TrendingUp : t === "falling" ? TrendingDown : Minus;
const trendTone = (t?: string) =>
  t === "rising" ? "text-crimson" : t === "falling" ? "text-emerald" : "text-muted";

export default function AnalyticsPage() {
  const [data, setData] = useState<any>(null);
  const [fc, setFc] = useState<any>(null);
  const [scope, setScope] = useState<string>("");
  const [isFullScreen, setIsFullScreen] = useState(false);

  const toggleFullScreen = useCallback(() => {
    if (!document.fullscreenElement && !isFullScreen) {
      document.documentElement.requestFullscreen().catch(() => {});
      document.documentElement.setAttribute("data-fullscreen", "true");
      setIsFullScreen(true);
    } else {
      if (document.exitFullscreen && document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
      document.documentElement.removeAttribute("data-fullscreen");
      setIsFullScreen(false);
    }
  }, [isFullScreen]);

  useEffect(() => {
    const onFsChange = () => {
      const fs = !!document.fullscreenElement;
      setIsFullScreen(fs);
      if (fs) {
        document.documentElement.setAttribute("data-fullscreen", "true");
      } else {
        document.documentElement.removeAttribute("data-fullscreen");
      }
    };
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  useEffect(() => {
    getJSON("/analytics").then(setData).catch(() => {});
    getJSON("/forecast").then((d) => setFc(d)).catch(() => {});
  }, []);

  useEffect(() => {
    const q = scope ? `?district=${encodeURIComponent(scope)}` : "";
    getJSON(`/forecast${q}`).then(setFc).catch(() => {});
  }, [scope]);

  const s = fc?.series;
  const TI = trendIcon(s?.trend);

  return (
    <div className={`space-y-6 ${isFullScreen ? 'fixed inset-0 z-[9999] bg-[#f7f7f9] p-6 m-0 overflow-auto' : 'relative'}`}>
      <motion.header variants={rise} custom={0} initial="hidden" animate="show" className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-brand/25 bg-brand/10 px-3 py-1">
            <Brain className="h-3.5 w-3.5 text-brand" />
            <span className="text-[10.5px] font-semibold uppercase tracking-[0.2em] text-brand">
              Predictive Intelligence · 6 evaluated models
            </span>
          </div>
          <h1 className="text-display text-4xl font-bold tracking-tight text-ink">Analytics & ML</h1>
          <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">
            Forecasting, density clustering, network link-prediction, narrative classification and
            recidivism scoring. Models are trained offline and validated on a held-out window
            later in time than their training data, and each is scored against the trivial
            baseline it has to beat.
          </p>
        </div>
        <button
          onClick={toggleFullScreen}
          className="flex shrink-0 items-center gap-2 rounded-full border border-black/10 bg-white px-4 py-2 text-sm font-medium transition-all hover:border-brand/40 hover:bg-brand/5 hover:text-brand shadow-sm"
        >
          {isFullScreen ? <Shrink className="h-4 w-4" /> : <Expand className="h-4 w-4" />}
          {isFullScreen ? "Exit Full Screen" : "Full Screen"}
        </button>
      </motion.header>

      {/* Model summary strip */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {[
          {
            l: "Next-week forecast",
            v: data?.forecast?.overall?.next_week ?? "—",
            sub: data?.forecast?.overall
              ? `${data.forecast.overall.change_pct > 0 ? "+" : ""}${data.forecast.overall.change_pct}% · ${data.forecast.overall.trend}`
              : "incidents",
            tone: "brand",
          },
          { l: "DBSCAN zones", v: data?.clusters?.length ?? "—", sub: "dense crime clusters", tone: "azure" },
          { l: "Predicted alliances", v: data?.predicted_links?.length ?? "—", sub: "Adamic-Adar links", tone: "crimson" },
          {
            l: "Classifier accuracy",
            v: data?.classifier?.accuracy != null ? `${Math.round(data.classifier.accuracy * 100)}%` : "—",
            sub: `${data?.classifier?.classes?.length ?? 0}-class NLP`,
            tone: "emerald",
          },
          { l: "Extreme-risk", v: data?.recidivism?.bands?.Extreme ?? "—", sub: "offenders flagged", tone: "crimson" },
        ].map((t, i) => (
          <motion.div key={t.l} variants={rise} custom={i + 1} initial="hidden" animate="show" className="bezel">
            <div className="bezel-core p-4">
              <p className="text-[11px] uppercase tracking-wider text-faint">{t.l}</p>
              <p className="text-display tabular mt-1.5 text-3xl font-bold text-ink">{t.v}</p>
              <p className="mt-0.5 text-[11.5px] text-muted">{t.sub}</p>
            </div>
          </motion.div>
        ))}
      </div>

      {/* Forecast */}
      <Section
        i={2}
        icon={TrendingUp}
        title="Crime Volume Forecast"
        sub="Ridge regression · trend + Fourier seasonality · 80% prediction interval"
      >
        <div className="grid gap-5 lg:grid-cols-[1fr_260px]">
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <button
                onClick={() => setScope("")}
                className={`rounded-full border px-3 py-1 text-[12px] transition-colors ${
                  !scope ? "border-brand/50 bg-brand/12 text-brand" : "border-black/10 text-muted hover:text-ink"
                }`}
              >
                Statewide
              </button>
              {(fc?.districts || []).map((d: string) => (
                <button
                  key={d}
                  onClick={() => setScope(d)}
                  className={`rounded-full border px-3 py-1 text-[12px] transition-colors ${
                    scope === d ? "border-brand/50 bg-brand/12 text-brand" : "border-black/10 text-muted hover:text-ink"
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
            <div className="h-[300px] w-full">
              {s ? (
                <ForecastChart labels={fc.week_labels} future={fc.future_labels} series={s} />
              ) : (
                <div className="h-full w-full animate-pulse rounded-xl bg-black/[0.03]" />
              )}
            </div>
            <div className="mt-2 flex items-center gap-4 text-[11px] text-faint">
              <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded bg-brand" /> Actual</span>
              <span className="flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed border-azure" /> Forecast (8 wks)</span>
              <span className="flex items-center gap-1.5"><span className="h-2 w-4 rounded bg-azure/25" /> Confidence band</span>
            </div>
          </div>
          <div className="space-y-3">
            {s && (
              <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
                <p className="text-[11px] uppercase tracking-wider text-faint">{fc.scope}</p>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="text-display tabular text-3xl font-bold text-ink">{s.next_week}</span>
                  <span className={`flex items-center gap-1 text-[13px] font-semibold ${trendTone(s.trend)}`}>
                    <TI className="h-4 w-4" />
                    {s.change_pct > 0 ? "+" : ""}
                    {s.change_pct}%
                  </span>
                </div>
                <p className="mt-1 text-[12px] text-muted">forecast incidents next week · avg {s.avg_weekly}/wk</p>
              </div>
            )}
            <div className="rounded-2xl border border-crimson/15 bg-crimson/[0.03] p-4">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-crimson">Rising districts</p>
              <div className="space-y-1.5">
                {(fc?.rising_districts || data?.forecast?.rising_districts || [])
                  .filter((r: any) => r.trend === undefined || r.trend === "rising" || r.change_pct > 0)
                  .slice(0, 5)
                  .map((r: any) => (
                    <div key={r.district} className="flex items-center justify-between text-[12.5px]">
                      <span className="text-ink/85">{r.district}</span>
                      <span className="tabular font-semibold text-crimson">+{r.change_pct}%</span>
                    </div>
                  ))}
                {!(fc?.rising_districts?.length || data?.forecast?.rising_districts?.length) && (
                  <p className="text-[12px] text-faint">No district trending up sharply.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      </Section>

      {/* Annual trend forecast */}
      <div id="trend-forecast" className="scroll-mt-28">
        <Section
          i={2}
          icon={Sparkles}
          title="Crime Trend Forecast — annual, per district"
          sub="Partial-pooling ridge regression · backtested against 3 baselines · 80% interval · pick a past year to see it miss"
        >
          <TrendForecast />
        </Section>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* DBSCAN clusters */}
        <Section i={3} icon={MapPinned} title="Crime Cluster Zones" sub="DBSCAN · haversine density clustering" tone="azure">
          <div className="space-y-2">
            {(data?.clusters || []).slice(0, 6).map((c: any) => (
              <Link prefetch={false}
                key={c.id}
                href={`/map`}
                className="group flex items-center justify-between gap-3 rounded-xl border border-black/[0.06] bg-black/[0.02] p-3.5 transition-all hover:border-azure/35"
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-semibold text-ink">{c.dominant_crime}</p>
                  <p className="text-[11.5px] text-faint">
                    {c.district} · {c.radius_km} km radius · sev {c.avg_severity}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="tabular rounded-lg bg-azure/12 px-2.5 py-1 text-[13px] font-bold text-azure">
                    {c.cases}
                  </span>
                  <ArrowUpRight className="h-4 w-4 shrink-0 text-faint transition-all group-hover:translate-x-0.5 group-hover:text-azure" />
                </div>
              </Link>
            ))}
            {!data?.clusters?.length && <div className="h-40 animate-pulse rounded-xl bg-black/[0.03]" />}
          </div>
        </Section>

        {/* Predicted links */}
        <Section i={3} icon={GitFork} title="Predicted Alliances" sub="Adamic-Adar link prediction on the co-offender graph" tone="crimson">
          <div className="space-y-2">
            {(data?.predicted_links || []).slice(0, 6).map((l: any, idx: number) => (
              <div key={idx} className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2 text-[13px]">
                    <Link prefetch={false} href={`/suspect?id=${l.a}`} className="truncate font-medium text-ink hover:text-brand">
                      {l.a_name}
                    </Link>
                    <span className="text-faint">↔</span>
                    <Link prefetch={false} href={`/suspect?id=${l.b}`} className="truncate font-medium text-ink hover:text-brand">
                      {l.b_name}
                    </Link>
                  </div>
                  <span className="tabular shrink-0 rounded-md bg-crimson/12 px-2 py-0.5 text-[11.5px] font-bold text-crimson">
                    {l.score}
                  </span>
                </div>
                <p className="mt-1 text-[11.5px] text-faint">
                  {l.common_associates} shared associates
                  {l.same_gang && l.gang ? ` · both ${l.gang}` : ""}
                  {l.risk ? ` · ${l.risk} risk` : ""}
                </p>
              </div>
            ))}
            {!data?.predicted_links?.length && <div className="h-40 animate-pulse rounded-xl bg-black/[0.03]" />}
          </div>
        </Section>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Recidivism */}
        <Section i={4} icon={ShieldAlert} title="Recidivism Risk Index" sub="Multi-factor offender prioritisation (0–100)" tone="crimson">
          <RecidivismPanel data={data?.recidivism} />
        </Section>

        {/* Live classifier */}
        <Section i={4} icon={FileSearch} title="FIR Narrative Classifier" sub="TF-IDF + Logistic Regression · reads a report, predicts the crime" tone="emerald">
          <ClassifierDemo metrics={data?.classifier} />
        </Section>
      </div>

      {/* AIML Model Evaluation & Feature Drivers */}
      <Section
        i={5}
        icon={Brain}
        title="AIML Analytics & Model Validation"
        sub="Held-out scores against baselines, and permutation feature importances"
        tone="emerald"
      >
        <ModelPerformancePanel />
      </Section>

      {/* AI Case-Suspect Deep Comparison Tool */}
      <Section
        i={6}
        icon={Brain}
        title="AI Case-Suspect Deep Comparison"
        sub="Side-by-side behavioral, spatiotemporal, and criminological specialty correlation audit"
        tone="brand"
      >
        <DeepCompareTool />
      </Section>

      {/* Socio-economic correlation */}
      <Section
        i={6}
        icon={Scale}
        title="Socio-Economic Crime Correlation"
        sub="Pearson correlation of district crime rates against demographic & economic indicators"
        tone="azure"
      >
        <SocioEconomic />
      </Section>

      {/* Socio-economic profile of the people on the FIRs — the person-level side of
          the district correlation above, aggregated server-side. */}
      <Section
        i={7}
        icon={Users}
        title="Socio-Economic Profile of FIRs"
        sub="Complainant occupation, and the age and gender of complainants, victims and accused · aggregate counts only"
        tone="azure"
      >
        <OccupationChart />
      </Section>
    </div>
  );
}

function DeepCompareTool() {
  const [cases, setCases] = useState<any[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState<string>("");
  const [suspects, setSuspects] = useState<any[]>([]);
  const [selectedSuspectId, setSelectedSuspectId] = useState<string>("");
  const [compareData, setCompareData] = useState<any>(null);
  const [loadingCases, setLoadingCases] = useState(true);
  const [loadingCompare, setLoadingCompare] = useState(false);
  const [loadingSuspects, setLoadingSuspects] = useState(false);

  // 1. Fetch cases on mount
  useEffect(() => {
    setLoadingCases(true);
    getJSON("/cases?limit=150")
      .then((d) => {
        setCases(d.cases || []);
        if (d.cases && d.cases.length > 0) {
          setSelectedCaseId(d.cases[0].case_id);
        }
      })
      .catch(() => {})
      .finally(() => setLoadingCases(false));
  }, []);

  // 2. Fetch suggested suspects when selectedCaseId changes
  useEffect(() => {
    if (!selectedCaseId) return;
    setLoadingSuspects(true);
    setSuspects([]);
    setSelectedSuspectId("");
    setCompareData(null);
    
    getJSON(`/case/${selectedCaseId}`)
      .then((d) => {
        const list = d.ai_suggested_suspects || [];
        setSuspects(list);
        if (list.length > 0) {
          setSelectedSuspectId(list[0].suspect_id);
        }
      })
      .catch(() => {})
      .finally(() => setLoadingSuspects(false));
  }, [selectedCaseId]);

  // 3. Fetch deep comparison when both are selected
  useEffect(() => {
    if (!selectedCaseId || !selectedSuspectId) {
      setCompareData(null);
      return;
    }
    setLoadingCompare(true);
    getJSON(`/analytics/compare?case_id=${selectedCaseId}&suspect_id=${selectedSuspectId}`)
      .then((d) => {
        setCompareData(d);
      })
      .catch(() => {})
      .finally(() => setLoadingCompare(false));
  }, [selectedCaseId, selectedSuspectId]);

  const matchColor = (isMatch: boolean) => 
    isMatch ? "text-emerald border-emerald/20 bg-emerald/5" : "text-faint border-black/10 bg-black/[0.02]";

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        {/* Case selector */}
        <div>
          <label htmlFor="cmp-case" className="text-[11px] font-bold uppercase tracking-wider text-faint block mb-1">Select Case Profile</label>
          {loadingCases ? (
            <div className="h-10 animate-pulse rounded-xl bg-black/[0.03]" />
          ) : (
            <select
                  id="cmp-case"
              value={selectedCaseId}
              onChange={(e) => setSelectedCaseId(e.target.value)}
              className="w-full rounded-xl border border-black/10 bg-black/[0.02] px-3.5 py-2.5 text-[13px] text-ink focus:border-brand/40 focus:outline-none"
            >
              {cases.map((c) => (
                <option key={c.case_id} value={c.case_id}>
                  {c.case_id} — {c.crime_type} ({c.district})
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Suspect selector */}
        <div>
          <label htmlFor="cmp-suspect" className="text-[11px] font-bold uppercase tracking-wider text-faint block mb-1">AI Match Suspects</label>
          {loadingSuspects ? (
            <div className="h-10 animate-pulse rounded-xl bg-black/[0.03]" />
          ) : suspects.length === 0 ? (
            <div className="rounded-xl border border-black/5 bg-black/[0.01] px-3.5 py-2.5 text-[13px] text-faint">
              No AI suggested suspects found for this case.
            </div>
          ) : (
            <select
                  id="cmp-suspect"
              value={selectedSuspectId}
              onChange={(e) => setSelectedSuspectId(e.target.value)}
              className="w-full rounded-xl border border-black/10 bg-black/[0.02] px-3.5 py-2.5 text-[13px] text-ink focus:border-brand/40 focus:outline-none"
            >
              {suspects.map((s) => (
                <option key={s.suspect_id} value={s.suspect_id}>
                  {s.name} ({s.match_score}% Match)
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {loadingCompare ? (
        <div className="h-60 animate-pulse rounded-2xl bg-black/[0.02] border border-black/[0.06] flex items-center justify-center text-muted">
          <Loader2 className="h-6 w-6 animate-spin text-brand" />
        </div>
      ) : compareData ? (
        <div className="space-y-4">
          {/* Match Score Gauge */}
          <div className="rounded-2xl border border-brand/15 bg-brand/[0.02] p-5">
            <div className="flex flex-col md:flex-row items-center justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="relative grid h-20 w-20 place-items-center rounded-full border-4 border-brand/12 bg-white font-bold text-brand shrink-0">
                  <span className="text-display text-2xl font-black">{compareData.match_score}%</span>
                </div>
                <div>
                  <h4 className="text-[16px] font-bold text-ink">AI Deep Match Analysis</h4>
                  <p className="text-[12.5px] text-muted max-w-xl mt-1 leading-relaxed">
                    {compareData.briefing}
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {/* MO comparison table */}
            <div className="rounded-2xl border border-black/[0.06] bg-black/[0.01] p-4 space-y-3">
              <h5 className="text-[12.5px] font-bold text-ink flex items-center gap-2">
                <ArrowLeftRight className="h-4 w-4 text-brand" /> Modus Operandi & Proximity
              </h5>
              
              <div className="space-y-2">
                {Object.entries(compareData.mo_match).map(([key, value]: any) => (
                  <div key={key} className={`rounded-xl border p-3 flex flex-col justify-between transition-colors ${matchColor(value.is_match)}`}>
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] font-bold uppercase tracking-wider capitalize text-faint">
                        {key.replace("_", " ")}
                      </span>
                      {value.is_match ? (
                        <span className="rounded bg-emerald/12 px-1.5 py-0.5 text-[9.5px] font-bold text-emerald flex items-center gap-1">
                          <CheckCircle2 className="h-3 w-3" /> MATCH (+{value.contrib}%)
                        </span>
                      ) : (
                        <span className="rounded bg-black/5 px-1.5 py-0.5 text-[9.5px] font-semibold text-muted">
                          MISMATCH
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-4 mt-2 text-[12.5px]">
                      <div>
                        <span className="text-faint text-[10.5px] block">Case Setting:</span>
                        <span className="font-semibold text-ink">{value.case_val}</span>
                      </div>
                      <div>
                        <span className="text-faint text-[10.5px] block">Suspect History:</span>
                        <span className="font-semibold text-ink truncate block" title={value.suspect_val}>
                          {value.suspect_val}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Geographical and Risk profile comparison */}
            <div className="space-y-4">
              {/* Spatiotemporal */}
              <div className="rounded-2xl border border-black/[0.06] bg-black/[0.01] p-4 space-y-3">
                <h5 className="text-[12.5px] font-bold text-ink flex items-center gap-2">
                  <MapPinned className="h-4 w-4 text-azure" /> Spatial Proximity
                </h5>
                <div className="grid grid-cols-2 gap-4">
                  <div className="rounded-xl border border-black/[0.04] bg-white p-3">
                    <span className="text-faint text-[10.5px] block">District History</span>
                    <span className="font-bold text-ink mt-1 block">
                      {compareData.geo_match.district_match ? "Active in District" : "No Past Active Record"}
                    </span>
                    <span className="text-[10px] text-faint mt-0.5 block">
                      Case District: {compareData.geo_match.district_match ? "Matched" : "Unmatched"}
                    </span>
                  </div>
                  <div className="rounded-xl border border-black/[0.04] bg-white p-3">
                    <span className="text-faint text-[10.5px] block">Closest Past Offense</span>
                    <span className="font-bold text-brand mt-1 block">
                      {compareData.geo_match.min_distance_km != null ? `${compareData.geo_match.min_distance_km} km` : "N/A"}
                    </span>
                    <span className="text-[10px] text-faint mt-0.5 block">
                      {compareData.geo_match.min_distance_km != null && compareData.geo_match.min_distance_km < 5.0 ? "High geographical overlap" : "Moderate/Low geographic tie"}
                    </span>
                  </div>
                </div>
              </div>

              {/* Suspect risk profile details */}
              <div className="rounded-2xl border border-black/[0.06] bg-black/[0.01] p-4 space-y-3">
                <h5 className="text-[12.5px] font-bold text-ink flex items-center gap-2">
                  <ShieldAlert className="h-4 w-4 text-crimson" /> Recidivism & Police Registry Flags
                </h5>
                
                {compareData.risk_data ? (
                  <div className="grid grid-cols-2 gap-2 text-[12px]">
                    <div className={`rounded-xl border p-2.5 flex items-center gap-2 ${compareData.risk_data.rowdy_sheeter ? "border-crimson/15 bg-crimson/[0.03] text-crimson" : "border-black/5 bg-black/[0.01] text-muted"}`}>
                      {compareData.risk_data.rowdy_sheeter ? <AlertCircle className="h-4 w-4 shrink-0" /> : <HelpCircle className="h-4 w-4 shrink-0" />}
                      <div className="min-w-0">
                        <span className="font-semibold block truncate">Rowdy Sheeter</span>
                        <span className="text-[10px] opacity-80">{compareData.risk_data.rowdy_sheeter ? "Flagged Active" : "No Flag"}</span>
                      </div>
                    </div>

                    <div className={`rounded-xl border p-2.5 flex items-center gap-2 ${compareData.risk_data.active_warrant ? "border-crimson/15 bg-crimson/[0.03] text-crimson" : "border-black/5 bg-black/[0.01] text-muted"}`}>
                      {compareData.risk_data.active_warrant ? <AlertCircle className="h-4 w-4 shrink-0" /> : <HelpCircle className="h-4 w-4 shrink-0" />}
                      <div className="min-w-0">
                        <span className="font-semibold block truncate">Active Warrant</span>
                        <span className="text-[10px] opacity-80">{compareData.risk_data.active_warrant ? "Evading Arrest" : "No Warrants"}</span>
                      </div>
                    </div>

                    <div className={`rounded-xl border p-2.5 flex items-center gap-2 ${compareData.risk_data.history_sheet ? "border-crimson/15 bg-crimson/[0.03] text-crimson" : "border-black/5 bg-black/[0.01] text-muted"}`}>
                      {compareData.risk_data.history_sheet ? <AlertCircle className="h-4 w-4 shrink-0" /> : <HelpCircle className="h-4 w-4 shrink-0" />}
                      <div className="min-w-0">
                        <span className="font-semibold block truncate">History Sheet</span>
                        <span className="text-[10px] opacity-80">{compareData.risk_data.history_sheet ? "Opened File" : "No File"}</span>
                      </div>
                    </div>

                    <div className="rounded-xl border border-black/5 bg-black/[0.01] p-2.5 flex items-center gap-2 text-ink/80">
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-brand" />
                      <div>
                        <span className="font-semibold block truncate">Past Arrests</span>
                        <span className="text-[10px] text-faint">{compareData.risk_data.past_arrests} records</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-[12px] text-faint">No risk registry profile loaded.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-black/15 p-12 text-center text-muted">
          Select a case and suspect above to start the deep AI match comparison.
        </div>
      )}
    </div>
  );
}

const SOCIO_FACTORS = [
  { key: "unemployment", label: "Unemployment %" },
  { key: "literacy", label: "Literacy %" },
  { key: "urbanization", label: "Urbanisation %" },
  { key: "per_capita_income_k", label: "Per-capita income (₹k)" },
  { key: "police_per_100k", label: "Police / 100k" },
];

// Reads the committed model registry. Everything rendered here is produced by an
// offline `python -m ml.train` run — no figure on this panel is written by hand.
const MODEL_LABEL: Record<string, string> = {
  narrative_classifier: "Narrative classifier",
  recidivism_risk: "Recidivism risk",
  conviction_outcome: "Trial outcome",
  case_closure: "Case progression",
  crime_forecast: "Volume forecast",
  hotspot_clusters: "Hotspot zones",
};
const TRIVIAL_BASELINE = /base rate|majority|stratified|naive|moving average|drift/i;

function ModelPerformancePanel() {
  const [reg, setReg] = useState<any>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getJSON("/models")
      .then(setReg)
      .catch(() => setErr(true));
  }, []);

  if (err || (reg && !reg.available))
    return (
      <p className="text-[12px] text-muted">
        No training run has been recorded yet. Run{" "}
        <code className="rounded bg-black/[0.05] px-1 py-0.5 font-mono text-[11px]">
          python -m ml.train
        </code>{" "}
        in <code className="font-mono text-[11px]">appsail/</code> to publish metrics.
      </p>
    );
  if (!reg)
    return (
      <div className="flex h-[220px] items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-faint" />
      </div>
    );

  const entries: any[] = Object.values(reg.models || {});

  // Headline score per supervised model, next to the best trivial baseline it faced.
  const PALETTE = ["#10b981", "#2563eb", "#8b5cf6", "#dc2b25", "#d97706", "#0891b2"];
  const modelMetrics = entries
    .filter((e) => e.metrics?.roc_auc != null || e.metrics?.accuracy != null)
    .map((e, i) => ({
      metric: MODEL_LABEL[e.name] || e.name,
      score: Number((((e.metrics.roc_auc ?? e.metrics.accuracy) as number) * 100).toFixed(1)),
      color: PALETTE[i % PALETTE.length],
    }));

  // Compare like with like. A model reported as ROC-AUC must be compared against a
  // baseline's ROC-AUC, never against its accuracy — they are different scales, and
  // mixing them makes a good model look beaten (or a poor one look fine).
  const compare = (e: any) => {
    const key = e.metrics?.roc_auc != null ? "roc_auc" : "accuracy";
    const score = e.metrics?.[key];
    const base = (e.baselines || [])
      .filter((b: any) => TRIVIAL_BASELINE.test(b.strategy) && b[key] != null)
      .reduce((acc: number | null, b: any) => Math.max(acc ?? 0, b[key]), null as number | null);
    return { key, score, base };
  };

  // Real permutation importance from whichever model reports the richest set.
  const withImportance = entries
    .filter((e) => e.permutation_importance?.length)
    .sort(
      (a, b) => b.permutation_importance.length - a.permutation_importance.length
    )[0];
  const featureImpact = withImportance
    ? withImportance.permutation_importance
        .filter((f: any) => f.importance > 0)
        .slice(0, 6)
        .map((f: any) => ({
          feature: f.feature.replace(/_/g, " "),
          importance: Number((f.importance * 100).toFixed(1)),
        }))
    : [];

  return (
    <div className="space-y-4">
      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-3">
          <h4 className="text-[13px] font-bold text-ink flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-emerald" /> Held-out performance (%)
          </h4>
          <div className="h-[220px] w-full rounded-2xl border border-black/[0.06] bg-black/[0.01] p-3">
            <ModelMetricsChart data={modelMetrics} />
          </div>
          <p className="text-[11px] text-faint">
            ROC-AUC, or accuracy for the multi-class classifier. Measured on records
            registered <em>after</em> everything each model was trained on
            {reg.dataset?.cases?.rows
              ? ` — ${reg.dataset.cases.rows.toLocaleString()} cases in the corpus.`
              : "."}
          </p>
        </div>

        <div className="space-y-3">
          <h4 className="text-[13px] font-bold text-ink flex items-center gap-2">
            <GitFork className="h-4 w-4 text-brand" /> Feature impact —{" "}
            {MODEL_LABEL[withImportance?.name] || "model"}
          </h4>
          <div className="h-[220px] w-full rounded-2xl border border-black/[0.06] bg-black/[0.01] p-3">
            <FeatureImportanceChart data={featureImpact} />
          </div>
          <p className="text-[11px] text-faint">
            Permutation importance: the drop in ROC-AUC (×100) when each feature is
            shuffled on the held-out window.
          </p>
        </div>
      </div>

      {/* the comparison that actually matters */}
      <div className="rounded-2xl border border-black/[0.06] bg-black/[0.01] p-3.5">
        <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
          Versus the trivial baseline
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1.5">
          {entries
            .filter((e) => e.metrics?.roc_auc != null || e.metrics?.accuracy != null)
            .map((e) => {
              const { key, score, base } = compare(e);
              const good = base != null && score > base;
              return (
                <span key={e.name} className="text-[11.5px] text-muted">
                  <span className="font-medium text-ink">
                    {MODEL_LABEL[e.name] || e.name}
                  </span>{" "}
                  <span className="tabular" title={key.replace("_", "-").toUpperCase()}>
                    {score.toFixed(3)}
                  </span>
                  {base != null && (
                    <span className={good ? "text-emerald" : "text-crimson"}>
                      {" "}
                      vs {base.toFixed(3)}
                    </span>
                  )}
                </span>
              );
            })}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px] text-faint">
        <span>
          Trained{" "}
          {reg.generated_at ? new Date(reg.generated_at).toLocaleDateString() : "—"} ·{" "}
          {reg.warnings?.length ?? 0} warning
          {(reg.warnings?.length ?? 0) === 1 ? "" : "s"} recorded ·{" "}
          {reg.data_provenance?.source === "synthetic" ? "simulated corpus" : "live corpus"}
        </span>
      </div>
    </div>
  );
}

function SocioEconomic() {
  const [data, setData] = useState<any>(null);
  const [factor, setFactor] = useState("unemployment");
  const [activeView, setActiveView] = useState<"scatter" | "vulnerability" | "radar">("scatter");
  const [radarDistrict, setRadarDistrict] = useState<string>("");

  useEffect(() => {
    getJSON("/socioeconomic").then(setData).catch(() => {});
  }, []);

  if (!data) return <div className="h-64 animate-pulse rounded-xl bg-black/[0.03]" />;

  const points = (data.districts || []).map((d: any) => ({
    district: d.district,
    x: d[factor],
    y: d.crime_rate_per_100k,
    z: d.total_crimes,
  }));
  const xLabel = SOCIO_FACTORS.find((f) => f.key === factor)?.label || factor;

  // A district missing an indicator is skipped, not defaulted. Substituting a
  // plausible number for a missing one produces a score that looks measured and
  // is not.
  const VULN_WEIGHTS = { unemployment: 3.5, literacyGap: 2.5, urbanisation: 0.2, crimeRate: 0.25 };
  const vulnerabilityData = (data.districts || [])
    .filter((d: any) =>
      d.unemployment != null && d.literacy != null &&
      d.urbanization != null && d.crime_rate_per_100k != null)
    .map((d: any) => ({
      district: d.district,
      score: Math.min(100, Math.round(
        d.unemployment * VULN_WEIGHTS.unemployment +
        (100 - d.literacy) * VULN_WEIGHTS.literacyGap +
        d.urbanization * VULN_WEIGHTS.urbanisation +
        d.crime_rate_per_100k * VULN_WEIGHTS.crimeRate
      )),
      level: d.unemployment > 10 ? "Critical" : d.unemployment > 7 ? "High" : "Moderate",
    }));

  // Radar built from the real socio-economic indicators, min-max normalised across
  // the districts actually present so the five axes share a scale. The previous
  // version was five hardcoded pairs of invented numbers.
  const RADAR_AXES: { subject: string; get: (d: any) => number | null }[] = [
    { subject: "Unemployment", get: (d) => d.unemployment ?? null },
    { subject: "Youth share", get: (d) => d.youth_pct ?? null },
    { subject: "Urbanisation", get: (d) => d.urbanization ?? null },
    { subject: "Literacy gap", get: (d) => (d.literacy != null ? 100 - d.literacy : null) },
    { subject: "Crime / 100k", get: (d) => d.crime_rate_per_100k ?? null },
  ];
  const districts = data.districts || [];
  const focusDistrict =
    districts.find((d: any) => d.district === radarDistrict) ||
    [...districts].sort(
      (a: any, b: any) => (b.crime_rate_per_100k || 0) - (a.crime_rate_per_100k || 0)
    )[0];

  const radarData = RADAR_AXES.map(({ subject, get }) => {
    const values = districts.map(get).filter((v: number | null) => v != null) as number[];
    if (!values.length || !focusDistrict) return { subject, districtAvg: 0, stateAvg: 0 };
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const norm = (v: number) => (hi > lo ? Math.round(((v - lo) / (hi - lo)) * 100) : 50);
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const own = get(focusDistrict);
    return {
      subject,
      districtAvg: own != null ? norm(own) : 0,
      stateAvg: norm(mean),
    };
  });
  const topCorrelation = (data.correlations || [])[0];

  return (
    <div className="space-y-6">
      {/* Sub-view navigation tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/[0.06] pb-4">
        <div className="flex items-center gap-1.5 rounded-full border border-black/10 bg-black/[0.03] p-1">
          <button
            onClick={() => setActiveView("scatter")}
            className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors ${
              activeView === "scatter" ? "bg-azure text-white shadow-sm" : "text-muted hover:text-ink"
            }`}
          >
            Scatter Correlation
          </button>
          <button
            onClick={() => setActiveView("vulnerability")}
            className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors ${
              activeView === "vulnerability" ? "bg-azure text-white shadow-sm" : "text-muted hover:text-ink"
            }`}
          >
            Vulnerability Index
          </button>
          <button
            onClick={() => setActiveView("radar")}
            className={`rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors ${
              activeView === "radar" ? "bg-azure text-white shadow-sm" : "text-muted hover:text-ink"
            }`}
          >
            Sociological Radar
          </button>
        </div>
      </div>

      {activeView === "scatter" && (
        <div className="grid gap-6 lg:grid-cols-[1.15fr_1fr]">
          <div>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {SOCIO_FACTORS.map((f) => (
                <button
                  key={f.key}
                  onClick={() => setFactor(f.key)}
                  className={`rounded-full border px-3 py-1 text-[12px] transition-colors ${
                    factor === f.key
                      ? "border-azure/50 bg-azure/12 text-azure font-semibold"
                      : "border-black/10 text-muted hover:text-ink"
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="h-[300px] w-full">
              <SocioScatter points={points} xLabel={xLabel} yLabel="Crimes / 100k" />
            </div>
            <p className="mt-1 text-center text-[11px] text-faint">
              Each point is a district · bubble size = total cases
            </p>
          </div>

          <div className="space-y-4">
            <div>
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
                Strongest correlations
              </p>
              <div className="space-y-2">
                {(data.correlations || []).slice(0, 6).map((c: any, i: number) => {
                  const pos = c.r >= 0;
                  const mag = Math.min(1, Math.abs(c.r));
                  return (
                    <div key={i} className="flex items-center gap-3">
                      <span className="w-[46%] shrink-0 truncate text-[12px] text-ink/85">
                        {c.factor} <span className="text-faint">↔</span> {c.metric}
                      </span>
                      <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-black/[0.06]">
                        <div
                          className={`absolute top-0 h-full rounded-full ${pos ? "bg-crimson left-1/2" : "bg-azure right-1/2"}`}
                          style={{ width: `${mag * 50}%` }}
                        />
                        <div className="absolute left-1/2 top-0 h-full w-px bg-black/15" />
                      </div>
                      <span className={`tabular w-11 shrink-0 text-right text-[12px] font-bold ${pos ? "text-crimson" : "text-azure"}`}>
                        {c.r > 0 ? "+" : ""}{c.r}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>

            {data.insights?.length > 0 && (
              <div className="rounded-xl border border-azure/15 bg-azure/[0.04] p-4">
                <p className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-azure">
                  <Lightbulb className="h-3.5 w-3.5" /> Key findings
                </p>
                <ul className="space-y-1.5">
                  {data.insights.slice(0, 4).map((s: string, i: number) => (
                    <li key={i} className="text-[12.5px] leading-relaxed text-ink/85">• {s}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {activeView === "vulnerability" && (
        <div className="space-y-4">
          <div className="h-[340px] w-full">
            <SocioVulnerabilityBar data={vulnerabilityData} />
          </div>
          <p className="mx-auto max-w-2xl text-center text-[11.5px] text-faint">
            Composite score = unemployment×{VULN_WEIGHTS.unemployment} + literacy gap×{VULN_WEIGHTS.literacyGap}
            {" "}+ urbanisation×{VULN_WEIGHTS.urbanisation} + crime rate per 100k×{VULN_WEIGHTS.crimeRate}, capped at 100.
            These weights are a stated convention, not a fitted model — the components are shown
            so the number can be checked. Districts missing any component are omitted rather than
            filled with a default.
          </p>
        </div>
      )}

      {activeView === "radar" && (
        <div className="grid gap-6 md:grid-cols-2 items-center">
          <div className="space-y-3">
            <select
              className="rounded-full border border-black/10 bg-white px-3.5 py-2 text-[13px] text-ink focus:border-brand/40 focus:outline-none"
              value={focusDistrict?.district || ""}
              onChange={(e) => setRadarDistrict(e.target.value)}
            >
              {districts.map((d: any) => (
                <option key={d.district} value={d.district}>{d.district}</option>
              ))}
            </select>
            <div className="h-[300px] w-full">
              <SocioRadarChart data={radarData} />
            </div>
          </div>
          <div className="space-y-3 p-4 rounded-2xl border border-black/[0.06] bg-black/[0.02]">
            <h4 className="font-bold text-[14px] text-ink flex items-center gap-2">
              <Scale className="h-4 w-4 text-azure" /> {focusDistrict?.district || "District"} vs state
            </h4>
            {topCorrelation ? (
              <p className="text-[12.5px] text-muted leading-relaxed">
                Strongest measured relationship across the {districts.length} districts:{" "}
                <span className="font-semibold text-ink">{topCorrelation.metric}</span>{" "}
                vs <span className="font-semibold text-ink">{topCorrelation.factor}</span>,
                Pearson r = {topCorrelation.r > 0 ? "+" : ""}{topCorrelation.r} ({topCorrelation.direction}).
              </p>
            ) : (
              <p className="text-[12.5px] text-muted">No correlations available.</p>
            )}
            <div className="space-y-2 pt-2 border-t border-black/[0.06]">
              {RADAR_AXES.map(({ subject, get }) => {
                const own = focusDistrict ? get(focusDistrict) : null;
                const vals = districts.map(get).filter((v: number | null) => v != null) as number[];
                const mean = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
                if (own == null || mean == null) return null;
                return (
                  <div key={subject} className="flex justify-between text-[12px]">
                    <span className="text-faint">{subject}:</span>
                    <span className="font-semibold text-ink tabular">
                      {own.toFixed(1)}{" "}
                      <span className={own > mean ? "text-crimson" : "text-emerald"}>
                        ({own > mean ? "+" : ""}{(own - mean).toFixed(1)} vs state mean {mean.toFixed(1)})
                      </span>
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="pt-2 text-[11px] text-faint leading-relaxed">
              Radar axes are min-max normalised to 0-100 across the {districts.length} districts
              so five different units share one scale. The figures above are the raw values.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function RecidivismPanel({ data }: { data: any }) {
  if (!data) return <div className="h-48 animate-pulse rounded-xl bg-black/[0.03]" />;
  const top = data.top || [];
  const maxIdx = 100;
  const bandTone: Record<string, string> = {
    Extreme: "text-crimson",
    High: "text-brand",
    Elevated: "text-azure",
    Moderate: "text-muted",
  };
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {Object.entries(data.bands || {}).map(([b, n]: any) => (
          <span key={b} className="rounded-full border border-black/10 bg-black/[0.02] px-2.5 py-1 text-[11.5px]">
            <span className={`font-semibold ${bandTone[b]}`}>{b}</span>{" "}
            <span className="tabular text-muted">{n}</span>
          </span>
        ))}
      </div>
      <div className="space-y-1.5">
        {top.slice(0, 8).map((o: any) => (
          <Link prefetch={false}
            key={o.criminal_id}
            href={`/suspect?id=${o.criminal_id}`}
            className="group flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-black/[0.03]"
          >
            <span className="w-24 shrink-0 truncate text-[12.5px] font-medium text-ink group-hover:text-brand">
              {o.name}
            </span>
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-black/[0.05]">
              <div
                className="h-full rounded-full bg-gradient-to-r from-brand-deep via-brand to-crimson"
                style={{ width: `${(o.risk_index / maxIdx) * 100}%` }}
              />
            </div>
            <span className="tabular w-9 shrink-0 text-right text-[12.5px] font-bold text-ink">{o.risk_index}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

const EXAMPLES = [
  "Two riders on a motorcycle snatched a woman's gold chain near the market and sped off without a plate.",
  "Caller posed as a bank officer, made the victim install a screen-share app and drained ₹4 lakh via UPI.",
  "Burglars broke the rear window grille at night and took cash and jewellery while the family slept.",
];

function ClassifierDemo({ metrics }: { metrics: any }) {
  const [text, setText] = useState(EXAMPLES[0]);
  const [preds, setPreds] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);

  const classify = async (t: string) => {
    if (!t.trim()) return;
    setBusy(true);
    try {
      const res = await apiFetch("/classify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: t }),
      });
      const d = await res.json();
      setPreds(d.predictions || []);
    } catch {
      setPreds([]);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    classify(EXAMPLES[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const maxP = useMemo(() => Math.max(0.01, ...preds.map((p) => p.prob)), [preds]);

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map((e, i) => (
          <button
            key={i}
            onClick={() => {
              setText(e);
              classify(e);
            }}
            className="rounded-full border border-black/10 bg-black/[0.02] px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-emerald/40 hover:text-ink"
          >
            Example {i + 1}
          </button>
        ))}
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        className="w-full resize-none rounded-xl border border-black/10 bg-black/[0.02] p-3 text-[13px] text-ink placeholder:text-faint focus:border-emerald/40 focus:outline-none"
        placeholder="Paste or type an FIR narrative…"
      />
      <button
        onClick={() => classify(text)}
        disabled={busy || !text.trim()}
        className="mt-2 inline-flex items-center gap-2 rounded-full bg-gradient-to-b from-emerald/90 to-emerald px-4 py-2 text-[12.5px] font-semibold text-black transition-all hover:brightness-105 active:scale-95 disabled:opacity-40"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
        Classify
      </button>

      {preds.length > 0 && (
        <div className="mt-4 space-y-2">
          {preds.map((p, i) => (
            <div key={p.label} className="flex items-center gap-3">
              <span className={`w-40 shrink-0 truncate text-[12.5px] ${i === 0 ? "font-bold text-ink" : "text-muted"}`}>
                {p.label}
              </span>
              <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-black/[0.05]">
                <div
                  className={`h-full rounded-full ${i === 0 ? "bg-emerald" : "bg-black/20"}`}
                  style={{ width: `${(p.prob / maxP) * 100}%` }}
                />
              </div>
              <span className="tabular w-11 shrink-0 text-right text-[12px] font-semibold text-ink">
                {Math.round(p.prob * 100)}%
              </span>
            </div>
          ))}
          {/* Explainable AI keywords for the top prediction */}
          {metrics?.class_keywords?.[preds[0].label] && (
            <div className="mt-2.5 rounded-xl bg-emerald/[0.04] p-3 border border-emerald/10">
              <p className="text-[10px] font-bold uppercase tracking-wider text-emerald mb-1.5 flex items-center gap-1.5">
                <Brain className="h-3 w-3" /> Predictive TF-IDF Keywords
              </p>
              <div className="flex flex-wrap gap-1">
                {metrics.class_keywords[preds[0].label].map((w: string) => (
                  <span key={w} className="rounded bg-emerald/12 px-2 py-0.5 text-[11px] font-medium text-emerald">
                    {w}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {metrics?.accuracy != null && (
        <p className="mt-3 text-[11px] text-faint">
          Held-out accuracy <span className="font-semibold text-emerald">{Math.round(metrics.accuracy * 100)}%</span> ·
          macro-F1 {metrics.f1_macro} · {metrics.n_test} test FIRs · {metrics.classes?.length} classes
        </p>
      )}
    </div>
  );
}
