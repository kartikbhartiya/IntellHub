"use client";

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Lock, LogIn, LogOut, ShieldCheck, EyeOff, Eye, AlertTriangle } from "lucide-react";
import { getJSON, login, logout, whoami, type Identity } from "@/lib/api";

const FADE_FROM = { opacity: 0, y: 16 };
const FADE_TO = { opacity: 1, y: 0 };
const EASE = [0.16, 1, 0.3, 1] as const;
const TRANSITIONS = Array.from({ length: 6 }, (_, i) => ({
  duration: 0.6,
  delay: i * 0.05,
  ease: EASE,
}));
const fade = (step = 0) => ({
  initial: FADE_FROM,
  animate: FADE_TO,
  transition: TRANSITIONS[Math.min(step, TRANSITIONS.length - 1)],
});

const ROLE_TONE: Record<string, string> = {
  analyst: "bg-azure/12 border-azure/25 text-azure",
  investigator: "bg-brand/12 border-brand/25 text-brand",
  supervisor: "bg-crimson/12 border-crimson/25 text-crimson",
};

export default function AccessPage() {
  const [me, setMe] = useState<Identity | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [watchlist, setWatchlist] = useState<any>(null);

  const refresh = useCallback(async () => {
    try {
      setMe(await whoami());
    } catch {
      /* backend not reachable — page still renders the policy */
    }
    try {
      setWatchlist(await getJSON("/recidivism"));
    } catch {
      setWatchlist(null);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      setPassword("");
      await refresh();
    } catch {
      setError("Invalid credentials");
    } finally {
      setBusy(false);
    }
  };

  const onLogout = async () => {
    logout();
    await refresh();
  };

  const role = me?.identity?.role ?? "analyst";
  const authed = me?.identity?.authenticated ?? false;
  const redacted = watchlist?.access?.redacted ?? true;

  return (
    <div className="space-y-7">
      <motion.header {...fade()}>
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <Lock className="h-8 w-8 text-brand" strokeWidth={2} /> Access Control
        </h1>
        <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-muted">
          Aggregate intelligence and person-level intelligence do not carry the same
          access rules. District statistics are open; a ranked list of named individuals
          is a dossier about people, and it is gated.
        </p>
      </motion.header>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* ------------------------------------------------- current identity */}
        <motion.div {...fade(1)} className="bezel">
          <div className="bezel-core h-full p-6">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-faint">
              <ShieldCheck className="h-4 w-4" /> Signed in as
            </div>
            <p className="text-display mt-3 text-2xl font-bold text-ink">
              {authed ? me?.identity?.user : "Not signed in"}
            </p>
            <span
              className={`mt-3 inline-block rounded-full border px-3 py-1 text-[12px] font-semibold ${
                ROLE_TONE[role] ?? ROLE_TONE.analyst
              }`}
            >
              {role}
            </span>
            <p className="mt-4 text-[12.5px] leading-relaxed text-muted">
              {me?.policy?.[role]}
            </p>

            {authed ? (
              <button
                onClick={onLogout}
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-full border border-black/10 bg-black/[0.03] px-4 py-2.5 text-[13px] font-medium text-ink transition-colors hover:border-crimson/40 hover:text-crimson"
              >
                <LogOut className="h-4 w-4" /> Sign out
              </button>
            ) : (
              <form onSubmit={onSubmit} className="mt-5 space-y-2">
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Username"
                  autoComplete="username"
                  className="w-full rounded-xl border border-black/10 bg-white px-3.5 py-2.5 text-[13px] text-ink outline-none transition-colors focus:border-brand/40"
                />
                <input
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  type="password"
                  placeholder="Password"
                  autoComplete="current-password"
                  className="w-full rounded-xl border border-black/10 bg-white px-3.5 py-2.5 text-[13px] text-ink outline-none transition-colors focus:border-brand/40"
                />
                {error && <p className="text-[12px] text-crimson">{error}</p>}
                <button
                  type="submit"
                  disabled={busy}
                  className="flex w-full items-center justify-center gap-2 rounded-full bg-brand px-4 py-2.5 text-[13px] font-semibold text-white transition-opacity disabled:opacity-50"
                >
                  <LogIn className="h-4 w-4" /> {busy ? "Signing in…" : "Sign in"}
                </button>
              </form>
            )}
          </div>
        </motion.div>

        {/* ------------------------------------------------------ role policy */}
        <motion.div {...fade(2)} className="bezel lg:col-span-2">
          <div className="bezel-core h-full p-6">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
              <ShieldCheck className="h-4 w-4" /> Role policy
            </div>
            <div className="mt-4 space-y-3">
              {(me?.roles ?? ["analyst", "investigator", "supervisor"]).map((r) => (
                <div
                  key={r}
                  className={`rounded-xl border p-4 transition-colors ${
                    r === role ? "border-brand/40 bg-brand/[0.04]" : "border-black/[0.07]"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${
                        ROLE_TONE[r] ?? ROLE_TONE.analyst
                      }`}
                    >
                      {r}
                    </span>
                    {r === role && (
                      <span className="text-[11px] font-medium text-brand">— current</span>
                    )}
                  </div>
                  <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
                    {me?.policy?.[r]}
                  </p>
                </div>
              ))}
            </div>
            {me?.warning && (
              <div className="mt-4 flex items-start gap-2 rounded-xl border border-brand/25 bg-brand/[0.06] p-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                <p className="text-[12px] leading-relaxed text-ink">{me.warning}</p>
              </div>
            )}
          </div>
        </motion.div>
      </div>

      {/* --------------------------------------------- live redaction proof */}
      <motion.div {...fade(3)} className="bezel">
        <div className="bezel-core p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-brand">
              {redacted ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              Recidivism watchlist — live
            </div>
            <span
              className={`rounded-full border px-3 py-1 text-[12px] font-semibold ${
                redacted
                  ? "border-azure/25 bg-azure/12 text-azure"
                  : "border-crimson/25 bg-crimson/12 text-crimson"
              }`}
            >
              {redacted ? "Redacted" : "Full access"}
            </span>
          </div>

          <p className="mt-3 max-w-3xl text-[13px] leading-relaxed text-muted">
            Below the <span className="font-semibold text-ink">supervisor</span> role this
            endpoint is <span className="font-semibold text-ink">redacted rather than refused</span>:
            the shape of the watchlist survives — counts, bands, method — while names become
            stable pseudonyms and calibrated probabilities are withheld. A 403 would hide
            that any control exists; this shows it working. Sign in above to see the
            same table change.
          </p>

          <div className="mt-5 overflow-x-auto">
            <table className="w-full min-w-[620px] border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-black/[0.08] text-[10px] uppercase tracking-[0.15em] text-faint">
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">Identifier</th>
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">Name</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Index</th>
                  <th scope="col" className="px-2 py-2.5 text-right font-semibold">Probability</th>
                  <th scope="col" className="px-2 py-2.5 text-left font-semibold">Band</th>
                </tr>
              </thead>
              <tbody>
                {(watchlist?.top ?? []).slice(0, 8).map((row: any, i: number) => (
                  <tr key={row.criminal_id ?? row.offender_id ?? i} className="border-b border-black/[0.05]">
                    <td className="px-2 py-2.5 font-mono text-[12px] text-muted">
                      {row.criminal_id ?? row.offender_id ?? "—"}
                    </td>
                    <td className={`px-2 py-2.5 ${row.redacted ? "text-faint" : "text-ink"}`}>
                      {row.name}
                    </td>
                    <td className="tabular px-2 py-2.5 text-right text-ink">
                      {row.index ?? row.risk_index ?? "—"}
                    </td>
                    <td className="tabular px-2 py-2.5 text-right">
                      {row.probability != null ? (
                        <span className="text-crimson">
                          {Math.round(row.probability * 100)}%
                        </span>
                      ) : (
                        <span className="text-faint">withheld</span>
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-muted">{row.band ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!watchlist?.top?.length && (
              <p className="py-8 text-center text-[13px] text-muted">
                Watchlist unavailable — is the backend running?
              </p>
            )}
          </div>

          {watchlist?.access?.note && (
            <p className="mt-4 border-t border-black/[0.07] pt-4 text-[12px] leading-relaxed text-muted">
              {watchlist.access.note}
            </p>
          )}
        </div>
      </motion.div>

      {/* ------------------------------------------------ demo credentials */}
      {me?.demo_mode && (
        <motion.div {...fade(4)} className="bezel">
          <div className="bezel-core p-6">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-faint">
              <AlertTriangle className="h-4 w-4" /> Demo credentials
            </div>
            <p className="mt-2 text-[13px] text-muted">
              Active because <code className="text-brand">AUTH_ACCOUNTS</code> is unset, and
              disabled the moment real credentials are configured. The API serves usernames
              and roles only — passwords are documented in the project README, never returned
              by an endpoint. Click a username to fill the form; the password is typed.
            </p>
            <div className="mt-4 flex flex-wrap gap-3">
              {me.demo_accounts.map((a) => (
                <button
                  key={a.username}
                  onClick={() => setUsername(a.username)}
                  className="rounded-xl border border-black/10 bg-black/[0.02] px-4 py-3 text-left transition-colors hover:border-brand/40"
                >
                  <p className="font-mono text-[12px] text-ink">{a.username}</p>
                  <p className="mt-0.5 text-[11px] uppercase tracking-wider text-faint">
                    {a.role}
                  </p>
                </button>
              ))}
            </div>
            <p className="mt-3 text-[11px] text-faint">
              Click a card to fill the username above.
            </p>
          </div>
        </motion.div>
      )}
    </div>
  );
}
