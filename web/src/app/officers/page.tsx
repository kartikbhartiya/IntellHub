"use client";

import { useEffect, useState, useMemo } from "react";
import {
  Users,
  Search,
  Award,
  BookOpen,
  SlidersHorizontal,
  BrainCircuit,
  CheckCircle2,
  User,
} from "lucide-react";
import { getJSON } from "@/lib/api";
import AccessRequired from "@/components/AccessRequired";

// Districts are derived from the officers actually loaded, never hardcoded. The
// previous fixed list held 10 entries — two of which (Mangaluru,
// Hubballi-Dharwad) are commissionerates rather than districts per
// appsail/karnataka.py — so the allocation form offered jurisdictions that
// could not return a result, and omitted 23 that exist.
const CRIME_TYPES = [
  "Chain Snatching",
  "House Break-in (Night)",
  "Vehicle Theft",
  "Extortion",
  "Narcotics Distribution",
  "Sandalwood Smuggling",
  "Cyber Fraud (Jamtara style)",
  "Murder",
  "Assault",
  "Homicide"
];

export default function OfficersPage() {
  const [officers, setOfficers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  // Filters
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDistrict, setSelectedDistrict] = useState("");
  const [selectedSpec, setSelectedSpec] = useState("");

  // Smart Recommender Widget States
  const [recDistrict, setRecDistrict] = useState("");
  const [recCrimeType, setRecCrimeType] = useState(CRIME_TYPES[6]); // Cyber Fraud
  const [recommendations, setRecommendations] = useState<any[]>([]);
  const [recLoading, setRecLoading] = useState(false);

  const DISTRICTS = useMemo(
    () => Array.from(new Set(officers.map((o: any) => o.district).filter(Boolean))).sort() as string[],
    [officers]
  );

  useEffect(() => {
    if (!recDistrict && DISTRICTS.length) setRecDistrict(DISTRICTS[0]);
  }, [DISTRICTS, recDistrict]);

  useEffect(() => {
    getJSON("/officers")
      .then((data) => {
        setOfficers(data);
        setLoading(false);
      })
      .catch((err: any) => {
        setForbidden(Boolean(err?.isForbidden));
        setError(err.message);
        setLoading(false);
      });
  }, []);

  // Fetch recommendations when inputs change
  useEffect(() => {
    if (!recDistrict || !recCrimeType) return;
    setRecLoading(true);
    getJSON(`/officers/recommend?district=${encodeURIComponent(recDistrict)}&crime_type=${encodeURIComponent(recCrimeType)}`)
      .then(setRecommendations)
      .catch((err) => console.error("recommend error", err))
      .finally(() => setRecLoading(false));
  }, [recDistrict, recCrimeType]);

  // Derived metrics
  const metrics = useMemo(() => {
    if (officers.length === 0) return { total: 0, avgClosure: 0, totalCommendations: 0, specialistsCount: 0 };
    const total = officers.length;
    const avgClosure = officers.reduce((acc, o) => acc + (o.closure_rate || 0), 0) / total;
    const totalCommendations = officers.reduce((acc, o) => acc + (o.commendations || 0), 0);
    const specialistsCount = officers.filter(o => o.specialization && o.specialization !== "General").length;
    return { total, avgClosure, totalCommendations, specialistsCount };
  }, [officers]);

  // Filtered Roster
  const filteredOfficers = useMemo(() => {
    return officers.filter((o) => {
      const matchesSearch =
        o.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        o.officer_id.toLowerCase().includes(searchTerm.toLowerCase()) ||
        o.rank.toLowerCase().includes(searchTerm.toLowerCase());
      const matchesDistrict = selectedDistrict ? o.district === selectedDistrict : true;
      const matchesSpec = selectedSpec ? o.specialization === selectedSpec : true;
      return matchesSearch && matchesDistrict && matchesSpec;
    });
  }, [officers, searchTerm, selectedDistrict, selectedSpec]);

  const uniqueSpecializations = useMemo(() => {
    const specs = new Set<string>();
    officers.forEach((o) => {
      if (o.specialization) specs.add(o.specialization);
    });
    return Array.from(specs);
  }, [officers]);

  if (loading) {
    return (
      <div className="flex h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-black/10 border-t-brand" />
      </div>
    );
  }

  if (forbidden) return <AccessRequired what="The officer roster" />;

  if (error) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center px-6 text-center">
        <h2 className="text-display text-xl font-bold text-ink">Officer roster unavailable</h2>
        <p className="mt-2 max-w-md text-[13px] text-muted">
          The intelligence core did not answer. No roster is shown rather than a
          partial or stale one.
        </p>
        <code className="mt-3 rounded bg-black/[0.04] px-2.5 py-1 font-mono text-[11px] text-faint">
          {error}
        </code>
      </div>
    );
  }

  return (
    <div className="space-y-7">
      <header>
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-brand/25 bg-brand/10 px-3 py-1">
          <BrainCircuit className="h-3.5 w-3.5 text-brand" />
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.2em] text-brand">
            Command Allocation Core
          </span>
        </div>
        <h1 className="text-display text-4xl font-bold tracking-tight text-ink">
          Personnel Registry & Smart Assignment
        </h1>
        <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-muted">
          SCRB monitoring of police workloads, closure rates, and domain specializations for optimal case allocation.
        </p>
      </header>

      {/* Metrics */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          { label: "Active Force", value: metrics.total, sub: "Investigating Officers", icon: Users, tone: "text-brand" },
          { label: "Avg Case Closure", value: `${Math.round(metrics.avgClosure * 100)}%`, sub: "Historical success", icon: CheckCircle2, tone: "text-emerald" },
          { label: "Total Commendations", value: metrics.totalCommendations, sub: "Exceptional merit awards", icon: Award, tone: "text-azure" },
          { label: "Domain Specialists", value: metrics.specialistsCount, sub: "Excludes general rank", icon: BookOpen, tone: "text-purple-600" },
        ].map((m, idx) => (
          <div key={idx} className="bezel">
            <div className="bezel-core p-5 relative overflow-hidden group">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-faint">{m.label}</span>
                <m.icon className={`h-4 w-4 ${m.tone}`} />
              </div>
              <p className="text-display mt-2 text-3xl font-bold text-ink">{m.value}</p>
              <p className="text-[12px] text-muted">{m.sub}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Smart Case Allocation Widget */}
      <div className="bezel">
        <div className="bezel-core p-6 space-y-6">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand/12 text-brand">
              <BrainCircuit className="h-[18px] w-[18px]" />
            </span>
            <div>
              <h2 className="text-[15px] font-bold text-ink">Smart Case Allocation Assistant</h2>
              <p className="text-[12px] text-faint">Calculate suitability scores based on workload, specialization, and historical clearance rates.</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Widget Filters */}
            <div className="space-y-4 rounded-xl border border-black/[0.06] bg-black/[0.02] p-5">
              <h3 className="text-xs font-bold uppercase tracking-wider text-faint">Filing Specifications</h3>
              <div>
                <label htmlFor="rec-district" className="block text-xs font-bold text-muted mb-1">Select Jurisdiction (District)</label>
                <select
                  id="rec-district"
                  value={recDistrict}
                  onChange={(e) => setRecDistrict(e.target.value)}
                  className="w-full rounded-xl border border-black/10 bg-white p-2.5 text-[13px] font-medium text-ink focus:outline-none focus:ring-2 focus:ring-brand/50"
                >
                  {DISTRICTS.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="rec-crime" className="block text-xs font-bold text-muted mb-1">Filing Crime Category</label>
                <select
                  id="rec-crime"
                  value={recCrimeType}
                  onChange={(e) => setRecCrimeType(e.target.value)}
                  className="w-full rounded-xl border border-black/10 bg-white p-2.5 text-[13px] font-medium text-ink focus:outline-none focus:ring-2 focus:ring-brand/50"
                >
                  {CRIME_TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Recommendations Output */}
            <div className="space-y-3.5">
              <h3 className="text-xs font-bold uppercase tracking-wider text-faint">Optimal Officers Recommendation</h3>
              {recLoading ? (
                <div className="flex h-40 items-center justify-center">
                  <div className="h-6 w-6 animate-spin rounded-full border-2 border-black/10 border-t-brand" />
                </div>
              ) : recommendations.length > 0 ? (
                <div className="space-y-2.5">
                  {recommendations.map((rec) => (
                    <div key={rec.officer_id} className="rounded-xl border border-black/[0.06] bg-black/[0.02] p-4 relative overflow-hidden">
                      <div className="absolute right-4 top-4 text-xs font-bold text-brand bg-brand/10 border border-brand/20 px-2 py-0.5 rounded-full">
                        Score: {rec.score}/100
                      </div>
                      <div className="flex items-start gap-3">
                        <div className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-black/[0.04]">
                          <User className="h-4 w-4 text-ink/80" />
                        </div>
                        <div className="min-w-0 pr-16">
                          <p className="font-bold text-[14px] text-ink leading-snug">
                            {rec.rank} {rec.name}
                          </p>
                          <p className="text-[11.5px] text-faint truncate">
                            ID: {rec.officer_id} · {rec.station} · Specialization: <span className="font-semibold text-brand-deep">{rec.specialization}</span>
                          </p>
                          <p className="mt-2 text-[12px] italic text-muted leading-relaxed">
                            {rec.justification}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex h-32 items-center justify-center rounded-xl border border-dashed border-black/[0.08] bg-black/[0.01]">
                  <p className="text-[13px] text-faint">No suitable officers found in this district.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Officer Roster Table */}
      <div className="bezel">
        <div className="bezel-core p-6 space-y-6">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-azure/12 text-azure">
                <SlidersHorizontal className="h-[18px] w-[18px]" />
              </span>
              <div>
                <h2 className="text-[15px] font-bold text-ink">Active Officer Roster</h2>
                <p className="text-[12px] text-faint">Personnel database matching KSP employees with their operational operational metrics.</p>
              </div>
            </div>

            {/* Filters */}
            <div className="flex flex-wrap gap-2">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
                <input
                  type="text"
                  placeholder="Search name, rank, KGID..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="rounded-full border border-black/10 bg-white py-1.5 pl-9 pr-4 text-[12.5px] font-medium text-ink focus:outline-none focus:ring-2 focus:ring-brand/50"
                />
              </div>

              <select
                value={selectedDistrict}
                onChange={(e) => setSelectedDistrict(e.target.value)}
                className="rounded-full border border-black/10 bg-white py-1.5 px-3.5 text-[12.5px] font-medium text-ink focus:outline-none focus:ring-2 focus:ring-brand/50"
              >
                <option value="">All Districts</option>
                {DISTRICTS.map((d) => (
                  <option key={d} value={d}>{d}</option>
                ))}
              </select>

              <select
                value={selectedSpec}
                onChange={(e) => setSelectedSpec(e.target.value)}
                className="rounded-full border border-black/10 bg-white py-1.5 px-3.5 text-[12.5px] font-medium text-ink focus:outline-none focus:ring-2 focus:ring-brand/50"
              >
                <option value="">All Specializations</option>
                {uniqueSpecializations.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Roster Grid */}
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left text-[13px]">
              <thead>
                <tr className="border-b border-black/[0.06] text-[11px] font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-3 pl-4">Officer ID / KGID</th>
                  <th scope="col" className="pb-3">Name</th>
                  <th scope="col" className="pb-3">Rank</th>
                  <th scope="col" className="pb-3">Posting Station</th>
                  <th scope="col" className="pb-3">Specialization</th>
                  <th scope="col" className="pb-3 text-right">Active cases</th>
                  <th scope="col" className="pb-3 text-right pr-4">Closure rate</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-black/[0.04] font-medium text-ink/90">
                {filteredOfficers.length > 0 ? (
                  filteredOfficers.map((o) => (
                    <tr key={o.officer_id} className="hover:bg-black/[0.01] transition-colors group">
                      <td className="py-3.5 pl-4 font-mono text-[12px] text-muted">{o.officer_id}</td>
                      <td className="py-3.5 font-bold text-ink">{o.name}</td>
                      <td className="py-3.5">
                        <span className="rounded bg-black/[0.05] px-1.5 py-0.5 text-[11px] font-semibold">
                          {o.rank}
                        </span>
                      </td>
                      <td className="py-3.5">
                        {o.station} <span className="text-[11px] text-faint block">{o.district}</span>
                      </td>
                      <td className="py-3.5">
                        <span className={`text-[12px] ${o.specialization === "General" ? "text-muted" : "text-brand-deep font-semibold"}`}>
                          {o.specialization || "General"}
                        </span>
                      </td>
                      <td className="py-3.5 text-right font-mono">{o.cases_assigned}</td>
                      <td className="py-3.5 text-right pr-4 font-mono">
                        <span className={o.closure_rate > 0.8 ? "text-emerald" : o.closure_rate > 0.5 ? "text-brand" : "text-crimson"}>
                          {Math.round(o.closure_rate * 100)}%
                        </span>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7} className="py-12 text-center text-faint">
                      No officers match the current filter selection.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
