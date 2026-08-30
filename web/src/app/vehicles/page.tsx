"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function VehiclesRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    const target = qs ? `/intel?tab=vehicles&${qs}` : `/intel?tab=vehicles`;
    router.replace(target);
  }, [router, searchParams]);

  return <div className="p-8 text-center text-muted font-sans">Opening Stolen Vehicles Registry…</div>;
}

export default function VehiclesRedirectPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted">Loading Vehicles Registry…</div>}>
      <VehiclesRedirectInner />
    </Suspense>
  );
}
