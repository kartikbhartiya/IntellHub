"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Global Error Caught:", error);
  }, [error]);

  // The stack is for whoever is fixing the fault, not for an officer mid-shift;
  // in a production build it is logged to the console above instead of shown.
  const showStack = process.env.NODE_ENV !== "production";

  return (
    // Rendered inside <main>, which already pads for the nav. h-screen/w-screen here
    // added a full viewport on top of that padding and produced a horizontal scroll.
    <div className="flex min-h-[60vh] w-full items-center justify-center px-4 py-10">
      <div role="alert" className="w-full max-w-2xl rounded-2xl border border-crimson/25 bg-white p-8 shadow-[var(--shadow-float)]">
        <h2 className="mb-4 text-2xl font-bold text-crimson">Something went wrong</h2>
        <p className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-black/[0.06] bg-black/[0.03] p-4 font-mono text-sm text-muted">
          {error.message || "An unexpected error occurred while rendering this page."}
          {showStack && error.stack ? `\n\n${error.stack}` : ""}
          {error.digest ? `\n\nReference: ${error.digest}` : ""}
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="rounded-full bg-gradient-to-b from-brand-soft to-brand px-5 py-2.5 font-semibold text-black transition-all hover:brightness-105"
            onClick={() => reset()}
          >
            Try Again
          </button>
          {/* The dashboard moved off the root, which is now the sign-in page. */}
          <Link
            prefetch={false}
            href="/overview"
            className="rounded-full border border-black/10 bg-black/[0.03] px-5 py-2.5 font-medium text-ink transition-colors hover:border-brand/40"
          >
            Back to overview
          </Link>
        </div>
      </div>
    </div>
  );
}
