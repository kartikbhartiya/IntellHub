/**
 * Client-side export.
 *
 * Every page in this platform is read-only browsing: an analyst can look at a
 * watchlist, a docket or a district ranking, and then has no way to take it
 * anywhere. In practice that means the work leaves through a screenshot or gets
 * retyped, which is both slower and less accurate than the thing it replaces.
 *
 * Export is done in the browser from data already fetched, so it needs no new
 * endpoint, no persistence layer and no server round-trip — which is why it can
 * land before the database work rather than after it.
 *
 * Two deliberate choices:
 *
 * - **Every export carries a provenance header.** A CSV that leaves this system
 *   and arrives in a meeting with no indication of when it was pulled, by whom,
 *   or that it came from simulated data, is exactly the artefact the model
 *   registry exists to prevent. The header travels with the file.
 * - **Nothing is invented to fill a column.** A missing value exports empty
 *   rather than as a plausible default.
 */

export interface ExportMeta {
  /** What this extract is, in words a reader outside the app will understand. */
  title: string;
  /** Filters or scope that produced it, e.g. "district=Belagavi, status=Open". */
  scope?: string;
  /** Set when the underlying corpus is not real records. */
  simulated?: boolean;
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = typeof value === "object" ? JSON.stringify(value) : String(value);
  // Quote when the value could break the row, and double any embedded quote.
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function provenance(meta: ExportMeta, rowCount: number): string[] {
  const lines = [
    `# ${meta.title}`,
    `# Exported: ${new Date().toISOString()}`,
    `# Rows: ${rowCount}`,
  ];
  if (meta.scope) lines.push(`# Scope: ${meta.scope}`);
  if (meta.simulated) {
    lines.push(
      "# SOURCE: simulated CCTNS-shaped data, not production KSP records.",
      "# Figures describe a documented simulation and are not field measurements."
    );
  }
  lines.push("# Decision support only — not evidence, and not a substitute for a case file.");
  return lines;
}

/** Build the CSV text. Separated from the download so it can be unit-tested. */
export function toCSV(
  rows: Record<string, unknown>[],
  columns: { key: string; label: string }[],
  meta: ExportMeta
): string {
  const header = columns.map((c) => csvCell(c.label)).join(",");
  const body = rows.map((r) => columns.map((c) => csvCell(r[c.key])).join(","));
  return [...provenance(meta, rows.length), header, ...body].join("\n");
}

function download(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoked on the next tick: revoking synchronously can cancel the download in
  // some browsers before it has read the blob.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function slug(text: string) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function exportCSV(
  rows: Record<string, unknown>[],
  columns: { key: string; label: string }[],
  meta: ExportMeta
) {
  const stamp = new Date().toISOString().slice(0, 10);
  download(
    `ksp-${slug(meta.title)}-${stamp}.csv`,
    toCSV(rows, columns, meta),
    "text/csv;charset=utf-8"
  );
}

/**
 * A printable briefing. Opens the browser's own print dialogue rather than
 * bundling a PDF library: it is ~100 lines instead of ~300 KB of JavaScript, it
 * respects the user's paper size, and "Save as PDF" is already in that dialogue.
 */
export function exportPrintable(
  rows: Record<string, unknown>[],
  columns: { key: string; label: string }[],
  meta: ExportMeta
) {
  const esc = (v: unknown) =>
    String(v ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] as string));

  const head = columns.map((c) => `<th scope="col">${esc(c.label)}</th>`).join("");
  const body = rows
    .map((r) => `<tr>${columns.map((c) => `<td>${esc(r[c.key])}</td>`).join("")}</tr>`)
    .join("");

  const win = window.open("", "_blank", "width=1100,height=800");
  if (!win) return;                       // pop-up blocked; nothing to clean up
  win.document.write(`<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>${esc(meta.title)}</title>
<style>
  body { font: 12px/1.45 system-ui, sans-serif; color: #1d1d1f; margin: 28px; }
  h1 { font-size: 19px; margin: 0 0 4px; }
  .meta { color: #6b6b73; font-size: 11px; margin-bottom: 14px; }
  .notice { border: 1px solid #d9b24a; background: #fdf6e3; padding: 8px 10px;
            font-size: 11px; margin-bottom: 14px; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ddd; padding: 5px 7px; text-align: left; }
  th { background: #f4f4f6; }
  tr:nth-child(even) td { background: #fafafa; }
  @media print { .noprint { display: none; } }
</style></head><body>
<h1>${esc(meta.title)}</h1>
<div class="meta">
  Exported ${esc(new Date().toLocaleString())} &middot; ${rows.length} rows
  ${meta.scope ? `&middot; ${esc(meta.scope)}` : ""}
</div>
${meta.simulated
      ? `<div class="notice"><strong>Simulated data.</strong> This extract comes from
         CCTNS-shaped simulated records, not production KSP data. Figures describe a
         documented simulation and are not field measurements.</div>`
      : ""}
<div class="notice">Decision support only. Not evidence, and not a substitute for a case file.</div>
<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
<p class="noprint" style="margin-top:16px;color:#6b6b73">
  Use your browser's Print dialogue and choose "Save as PDF".</p>
</body></html>`);
  win.document.close();
}
