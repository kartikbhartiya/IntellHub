"use client";

import { useEffect } from "react";
import { useRouter, useParams } from "next/navigation";

export default function ClientRedirect() {
  const router = useRouter();
  const params = useParams();

  useEffect(() => {
    const rawId = params?.id;
    if (rawId) {
      const idStr = Array.isArray(rawId) ? rawId[0] : rawId;
      router.replace(`/case?id=${encodeURIComponent(idStr)}`);
    } else {
      router.replace("/cases");
    }
  }, [params, router]);

  return (
    <div className="flex h-[60vh] flex-col items-center justify-center text-center">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-brand border-t-transparent" />
      <p className="mt-4 text-sm text-muted">Loading case dossier…</p>
    </div>
  );
}
