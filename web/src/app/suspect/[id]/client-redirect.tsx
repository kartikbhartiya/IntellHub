"use client";

import { useEffect, Suspense } from "react";
import { useRouter, useParams, useSearchParams } from "next/navigation";

function SuspectIdRedirectInner() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();

  useEffect(() => {
    let rawId = (params?.id as string) || searchParams.get("id") || "";
    if (rawId && rawId !== "index") {
      if (/^\d+$/.test(rawId) && !rawId.toUpperCase().startsWith("SUS-")) {
        const num = parseInt(rawId, 10);
        rawId = `SUS-${num < 1000 ? num + 1000 : num}`;
      } else if (!rawId.toUpperCase().startsWith("SUS-")) {
        rawId = `SUS-${rawId.toUpperCase()}`;
      }
    } else {
      rawId = "";
    }
    const target = rawId ? `/suspect?id=${encodeURIComponent(rawId)}` : "/intel?tab=criminals";
    router.replace(target);
  }, [router, params, searchParams]);

  return <div className="p-8 text-center text-muted font-sans">Opening Suspect Dossier…</div>;
}

export default function SuspectIdClientRedirect() {
  return (
    <Suspense fallback={<div className="p-8 text-center text-muted">Loading Suspect Dossier…</div>}>
      <SuspectIdRedirectInner />
    </Suspense>
  );
}
