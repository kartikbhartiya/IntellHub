"use client";

/* Socio-economic profile of the people on the FIRs in scope.
 *
 * Reads GET /analytics/demographics — aggregate counts only, computed server-side
 * from ComplainantDetails, Victim and Accused. Nothing person-level reaches the
 * browser, and the backend never breaks complainants down by caste or religion,
 * so neither does this chart.
 *
 * Two design choices worth stating:
 *
 * - Colour follows the occupation, not its rank. Each occupation keeps its hue
 *   (assigned by OccupationID) when a filter reorders the ranking, so a reader who
 *   learned "Farmer is blue" is never misled by a repaint. The donut is drawn in that
 *   fixed order for the same reason; the list beside it is the ranking.
 * - The corpus is simulated and its occupation field is close to uniform. The
 *   backend tests that and says so in `note`; the note is shown under the chart
 *   rather than tucked away, because a ranked list of noise reads like a finding.
 */

import { useEffect, useMemo, useState } from "react";
import { Info, Loader2, RotateCcw, ShieldCheck } from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";

/* ------------------------------------------------------------------ types */
type Category = {
  id: number | null;
  name: string;
  count: number;
  share: number;
  residual: boolean;
};
type Band = { band: string; count: number; share: number };
type Dist = { name: string; count: number; share: number };
type Population = { n: number; age_bands: Band[]; gender: Dist[]; distinct_persons?: number };

type Demographics = {
  filters: { district: string | null; crime_type: string | null };
  options: { districts: string[]; crime_types: string[] };
  firs: number;
  withheld: boolean;
  occupation: {
    n: number;
    categories: Category[];
    top: Category | null;
    uniformity: {
      testable: boolean;
      chi2: number | null;
      df: number | null;
      critical_5pct: number | null;
      near_uniform: boolean | null;
    };
  } | null;
  complainant: Population | null;
  victim: Population | null;
  accused: Population | null;
  note: string | null;
  notes: string[];
  source: string;
};

/* ---------------------------------------------------------------- palette
 * Categorical slots in a fixed order, checked with the dataviz palette validator
 * against the white card surface: worst adjacent colour-blind ΔE 9.1, worst
 * adjacent normal-vision ΔE 19.6. Aqua, yellow and pink fall below 3:1 contrast on
 * white, which is why every bar row carries its name and value as text and colour
 * never has to identify anything on its own. Residual buckets ("Others",
 * "Not recorded") are grey: they are not an occupation and should not compete.
 */
const SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const RESIDUAL = "#a1a1aa";
const SERIES = "#2f74c0"; // single-series bars (age bands): one series, one colour
const GENDER_COLOR: Record<string, string> = {
  Male: "#2a78d6",
  Female: "#eb6834",
  Transgender: "#1baf7a",
  "Not recorded": RESIDUAL,
};

const fmt = (n: number) => n.toLocaleString("en-IN");
const pct = (s: number, dp = 1) => `${(s * 100).toFixed(dp)}%`;
const catKey = (c: Category) => (c.id == null ? `x:${c.name}` : `id:${c.id}`);

function colourMap(cats: Category[]) {
  const named = cats
    .filter((c) => !c.residual && c.id != null)
    .sort((a, b) => (a.id as number) - (b.id as number));
  const m = new Map<string, string>();
  named.forEach((c, i) => m.set(catKey(c), SLOTS[i] ?? RESIDUAL));
  cats.forEach((c) => {
    if (!m.has(catKey(c))) m.set(catKey(c), RESIDUAL);
  });
  return m;
}

/* ------------------------------------------------------------------ donut */
function arc(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  // A full ring cannot be one SVG arc (start == end draws nothing), so a single
  // 100% category is drawn as two halves.
  if (a1 - a0 >= Math.PI * 2 - 1e-6) {
    const mid = a0 + Math.PI;
    return arc(cx, cy, r0, r1, a0, mid) + " " + arc(cx, cy, r0, r1, mid, a0 + Math.PI * 2);
  }
  const p = (r: number, a: number) => `${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return [
    `M${p(r1, a0)}`,
    `A${r1},${r1} 0 ${large} 1 ${p(r1, a1)}`,
    `L${p(r0, a1)}`,
    `A${r0},${r0} 0 ${large} 0 ${p(r0, a0)}`,
    "Z",
  ].join(" ");
}

function Donut({
  cats,
  colours,
  n,
  top,
  active,
  setActive,
}: {
  cats: Category[];
  colours: Map<string, string>;
  n: number;
  top: Category | null;
  active: string | null;
  setActive: (k: string | null) => void;
}) {
  const size = 200;
  const c = size / 2;
  const r1 = 92;
  const r0 = 64;
  // Fixed (entity) order around the ring — see the note at the top of the file.
  const ordered = useMemo(
    () =>
      [...cats]
        .filter((x) => x.count > 0)
        .sort((a, b) => (a.id ?? 1e9) - (b.id ?? 1e9) || a.name.localeCompare(b.name)),
    [cats]
  );
  const segs = useMemo(() => {
    const out: { cat: Category; a0: number; a1: number }[] = [];
    let a = -Math.PI / 2;
    for (const x of ordered) {
      const sweep = n ? (x.count / n) * Math.PI * 2 : 0;
      out.push({ cat: x, a0: a, a1: a + sweep });
      a += sweep;
    }
    return out;
  }, [ordered, n]);
  const focus = cats.find((x) => catKey(x) === active) ?? top;

  return (
    <div className="relative mx-auto h-[200px] w-[200px] shrink-0">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="h-full w-full"
        role="img"
        aria-label={
          top ? `Largest occupation: ${top.name}, ${pct(top.share)} of ${fmt(n)} complainants` : "No data"
        }
      >
        {segs.map(({ cat, a0, a1 }) => {
          const k = catKey(cat);
          const on = active === k;
          return (
            <path
              key={k}
              d={arc(c, c, r0, on ? r1 + 4 : r1, a0, a1)}
              fill={colours.get(k)}
              // A 2px surface-coloured edge is the gap between segments, not a border.
              stroke="#ffffff"
              strokeWidth={2}
              strokeLinejoin="round"
              opacity={active && !on ? 0.3 : 1}
              style={{ transition: "opacity 160ms ease" }}
              onMouseEnter={() => setActive(k)}
              onMouseLeave={() => setActive(null)}
            >
              <title>{`${cat.name}: ${fmt(cat.count)} (${pct(cat.share)})`}</title>
            </path>
          );
        })}
      </svg>
      {focus && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-12 text-center">
          <span className="text-[26px] font-bold leading-none text-ink">{pct(focus.share)}</span>
          <span className="mt-1.5 line-clamp-2 text-[11.5px] font-medium leading-tight text-muted">
            {focus.name}
          </span>
          <span className="tabular mt-0.5 text-[10.5px] text-faint">{fmt(focus.count)}</span>
        </div>
      )}
    </div>
  );
}

/* ----------------------------------------------------------- ranked bars */
function RankedBars({
  cats,
  colours,
  active,
  setActive,
}: {
  cats: Category[];
  colours: Map<string, string>;
  active: string | null;
  setActive: (k: string | null) => void;
}) {
  const max = Math.max(...cats.map((c) => c.share), 0.0001);
  return (
    <ul className="min-w-0 flex-1 space-y-1" aria-label="Complainants by occupation, largest first">
      {cats.map((c, i) => {
        const k = catKey(c);
        const on = active === k;
        return (
          <li
            key={k}
            tabIndex={0}
            onMouseEnter={() => setActive(k)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(k)}
            onBlur={() => setActive(null)}
            className={`rounded-lg px-2 py-1.5 outline-none transition-[background-color,opacity] duration-150 focus-visible:ring-2 focus-visible:ring-brand/40 ${
              on ? "bg-black/[0.035]" : ""
            } ${active && !on ? "opacity-55" : ""}`}
          >
            <div className="flex items-baseline gap-2 text-[12.5px]">
              <span className="tabular w-4 shrink-0 text-right text-[10.5px] text-faint">{i + 1}</span>
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 translate-y-[1px] rounded-[3px]"
                style={{ background: colours.get(k) }}
              />
              <span className="min-w-0 flex-1 truncate font-medium text-ink">{c.name}</span>
              <span className="tabular shrink-0 text-[11.5px] text-muted">{fmt(c.count)}</span>
              <span className="tabular w-12 shrink-0 text-right font-semibold text-ink">{pct(c.share)}</span>
            </div>
            <div className="ml-6 mt-1 h-1.5 rounded-r-full bg-black/[0.05]">
              <div
                className="h-full rounded-r-full transition-[width] duration-500 ease-out"
                style={{ width: `${(c.share / max) * 100}%`, background: colours.get(k) }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* --------------------------------------------------- age & gender panels */
const POPULATIONS: { key: "complainant" | "victim" | "accused"; label: string; unit: string }[] = [
  { key: "complainant", label: "Complainants", unit: "people" },
  { key: "victim", label: "Victims", unit: "people" },
  { key: "accused", label: "Accused", unit: "FIR records" },
];

function AgePanels({ data }: { data: Demographics }) {
  // One x-scale across the three panels, so a longer bar means a larger share
  // wherever it sits.
  const max = Math.max(
    0.0001,
    ...POPULATIONS.flatMap((p) => (data[p.key]?.age_bands ?? []).map((b) => b.share))
  );
  return (
    // Container query, not a viewport breakpoint: beside the occupation card this
    // panel is ~440px wide and three columns would squeeze the bars to nothing.
    <div className="grid gap-5 @2xl:grid-cols-3">
      {POPULATIONS.map((p) => {
        const pop = data[p.key];
        return (
          <div key={p.key} className="min-w-0">
            <p className="text-[12px] font-semibold text-ink">{p.label}</p>
            <p className="tabular mb-2 text-[11px] text-faint">
              {pop ? `N = ${fmt(pop.n)} ${p.unit}` : "Not available"}
            </p>
            <div className="space-y-1.5">
              {(pop?.age_bands ?? []).map((b) => (
                <div key={b.band} className="flex items-center gap-2" title={`${b.band}: ${fmt(b.count)}`}>
                  <span className="tabular w-[52px] shrink-0 text-[11.5px] text-muted">{b.band}</span>
                  <div className="h-2 flex-1 rounded-r-full bg-black/[0.05]">
                    <div
                      className="h-full rounded-r-full transition-[width] duration-500 ease-out"
                      style={{ width: `${(b.share / max) * 100}%`, background: SERIES }}
                    />
                  </div>
                  <span className="tabular w-11 shrink-0 text-right text-[11.5px] font-semibold text-ink">
                    {pct(b.share, 0)}
                  </span>
                  <span className="tabular w-12 shrink-0 text-right text-[11px] text-faint">{fmt(b.count)}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function GenderPanels({ data }: { data: Demographics }) {
  return (
    <div className="space-y-5">
      {POPULATIONS.map((p) => {
        const pop = data[p.key];
        const parts = (pop?.gender ?? []).filter((g) => g.count > 0);
        return (
          <div key={p.key}>
            <div className="mb-1.5 flex items-baseline justify-between gap-2">
              <p className="text-[12px] font-semibold text-ink">{p.label}</p>
              <p className="tabular text-[11px] text-faint">
                {pop ? `N = ${fmt(pop.n)} ${p.unit}` : "Not available"}
              </p>
            </div>
            {/* 100% stacked bar; the 2px white gaps separate segments. */}
            <div className="flex h-5 w-full gap-[2px] overflow-hidden rounded-md bg-black/[0.04]">
              {parts.map((g) => (
                <div
                  key={g.name}
                  title={`${g.name}: ${fmt(g.count)} (${pct(g.share)})`}
                  className="h-full transition-[width] duration-500 ease-out"
                  style={{ width: `${g.share * 100}%`, background: GENDER_COLOR[g.name] ?? RESIDUAL }}
                />
              ))}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
              {(pop?.gender ?? []).map((g) => (
                <span key={g.name} className="flex items-center gap-1.5 text-[11.5px] text-muted">
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-[2px]"
                    style={{ background: GENDER_COLOR[g.name] ?? RESIDUAL }}
                  />
                  {g.name}
                  <span className="tabular font-semibold text-ink">{pct(g.share)}</span>
                  <span className="tabular text-faint">{fmt(g.count)}</span>
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------- component */
// `good` is the last successful payload, kept through a refetch (drawn faded) and
// through a failed one, so changing a filter never flashes the chart away and back.
type Loaded = { key: string; good: Demographics | null; error?: string };

export default function OccupationChart() {
  const [district, setDistrict] = useState("");
  const [crimeType, setCrimeType] = useState("");
  const [tab, setTab] = useState<"age" | "gender">("age");
  const [active, setActive] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  const query = useMemo(() => {
    const q = new URLSearchParams();
    if (district) q.set("district", district);
    if (crimeType) q.set("crime_type", crimeType);
    const s = q.toString();
    return s ? `?${s}` : "";
  }, [district, crimeType]);
  const key = `${query}#${nonce}`;

  useEffect(() => {
    let live = true;
    getJSON<Demographics>(`/analytics/demographics${query}`)
      .then((d) => {
        if (live) setLoaded({ key, good: d });
      })
      .catch((e) => {
        if (!live) return;
        setLoaded((prev) => ({
          key,
          good: prev?.good ?? null,
          error:
            e instanceof ApiError && e.status === 400
              ? "That filter is not in the corpus."
              : e?.message || "The demographic breakdown could not be loaded.",
        }));
      });
    return () => {
      live = false;
    };
  }, [query, key]);

  const loading = loaded?.key !== key;
  const data = loaded?.good ?? null;
  const error = loading ? undefined : loaded?.error;
  const occ = data?.occupation ?? null;
  const cats = useMemo(() => occ?.categories ?? [], [occ]);
  const colours = useMemo(() => colourMap(cats), [cats]);
  const districts = data?.options.districts ?? [];
  const crimeTypes = data?.options.crime_types ?? [];

  const select =
    "rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] text-ink shadow-sm focus:border-brand/40 focus:outline-none";

  return (
    <div className="space-y-4">
      {/* One filter row, above everything it scopes. */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          aria-label="Filter by district"
          className={select}
          value={district}
          onChange={(e) => setDistrict(e.target.value)}
        >
          <option value="">All districts</option>
          {districts.map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter by crime type"
          className={select}
          value={crimeType}
          onChange={(e) => setCrimeType(e.target.value)}
        >
          <option value="">All crime types</option>
          {crimeTypes.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {(district || crimeType) && (
          <button
            onClick={() => {
              setDistrict("");
              setCrimeType("");
            }}
            className="inline-flex items-center gap-1.5 rounded-full border border-black/10 px-3 py-2 text-[12px] text-muted transition-colors hover:text-ink"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Reset
          </button>
        )}
        {loading && data && <Loader2 className="h-4 w-4 animate-spin text-faint" aria-label="Updating" />}
        {data && (
          <span className="tabular ml-auto text-[11.5px] text-faint">
            {fmt(data.firs)} FIRs in scope
          </span>
        )}
      </div>

      {!data && loading && (
        <div className="grid gap-5 lg:grid-cols-[1.25fr_1fr]">
          <div className="h-[300px] animate-pulse rounded-2xl bg-black/[0.03]" />
          <div className="h-[300px] animate-pulse rounded-2xl bg-black/[0.03]" />
        </div>
      )}

      {error && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-crimson/20 bg-crimson/[0.03] px-4 py-3 text-[12.5px] text-ink">
          <span>{error}</span>
          <button
            onClick={() => setNonce((n) => n + 1)}
            className="rounded-full border border-black/10 bg-white px-3 py-1 text-[12px] font-medium hover:border-brand/40"
          >
            Retry
          </button>
        </div>
      )}

      {data?.withheld && (
        <div className="flex items-start gap-2 rounded-2xl border border-black/[0.07] bg-black/[0.02] p-4 text-[12.5px] leading-relaxed text-muted">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald" />
          <span>{data.note}</span>
        </div>
      )}

      {data && !data.withheld && (
        <div
          className={`grid gap-5 transition-opacity duration-200 lg:grid-cols-[1.25fr_1fr] ${
            loading ? "opacity-50" : ""
          }`}
          aria-busy={loading}
        >
          {/* ------------------------------------------------ By occupation */}
          <div className="min-w-0 rounded-2xl border border-black/[0.07] bg-white p-5">
            <div className="mb-4 flex items-baseline justify-between gap-3">
              <div>
                <h4 className="text-[14px] font-bold text-ink">By occupation</h4>
                <p className="text-[11.5px] text-faint">Complainants named on the FIR</p>
              </div>
              <span className="tabular shrink-0 rounded-full bg-black/[0.04] px-2.5 py-1 text-[11.5px] font-semibold text-ink">
                N = {fmt(occ?.n ?? 0)}
              </span>
            </div>
            {occ && occ.n > 0 ? (
              <div className="flex flex-col items-stretch gap-5 sm:flex-row sm:items-center">
                <Donut
                  cats={cats}
                  colours={colours}
                  n={occ.n}
                  top={occ.top}
                  active={active}
                  setActive={setActive}
                />
                <RankedBars cats={cats} colours={colours} active={active} setActive={setActive} />
              </div>
            ) : (
              <p className="py-10 text-center text-[12.5px] text-faint">
                No complainant records in this scope.
              </p>
            )}
            {data.note && (
              <div className="mt-4 flex items-start gap-2 rounded-xl border border-azure/15 bg-azure/[0.04] p-3">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-azure" />
                <p className="text-[11.5px] leading-relaxed text-ink/80">{data.note}</p>
              </div>
            )}
          </div>

          {/* ---------------------------------------------- age & gender */}
          <div className="@container min-w-0 rounded-2xl border border-black/[0.07] bg-white p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h4 className="text-[14px] font-bold text-ink">
                  {tab === "age" ? "By age band" : "By gender"}
                </h4>
                <p className="text-[11.5px] text-faint">Complainants, victims and accused</p>
              </div>
              <div
                role="tablist"
                aria-label="Distribution"
                className="flex rounded-full border border-black/10 bg-black/[0.03] p-0.5 text-[11.5px] font-medium"
              >
                {(["age", "gender"] as const).map((t) => (
                  <button
                    key={t}
                    role="tab"
                    aria-selected={tab === t}
                    onClick={() => setTab(t)}
                    className={`rounded-full px-3 py-1 transition-colors ${
                      tab === t ? "bg-azure text-white shadow-sm" : "text-muted hover:text-ink"
                    }`}
                  >
                    {t === "age" ? "Age" : "Gender"}
                  </button>
                ))}
              </div>
            </div>
            {tab === "age" ? <AgePanels data={data} /> : <GenderPanels data={data} />}
            {data.notes.length > 0 && (
              <ul className="mt-4 space-y-1 border-t border-black/[0.06] pt-3">
                {data.notes.map((n, i) => (
                  <li key={i} className="text-[11px] leading-relaxed text-faint">
                    {n}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
