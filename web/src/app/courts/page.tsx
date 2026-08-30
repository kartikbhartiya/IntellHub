"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function CourtsRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    const target = qs ? `/intel?tab=courts&${qs}` : `/intel?tab=courts`;
    router.replace(target);
  }, [router, searchParams]);

  return <div className="p-8 text-center text-muted font-sans">Opening Court & Chargesheet Registry…</div>;
}

export default function CourtsRedirectPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted">Loading Court Registry…</div>}>
      <CourtsRedirectInner />
    </Suspense>
  );
}
