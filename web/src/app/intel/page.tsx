"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { Boxes, Car, UserSearch, ShieldAlert, Gavel, Search, Network, AlertTriangle, ArrowRight } from "lucide-react";
import { getJSON } from "@/lib/api";

const TABS = [
  { id: "vehicles", label: "Stolen Vehicles", icon: Car },
  { id: "missing", label: "Missing Persons", icon: UserSearch },
  { id: "cybercrime", label: "Cyber & Fraud Rings", icon: Network },
  { id: "cctv", label: "CCTV Logs", icon: Search },
  { id: "complaints", label: "Public Complaints", icon: AlertTriangle },
  { id: "criminals", label: "Offender Registry", icon: Boxes },
  { id: "officers", label: "Officers", icon: ShieldAlert },
  { id: "courts", label: "Courts", icon: Gavel },
];

const COLUMNS: Record<string, { key: string; label: string; badge?: boolean; link?: "case" | "suspect" }[]> = {
  vehicles: [
    { key: "vehicle_number", label: "Vehicle" },
    { key: "make_model", label: "Make / Model" },
    { key: "owner_name", label: "Owner" },
    { key: "theft_district", label: "District" },
    { key: "theft_date", label: "Stolen" },
    { key: "recovery_status", label: "Status", badge: true },
    { key: "linked_case_id", label: "FIR", link: "case" },
  ],
  missing: [
    { key: "person_id", label: "ID" },
    { key: "name", label: "Name" },
    { key: "age", label: "Age" },
    { key: "gender", label: "Sex" },
    { key: "last_seen_district", label: "Last Seen" },
    { key: "date_missing", label: "Since" },
    { key: "found_status", label: "Status", badge: true },
  ],
  cctv: [
    { key: "camera_id", label: "Camera ID" },
    { key: "district", label: "District" },
    { key: "timestamp", label: "Timestamp" },
    { key: "detected_faces", label: "Facial Recognition Hits" },
    { key: "detected_vehicle", label: "ANPR Plate Match" },
    { key: "case_link", label: "FIR", link: "case" },
  ],
  complaints: [
    { key: "complaint_id", label: "Complaint ID" },
    { key: "caller_phone", label: "Caller Phone" },
    { key: "district", label: "District" },
    { key: "emergency_type", label: "Emergency Type" },
    { key: "response_time_min", label: "Response (Min)" },
    { key: "assigned_officer", label: "Assigned Officer" },
    { key: "resolution_status", label: "Status", badge: true },
    { key: "linked_case_id", label: "FIR", link: "case" },
  ],
  criminals: [
    { key: "PersonID", label: "Person ID", link: "suspect" },
    { key: "name", label: "Offender Profile" },
    { key: "linked_crime_types", label: "Crime Involvement" },
    { key: "total_cases", label: "Priors & Repeat Status" },
    { key: "gang_affiliation", label: "Gang / Syndicate" },
    { key: "risk_level", label: "Risk Level", badge: true },
    { key: "active_warrant", label: "Active Warrant" },
    { key: "rowdy_sheeter", label: "Rowdy Sheeter" },
  ],
  officers: [
    { key: "officer_id", label: "ID" },
    { key: "name", label: "Name" },
    { key: "rank", label: "Rank" },
    { key: "station", label: "Station" },
    { key: "specialization", label: "Specialization" },
    { key: "cases_assigned", label: "Cases" },
    { key: "closure_rate", label: "Closure" },
  ],
  courts: [
    { key: "court_case_no", label: "Case No." },
    { key: "fir_number", label: "FIR", link: "case" },
    { key: "court_name", label: "Court" },
    { key: "chargesheet_status", label: "Chargesheet", badge: true },
    { key: "conviction_status", label: "Verdict", badge: true },
    { key: "next_hearing", label: "Next Hearing" },
  ],
};

const badgeTone = (v: string) => {
  const s = String(v).toLowerCase();
  if (["critical"].some((x) => s.includes(x))) return "bg-crimson/15 text-crimson border-crimson/30 font-bold";
  if (["high"].some((x) => s.includes(x))) return "bg-brand/15 text-brand border-brand/30 font-bold";
  if (["recovered", "found", "convicted", "filed", "resolved"].some((x) => s.includes(x)))
    return "bg-emerald/12 text-emerald border-emerald/25";
  if (["not recovered", "missing", "pending", "not filed"].some((x) => s.includes(x)))
    return "bg-crimson/12 text-crimson border-crimson/25";
  if (["acquitted"].some((x) => s.includes(x))) return "bg-azure/12 text-azure border-azure/25";
  if (["deceased"].some((x) => s.includes(x))) return "bg-black/8 text-muted border-black/15";
  return "bg-brand/12 text-brand border-brand/25";
};

function CyberRings() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    getJSON("/cyber/rings").then(setData).catch(() => {});
  }, []);
  if (!data) return <div className="h-40 animate-pulse rounded-2xl bg-black/[0.03]" />;
  const fmt = (n: number) => "₹" + (n ?? 0).toLocaleString("en-IN");
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          { l: "Cyber cases", v: data.cases?.toLocaleString() },
          { l: "Total fraud", v: fmt(data.total_fraud) },
          { l: "Victims", v: data.victims?.toLocaleString() },
          { l: "Rings detected", v: data.rings?.length },
        ].map((s) => (
          <div key={s.l} className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
            <p className="text-display tabular text-2xl font-bold text-ink">{s.v ?? "—"}</p>
            <p className="text-[11px] uppercase tracking-wider text-faint">{s.l}</p>
          </div>
        ))}
      </div>
      <div className="space-y-2">
        {(data.rings || []).map((r: any, i: number) => (
          <div key={i} className="rounded-xl border border-crimson/15 bg-crimson/[0.04] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-crimson" />
                <span className="font-mono text-[13px] font-semibold text-ink">{r.identifier}</span>
              </div>
              <div className="tabular flex gap-4 text-[12.5px]">
                <span className="text-crimson font-bold">{r.linked_cases} cases linked</span>
                <span className="text-brand">{fmt(r.total_fraud)}</span>
              </div>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(r.cases || []).map((c: string) => (
                <Link prefetch={false}
                  key={c}
                  href={`/case?id=${c}`}
                  className="rounded-md border border-black/10 bg-black/[0.03] px-2 py-0.5 font-mono text-[11px] text-muted transition-colors hover:border-brand/40 hover:text-ink"
                >
                  {c}
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function IntelInner() {
  const params = useSearchParams();
  const router = useRouter();
  const [tab, setTab] = useState(params.get("tab") || "vehicles");
  const [data, setData] = useState<any>(null);
  const [q, setQ] = useState(params.get("q") || "");
  const [repeatOnly, setRepeatOnly] = useState(params.get("repeat_only") === "true" || params.get("repeat") === "true");
  const [crimeTypeFilter, setCrimeTypeFilter] = useState(params.get("crime_type") || params.get("crime") || "");

  useEffect(() => {
    if (tab === "cybercrime") return;
    setData(null);
    const qs = new URLSearchParams();
    if (q) qs.set("q", q);
    if (repeatOnly) qs.set("repeat_only", "true");
    if (crimeTypeFilter) qs.set("crime_type", crimeTypeFilter);

    getJSON(`/registry/${tab}?${qs.toString()}`).then(setData).catch(() => setData({ rows: [] }));
  }, [tab, repeatOnly, crimeTypeFilter]);

  const cols = COLUMNS[tab] || [];
  const rows = useMemo(() => {
    let r = data?.rows || [];
    if (tab === "criminals") {
      if (repeatOnly) {
        r = r.filter(
          (row: any) =>
            row.is_repeat_offender ||
            (row.total_cases && Number(row.total_cases) >= 2) ||
            String(row.rowdy_sheeter).toLowerCase() === "true" ||
            String(row.history_sheet).toLowerCase() === "true" ||
            (row.past_fir_count && Number(row.past_fir_count) >= 2)
        );
      }
      if (crimeTypeFilter) {
        const ct = crimeTypeFilter.toLowerCase().trim();
        r = r.filter((row: any) => {
          if (ct.includes("homicide") || ct.includes("murder")) {
            return (
              row.homicide_involved ||
              (row.linked_crime_types &&
                row.linked_crime_types.some(
                  (x: string) => x.toLowerCase().includes("homicide") || x.toLowerCase().includes("murder")
                )) ||
              (row.gang_affiliation && row.gang_affiliation.toLowerCase().includes("homicide")) ||
              (row.gang_affiliation && row.gang_affiliation.toLowerCase().includes("murder"))
            );
          }
          return (
            (row.linked_crime_types && row.linked_crime_types.some((x: string) => x.toLowerCase().includes(ct))) ||
            (row.gang_affiliation && row.gang_affiliation.toLowerCase().includes(ct))
          );
        });
      }
    }
    if (!q.trim()) return r;
    const t = q.toLowerCase().trim();
    return r.filter((row: any) => Object.values(row).some((v) => String(v).toLowerCase().includes(t)));
  }, [data, q, tab, repeatOnly, crimeTypeFilter]);

  const setActive = (id: string) => {
    setTab(id);
    setQ("");
    router.replace(`/intel?tab=${id}`);
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <Boxes className="h-8 w-8 text-brand" strokeWidth={2} /> Intelligence Registries
        </h1>
        <p className="mt-2 text-[15px] text-muted">
          CCTNS-style cross-entity records — vehicles, missing persons, cyber trails, offender profiles and courts —
          all linked into the intelligence core.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {TABS.map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setActive(t.id)}
              className={`group relative flex items-center gap-2 rounded-full px-4 py-2 text-[13px] font-medium transition-colors ${
                active ? "text-black font-semibold" : "text-muted hover:text-ink"
              }`}
            >
              {active && (
                <motion.span
                  layoutId="intel-tab"
                  transition={{ type: "spring", stiffness: 380, damping: 32 }}
                  className="absolute inset-0 -z-10 rounded-full bg-gradient-to-b from-brand-soft to-brand"
                />
              )}
              <t.icon className="h-4 w-4" />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "cybercrime" ? (
        <CyberRings />
      ) : (
        <>
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="relative max-w-md flex-1">
              <Search className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder={`Search ${tab} (e.g. name, alias, gang, ID)…`}
                className="w-full rounded-full border border-black/10 bg-black/[0.03] py-2.5 pl-10 pr-4 text-sm text-ink placeholder:text-faint focus:border-brand/40 focus:outline-none"
              />
            </div>

            {tab === "criminals" && (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setRepeatOnly(!repeatOnly)}
                  className={`flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs font-bold transition-all ${
                    repeatOnly
                      ? "border-crimson/40 bg-crimson/15 text-crimson shadow-sm"
                      : "border-black/10 bg-black/[0.03] text-muted hover:border-black/20 hover:text-ink"
                  }`}
                >
                  <ShieldAlert className="h-3.5 w-3.5" />
                  Repeat Offenders Only
                </button>
                <select
                  value={crimeTypeFilter}
                  onChange={(e) => setCrimeTypeFilter(e.target.value)}
                  className="rounded-full border border-black/10 bg-white px-3.5 py-1.5 text-xs font-semibold text-ink focus:border-brand/40 focus:outline-none"
                >
                  <option value="">All Crime Categories</option>
                  <option value="Homicide">🩸 Homicide / Murder</option>
                  <option value="Cyber">💳 Cyber & Financial Fraud</option>
                  <option value="Robbery">🔫 Armed Robbery</option>
                  <option value="Extortion">🗡️ Extortion & Syndicate</option>
                  <option value="Vehicle">🏎️ Motor Vehicle Theft</option>
                  <option value="Narcotics">💊 Narcotics Trafficking</option>
                  <option value="Assault">🥊 Aggravated Assault</option>
                </select>
              </div>
            )}
          </div>

          <div className="bezel">
            <div className="bezel-core overflow-hidden p-0">
              {!data ? (
                <div className="space-y-2 p-4">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="h-10 animate-pulse rounded-lg bg-black/[0.03]" />
                  ))}
                </div>
              ) : rows.length === 0 ? (
                <div className="p-12 text-center text-muted">
                  No records match the selected filters.
                  {(repeatOnly || crimeTypeFilter) && (
                    <button
                      onClick={() => {
                        setRepeatOnly(false);
                        setCrimeTypeFilter("");
                        setQ("");
                      }}
                      className="ml-2 font-semibold text-brand hover:underline"
                    >
                      Clear filters
                    </button>
                  )}
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-[13px]">
                    <thead>
                      <tr className="border-b border-black/[0.07] text-[11px] uppercase tracking-wider text-faint">
                        {cols.map((c) => (
                          <th scope="col" key={c.key} className="px-4 py-3 font-semibold">{c.label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.slice(0, 150).map((row: any, i: number) => {
                        const sId = row.PersonID || row.criminal_id || row.suspect_id;
                        return (
                          <tr
                            key={i}
                            onClick={(e) => {
                              if (tab === "criminals" && sId) {
                                const target = e.target as HTMLElement;
                                if (!target.closest("a") && !target.closest("button")) {
                                  router.push(`/suspect?id=${sId}`);
                                }
                              }
                            }}
                            className={`border-b border-black/[0.04] transition-colors ${
                              tab === "criminals" ? "cursor-pointer hover:bg-brand/[0.05]" : "hover:bg-black/[0.02]"
                            }`}
                          >
                            {cols.map((c) => {
                              const val = row[c.key];
                              return (
                                <td key={c.key} className="px-4 py-2.5 text-ink/85">
                                  {c.badge ? (
                                    <span className={`rounded-md border px-2 py-0.5 text-[11px] font-semibold ${badgeTone(val)}`}>
                                      {val}
                                    </span>
                                  ) : c.link === "case" && val ? (
                                    <Link prefetch={false} href={`/case?id=${val}`} className="font-mono text-brand hover:underline">
                                      {val}
                                    </Link>
                                  ) : c.link === "suspect" && val ? (
                                    <Link prefetch={false} href={`/suspect?id=${val}`} className="font-mono text-crimson hover:underline font-bold">
                                      {val}
                                    </Link>
                                  ) : c.key === "name" ? (
                                    tab === "criminals" && sId ? (
                                      <Link prefetch={false} href={`/suspect?id=${sId}`} className="group flex flex-col">
                                        <span className="font-semibold text-ink transition-colors group-hover:text-crimson group-hover:underline flex items-center gap-1">
                                          {row.name || "—"}
                                          <ArrowRight className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100 text-crimson" />
                                        </span>
                                        {row.alias && <span className="text-[11px] text-faint font-mono">Alias: "{row.alias}"</span>}
                                      </Link>
                                    ) : (
                                      <div className="flex flex-col">
                                        <span className="font-semibold text-ink">{row.name || "—"}</span>
                                        {row.alias && <span className="text-[11px] text-faint font-mono">Alias: "{row.alias}"</span>}
                                      </div>
                                    )
                                  ) : c.key === "linked_crime_types" ? (
                                    <div className="flex flex-wrap gap-1">
                                      {row.homicide_involved && (
                                        <span className="rounded bg-crimson/15 border border-crimson/30 px-2 py-0.5 font-bold text-[10.5px] text-crimson">
                                          🩸 Homicide
                                        </span>
                                      )}
                                      {Array.isArray(row.linked_crime_types) ? (
                                        row.linked_crime_types.map((ct: string) => (
                                          <span key={ct} className="rounded bg-black/[0.04] border border-black/10 px-1.5 py-0.5 text-[10.5px] font-medium text-ink">
                                            {ct}
                                          </span>
                                        ))
                                      ) : (
                                        <span className="text-muted">{String(val || "General")}</span>
                                      )}
                                    </div>
                                  ) : c.key === "total_cases" ? (
                                    <div className="flex items-center gap-2">
                                      <span className="font-mono font-bold text-ink">{row.total_cases || row.past_fir_count || 1} cases</span>
                                      {(row.is_repeat_offender || (row.total_cases && row.total_cases >= 2)) && (
                                        <span className="rounded-full bg-brand/12 border border-brand/30 px-2 py-0.5 text-[10px] font-bold text-brand uppercase">
                                          Repeat Offender
                                        </span>
                                      )}
                                    </div>
                                  ) : c.key === "detected_faces" && val ? (
                                    <div className="flex flex-wrap gap-1">
                                      {String(val).split(";").map((face) => (
                                        <Link prefetch={false} key={face} href={`/suspect?id=${face}`} className="rounded bg-crimson/10 px-1.5 py-0.5 font-mono text-[10.5px] font-bold text-crimson hover:bg-crimson/20 transition-colors">
                                          {face}
                                        </Link>
                                      ))}
                                    </div>
                                  ) : (typeof val === "boolean" || val === "True" || val === "False" || val === true || val === false) && (c.key === "rowdy_sheeter" || c.key === "history_sheet" || c.key === "active_warrant") ? (
                                    <span className={`rounded-full px-2.5 py-0.5 text-[10.5px] font-bold border ${
                                      String(val).toLowerCase() === "true" 
                                        ? "bg-emerald/10 text-emerald border-emerald/25" 
                                        : "bg-black/[0.04] text-muted border-black/10"
                                    }`}>
                                      {String(val).toLowerCase() === "true" ? "Yes" : "No"}
                                    </span>
                                  ) : (
                                    <span className={c.key.includes("id") || c.key.includes("number") ? "font-mono" : ""}>
                                      {String(val ?? "—")}
                                    </span>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
          {data?.total > rows.length && (
            <p className="text-center text-[12px] text-faint">
              Showing {Math.min(rows.length, 150)} of {data.total.toLocaleString()} records
            </p>
          )}
        </>
      )}
    </div>
  );
}

export default function IntelPage() {
  return (
    <Suspense fallback={<div className="h-40 animate-pulse rounded-xl bg-black/[0.03]" />}>
      <IntelInner />
    </Suspense>
  );
}
