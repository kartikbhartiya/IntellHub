"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function SuspectsRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    const target = qs ? `/intel?tab=criminals&${qs}` : `/intel?tab=criminals`;
    router.replace(target);
  }, [router, searchParams]);

  return <div className="p-8 text-center text-muted font-sans">Redirecting to Offender Registry…</div>;
}

export default function SuspectsRedirectPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted">Loading Offender Registry…</div>}>
      <SuspectsRedirectInner />
    </Suspense>
  );
}
