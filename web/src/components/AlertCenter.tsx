"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, BellRing, Car, ScanFace, Settings2, Volume2, VolumeX, X, Monitor } from "lucide-react";
import { Face } from "./CameraFeed";
import { useAlerts, markAllSeen, updateSettings, clearFresh, ago, useNow, type SurvAlert } from "@/lib/alerts";

function Badge({ p }: { p: string | null }) {
  return (
    <span className={`rounded px-1.5 py-px text-[10px] font-bold ${p === "P1" ? "bg-crimson text-white" : "bg-[#f08a24]/20 text-[#b45f0a]"}`}>{p}</span>
  );
}

function Row({ a, now, onOpen }: { a: SurvAlert; now: number; onOpen: () => void }) {
  return (
    <button onClick={onOpen} className="flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-black/[0.04]">
      {a.kind === "face" && a.face_match?.person?.photo_url ? (
        <Face src={a.face_match.person.photo_url} name={a.face_match.person.name} capture className="h-9 w-9 shrink-0 rounded-lg text-[11px]" />
      ) : (
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-azure/10 text-azure">
          {a.kind === "face" ? <ScanFace className="h-4 w-4" /> : <Car className="h-4 w-4" />}
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <Badge p={a.priority} />
          <span className="truncate text-[12.5px] font-semibold text-ink">
            {a.kind === "face" ? a.face_match?.person?.name : a.plate_read}
          </span>
        </span>
        <span className="block truncate text-[11.5px] text-muted">
          {a.kind === "face" ? `${Math.round((a.face_match?.similarity || 0) * 100)}% face match` : `stolen ${a.plate_match?.make_model || "vehicle"}`} · {a.camera_name}
        </span>
        <span className="block text-[10.5px] text-faint">{ago(a.ts, now)} · {a.reasons.slice(0, 2).join(" · ")}</span>
      </span>
    </button>
  );
}

/** The nav bell: unread live alerts, a dropdown, toasts for new ones, and settings. */
export default function AlertCenter() {
  const { alerts, unread, locked, settings, fresh } = useAlerts();
  const [open, setOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const router = useRouter();
  const now = useNow();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => {
    if (!fresh.length) return;
    const t = setTimeout(clearFresh, 9000);
    return () => clearTimeout(t);
  }, [fresh]);

  if (locked) return null;
  const openAlert = (a: SurvAlert) => {
    setOpen(false);
    router.push(`/surveillance?alert=${encodeURIComponent(a.event_id)}`);
  };
  const list = alerts.filter((a) => (a.priority === "P1" || a.priority === "P2") && (!settings.district || a.district === settings.district)).slice(0, 8);

  return (
    <div ref={ref} className="relative ml-1">
      <button
        type="button"
        onClick={() => {
          setOpen((o) => !o);
          if (!open) markAllSeen();
        }}
        aria-label={`${unread} new surveillance alerts`}
        title="Live CCTV & ANPR alerts"
        className={`relative grid h-11 w-11 place-items-center rounded-full transition-colors md:h-9 md:w-9 ${unread ? "bg-crimson/10 text-crimson" : "text-muted hover:bg-black/[0.04] hover:text-ink"}`}
      >
        {unread ? <BellRing className="h-[17px] w-[17px] animate-[wiggle_1.2s_ease-in-out_infinite]" /> : <Bell className="h-[17px] w-[17px]" />}
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 grid h-[18px] min-w-[18px] place-items-center rounded-full bg-crimson px-1 text-[10px] font-bold text-white ring-2 ring-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.18 }}
            className="fixed right-4 top-[84px] z-50 w-[min(92vw,380px)] overflow-hidden rounded-2xl border border-black/10 bg-white shadow-[0_30px_80px_-24px_rgba(17,20,32,0.35)] md:absolute md:right-0 md:top-12"
          >
            <div className="flex items-center justify-between border-b border-black/[0.06] px-4 py-3">
              <div>
                <div className="text-[13px] font-bold text-ink">Live alerts</div>
                <div className="text-[11px] text-faint">Face & plate matches awaiting review</div>
              </div>
              <div className="flex items-center gap-1">
                <button onClick={() => updateSettings({ sound: !settings.sound })} title={settings.sound ? "Sound on" : "Sound off"}
                        className="grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-black/[0.05] hover:text-ink">
                  {settings.sound ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />}
                </button>
                <button onClick={() => setShowSettings((s) => !s)} title="Alert settings"
                        className={`grid h-8 w-8 place-items-center rounded-full hover:bg-black/[0.05] ${showSettings ? "text-brand" : "text-muted hover:text-ink"}`}>
                  <Settings2 className="h-4 w-4" />
                </button>
              </div>
            </div>
            {showSettings && (
              <div className="space-y-2 border-b border-black/[0.06] bg-black/[0.02] px-4 py-3 text-[12px]">
                <label className="flex items-center justify-between">
                  <span>Notify for</span>
                  <select value={settings.minPriority} onChange={(e) => updateSettings({ minPriority: e.target.value as any })}
                          className="rounded-lg border border-black/10 bg-white px-2 py-1">
                    <option value="P1">P1 only</option>
                    <option value="P2">P1 and P2</option>
                  </select>
                </label>
                <label className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5"><Monitor className="h-3.5 w-3.5" /> Desktop notifications</span>
                  <input type="checkbox" checked={settings.desktop}
                         onChange={async (e) => {
                           const on = e.target.checked;
                           if (on && typeof Notification !== "undefined" && Notification.permission !== "granted") {
                             const p = await Notification.requestPermission();
                             if (p !== "granted") return;
                           }
                           updateSettings({ desktop: on });
                         }} />
                </label>
                <label className="flex items-center justify-between">
                  <span>Only my district</span>
                  <select value={settings.district} onChange={(e) => updateSettings({ district: e.target.value })}
                          className="max-w-[160px] rounded-lg border border-black/10 bg-white px-2 py-1">
                    <option value="">All Karnataka</option>
                    {[...new Set(alerts.map((a) => a.district))].sort().map((d) => <option key={d}>{d}</option>)}
                  </select>
                </label>
              </div>
            )}
            <div className="max-h-[60vh] overflow-y-auto p-1.5">
              {list.length === 0 && <p className="px-3 py-6 text-center text-[12.5px] text-muted">No alerts right now.</p>}
              {list.map((a) => <Row key={a.event_id} a={a} now={now} onOpen={() => openAlert(a)} />)}
            </div>
            <Link href="/surveillance" onClick={() => setOpen(false)}
                  className="block border-t border-black/[0.06] px-4 py-2.5 text-center text-[12.5px] font-semibold text-brand hover:bg-brand/5">
              Open CCTV & ANPR command →
            </Link>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Toasts for new alerts */}
      <div className="pointer-events-none fixed right-4 top-[92px] z-[70] flex w-[min(92vw,360px)] flex-col gap-2">
        <AnimatePresence>
          {fresh.map((a) => (
            <motion.div
              key={a.event_id}
              initial={{ opacity: 0, x: 40, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40 }}
              transition={{ type: "spring", stiffness: 380, damping: 30 }}
              className={`pointer-events-auto overflow-hidden rounded-2xl border bg-white shadow-[0_24px_60px_-20px_rgba(17,20,32,0.4)] ${a.priority === "P1" ? "border-crimson/40" : "border-[#f08a24]/40"}`}
            >
              <div className={`h-1 ${a.priority === "P1" ? "bg-crimson" : "bg-[#f08a24]"}`} />
              <div className="flex items-start gap-1 p-1">
                <div className="min-w-0 flex-1"><Row a={a} now={now} onOpen={() => openAlert(a)} /></div>
                <button onClick={clearFresh} className="m-1.5 text-faint hover:text-ink" aria-label="Dismiss"><X className="h-4 w-4" /></button>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
