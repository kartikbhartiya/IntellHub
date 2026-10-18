"use client";

/**
 * Query filters (sent to the server, so expansion respects them too) and display
 * options (applied on the canvas only). Kept visually separate because they behave
 * differently: changing a filter reloads the view, changing a display option does not.
 */
import { RotateCcw } from "lucide-react";
import type { ColorBy, EntityType, Filters, NetworkMeta } from "./types";
import { DEFAULT_FILTERS, ENTITY_TYPES } from "./types";
import { TYPE_STYLE } from "./style";
import ShapeGlyph from "./ShapeGlyph";

interface Props {
  filters: Filters;
  onFilters: (f: Filters) => void;
  meta: NetworkMeta | null;
  colorBy: ColorBy;
  onColorBy: (c: ColorBy) => void;
  showPredicted: boolean;
  onShowPredicted: (v: boolean) => void;
  predictedCount: number;
  showLabels: boolean;
  onShowLabels: (v: boolean) => void;
}

export default function FilterPanel({ filters, onFilters, meta, colorBy, onColorBy, showPredicted, onShowPredicted, predictedCount, showLabels, onShowLabels }: Props) {
  const set = (patch: Partial<Filters>) => onFilters({ ...filters, ...patch });
  const toggleType = (t: EntityType) => {
    const has = filters.types.includes(t);
    const next = has ? filters.types.filter((x) => x !== t) : [...filters.types, t];
    if (next.length) set({ types: ENTITY_TYPES.filter((x) => next.includes(x)) });
  };
  const years = meta?.years ? range(meta.years[0], meta.years[1]) : [];
  const dirty = JSON.stringify(filters) !== JSON.stringify(DEFAULT_FILTERS);

  return (
    <div className="space-y-4">
      <Group title="Entity types" hint={filters.types.includes("Case") ? undefined : "FIRs hidden: people are joined by derived co-accused links."}>
        <div className="flex flex-wrap gap-1.5">
          {ENTITY_TYPES.map((t) => {
            const on = filters.types.includes(t);
            return (
              <button
                key={t}
                onClick={() => toggleType(t)}
                aria-pressed={on}
                className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] font-semibold transition-colors ${
                  on ? "border-black/15 bg-white text-ink shadow-sm" : "border-transparent bg-black/[0.04] text-faint line-through"}`}
              >
                <ShapeGlyph shape={TYPE_STYLE[t].shape} color={on ? TYPE_STYLE[t].color : "#b9bdc7"} />
                {TYPE_STYLE[t].plural}
              </button>
            );
          })}
        </div>
      </Group>

      <Group title="Expansion depth">
        <Segmented
          value={String(filters.depth)}
          options={[{ v: "1", label: "1 hop" }, { v: "2", label: "2 hops" }]}
          onChange={(v) => set({ depth: v === "1" ? 1 : 2 })}
        />
      </Group>

      <Group title="Node cap" hint="Larger views take longer to settle. The server never returns more than 500.">
        <Segmented
          value={String(filters.maxNodes)}
          options={[{ v: "0", label: "Auto" }, { v: "100", label: "100" }, { v: "300", label: "300" }, { v: "500", label: "500" }]}
          onChange={(v) => set({ maxNodes: Number(v) })}
        />
      </Group>

      <Group title="FIR filters" hint="With any of these set, people are reached only through matching FIRs.">
        <div className="grid grid-cols-2 gap-1.5">
          <Select label="District" value={filters.district} onChange={(v) => set({ district: v })} options={meta?.districts || []} wide />
          <Select label="Crime type" value={filters.crimeType} onChange={(v) => set({ crimeType: v })} options={meta?.crime_types || []} wide />
          <Select label="From year" value={filters.yearFrom} onChange={(v) => set({ yearFrom: v })} options={years} />
          <Select label="To year" value={filters.yearTo} onChange={(v) => set({ yearTo: v })} options={years} />
        </div>
      </Group>

      <Group title="People">
        <div className="space-y-1">
          <Check label="Only rowdy-sheeters" checked={filters.rowdy} onChange={(v) => set({ rowdy: v })} />
          <Check label="Only active warrants" checked={filters.warrant} onChange={(v) => set({ warrant: v })} />
        </div>
      </Group>

      {dirty && (
        <button onClick={() => onFilters(DEFAULT_FILTERS)} className="flex items-center gap-1.5 text-[11.5px] font-semibold text-muted hover:text-ink">
          <RotateCcw className="h-3.5 w-3.5" /> Reset filters
        </button>
      )}

      <div className="border-t border-black/[0.06] pt-4">
        <Group title="Colour by">
          <Segmented
            value={colorBy}
            options={[{ v: "type", label: "Type" }, { v: "community", label: "Syndicate" }, { v: "risk", label: "Risk" }]}
            onChange={(v) => onColorBy(v as ColorBy)}
          />
        </Group>
        <div className="mt-3 space-y-1">
          <Check
            label={`Predicted leads overlay${predictedCount ? ` (${predictedCount})` : ""}`}
            checked={showPredicted}
            onChange={onShowPredicted}
            hint="Adamic-Adar over shared FIRs and registry associates. A lead to check, not evidence."
          />
          <Check label="Labels" checked={showLabels} onChange={onShowLabels} hint="Selected and hovered neighbourhoods are always labelled." />
        </div>
      </div>
    </div>
  );
}

function range(a: number, b: number) {
  return Array.from({ length: Math.max(0, b - a + 1) }, (_, i) => String(a + i));
}

function Group({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">{title}</h4>
      {children}
      {hint && <p className="mt-1.5 text-[10.5px] leading-snug text-faint">{hint}</p>}
    </div>
  );
}

export function Segmented({ value, options, onChange }: { value: string; options: { v: string; label: string }[]; onChange: (v: string) => void }) {
  return (
    <div className="flex rounded-lg bg-black/[0.045] p-0.5" role="radiogroup">
      {options.map((o) => (
        <button
          key={o.v}
          role="radio"
          aria-checked={value === o.v}
          onClick={() => onChange(o.v)}
          className={`flex-1 rounded-md px-2 py-1.5 text-[11.5px] font-semibold transition-all ${
            value === o.v ? "bg-white text-ink shadow-sm" : "text-muted hover:text-ink"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Select({ label, value, onChange, options, wide }: { label: string; value: string; onChange: (v: string) => void; options: string[]; wide?: boolean }) {
  return (
    <label className={`block ${wide ? "col-span-2" : ""}`}>
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-full rounded-lg border bg-white px-2.5 py-2 text-[12px] font-medium focus:border-brand/50 focus:outline-none ${
          value ? "border-brand/40 text-ink" : "border-black/10 text-muted"}`}
      >
        <option value="">{`Any ${label.toLowerCase()}`}</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  );
}

function Check({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 hover:bg-black/[0.03]" title={hint}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-0.5 h-3.5 w-3.5 accent-[#b07d16]" />
      <span className="text-[12px] font-medium text-ink">{label}</span>
    </label>
  );
}
