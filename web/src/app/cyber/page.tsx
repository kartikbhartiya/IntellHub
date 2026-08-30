"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function CyberRedirectInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    const target = qs ? `/intel?tab=cybercrime&${qs}` : `/intel?tab=cybercrime`;
    router.replace(target);
  }, [router, searchParams]);

  return <div className="p-8 text-center text-muted font-sans">Opening Cyber & Fraud Rings…</div>;
}

export default function CyberRedirectPage() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted">Loading Cyber Registry…</div>}>
      <CyberRedirectInner />
    </Suspense>
  );
}
