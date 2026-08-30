"use client";

import { useState } from "react";
import { Download, FileSpreadsheet, Printer, Check } from "lucide-react";
import { exportCSV, exportPrintable, type ExportMeta } from "@/lib/export";

/**
 * Takes what is already on screen out of the browser.
 *
 * Deliberately disabled with an explanation when there is nothing to export,
 * rather than producing an empty file — a zero-row CSV in a meeting is worse
 * than no CSV, because it looks like an answer.
 */
export default function ExportButton({
  rows,
  columns,
  meta,
  className = "",
}: {
  rows: Record<string, unknown>[];
  columns: { key: string; label: string }[];
  meta: ExportMeta;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const empty = !rows || rows.length === 0;

  const run = (kind: "csv" | "print") => {
    if (empty) return;
    if (kind === "csv") exportCSV(rows, columns, meta);
    else exportPrintable(rows, columns, meta);
    setDone(kind);
    setOpen(false);
    setTimeout(() => setDone(null), 2000);
  };

  return (
    <div className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={empty}
        aria-haspopup="menu"
        aria-expanded={open}
        title={empty ? "Nothing to export yet" : `Export ${rows.length} rows`}
        className="inline-flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-3.5 py-2 text-[12.5px] font-medium text-ink transition-all hover:border-brand/40 hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
      >
        {done ? <Check className="h-3.5 w-3.5 text-emerald" /> : <Download className="h-3.5 w-3.5" />}
        {done ? "Exported" : "Export"}
        {!empty && <span className="tabular text-faint">({rows.length})</span>}
      </button>

      {open && !empty && (
        <>
          {/* Click-away closes the menu; keyboard users get Escape via the buttons. */}
          <div
            className="fixed inset-0 z-30"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div
            role="menu"
            className="absolute right-0 z-40 mt-1.5 w-56 overflow-hidden rounded-xl border border-black/10 bg-white shadow-[0_18px_50px_-15px_rgba(17,20,32,0.25)]"
            onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
          >
            <button
              role="menuitem"
              type="button"
              onClick={() => run("csv")}
              className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-[12.5px] text-ink transition-colors hover:bg-black/[0.04]"
            >
              <FileSpreadsheet className="h-4 w-4 text-emerald" />
              <span>
                CSV
                <span className="block text-[11px] text-faint">
                  for a spreadsheet
                </span>
              </span>
            </button>
            <button
              role="menuitem"
              type="button"
              onClick={() => run("print")}
              className="flex w-full items-center gap-2.5 border-t border-black/[0.06] px-3.5 py-2.5 text-left text-[12.5px] text-ink transition-colors hover:bg-black/[0.04]"
            >
              <Printer className="h-4 w-4 text-azure" />
              <span>
                Printable briefing
                <span className="block text-[11px] text-faint">
                  print dialogue → Save as PDF
                </span>
              </span>
            </button>
            <p className="border-t border-black/[0.06] bg-black/[0.02] px-3.5 py-2 text-[10.5px] leading-relaxed text-faint">
              Both carry a provenance header recording when it was pulled and that
              the corpus is simulated.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
