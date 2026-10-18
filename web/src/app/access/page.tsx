"use client";

/**
 * Account & roles: who you are to the API, what that lets you see, and how every
 * other role differs.
 *
 * Signing in moved to the landing page; this page is where the access model is
 * explained and inspected. Everything it states comes from the API — the identity
 * from /auth/me, the matrix from /auth/roles, the personas from /auth/demo — so it
 * describes the rules the backend enforces, not a copy of them that can drift.
 * The watchlist block below is the live proof: the same endpoint, redacted or not,
 * depending on the token this browser holds.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  Check,
  Eye,
  EyeOff,
  Landmark,
  LoaderCircle,
  LogIn,
  LogOut,
  MapPin,
  Minus,
  ShieldCheck,
  UserCog,
  type LucideIcon,
} from "lucide-react";
import {
  ApiError,
  demoLogin,
  getJSON,
  getRoleCatalog,
  logout,
  type DemoInfo,
  type DemoPersona,
  type IdentityCore,
  type RoleCatalog,
  type ScopeLevel,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import {
  isCurrentPersona,
  jurisdictionLabel,
  roleLabel,
  scopeLevel,
  useDemoInfo,
  useIdentity,
} from "@/lib/session";

type T = (key: string, fallback: string) => string;

const EASE = [0.16, 1, 0.3, 1] as const;
const fade = (step = 0) => ({
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.6, delay: Math.min(step, 6) * 0.05, ease: EASE },
});

const SCOPE_ICON: Record<ScopeLevel, LucideIcon> = { state: Landmark, district: Building2, station: MapPin };

/** Tier tint: aggregates (azure) → case work (gold) → command (crimson) → admin (ink). */
function tone(tier: number | undefined) {
  if (tier === undefined) return "bg-black/[0.04] border-black/10 text-muted";
  if (tier >= 3) return "bg-ink/[0.08] border-ink/25 text-ink";
  if (tier === 2) return "bg-crimson/12 border-crimson/25 text-crimson";
  if (tier === 1) return "bg-brand/12 border-brand/25 text-brand-deep";
  return "bg-azure/12 border-azure/25 text-azure";
}

const LEGACY_TIER: Record<string, number> = { analyst: 0, investigator: 1, supervisor: 2 };

/**
 * Plain-language names for the permission strings the API returns. A permission
 * this list does not know is still shown, under its own identifier — the matrix
 * must never silently drop a grant because the client was not updated.
 */
const PERMISSION_LABELS: Record<string, string> = {
  view_aggregates: "Aggregate statistics & maps",
  view_cases: "Case files (FIRs)",
  view_dossiers: "Offender dossiers",
  view_network: "Co-offender network",
  use_copilot: "Kavya copilot",
  surveillance_view: "CCTV & ANPR alerts",
  surveillance_decide: "Verify or reject CCTV matches",
  surveillance_escalate: "Escalate CCTV alerts",
  watchlist_names: "Named recidivism watchlist",
  audit_read: "Audit log",
  contest_resolve: "Resolve challenges to a score",
  system_admin: "System settings, sync & retention",
};

function permissionLabel(p: string, t: T) {
  const en = PERMISSION_LABELS[p] ?? p.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  return t(`perm.${p}`, en);
}

function scopeWord(level: ScopeLevel | undefined, t: T) {
  if (level === "station") return t("account.scope.station", "Station");
  if (level === "district") return t("account.scope.district", "District");
  return t("account.scope.state", "State-wide");
}

interface WatchRow {
  criminal_id?: string;
  offender_id?: string;
  name?: string;
  index?: number;
  risk_index?: number;
  probability?: number | null;
  band?: string;
  redacted?: boolean;
}
interface Watchlist {
  top?: WatchRow[];
  access?: { redacted?: boolean; note?: string | null };
}

/* ------------------------------------------------------------------ persona switcher */

function PersonaCard({
  persona,
  info,
  identity,
  busy,
  onSwitch,
  t,
}: {
  persona: DemoPersona;
  info: DemoInfo;
  identity: IdentityCore | null;
  busy: string | null;
  onSwitch: (p: DemoPersona, district?: string) => void;
  t: T;
}) {
  const current = isCurrentPersona(identity, persona);
  const canPick = persona.switchable_district && info.districts.length > 0;
  const [picked, setPicked] = useState<string | null>(null);
  const selectId = useId();
  const district = canPick
    ? picked ?? (current ? identity?.scope?.district : null) ?? persona.district ?? info.districts[0]
    : persona.district;
  const Icon = SCOPE_ICON[persona.scope_level] ?? Landmark;
  const sameAsNow = current && (!canPick || district === identity?.scope?.district);
  const where =
    persona.scope_level === "state"
      ? t("profile.allDistricts", "All 31 districts")
      : persona.scope_level === "station"
        ? [persona.station, persona.district].filter(Boolean).join(", ")
        : district ?? "";

  return (
    <li
      className={`flex flex-col rounded-2xl border p-4 transition-colors ${
        current ? "border-brand/40 bg-brand/[0.04]" : "border-black/[0.07] bg-white hover:border-black/15"
      }`}
    >
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-black/[0.04] text-ink/70">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <p className="text-[13.5px] font-semibold leading-tight text-ink">{persona.label}</p>
          <p className="mt-0.5 text-[11.5px] text-muted">{persona.role_label}</p>
        </div>
        {current && (
          <span className="ml-auto shrink-0 rounded-full bg-brand/15 px-2 py-0.5 text-[10.5px] font-semibold text-brand-deep">
            {t("profile.current", "Current")}
          </span>
        )}
      </div>
      <p className="mt-3 flex items-center gap-1.5 text-[12px] font-medium text-ink/80">
        <MapPin className="h-3.5 w-3.5 text-brand" aria-hidden="true" /> {where}
      </p>
      {persona.description && <p className="mt-1.5 text-[12px] leading-relaxed text-muted">{persona.description}</p>}
      <div className="mt-auto flex items-center gap-2 pt-4">
        {canPick && (
          <>
            <label htmlFor={selectId} className="sr-only">
              {t("profile.chooseDistrict", "District")}
            </label>
            <select
              id={selectId}
              value={district ?? ""}
              disabled={!!busy}
              onChange={(e) => setPicked(e.target.value)}
              className="h-9 min-w-0 flex-1 rounded-lg border border-black/10 bg-white px-2 text-[12.5px] text-ink outline-none focus:border-brand/50"
            >
              {info.districts.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </>
        )}
        <button
          type="button"
          disabled={!!busy || sameAsNow}
          onClick={() => onSwitch(persona, canPick ? district ?? undefined : undefined)}
          className={`flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 text-[12px] font-semibold transition-colors disabled:cursor-default disabled:opacity-40 ${
            canPick ? "" : "ml-auto"
          } bg-ink text-white hover:bg-brand`}
        >
          {busy === persona.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
          {sameAsNow ? t("profile.current", "Current") : t("account.useThis", "Use this role")}
        </button>
      </div>
    </li>
  );
}

/* ------------------------------------------------------------------ page */

export default function AccountPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { me, identity, authenticated, loading, error: meDown, epoch } = useIdentity();
  const { info: demo } = useDemoInfo();
  const [catalog, setCatalog] = useState<RoleCatalog | null>(null);
  const [rolesFailure, setRolesFailure] = useState<number | null>(null);
  const [watchlist, setWatchlist] = useState<Watchlist | null>(null);
  const [watchFailed, setWatchFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [switchFailure, setSwitchFailure] = useState<string | null>(null);

  useEffect(() => {
    getRoleCatalog()
      .then(setCatalog)
      .catch((e) => setRolesFailure(e instanceof ApiError ? e.status : 0));
  }, []);

  // Re-read on every token change: the redaction is the point of this block, and
  // it must follow the identity, not the identity at first render.
  useEffect(() => {
    let alive = true;
    getJSON<Watchlist>("/recidivism")
      .then((w) => {
        if (!alive) return;
        setWatchlist(w);
        setWatchFailed(false);
      })
      .catch(() => {
        if (!alive) return;
        setWatchlist(null);
        setWatchFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [epoch]);

  const roles = catalog ? catalog.roles : null;
  const role = identity?.role ?? "analyst";
  const myRole = roles?.find((r) => r.role === role);
  const tier = identity?.tier ?? myRole?.tier ?? LEGACY_TIER[role];
  // The identity's own grants when the API sends them; otherwise the API's
  // catalogue entry for the role. Never a client-side guess.
  const granted = identity?.permissions ?? myRole?.permissions;
  const ScopeIcon = SCOPE_ICON[scopeLevel(identity)];

  // Columns in the order the API listed the roles; rows in the order of its
  // permission glossary, then anything a role holds that the glossary omits — so
  // a new permission appears without a client change.
  const permissions = useMemo(() => {
    const seen: string[] = [];
    const add = (p: string) => {
      if (!seen.includes(p)) seen.push(p);
    };
    (catalog?.permissions ?? []).forEach((p) => add(p.permission));
    for (const r of catalog?.roles ?? []) r.permissions.forEach(add);
    (granted ?? []).forEach(add);
    return seen;
  }, [catalog, granted]);
  const permissionHelp = useMemo(
    () => new Map((catalog?.permissions ?? []).map((p) => [p.permission, p.description])),
    [catalog],
  );

  const redacted = watchlist?.access?.redacted ?? true;

  const onSignOut = () => {
    logout();
    router.push("/");
  };

  const onSwitch = async (p: DemoPersona, district?: string) => {
    if (busy) return;
    setBusy(p.id);
    setSwitchFailure(null);
    try {
      await demoLogin(p.id, district);
      // Pages load their data in mount effects from the stored token, so a full
      // reload is what makes every panel — this one included — re-fetch as the new role.
      window.location.reload();
    } catch (e) {
      setSwitchFailure(
        e instanceof ApiError
          ? `${t("profile.switchFailed", "Could not switch role")} (HTTP ${e.status}).`
          : t("landing.err.network", "Could not reach the intelligence core. Check that the backend is running, then retry."),
      );
      setBusy(null);
    }
  };

  return (
    <div className="space-y-7">
      <motion.header {...fade()}>
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <UserCog className="h-8 w-8 text-brand" strokeWidth={2} aria-hidden="true" /> {t("account.title", "Account & roles")}
        </h1>
        <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-muted">
          {t(
            "account.intro",
            "Every role sees aggregate intelligence. Case files, dossiers and named watchlists are person-level records: each role reaches only what its rank and jurisdiction require, the API enforces it, and every access is audited.",
          )}
        </p>
      </motion.header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* ------------------------------------------------------------ you */}
        <motion.section {...fade(1)} className="bezel" aria-labelledby="you-title">
          <div className="bezel-core flex h-full flex-col p-6">
            <p id="you-title" className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-faint">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" />
              {authenticated ? t("account.signedInAs", "Signed in as") : t("profile.anonymous", "Not signed in")}
            </p>
            {loading && !me ? (
              <div className="mt-4 space-y-2" aria-busy="true">
                <div className="shimmer h-7 w-40 rounded-md" />
                <div className="shimmer h-5 w-24 rounded-full" />
              </div>
            ) : (
              <>
                <p className="text-display mt-3 break-words text-2xl font-bold text-ink">
                  {authenticated ? identity?.user : t("account.visitor", "Visitor")}
                </p>
                <span className={`mt-3 inline-flex w-fit items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-semibold ${tone(tier)}`}>
                  {roleLabel(identity) || t("account.analyst", "Analyst")}
                  {tier !== undefined && <span className="font-medium opacity-70">· {t("account.tier", "tier")} {tier}</span>}
                </span>
                <p className="mt-4 flex items-center gap-2 text-[12.5px] font-medium text-ink">
                  <ScopeIcon className="h-4 w-4 text-brand" aria-hidden="true" />
                  <span className="sr-only">{t("account.jurisdiction", "Jurisdiction")}:</span>
                  {jurisdictionLabel(identity, t)}
                </p>
                <p className="mt-3 text-[12.5px] leading-relaxed text-muted">
                  {myRole?.description ?? me?.policy?.[role] ?? ""}
                </p>
                {!authenticated && (
                  <p className="mt-2 text-[12px] leading-relaxed text-faint">
                    {t("account.anonNote", "Without a sign-in the API treats you as an analyst: aggregates only, person-level blocks pseudonymised.")}
                  </p>
                )}
              </>
            )}
            {meDown && (
              <p className="mt-3 text-[12px] text-crimson">
                {t("account.meDown", "The intelligence core did not answer, so your identity could not be read.")}
              </p>
            )}

            <div className="mt-auto pt-5">
              {authenticated ? (
                <button
                  type="button"
                  onClick={onSignOut}
                  className="flex w-full items-center justify-center gap-2 rounded-full border border-black/10 bg-black/[0.03] px-4 py-2.5 text-[13px] font-medium text-ink transition-colors hover:border-crimson/40 hover:text-crimson"
                >
                  <LogOut className="h-4 w-4" aria-hidden="true" /> {t("common.signOut", "Sign out")}
                </button>
              ) : (
                <Link
                  prefetch={false}
                  href="/?next=%2Faccess"
                  className="flex w-full items-center justify-center gap-2 rounded-full bg-brand px-4 py-2.5 text-[13px] font-semibold text-white transition-[filter] hover:brightness-110"
                >
                  <LogIn className="h-4 w-4" aria-hidden="true" /> {t("common.signIn", "Sign in")}
                </Link>
              )}
            </div>
          </div>
        </motion.section>

        {/* --------------------------------------------------- your permissions */}
        <motion.section {...fade(2)} className="bezel lg:col-span-2" aria-labelledby="perm-title">
          <div className="bezel-core h-full p-6">
            <h2 id="perm-title" className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
              <ShieldCheck className="h-4 w-4" aria-hidden="true" /> {t("account.permissions", "Your permissions")}
            </h2>
            {granted && permissions.length > 0 ? (
              <ul className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
                {permissions.map((p) => {
                  const has = granted.includes(p);
                  return (
                    <li
                      key={p}
                      title={permissionHelp.get(p)}
                      className={`flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-[12.5px] ${
                        has ? "border-emerald/25 bg-emerald/[0.05] text-ink" : "border-black/[0.06] text-faint"
                      }`}
                    >
                      {has ? (
                        <Check className="h-4 w-4 shrink-0 text-emerald" aria-hidden="true" />
                      ) : (
                        <Minus className="h-4 w-4 shrink-0" aria-hidden="true" />
                      )}
                      <span className="min-w-0 flex-1">{permissionLabel(p, t)}</span>
                      <span className="sr-only">{has ? t("account.granted", "granted") : t("account.notGranted", "not granted")}</span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="mt-4 space-y-2 text-[12.5px] leading-relaxed text-muted">
                {loading || (!roles && rolesFailure === null) ? (
                  <div className="shimmer h-24 w-full rounded-xl" aria-busy="true" />
                ) : (
                  <>
                    {me?.policy?.[role] && <p>{me.policy[role]}</p>}
                    <p className="text-faint">
                      {t("account.noGrants", "This backend did not report individual permissions for your role, so none are listed here rather than guessed.")}
                    </p>
                  </>
                )}
              </div>
            )}
            {me?.warning && (
              <div className="mt-4 flex items-start gap-2 rounded-xl border border-brand/25 bg-brand/[0.06] p-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
                <p className="text-[12px] leading-relaxed text-ink">{me.warning}</p>
              </div>
            )}
          </div>
        </motion.section>
      </div>

      {/* ----------------------------------------------------- persona switcher */}
      {demo && demo.personas.length > 0 && (
        <motion.section {...fade(3)} className="bezel" aria-labelledby="switch-title">
          <div className="bezel-core p-6">
            <h2 id="switch-title" className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
              <UserCog className="h-4 w-4" aria-hidden="true" /> {t("account.switch", "Switch demo persona")}
            </h2>
            <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-muted">
              {t(
                "account.switchNote",
                "Demo mode only. Each persona is a real token issued by the API with that role and jurisdiction, and each switch is written to the audit log. District and station roles see their own jurisdiction in lists; opening a record outside it is allowed but flagged and audited.",
              )}
            </p>
            <ul className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {demo.personas.map((p) => (
                <PersonaCard key={p.id} persona={p} info={demo} identity={identity} busy={busy} onSwitch={onSwitch} t={t} />
              ))}
            </ul>
            {switchFailure && (
              <p role="alert" className="mt-3 text-[12.5px] text-crimson">
                {switchFailure}
              </p>
            )}
          </div>
        </motion.section>
      )}

      {/* ---------------------------------------------------------- role matrix */}
      <motion.section {...fade(4)} className="bezel" aria-labelledby="matrix-title">
        <div className="bezel-core p-6">
          <h2 id="matrix-title" className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
            <ShieldCheck className="h-4 w-4" aria-hidden="true" /> {t("account.matrix", "Roles and permissions")}
          </h2>
          <p className="mt-2 max-w-3xl text-[13px] leading-relaxed text-muted">
            {t("account.matrixNote", "As enforced by the API. Your role's column is highlighted.")}
          </p>

          {roles === null && rolesFailure === null && <div className="shimmer mt-5 h-64 w-full rounded-xl" aria-busy="true" />}
          {rolesFailure !== null && (
            <p className="mt-5 rounded-xl border border-black/[0.07] bg-black/[0.02] p-4 text-[12.5px] text-muted">
              {rolesFailure === 404
                ? t("account.rolesMissing", "This backend does not publish a role catalogue (GET /auth/roles returned 404).")
                : rolesFailure === 0
                  ? t("account.rolesDown", "The role catalogue could not be loaded — the intelligence core did not answer.")
                  : `${t("account.rolesFailed", "The role catalogue could not be loaded")} (HTTP ${rolesFailure}).`}
            </p>
          )}

          {roles && roles.length > 0 && (
            <>
              <div className="mt-5 overflow-x-auto rounded-xl border border-black/[0.07]">
                <table className="w-full min-w-[1040px] border-collapse text-[12.5px]">
                  <caption className="sr-only">{t("account.matrix", "Roles and permissions")}</caption>
                  <thead>
                    <tr className="border-b border-black/[0.08] bg-black/[0.02] align-bottom">
                      <th scope="col" className="sticky left-0 z-10 min-w-[210px] bg-[#fafafb] px-3 py-3 text-left text-[10px] font-semibold uppercase tracking-[0.15em] text-faint">
                        {t("account.permission", "Permission")}
                      </th>
                      {roles.map((r) => {
                        const mine = r.role === role;
                        return (
                          <th
                            key={r.role}
                            scope="col"
                            aria-current={mine ? "true" : undefined}
                            className={`min-w-[92px] px-2 py-3 text-center align-bottom ${mine ? "bg-brand/[0.09]" : ""}`}
                          >
                            {mine && (
                              <span className="mb-1 inline-block rounded-full bg-brand px-1.5 py-px text-[9.5px] font-bold uppercase tracking-wider text-white">
                                {t("account.you", "You")}
                              </span>
                            )}
                            <span className="block text-[11.5px] font-semibold leading-tight text-ink">{r.label}</span>
                            <span className="mt-0.5 block text-[10px] font-medium text-faint">
                              {scopeWord(r.scope_level, t)} · {t("account.tier", "tier")} {r.tier}
                            </span>
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {permissions.map((p) => (
                      <tr key={p} className="border-b border-black/[0.05] last:border-b-0">
                        <th scope="row" title={p} className="sticky left-0 z-10 max-w-[280px] bg-white px-3 py-2.5 text-left font-medium text-ink">
                          {permissionLabel(p, t)}
                          {/* The server's own description, untranslated: it is the
                              policy text, and a paraphrase could drift from it. */}
                          <span className="mt-0.5 block text-[10.5px] font-normal leading-snug text-faint">
                            {permissionHelp.get(p) ?? p}
                          </span>
                        </th>
                        {roles.map((r) => {
                          const has = r.permissions.includes(p);
                          const mine = r.role === role;
                          return (
                            <td key={r.role} className={`px-2 py-2.5 text-center ${mine ? "bg-brand/[0.06]" : ""}`}>
                              {has ? (
                                <Check className="mx-auto h-4 w-4 text-emerald" aria-hidden="true" />
                              ) : (
                                <Minus className="mx-auto h-4 w-4 text-black/20" aria-hidden="true" />
                              )}
                              <span className="sr-only">{has ? t("account.granted", "granted") : t("account.notGranted", "not granted")}</span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <dl className="mt-5 grid grid-cols-1 gap-x-8 gap-y-3 md:grid-cols-2 xl:grid-cols-3">
                {roles.map((r) => (
                  <div key={r.role} className={`rounded-xl p-3 ${r.role === role ? "bg-brand/[0.05] ring-1 ring-brand/25" : ""}`}>
                    <dt className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${tone(r.tier)}`}>{r.label}</span>
                      <span className="font-mono text-[10.5px] text-faint">{r.role}</span>
                    </dt>
                    <dd className="mt-1.5 text-[12px] leading-relaxed text-muted">
                      <span className="font-medium text-ink/80">{scopeWord(r.scope_level, t)}.</span> {r.description}
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </div>
      </motion.section>

      {/* --------------------------------------------- live redaction proof */}
      <motion.section {...fade(5)} className="bezel" aria-labelledby="watch-title">
        <div className="bezel-core p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 id="watch-title" className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
              {redacted ? <EyeOff className="h-4 w-4" aria-hidden="true" /> : <Eye className="h-4 w-4" aria-hidden="true" />}
              {t("account.watchTitle", "Recidivism watchlist — live")}
            </h2>
            {watchlist && (
              <span
                className={`rounded-full border px-3 py-1 text-[12px] font-semibold ${
                  redacted ? "border-azure/25 bg-azure/12 text-azure" : "border-crimson/25 bg-crimson/12 text-crimson"
                }`}
              >
                {redacted ? t("account.redacted", "Redacted") : t("account.fullAccess", "Full access")}
              </span>
            )}
          </div>

          <p className="mt-3 max-w-3xl text-[13px] leading-relaxed text-muted">
            {t(
              "account.watchNote",
              "For a role without the named-watchlist permission this endpoint is redacted rather than refused: counts, bands and method survive, while names become stable pseudonyms and calibrated probabilities are withheld. A 403 would hide that any control exists; this shows it working. Switch to a district or state head and the same table changes.",
            )}
          </p>

          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[620px] border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-black/[0.08] text-[10px] uppercase tracking-[0.15em] text-faint">
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">{t("account.col.id", "Identifier")}</th>
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">{t("common.name", "Name")}</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">{t("account.col.index", "Index")}</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">{t("account.col.prob", "Probability")}</th>
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">{t("account.col.band", "Band")}</th>
                </tr>
              </thead>
              <tbody>
                {(watchlist?.top ?? []).slice(0, 8).map((row, i) => (
                  <tr key={row.criminal_id ?? row.offender_id ?? i} className="border-b border-black/[0.05]">
                    <td className="px-2 py-2.5 font-mono text-[12px] text-muted">{row.criminal_id ?? row.offender_id ?? "—"}</td>
                    <td className={`px-2 py-2.5 ${row.redacted ? "text-faint" : "text-ink"}`}>{row.name}</td>
                    <td className="tabular px-2 py-2.5 text-right text-ink">{row.index ?? row.risk_index ?? "—"}</td>
                    <td className="tabular px-2 py-2.5 text-right">
                      {row.probability != null ? (
                        <span className="text-crimson">{Math.round(row.probability * 100)}%</span>
                      ) : (
                        <span className="text-faint">{t("account.withheld", "withheld")}</span>
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-muted">{row.band ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!watchlist?.top?.length && (
              <p className="py-8 text-center text-[13px] text-muted">
                {watchFailed
                  ? t("account.watchDown", "Watchlist unavailable — is the backend running?")
                  : watchlist
                    ? t("account.watchEmpty", "The watchlist returned no rows.")
                    : t("common.loading", "Loading…")}
              </p>
            )}
          </div>

          {watchlist?.access?.note && (
            <p className="mt-4 border-t border-black/[0.07] pt-4 text-[12px] leading-relaxed text-muted">{watchlist.access.note}</p>
          )}
        </div>
      </motion.section>

      {/* ------------------------------------------------ password accounts */}
      {me?.demo_mode && me.demo_accounts.length > 0 && (
        <motion.section {...fade(6)} className="bezel" aria-labelledby="demo-accounts-title">
          <div className="bezel-core p-6">
            <h2 id="demo-accounts-title" className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-faint">
              <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {t("account.demoAccounts", "Demo password accounts")}
            </h2>
            <p className="mt-2 max-w-3xl text-[13px] text-muted">
              {t(
                "account.demoAccountsNote",
                "Active because AUTH_ACCOUNTS is unset, and disabled the moment real credentials are configured. The API serves usernames and roles only — passwords are documented in the project README, never returned by an endpoint.",
              )}
            </p>
            <ul className="mt-4 flex flex-wrap gap-2.5">
              {me.demo_accounts.map((a) => (
                <li key={a.username} className="rounded-xl border border-black/10 bg-black/[0.02] px-4 py-2.5">
                  <p className="font-mono text-[12px] text-ink">{a.username}</p>
                  <p className="mt-0.5 text-[10.5px] uppercase tracking-wider text-faint">
                    {roles?.find((r) => r.role === a.role)?.label ?? a.role}
                  </p>
                </li>
              ))}
            </ul>
            {!authenticated && (
              <Link
                prefetch={false}
                href="/?next=%2Faccess"
                className="mt-4 inline-flex items-center gap-1.5 text-[13px] font-semibold text-brand hover:text-brand-deep"
              >
                {t("account.goSignIn", "Go to the sign-in page")} <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Link>
            )}
          </div>
        </motion.section>
      )}
    </div>
  );
}
