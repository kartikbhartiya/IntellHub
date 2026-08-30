"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function MissingRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    const target = qs ? `/intel?tab=missing&${qs}` : `/intel?tab=missing`;
    router.replace(target);
  }, [router, searchParams]);

  return <div className="p-8 text-center text-muted font-sans">Opening Missing Persons Registry…</div>;
}

export default function MissingRedirectPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted">Loading Missing Persons Registry…</div>}>
      <MissingRedirectInner />
    </Suspense>
  );
}
