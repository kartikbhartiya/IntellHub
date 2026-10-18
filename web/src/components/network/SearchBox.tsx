"use client";

/**
 * Typeahead over every entity in the graph: people (name, SUS id, alias, gang),
 * FIRs (number, crime type, district), officers (name, KGID), vehicles (plate,
 * however it is typed) and cyber identifiers (phone, UPI, account).
 *
 * Ranking is the server's. This component debounces, cancels stale answers by
 * sequence number (getJSON takes no AbortSignal), and handles the keyboard the way
 * a combobox should: arrows move, Enter picks, Escape closes.
 */
import { useEffect, useId, useRef, useState } from "react";
import { AtSign, Car, FileText, Landmark, LoaderCircle, Search, ShieldCheck, Smartphone, User, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { search } from "./netApi";
import type { EntityType, Facet, SearchResult } from "./types";
import { TYPE_STYLE } from "./style";

export function TypeIcon({ type, id = "", className = "h-3.5 w-3.5" }: { type: EntityType; id?: string; className?: string }) {
  const p = { className, style: { color: TYPE_STYLE[type]?.color }, "aria-hidden": true as const };
  if (type === "Suspect") return <User {...p} />;
  if (type === "Case") return <FileText {...p} />;
  if (type === "Officer") return <ShieldCheck {...p} />;
  if (type === "Vehicle") return <Car {...p} />;
  if (id.startsWith("UPI:")) return <AtSign {...p} />;
  if (id.startsWith("ACCT:")) return <Landmark {...p} />;
  return <Smartphone {...p} />;
}

interface Props {
  placeholder?: string;
  onPick: (r: SearchResult) => void;
  onFacet?: (f: Facet) => void;
  /** For the path pickers: the entity currently chosen, shown in place of the text. */
  value?: SearchResult | null;
  onClear?: () => void;
  autoFocus?: boolean;
  label?: string;
}

export default function SearchBox({ placeholder = "Search name, FIR, KGID, plate, phone…", onPick, onFacet, value, onClear, autoFocus, label }: Props) {
  const listId = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [facets, setFacets] = useState<Facet[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const seq = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);

  const query = text.trim();
  const live = query.length >= 2;

  useEffect(() => {
    // A query too short to search leaves the last answer in state but hidden
    // (`live` gates rendering), and bumping the sequence drops any reply in flight.
    const mine = ++seq.current;
    if (query.length < 2) return;
    const t = setTimeout(() => {
      setLoading(true);
      search(query)
        .then((r) => {
          if (mine !== seq.current) return;
          setResults(r.results || []);
          setFacets(onFacet ? r.facets || [] : []);
          setError(null);
          setActive(0);
        })
        .catch((e) => {
          if (mine !== seq.current) return;
          setResults([]);
          setFacets([]);
          setError(e instanceof ApiError && e.isForbidden
            ? "Sign in as an investigator to search the graph."
            : "Search is unavailable — the intelligence core did not answer.");
        })
        .finally(() => {
          if (mine === seq.current) setLoading(false);
        });
    }, 180);
    return () => clearTimeout(t);
  }, [query, onFacet]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const pick = (r: SearchResult) => {
    onPick(r);
    setText("");
    setResults([]);
    setOpen(false);
  };

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-black/10 bg-white px-3 py-2">
        <TypeIcon type={value.type} id={value.id} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold text-ink">{value.label}</div>
          <div className="truncate text-[11px] text-muted">{value.sub || value.id}</div>
        </div>
        {onClear && (
          <button onClick={onClear} className="rounded-full p-1 text-faint hover:bg-black/5 hover:text-ink" aria-label={`Clear ${label || "selection"}`}>
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
    );
  }

  const shownResults = live ? results : [];
  const shownFacets = live ? facets : [];
  const shownError = live ? error : null;
  const showList = open && live && (shownResults.length > 0 || shownFacets.length > 0 || !!shownError || !loading);

  return (
    <div ref={boxRef} className="relative">
      <div className="relative flex items-center">
        <Search className="pointer-events-none absolute left-3 h-4 w-4 text-faint" aria-hidden />
        <input
          role="combobox"
          aria-label={label || "Search the network"}
          aria-expanded={showList}
          aria-controls={listId}
          aria-activedescendant={showList && shownResults[active] ? `${listId}-${active}` : undefined}
          aria-autocomplete="list"
          autoFocus={autoFocus}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(shownResults.length - 1, a + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(0, a - 1));
            } else if (e.key === "Enter" && shownResults[active]) {
              e.preventDefault();
              pick(shownResults[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          placeholder={placeholder}
          className="w-full rounded-xl border border-black/10 bg-white py-2.5 pl-9 pr-8 text-[13px] font-medium text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none focus:ring-2 focus:ring-brand/20"
        />
        {live && loading && <LoaderCircle className="absolute right-3 h-4 w-4 animate-spin text-faint" aria-hidden />}
      </div>

      {showList && (
        <div
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 z-50 mt-1.5 max-h-[340px] overflow-y-auto rounded-xl border border-black/10 bg-white p-1 shadow-[0_18px_50px_-18px_rgba(17,20,32,0.4)]"
        >
          {shownError && <div className="px-3 py-2.5 text-[12px] text-crimson">{shownError}</div>}
          {!shownError && !loading && shownResults.length === 0 && shownFacets.length === 0 && (
            <div className="px-3 py-2.5 text-[12px] text-muted">Nothing in the graph matches “{query}”.</div>
          )}
          {shownFacets.length > 0 && (
            <div className="flex flex-wrap gap-1 border-b border-black/[0.06] px-2 pb-2 pt-1.5">
              {shownFacets.map((f) => (
                <button
                  key={`${f.field}:${f.value}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    onFacet?.(f);
                    setText("");
                    setOpen(false);
                  }}
                  className="rounded-full border border-brand/30 bg-brand/[0.06] px-2.5 py-1 text-[11px] font-semibold text-brand-deep hover:bg-brand/12"
                  title={`Filter FIRs to ${f.label.toLowerCase()} ${f.value}`}
                >
                  Filter · {f.label}: {f.value} <span className="font-normal text-muted">({f.count.toLocaleString()} FIRs)</span>
                </button>
              ))}
            </div>
          )}
          {shownResults.map((r, i) => (
            <button
              key={r.id}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(r)}
              className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left ${i === active ? "bg-black/[0.045]" : ""}`}
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-black/[0.035]">
                <TypeIcon type={r.type} id={r.id} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-semibold text-ink">{r.label}</span>
                <span className="block truncate text-[11px] text-muted">
                  {TYPE_STYLE[r.type]?.label} · {r.sub || r.id}
                </span>
              </span>
              {r.warrant && <span className="rounded bg-crimson/10 px-1.5 py-0.5 text-[10px] font-bold text-crimson" title="Active warrant">W</span>}
              {r.rowdy && <span className="rounded bg-brand/12 px-1.5 py-0.5 text-[10px] font-bold text-brand" title="Rowdy-sheeter">R</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
