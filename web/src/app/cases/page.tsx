"use client";

import { useEffect, useState, useMemo, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { motion } from "framer-motion";
import {
  Search,
  AlertCircle,
  FileCheck2,
  Loader2,
  ArrowRight,
  Folders,
  HeartPulse,
  SlidersHorizontal,
  FilePlus2,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import AccessRequired from "@/components/AccessRequired";
import ExportButton from "@/components/ExportButton";
import { useIdentity } from "@/lib/session";

const STATUSES = ["All", "Open", "Investigating", "Closed"];
const SORTS = [
  { id: "recent", label: "Most recent" },
  { id: "severity", label: "Highest severity" },
  { id: "casualties", label: "Most casualties" },
];

function CasesContent() {
  const router = useRouter();
  const { identity } = useIdentity();
  const canRegister = !!identity?.permissions?.includes("register_fir");
  const searchParams = useSearchParams();

  const [filters, setFilters] = useState({
    status: "All",
    district: "",
    crime_type: "",
    time_of_day: "",
    min_severity: 0,
    casualties: false,
    sort: "recent",
    q: "",
  });
  const [data, setData] = useState<any>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);

  // Check for direct case ID redirect in search params (?id=... or ?case_id=...)
  useEffect(() => {
    const directId = searchParams.get("id") || searchParams.get("case_id");
    if (directId) {
      router.replace(`/case?id=${encodeURIComponent(directId)}`);
    }
  }, [searchParams, router]);

  const set = (patch: Partial<typeof filters>) => setFilters((f) => ({ ...f, ...patch }));

  // Initialize filters from URL query parameters
  useEffect(() => {
    const ct = searchParams.get("crime_type");
    const d = searchParams.get("district");
    const st = searchParams.get("status");
    const q = searchParams.get("q");
    const sort = searchParams.get("sort");
    // A navigation (the assistant's or a link's) describes the whole filter set. Merging
    // it into whatever was set before kept a stale district from an earlier visit, so
    // "chain snatching in Bengaluru" could open filtered to somewhere else.
    if (!(ct || d || st || q || sort)) return;
    const patch: Partial<typeof filters> = { crime_type: ct || "", district: d || "", q: q || "" };
    if (st) patch.status = st.charAt(0).toUpperCase() + st.slice(1).toLowerCase();
    if (sort) (patch as any).sort = sort;
    setFilters((f) => ({ ...f, ...patch }));
  }, [searchParams]);

  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      setLoadError(null);
      setForbidden(false);
      const qs = new URLSearchParams();
      if (filters.status !== "All") qs.set("status", filters.status);
      if (filters.district) qs.set("district", filters.district);
      if (filters.crime_type) qs.set("crime_type", filters.crime_type);
      if (filters.time_of_day) qs.set("time_of_day", filters.time_of_day);
      if (filters.min_severity) qs.set("min_severity", String(filters.min_severity));
      if (filters.casualties) qs.set("casualties", "true");
      qs.set("sort", filters.sort);
      if (filters.q.trim()) qs.set("q", filters.q.trim());

      apiFetch(`/cases?${qs}`)
        .then((r) => r.json())
        .then(setData)
        .catch((e: any) => {
          setForbidden(Boolean(e?.isForbidden));
          // Surface the failure. The previous version substituted eight invented
          // FIRs here and then ran the user's filters over them, so a dead backend
          // was indistinguishable from a live registry.
          setData(null);
          setLoadError(String(e?.message || e));
        })
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [filters]);

  // Every matching case was rendered at once. On a phone that is one card per
  // screen and a 45,880px page — about fifty-five phone-heights of scrolling, with
  // no way to reach the filters again without swiping all the way back. Rendering a
  // window and letting the reader ask for more keeps every case reachable without
  // making the page unusable to get through.
  const PAGE = 24;
  const [shown, setShown] = useState(PAGE);
  const rawCases = data?.cases || [];
  // A new filter produces a different result set, so the window has to start over —
  // otherwise a narrower filter silently keeps showing the old, larger count.
  useEffect(() => setShown(PAGE), [filters, rawCases.length]);

  const cases = useMemo(() => {
    let result = [...rawCases];
    if (filters.status !== "All") {
      const target = filters.status.toLowerCase().trim();
      result = result.filter((c: any) => {
        const st = (c.status || "").toLowerCase().trim();
        if (target === "open" || target === "investigating") {
          return !st.includes("closed") && !st.includes("disposed") && !st.includes("convicted");
        }
        if (target === "closed") {
          return st.includes("closed") || st.includes("disposed") || st.includes("convicted") || st.includes("acquitted");
        }
        return st.includes(target);
      });
    }
    return result;
  }, [rawCases, filters.status]);
  // Filter options come from the corpus, never from a hardcoded list. The previous
  // defaults offered commissionerates as districts and crime types the 31-district
  // corpus does not contain, so the filters advertised jurisdictions that cannot
  // return a result. With no data loaded the selects collapse to their "All" option.
  const options = useMemo(
    () => ({
      districts: data?.districts || [],
      crime_types: data?.crime_types || [],
      time_bands: data?.time_bands || [],
    }),
    [data]
  );

  const selectCls =
    "rounded-full border border-black/10 bg-white px-3.5 py-2 text-[13px] text-ink focus:border-brand/40 focus:outline-none";

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
            <Folders className="h-8 w-8 text-brand" strokeWidth={2} /> Case Directory
          </h1>
          <p className="mt-2 text-[15px] text-muted">
            Filter {data ? `${(data.total ?? cases.length).toLocaleString()} ` : ""}FIRs by status, district,
            crime type, severity and casualties.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canRegister && (
            <Link
              prefetch={false}
              href="/fir/new"
              className="inline-flex items-center gap-1.5 rounded-xl bg-brand px-3.5 py-2 text-[13px] font-semibold text-white shadow-sm transition-opacity hover:opacity-90"
            >
              <FilePlus2 className="h-4 w-4" /> Register FIR
            </Link>
          )}
          <ExportButton
            rows={cases}
            columns={[
              { key: "case_id", label: "FIR / Crime No" },
              { key: "crime_type", label: "Crime type" },
              { key: "district", label: "District" },
              { key: "police_station", label: "Station" },
              { key: "status", label: "Status" },
              { key: "severity_score", label: "Severity" },
              { key: "timestamp", label: "Incident date" },
              { key: "ipc_bns_sections", label: "Sections" },
            ]}
            meta={{
              title: "Case registry extract",
              scope: [
                filters.status !== "All" ? `status=${filters.status}` : null,
                filters.district ? `district=${filters.district}` : null,
                filters.crime_type ? `crime=${filters.crime_type}` : null,
                filters.q.trim() ? `search="${filters.q.trim()}"` : null,
              ].filter(Boolean).join(", ") || "no filters",
              simulated: true,
            }}
          />
          <span className="rounded-full border border-brand/20 bg-brand/10 px-3 py-1.5 text-xs font-bold text-brand">
            Live SCRB Corpus
          </span>
        </div>
      </header>

      {/* Filter bar */}
      <div className="bezel">
        <div className="bezel-core space-y-4 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-1 rounded-full border border-black/10 bg-black/[0.03] p-1">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  onClick={() => set({ status: s })}
                  className={`relative rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors ${
                    filters.status === s ? "text-black" : "text-muted hover:text-ink"
                  }`}
                >
                  {filters.status === s && (
                    <motion.span
                      layoutId="case-status"
                      transition={{ type: "spring", stiffness: 380, damping: 32 }}
                      className="absolute inset-0 -z-10 rounded-full bg-brand"
                    />
                  )}
                  {s}
                </button>
              ))}
            </div>
            <div className="relative min-w-[220px] flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
              <input
                value={filters.q}
                onChange={(e) => set({ q: e.target.value })}
                placeholder="Search FIR number, crime type, district, section..."
                className="w-full rounded-full border border-black/10 bg-black/[0.03] py-2 pl-10 pr-4 text-[13px] text-ink placeholder:text-faint focus:border-brand/40 focus:outline-none"
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <SlidersHorizontal className="h-4 w-4 text-faint" />
            <select className={selectCls} value={filters.district} onChange={(e) => set({ district: e.target.value })}>
              <option value="">All districts</option>
              {options.districts.map((d: string) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <select className={selectCls} value={filters.crime_type} onChange={(e) => set({ crime_type: e.target.value })}>
              <option value="">All crime types</option>
              {options.crime_types.map((c: string) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
            <select className={selectCls} value={filters.time_of_day} onChange={(e) => set({ time_of_day: e.target.value })}>
              <option value="">Any time</option>
              {options.time_bands.map((t: string) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>

            <label className="flex items-center gap-2 rounded-full border border-black/10 bg-black/[0.03] px-3 py-2 text-[13px] text-muted">
              Min severity
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={filters.min_severity}
                onChange={(e) => set({ min_severity: Number(e.target.value) })}
                className="accent-brand"
              />
              <span className="tabular w-7 font-semibold text-ink">{filters.min_severity}</span>
            </label>

            <button
              onClick={() => set({ casualties: !filters.casualties })}
              className={`flex items-center gap-1.5 rounded-full border px-3.5 py-2 text-[13px] font-medium transition-all ${
                filters.casualties
                  ? "border-crimson/50 bg-crimson/15 text-crimson"
                  : "border-black/10 bg-black/[0.03] text-muted hover:text-ink"
              }`}
            >
              <HeartPulse className="h-4 w-4" /> With casualties
            </button>

            <select className={`${selectCls} ml-auto`} value={filters.sort} onChange={(e) => set({ sort: e.target.value })}>
              {SORTS.map((s) => (
                <option key={s.id} value={s.id}>Sort: {s.label}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Results */}
      {loading ? (
        <div className="flex h-64 flex-col items-center justify-center">
          <Loader2 className="mb-3 h-7 w-7 animate-spin text-brand" />
          <p className="text-sm text-muted">Querying case database…</p>
        </div>
      ) : forbidden ? (
        <AccessRequired what="The case registry" />
      ) : loadError ? (
        <div className="flex h-64 flex-col items-center justify-center rounded-2xl border-2 border-dashed border-crimson/25 px-6 text-center">
          <AlertCircle className="mb-3 h-10 w-10 text-crimson" />
          <h3 className="text-lg font-bold text-ink">Case registry unavailable</h3>
          <p className="mt-1 max-w-md text-sm text-muted">
            The intelligence core did not answer. No cases are shown, because showing
            anything here would mean showing records that were not retrieved.
          </p>
          <code className="mt-3 rounded bg-black/[0.04] px-2.5 py-1 font-mono text-[11px] text-faint">
            {loadError}
          </code>
        </div>
      ) : cases.length === 0 ? (
        <div className="flex h-64 flex-col items-center justify-center rounded-2xl border-2 border-dashed border-black/10">
          <Search className="mb-3 h-10 w-10 text-faint" />
          <h3 className="text-lg font-bold text-ink">No matching cases</h3>
          <p className="mt-1 text-sm text-muted">Loosen the filters and try again.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {cases.slice(0, shown).map((c: any, idx: number) => (
            <motion.div
              key={c.case_id}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35, delay: Math.min(idx * 0.015, 0.25) }}
            >
              <Link prefetch={false}
                href={`/case?id=${encodeURIComponent(c.case_id)}`}
                className="group block rounded-2xl border border-black/[0.07] bg-black/[0.02] p-5 transition-all hover:border-brand/30 hover:bg-black/[0.04]"
              >
                <div className="mb-4 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    {/* CrimeNo and case_id are the same value in this corpus (checked: identical
                        on 200/200 rows), so this printed the same 18-digit number twice with a
                        dot between it — two wrapped lines of noise at the top of every card on a
                        phone. Shown only when they actually differ. */}
                    <span className="break-all text-[12px] font-bold tracking-wide text-brand">
                      {c.case_id}
                      {c.CrimeNo && String(c.CrimeNo) !== String(c.case_id) ? ` · ${c.CrimeNo}` : ""}
                    </span>
                    <h3 className="mt-1 truncate font-bold text-ink">{c.crime_type}</h3>
                  </div>
                  {c.status === "Closed" ? (
                    <span className="flex items-center gap-1 rounded-md border border-emerald/20 bg-emerald/10 px-2 py-1 text-[10px] font-bold text-emerald">
                      <FileCheck2 className="h-3 w-3" /> CLOSED
                    </span>
                  ) : c.status === "Investigating" ? (
                    <span className="rounded-md border border-azure/20 bg-azure/10 px-2 py-1 text-[10px] font-bold text-azure">
                      INVESTIGATING
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 rounded-md border border-brand/20 bg-brand/10 px-2 py-1 text-[10px] font-bold text-brand">
                      <AlertCircle className="h-3 w-3" /> OPEN
                    </span>
                  )}
                </div>

                <div className="space-y-2 text-[13px]">
                  <div className="flex justify-between">
                    <span className="text-faint">District</span>
                    <span className="truncate pl-2 text-ink/85 font-medium">{c.district}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-faint">Severity Score</span>
                    <span className={`tabular font-semibold ${c.severity_score > 70 ? "text-crimson" : c.severity_score > 40 ? "text-brand" : "text-emerald"}`}>
                      {c.severity_score}/100
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-faint">Casualties</span>
                    <span className={`tabular font-semibold ${c.casualties > 0 ? "text-crimson" : "text-muted"}`}>
                      {c.fatalities ?? 0} dead · {c.injured ?? 0} injured
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-faint">Filing Date</span>
                    <span className="text-muted">{c.timestamp ? new Date(c.timestamp).toLocaleDateString() : "—"}</span>
                  </div>
                </div>

                <div className="mt-4 flex items-center justify-between pt-3 border-t border-black/[0.05] text-[12.5px] font-medium text-muted transition-colors group-hover:text-ink">
                  <span>Open dossier</span>
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1 text-brand" />
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}

      {cases.length > shown && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => setShown((n) => n + PAGE)}
            className="min-h-[44px] rounded-full border border-black/10 bg-black/[0.03] px-6 text-[13px] font-medium text-ink transition-colors hover:border-brand/40 hover:bg-black/[0.06] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            Show {Math.min(PAGE, cases.length - shown)} more
            <span className="ml-2 text-faint">({cases.length - shown} remaining)</span>
          </button>
        </div>
      )}

      {/* Counts stay honest about all three numbers: what is on screen, how many
          matched the filters, and how many exist. Reporting only the rendered count
          would make a windowed list look like a complete result. */}
      <p className="text-center text-[12px] text-faint">
        Showing {Math.min(shown, cases.length).toLocaleString()} of{" "}
        {cases.length.toLocaleString()} matching
        {data?.total > cases.length
          ? ` — ${data.total.toLocaleString()} in the corpus; refine filters to narrow.`
          : "."}
      </p>
    </div>
  );
}

export default function CasesDirectory() {
  return (
    <Suspense fallback={
      <div className="flex h-[60vh] flex-col items-center justify-center">
        <Loader2 className="mb-3 h-8 w-8 animate-spin text-brand" />
        <p className="text-sm text-muted">Loading Case Directory…</p>
      </div>
    }>
      <CasesContent />
    </Suspense>
  );
}
