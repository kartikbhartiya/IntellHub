"use client";

/* Karnataka Live News — public police and public-safety headlines beside our own records.
 *
 * Everything on this page comes from GET /news, which aggregates public sources (the
 * Karnataka RSS feeds of The Hindu, The Times of India and The Indian Express, Bing News,
 * GDELT, Google News RSS; NewsAPI and GNews when the operator has set keys). The page never
 * substitutes anything for a feed it could not load: when every source is down it says
 * so, lists what each source answered, and offers a retry. An empty news page is a
 * correct page; a page of invented headlines would be a police screen that lies.
 *
 * The "IntelHub: N FIRs" chip on a card is the join the backend makes from the districts
 * and crime type a keyword rule found in the headline to the FIR counts in our corpus.
 * It describes our records for that district, not the incident the article reports.
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowUpRight,
  BadgeCheck,
  Database,
  HeartHandshake,
  Info,
  Laptop,
  MapPin,
  Megaphone,
  Newspaper,
  Pill,
  RefreshCw,
  Rss,
  Scale,
  Search,
  ShieldAlert,
  TrafficCone,
  TrendingUp,
  TriangleAlert,
  WifiOff,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { ApiError, getJSON } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/* ------------------------------------------------------------------ types */
type FeedLang = "en" | "kn";
type FeedStatus = "ok" | "stale" | "unreachable" | "disabled";

interface IntelHubLink {
  district: string | null;
  crime_type: string | null;
  cases_30d: number;
  cases_total: number;
  window_days: number;
  as_of: string;
  link: string;
}

interface NewsItem {
  id: string;
  title: string;
  summary: string | null;
  url: string;
  source: string | null;
  published: string | null;
  image: string | null;
  lang: FeedLang;
  category: string;
  categories: string[];
  districts: string[];
  crime_types: string[];
  karnataka: boolean;
  via: string[];
  also_in: string[];
  intelhub: IntelHubLink | null;
}

interface NewsSource {
  name: string;
  ok: boolean;
  configured: boolean;
  count: number;
  stale: boolean;
  last_ok: string | null;
  error?: string;
}

interface NewsPayload {
  items: NewsItem[];
  total: number;
  facet_total: number;
  fetched_at: string | null;
  checked_at: string | null;
  status: FeedStatus;
  stale: boolean;
  sources: NewsSource[];
  categories: Record<string, number>;
  category_labels: Record<string, string>;
  districts: Record<string, number>;
  district_options: string[];
  lang: FeedLang;
  dataset: { records_up_to: string; window_days: number } | null;
  cache_ttl_s: number;
  note: string;
}

/* ------------------------------------------------------------------ constants */
const AUTO_REFRESH_MS = 5 * 60 * 1000;
const PAGE_SIZE = 24;
const SEARCH_DEBOUNCE_MS = 350;

const CATEGORY_ORDER = [
  "crime", "cybercrime", "narcotics", "traffic", "women_child", "law_order", "policing", "courts", "general",
];

// One tone per category, drawn from the theme where it has one (crimson, azure, gold) and
// from muted neighbours where it does not, so a card with no photo still says what it is.
const CATEGORY_META: Record<string, { label: string; tone: string; Icon: LucideIcon }> = {
  crime: { label: "Crime", tone: "#dc2b25", Icon: ShieldAlert },
  cybercrime: { label: "Cybercrime", tone: "#2f74c0", Icon: Laptop },
  narcotics: { label: "Narcotics", tone: "#7a4cc0", Icon: Pill },
  traffic: { label: "Traffic & road safety", tone: "#c2680c", Icon: TrafficCone },
  women_child: { label: "Women & child safety", tone: "#c03d78", Icon: HeartHandshake },
  law_order: { label: "Law & order / protests", tone: "#b07d16", Icon: Megaphone },
  policing: { label: "Policing & governance", tone: "#3e4a5c", Icon: BadgeCheck },
  courts: { label: "Courts", tone: "#0f766e", Icon: Scale },
  general: { label: "General", tone: "#6b6b73", Icon: Newspaper },
};

function meta(cat: string) {
  return CATEGORY_META[cat] ?? CATEGORY_META.general;
}

/* ------------------------------------------------------------------ helpers */
function minutesSince(iso: string | null, now: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Math.max(0, Math.floor((now - t) / 60000)) : null;
}

function relative(iso: string | null, now: number): string {
  const m = minutesSince(iso, now);
  if (m === null) return "Undated";
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 24 * 60) return `${Math.floor(m / 60)} h ago`;
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(iso as string));
}

function clock(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}

function updatedLabel(iso: string | null, now: number): string {
  const m = minutesSince(iso, now);
  if (m === null) return "Not updated yet";
  if (m < 1) return "Updated just now";
  if (m < 60) return `Updated ${m} min ago`;
  return `Updated ${Math.floor(m / 60)} h ago`;
}

/** "Cyber Fraud (Jamtara style)" -> "Cyber Fraud" for a chip that has to fit a card. */
function shortCrime(ct: string) {
  return ct.replace(/\s*\(.*\)\s*$/, "");
}

function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/* ------------------------------------------------------------------ small pieces */
function LiveDot({ status }: { status: FeedStatus | null }) {
  const tone =
    status === "ok" ? "bg-emerald text-emerald" : status === "stale" ? "bg-brand text-brand" : status ? "bg-crimson text-crimson" : "bg-faint text-faint";
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {status === "ok" && <span className={`pulse-ring absolute inset-0 rounded-full ${tone}`} />}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${tone}`} />
    </span>
  );
}

function statusWord(status: FeedStatus | null) {
  if (status === "ok") return "Live";
  if (status === "stale") return "Partly stale";
  if (status === "disabled") return "Switched off";
  if (status === "unreachable") return "Unreachable";
  return "Connecting";
}

/** A tasteful stand-in for a story with no photo, or whose photo failed to load. */
function CategoryArt({ category, source, large }: { category: string; source: string | null; large?: boolean }) {
  const { tone, Icon } = meta(category);
  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{ background: `linear-gradient(135deg, ${tone}24 0%, ${tone}0d 55%, #ffffff 100%)` }}
      aria-hidden
    >
      <div
        className="absolute inset-0"
        style={{ backgroundImage: `radial-gradient(${tone}2e 1px, transparent 1px)`, backgroundSize: "14px 14px" }}
      />
      <div className="absolute -right-6 -top-6 h-28 w-28 rounded-full" style={{ background: `${tone}14` }} />
      <div className="absolute inset-0 grid place-items-center">
        <span
          className={`grid place-items-center rounded-2xl bg-white/85 shadow-sm ring-1 ring-black/5 ${large ? "h-16 w-16" : "h-12 w-12"}`}
          style={{ color: tone }}
        >
          <Icon className={large ? "h-8 w-8" : "h-6 w-6"} strokeWidth={1.8} />
        </span>
      </div>
      {source && (
        <span
          className="absolute bottom-2.5 left-3 max-w-[85%] truncate text-[10.5px] font-semibold uppercase tracking-[0.14em]"
          style={{ color: tone }}
        >
          {source}
        </span>
      )}
    </div>
  );
}

function Media({ item, large }: { item: NewsItem; large?: boolean }) {
  // A broken photo is replaced by the category art, never shown as a broken image.
  const [failed, setFailed] = useState(false);
  if (!item.image || failed) return <CategoryArt category={item.category} source={item.source} large={large} />;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- third-party news images; static export, nothing to optimise
    <img
      src={item.image}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
    />
  );
}

function CategoryTag({ category, label }: { category: string; label?: string }) {
  const m = meta(category);
  return (
    <span className="inline-flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[0.12em]" style={{ color: m.tone }}>
      <m.Icon className="h-3.5 w-3.5" strokeWidth={2.2} />
      {label ?? m.label}
    </span>
  );
}

function IntelHubChip({ ih }: { ih: IntelHubLink }) {
  const what = ih.crime_type ? `${shortCrime(ih.crime_type)} FIRs` : "FIRs";
  const where = ih.district ?? "Karnataka";
  return (
    <Link
      prefetch={false}
      href={ih.link}
      title={
        `${ih.cases_30d.toLocaleString("en-IN")} ${what} in ${where} in the ${ih.window_days} days to ${ih.as_of}; ` +
        `${ih.cases_total.toLocaleString("en-IN")} in all. These are IntelHub records for the area, not the incident in this article.`
      }
      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-brand/25 bg-brand/[0.07] px-2.5 py-1 text-[11.5px] font-semibold text-brand-deep transition-colors hover:border-brand/45 hover:bg-brand/[0.13]"
    >
      <Database className="h-3 w-3 shrink-0" />
      <span className="truncate">
        IntelHub: {ih.cases_30d.toLocaleString("en-IN")} {what} in {where} ({ih.window_days}d)
      </span>
    </Link>
  );
}

function DistrictChips({ districts, onPick, max = 3 }: { districts: string[]; onPick: (d: string) => void; max?: number }) {
  if (!districts.length) return null;
  const shown = districts.slice(0, max);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {shown.map((d) => (
        <button
          key={d}
          type="button"
          onClick={() => onPick(d)}
          title={`Show news from ${d}`}
          className="inline-flex items-center gap-1 rounded-md bg-black/[0.04] px-1.5 py-0.5 text-[11px] font-medium text-muted transition-colors hover:bg-black/[0.08] hover:text-ink"
        >
          <MapPin className="h-3 w-3" />
          {d}
        </button>
      ))}
      {districts.length > max && <span className="text-[11px] text-faint">+{districts.length - max}</span>}
    </div>
  );
}

function ReadMore({ url, className = "" }: { url: string; className?: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={`inline-flex items-center gap-1 text-[12.5px] font-semibold text-ink transition-colors hover:text-brand ${className}`}
    >
      Read more <ArrowUpRight className="h-3.5 w-3.5" />
      <span className="sr-only">(opens the publisher&apos;s site in a new tab)</span>
    </a>
  );
}

/* ------------------------------------------------------------------ cards */
function LeadStory({ item, now, labels, onDistrict }: {
  item: NewsItem; now: number; labels: Record<string, string>; onDistrict: (d: string) => void;
}) {
  return (
    <motion.article
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
      className="bezel"
    >
      <div className="bezel-core group grid overflow-hidden md:grid-cols-[1.15fr_1fr]">
        <div className="relative aspect-[16/9] md:aspect-auto md:min-h-[300px]">
          {/* keyed: a new lead must not inherit the previous lead's failed-image state */}
          <Media key={item.id} item={item} large />
          <span className="absolute left-3 top-3 rounded-full bg-ink/85 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.16em] text-white backdrop-blur">
            Lead story
          </span>
        </div>
        <div className="flex flex-col p-6 md:p-7">
          <CategoryTag category={item.category} label={labels[item.category]} />
          <h2 className="text-display mt-3 text-[1.55rem] font-bold leading-[1.18] text-ink md:text-[1.75rem]">
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:text-brand-deep">
              {item.title}
            </a>
          </h2>
          {item.summary && <p className="mt-3 line-clamp-4 text-[14.5px] leading-relaxed text-muted">{item.summary}</p>}
          <div className="mt-4 space-y-2.5">
            <DistrictChips districts={item.districts} onPick={onDistrict} max={4} />
            {item.intelhub && <IntelHubChip ih={item.intelhub} />}
          </div>
          <div className="mt-auto flex flex-wrap items-center justify-between gap-3 pt-5 text-[12.5px] text-faint">
            <span className="min-w-0 truncate">
              <span className="font-semibold text-muted">{item.source || hostOf(item.url)}</span>
              {" · "}
              <time dateTime={item.published ?? undefined} title={clock(item.published)}>{relative(item.published, now)}</time>
              {item.also_in.length > 0 && ` · also in ${item.also_in.length} more`}
            </span>
            <ReadMore url={item.url} />
          </div>
        </div>
      </div>
    </motion.article>
  );
}

function NewsCard({ item, now, labels, index, onDistrict }: {
  item: NewsItem; now: number; labels: Record<string, string>; index: number; onDistrict: (d: string) => void;
}) {
  return (
    <motion.article
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: Math.min(index, 8) * 0.035, ease: [0.16, 1, 0.3, 1] }}
      className="group flex flex-col overflow-hidden rounded-[1.1rem] border border-black/[0.06] bg-white shadow-[var(--shadow-card)] transition-[transform,box-shadow] duration-300 hover:-translate-y-0.5 hover:shadow-[var(--shadow-float)]"
    >
      <div className="relative aspect-[16/9] overflow-hidden bg-black/[0.03]">
        <Media item={item} />
      </div>
      <div className="flex flex-1 flex-col p-4">
        <CategoryTag category={item.category} label={labels[item.category]} />
        <h3 className="mt-2 line-clamp-3 text-[15px] font-bold leading-snug text-ink">
          <a href={item.url} target="_blank" rel="noopener noreferrer" className="hover:text-brand-deep">
            {item.title}
          </a>
        </h3>
        {item.summary && <p className="mt-1.5 line-clamp-2 text-[13px] leading-relaxed text-muted">{item.summary}</p>}
        {(item.districts.length > 0 || item.intelhub) && (
          <div className="mt-3 space-y-2">
            <DistrictChips districts={item.districts} onPick={onDistrict} />
            {item.intelhub && <IntelHubChip ih={item.intelhub} />}
          </div>
        )}
        <div className="mt-auto flex items-center justify-between gap-2 pt-3.5 text-[12px] text-faint">
          <span className="min-w-0 truncate">
            <span className="font-semibold text-muted">{item.source || hostOf(item.url)}</span>
            {" · "}
            <time dateTime={item.published ?? undefined} title={clock(item.published)}>{relative(item.published, now)}</time>
          </span>
          <ReadMore url={item.url} className="shrink-0" />
        </div>
      </div>
    </motion.article>
  );
}

function CardSkeleton() {
  return (
    <div className="overflow-hidden rounded-[1.1rem] border border-black/[0.06] bg-white">
      <div className="shimmer aspect-[16/9]" />
      <div className="space-y-2.5 p-4">
        <div className="shimmer h-3 w-24 rounded" />
        <div className="shimmer h-4 w-full rounded" />
        <div className="shimmer h-4 w-4/5 rounded" />
        <div className="shimmer h-3 w-2/3 rounded" />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ side rail */
function TrendingDistricts({ counts, active, onPick }: {
  counts: Record<string, number>; active: string; onPick: (d: string) => void;
}) {
  const rows = Object.entries(counts).slice(0, 8);
  const max = rows.length ? rows[0][1] : 1;
  return (
    <section className="bezel">
      <div className="bezel-core p-5">
        <h2 className="flex items-center gap-2 text-[13.5px] font-bold text-ink">
          <TrendingUp className="h-4 w-4 text-brand" /> Districts in the news
        </h2>
        {rows.length === 0 ? (
          <p className="mt-3 text-[12.5px] leading-relaxed text-faint">No Karnataka district is named in the current headlines.</p>
        ) : (
          <ul className="mt-3.5 space-y-1.5">
            {rows.map(([d, n]) => (
              <li key={d}>
                <button
                  type="button"
                  onClick={() => onPick(active === d ? "" : d)}
                  aria-pressed={active === d}
                  className={`group w-full rounded-lg px-2 py-1.5 text-left transition-colors ${active === d ? "bg-brand/[0.08]" : "hover:bg-black/[0.03]"}`}
                >
                  <div className="flex items-center justify-between text-[12.5px]">
                    <span className={`font-semibold ${active === d ? "text-brand-deep" : "text-ink"}`}>{d}</span>
                    <span className="tabular text-faint">{n}</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-black/[0.05]">
                    <div className="h-full rounded-full bg-brand-soft" style={{ width: `${Math.max(8, (n / max) * 100)}%` }} />
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function SourceRow({ s }: { s: NewsSource }) {
  let pill: { text: string; cls: string };
  if (!s.configured) pill = { text: "Not configured", cls: "bg-black/[0.05] text-faint" };
  else if (s.ok) pill = { text: `Live · ${s.count}`, cls: "bg-emerald/12 text-emerald" };
  else if (s.stale) pill = { text: `Stale · ${s.count}`, cls: "bg-brand/12 text-brand-deep" };
  else pill = { text: "Unreachable", cls: "bg-crimson/10 text-crimson" };
  return (
    <li className="py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[12.5px] font-semibold text-ink">
          <Rss className="h-3.5 w-3.5 text-faint" /> {s.name}
        </span>
        <span className={`rounded-md px-2 py-0.5 text-[10.5px] font-bold uppercase tracking-wide ${pill.cls}`}>{pill.text}</span>
      </div>
      {s.error && (
        <p className="mt-1 line-clamp-2 text-[11.5px] leading-snug text-faint" title={s.error}>
          {s.error}
        </p>
      )}
      {s.configured && !s.ok && s.last_ok && (
        <p className="mt-0.5 text-[11px] text-faint">Last good fetch {clock(s.last_ok)}</p>
      )}
    </li>
  );
}

function SourcesPanel({ sources }: { sources: NewsSource[] }) {
  return (
    <section className="bezel">
      <div className="bezel-core p-5">
        <h2 className="flex items-center gap-2 text-[13.5px] font-bold text-ink">
          <Rss className="h-4 w-4 text-brand" /> Sources
        </h2>
        {sources.length === 0 ? (
          <p className="mt-3 text-[12.5px] text-faint">Waiting for the first fetch…</p>
        ) : (
          <ul className="mt-3.5 divide-y divide-black/[0.06]">
            {sources.map((s) => <SourceRow key={s.name} s={s} />)}
          </ul>
        )}
      </div>
    </section>
  );
}

function VerificationNote({ dataset }: { dataset: NewsPayload["dataset"] }) {
  return (
    <section className="rounded-[1.1rem] border border-black/[0.06] bg-white/70 p-5">
      <h2 className="flex items-center gap-2 text-[13px] font-bold text-ink">
        <Info className="h-4 w-4 text-azure" /> About these headlines
      </h2>
      <p className="mt-2.5 text-[12.5px] leading-relaxed text-muted">
        Headlines come from public news sources and aggregators. <strong className="font-semibold text-ink">They are not verified by KSP.</strong>
      </p>
      <p className="mt-2 text-[12px] leading-relaxed text-faint">
        Categories, districts and crime types are keyword matches on the headline, not a reading of the story.
        An IntelHub chip counts our own records for that district
        {dataset ? ` (simulated, CCTNS-shaped; ${dataset.window_days} days to ${dataset.records_up_to})` : ""} — it does not
        mean the reported incident is one of them.
      </p>
    </section>
  );
}

/* ------------------------------------------------------------------ page */
function NewsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { lang: uiLang } = useI18n();

  const [langPick, setLangPick] = useState<FeedLang | null>(() => {
    const l = params.get("lang");
    return l === "kn" || l === "en" ? l : null;
  });
  // The feed follows the UI language until the officer picks one here.
  const lang: FeedLang = langPick ?? (uiLang === "kn" ? "kn" : "en");
  const [category, setCategory] = useState(() => params.get("category") || "");
  const [district, setDistrict] = useState(() => params.get("district") || "");
  const [query, setQuery] = useState(() => params.get("q") || "");
  const [debouncedQuery, setDebouncedQuery] = useState(query);

  const [data, setData] = useState<NewsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  // "Show more" is remembered per query, so a new filter starts again at one page.
  const [paging, setPaging] = useState<{ key: string; n: number }>({ key: "", n: PAGE_SIZE });
  const [now, setNow] = useState(() => Date.now());
  const seq = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  const qs = useMemo(() => {
    const p = new URLSearchParams({ lang, limit: "120" });
    if (category) p.set("category", category);
    if (district) p.set("district", district);
    if (debouncedQuery) p.set("q", debouncedQuery);
    return p.toString();
  }, [lang, category, district, debouncedQuery]);

  // Responses can arrive out of order (a slow forced refresh, then a quick filter change);
  // only the latest request may write state.
  const load = useCallback(
    (force = false) => {
      const mine = ++seq.current;
      return getJSON<NewsPayload>(`/news?${qs}${force ? "&refresh=1" : ""}`)
        .then((body) => {
          if (mine !== seq.current) return;
          setData(body);
          setError(null);
        })
        .catch((e: unknown) => {
          if (mine !== seq.current) return;
          setError(
            e instanceof ApiError && e.status === 400
              ? "A filter in this address is not one the news service recognises."
              : e instanceof ApiError
                ? e.message
                : "The news service could not be reached.",
          );
        })
        .finally(() => {
          if (mine !== seq.current) return;
          setLoading(false);
          setRefreshing(false);
          setNow(Date.now());
        });
    },
    [qs],
  );

  // Fetch on every filter change, and keep the URL shareable (/news?district=Mysuru).
  useEffect(() => {
    load(false);
    const p = new URLSearchParams();
    if (category) p.set("category", category);
    if (district) p.set("district", district);
    if (debouncedQuery) p.set("q", debouncedQuery);
    if (langPick) p.set("lang", langPick);
    const next = p.toString();
    router.replace(next ? `/news?${next}` : "/news", { scroll: false });
  }, [load, category, district, debouncedQuery, langPick, router]);

  // Auto-refresh every five minutes while the tab is visible; the clock ticks for "N min ago".
  useEffect(() => {
    const poll = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) load(false);
    }, AUTO_REFRESH_MS);
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [load]);

  const labels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const c of CATEGORY_ORDER) out[c] = data?.category_labels?.[c] ?? meta(c).label;
    return out;
  }, [data]);

  const visible = paging.key === qs ? paging.n : PAGE_SIZE;
  const items = useMemo(() => data?.items ?? [], [data]);
  // Lead with the newest of the top few that has a photo; a text-only lead is fine too.
  const lead = useMemo(() => items.slice(0, 4).find((i) => i.image) ?? items[0] ?? null, [items]);
  const rest = useMemo(() => items.filter((i) => i !== lead), [items, lead]);
  const districtOptions = data?.district_options ?? (district ? [district] : []);
  const status: FeedStatus | null = data?.status ?? null;
  const filtered = Boolean(category || district || debouncedQuery);
  const sourceErrors = (data?.sources ?? []).filter((s) => s.configured && !s.ok && s.error);
  const staleSince = useMemo(
    () => (data?.sources ?? []).filter((s) => s.stale && s.last_ok).map((s) => s.last_ok as string).sort()[0] ?? null,
    [data],
  );

  // The spinner is set here, in the event, not inside load(): load also runs from effects.
  const refresh = () => {
    setRefreshing(true);
    load(true);
  };

  const pickDistrict = useCallback((d: string) => {
    setDistrict(d);
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const clearFilters = () => {
    setCategory("");
    setDistrict("");
    setQuery("");
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.header
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        className="flex flex-wrap items-end justify-between gap-5"
      >
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-faint">
            <LiveDot status={status} /> <span aria-live="polite">{statusWord(status)}</span> · Public media monitor
          </p>
          <h1 className="text-display mt-2 flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
            <Newspaper className="h-8 w-8 text-brand" strokeWidth={2} /> Karnataka Live News
          </h1>
          <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">
            Police and public-safety headlines from public news sources, each placed against what
            IntelHub&apos;s own records say about the same district.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <div role="group" aria-label="Feed language" className="flex rounded-full border border-black/10 bg-white p-0.5 text-[12.5px] font-semibold">
            {(["en", "kn"] as FeedLang[]).map((l) => (
              <button
                key={l}
                type="button"
                aria-pressed={lang === l}
                onClick={() => setLangPick(l)}
                className={`rounded-full px-3.5 py-1.5 transition-colors ${lang === l ? "bg-ink text-white" : "text-muted hover:text-ink"}`}
              >
                {l === "en" ? "English" : "ಕನ್ನಡ"}
              </button>
            ))}
          </div>
          <span className="tabular text-[12.5px] text-faint" title={data?.checked_at ? `Last checked ${clock(data.checked_at)}` : undefined}>
            {updatedLabel(data?.fetched_at ?? null, now)}
          </span>
          <button
            type="button"
            onClick={refresh}
            disabled={refreshing}
            className="inline-flex items-center gap-2 rounded-full border border-black/10 bg-white py-1.5 pl-3 pr-3.5 text-[12.5px] font-semibold text-ink transition-colors hover:border-brand/40 disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </motion.header>

      {/* Filters */}
      <section className="space-y-3">
        <div className="flex flex-col gap-2.5 sm:flex-row">
          <label className="relative flex-1">
            <span className="sr-only">Search headlines</span>
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={lang === "kn" ? "ಸುದ್ದಿ ಹುಡುಕಿ… / Search headlines" : "Search headlines, outlets…"}
              className="w-full rounded-xl border border-black/10 bg-white py-2.5 pl-10 pr-9 text-[14px] text-ink outline-none transition-colors placeholder:text-faint focus:border-brand/50"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Clear search"
                className="absolute right-2.5 top-1/2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-full text-faint hover:bg-black/5 hover:text-ink"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </label>
          <label className="relative sm:w-64">
            <span className="sr-only">District</span>
            <MapPin className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
            <select
              value={district}
              onChange={(e) => setDistrict(e.target.value)}
              className="w-full appearance-none rounded-xl border border-black/10 bg-white py-2.5 pl-10 pr-8 text-[14px] text-ink outline-none focus:border-brand/50"
            >
              <option value="">All Karnataka</option>
              {districtOptions.map((d) => (
                <option key={d} value={d}>
                  {d}
                  {data?.districts?.[d] ? ` (${data.districts[d]})` : ""}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1 pb-1" role="group" aria-label="Category">
          <CategoryChip active={!category} onClick={() => setCategory("")} label="All" count={data?.facet_total} />
          {CATEGORY_ORDER.map((c) => {
            const n = data?.categories?.[c] ?? 0;
            if (!n && category !== c && c === "general") return null;
            return (
              <CategoryChip
                key={c}
                category={c}
                active={category === c}
                onClick={() => setCategory(category === c ? "" : c)}
                label={labels[c]}
                count={data ? n : undefined}
              />
            );
          })}
        </div>
      </section>

      {error && data && (
        <div className="flex items-start gap-3 rounded-xl border border-crimson/20 bg-crimson/[0.05] px-4 py-3 text-[13px] text-crimson">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>Could not refresh: {error} Showing the headlines already loaded{data.checked_at ? ` (checked ${relative(data.checked_at, now)})` : ""}.</span>
        </div>
      )}

      {data?.stale && items.length > 0 && (
        <div className="flex items-start gap-3 rounded-xl border border-brand/25 bg-brand/[0.06] px-4 py-3 text-[13px] text-brand-deep">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Some sources are unreachable right now, so part of this feed is their last good fetch
            {staleSince ? ` (${clock(staleSince)})` : ""}. It refreshes on its own when they recover.
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_300px] xl:grid-cols-[minmax(0,1fr)_320px]">
        {/* Main column */}
        <div className="min-w-0 space-y-6">
          {loading && !data ? (
            <>
              <div className="bezel">
                <div className="bezel-core grid overflow-hidden md:grid-cols-[1.15fr_1fr]">
                  <div className="shimmer aspect-[16/9] md:aspect-auto md:min-h-[300px]" />
                  <div className="space-y-3 p-7">
                    <div className="shimmer h-3 w-28 rounded" />
                    <div className="shimmer h-7 w-full rounded" />
                    <div className="shimmer h-7 w-3/4 rounded" />
                    <div className="shimmer h-4 w-5/6 rounded" />
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
              </div>
            </>
          ) : error && !data ? (
            <EmptyState
              icon={WifiOff}
              title="Could not load Live News"
              body={`${error} Nothing is shown rather than anything invented.`}
              onRetry={refresh}
              onClear={filtered ? clearFilters : undefined}
            />
          ) : items.length === 0 && (status === "unreachable" || status === "disabled") ? (
            <EmptyState
              icon={WifiOff}
              title={status === "disabled" ? "Live News is switched off on this deployment" : "The public news feeds are unreachable"}
              body={
                status === "disabled"
                  ? "Outbound fetching is disabled on this server (NEWS_FETCH=off), as it should be on a closed network. Your IntelHub records are unaffected."
                  : "None of the news sources answered from this server. Nothing is shown rather than anything invented — retry in a minute, or check the server's outbound access."
              }
              onRetry={status === "disabled" ? undefined : refresh}
              details={sourceErrors}
            />
          ) : items.length === 0 ? (
            <EmptyState
              icon={Search}
              title="No headlines match these filters"
              body={`Nothing in the last seven days of ${lang === "kn" ? "Kannada" : "English"} coverage matches${district ? ` ${district}` : ""}${category ? ` · ${labels[category] ?? category}` : ""}${debouncedQuery ? ` · “${debouncedQuery}”` : ""}.`}
              onClear={filtered ? clearFilters : undefined}
              onRetry={filtered ? undefined : refresh}
              details={sourceErrors}
            />
          ) : (
            <>
              {lead && <LeadStory item={lead} now={now} labels={labels} onDistrict={pickDistrict} />}
              {rest.length > 0 && (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                  {rest.slice(0, visible).map((it, i) => (
                    <NewsCard key={it.id} item={it} now={now} labels={labels} index={i} onDistrict={pickDistrict} />
                  ))}
                </div>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3 text-[12.5px] text-faint">
                <span className="tabular">
                  Showing {Math.min(rest.length, visible) + (lead ? 1 : 0)} of {data?.total ?? items.length} headlines
                  {data && data.total > items.length ? ` (newest ${items.length} loaded)` : ""}
                </span>
                {rest.length > visible && (
                  <button
                    type="button"
                    onClick={() => setPaging({ key: qs, n: visible + PAGE_SIZE })}
                    className="rounded-full border border-black/10 bg-white px-4 py-2 font-semibold text-ink transition-colors hover:border-brand/40"
                  >
                    Show {Math.min(PAGE_SIZE, rest.length - visible)} more
                  </button>
                )}
              </div>
            </>
          )}
        </div>

        {/* Side rail */}
        <aside className="space-y-4 lg:sticky lg:top-28 lg:self-start">
          <TrendingDistricts counts={data?.districts ?? {}} active={district} onPick={setDistrict} />
          <SourcesPanel sources={data?.sources ?? []} />
          <VerificationNote dataset={data?.dataset ?? null} />
        </aside>
      </div>
    </div>
  );
}

function CategoryChip({ category, active, onClick, label, count }: {
  category?: string; active: boolean; onClick: () => void; label: string; count?: number;
}) {
  const m = category ? meta(category) : null;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[12.5px] font-semibold transition-colors ${
        active ? "border-ink bg-ink text-white" : "border-black/10 bg-white text-ink hover:border-black/25"
      } ${count === 0 && !active ? "opacity-55" : ""}`}
    >
      {m && <m.Icon className="h-3.5 w-3.5" style={{ color: active ? "#ffffff" : m.tone }} strokeWidth={2.2} />}
      {label}
      {typeof count === "number" && (
        <span className={`tabular rounded-full px-1.5 text-[11px] ${active ? "bg-white/20 text-white" : "bg-black/[0.05] text-muted"}`}>
          {count}
        </span>
      )}
    </button>
  );
}

function EmptyState({ icon: Icon, title, body, onRetry, onClear, details }: {
  icon: LucideIcon; title: string; body: string; onRetry?: () => void; onClear?: () => void; details?: NewsSource[];
}) {
  return (
    <div className="bezel">
      <div className="bezel-core flex flex-col items-center px-6 py-14 text-center">
        <span className="grid h-14 w-14 place-items-center rounded-2xl bg-black/[0.04] text-muted">
          <Icon className="h-7 w-7" strokeWidth={1.8} />
        </span>
        <h2 className="text-display mt-5 text-xl font-bold text-ink">{title}</h2>
        <p className="mt-2 max-w-lg text-[14px] leading-relaxed text-muted">{body}</p>
        <div className="mt-5 flex flex-wrap justify-center gap-2.5">
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-2 rounded-full bg-ink px-4 py-2 text-[13px] font-semibold text-white transition-opacity hover:opacity-90"
            >
              <RefreshCw className="h-3.5 w-3.5" /> Retry
            </button>
          )}
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              className="rounded-full border border-black/10 bg-white px-4 py-2 text-[13px] font-semibold text-ink hover:border-brand/40"
            >
              Clear filters
            </button>
          )}
        </div>
        {details && details.length > 0 && (
          <details className="mt-6 w-full max-w-lg rounded-xl border border-black/[0.07] bg-black/[0.02] px-4 py-3 text-left">
            <summary className="cursor-pointer text-[12.5px] font-semibold text-muted">What each source answered</summary>
            <ul className="mt-2.5 space-y-1.5 text-[12px] text-muted">
              {details.map((s) => (
                <li key={s.name} className="break-words">
                  <span className="font-semibold text-ink">{s.name}:</span> {s.error}
                  {s.last_ok ? ` (last good fetch ${clock(s.last_ok)})` : " (never reached from this server)"}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

export default function NewsPage() {
  return (
    <Suspense fallback={<div className="h-40 animate-pulse rounded-xl bg-black/[0.03]" />}>
      <NewsInner />
    </Suspense>
  );
}
