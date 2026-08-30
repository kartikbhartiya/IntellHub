"use client";

import { useEffect } from "react";

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

  return (
    <div className="flex h-screen w-screen items-center justify-center p-8">
      <div className="max-w-2xl rounded-2xl border border-crimson/25 bg-white p-8 shadow-[var(--shadow-float)]">
        <h2 className="mb-4 text-2xl font-bold text-crimson">Something went wrong</h2>
        <p className="max-h-64 overflow-auto rounded-lg border border-black/[0.06] bg-black/[0.03] p-4 font-mono text-sm text-muted">
          {error.message}
          {"\n\n"}
          {error.stack}
        </p>
        <button
          className="mt-6 rounded-full bg-gradient-to-b from-brand-soft to-brand px-5 py-2.5 font-semibold text-black transition-all hover:brightness-105"
          onClick={() => reset()}
        >
          Try Again
        </button>
      </div>
    </div>
  );
}
