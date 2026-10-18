"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Lock, ArrowRight } from "lucide-react";

/**
 * Shown when the API refuses a person-level surface for lack of a role.
 *
 * This state exists because the refusal is the feature. Person-level records —
 * dossiers, registries, the co-offender graph — are gated server-side, and a
 * signed-out visitor genuinely cannot see them. Rendering an empty table or a
 * generic "something went wrong" would misreport a working control as a fault,
 * and would teach an operator to ignore it.
 *
 * Aggregate surfaces do not use this: they stay readable signed out and
 * pseudonymise the person-level blocks they contain, so the control is visible
 * without credentials.
 */
export default function AccessRequired({
  what = "This record",
  requires = "investigator",
}: {
  what?: string;
  requires?: string;
}) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-[52vh] flex-col items-center justify-center px-6 text-center">
      <span className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-brand/12 text-brand">
        <Lock className="h-6 w-6" />
      </span>
      <h2 className="text-display text-2xl font-bold text-ink">Sign in to view this</h2>
      <p className="mt-2 max-w-md text-[14px] leading-relaxed text-muted">
        {what} is person-level data and is restricted to the{" "}
        <span className="font-semibold text-ink">{requires}</span> role or above.
        The restriction is enforced by the API, not by this page.
      </p>
      <p className="mt-3 max-w-md text-[12.5px] text-faint">
        Aggregate intelligence — forecasts, hotspots, district indices, the model
        registry — remains available without signing in.
      </p>
      <Link prefetch={false}
        href={`/?next=${encodeURIComponent(pathname || "/overview")}`}
        className="mt-6 inline-flex items-center gap-2 rounded-full bg-brand px-5 py-2.5 text-sm font-semibold text-white transition-all hover:brightness-110"
      >
        Sign in <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}
