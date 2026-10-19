"use client";

/**
 * Profile of the selected entity, from /network/entity/<id>.
 *
 * The header renders immediately from the node already on the canvas; the
 * profile fills in when it arrives. What the API withholds (the recidivism index
 * below `watchlist_names`) is shown as withheld, with the reason — never as a
 * blank that could be read as "no risk".
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowRight, Crosshair, EyeOff, LoaderCircle, MapPin, Route, ShieldAlert, Users, Waypoints, X,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { entity } from "./netApi";
import type { CaseRow, CountRow, EntityProfile, NetNode } from "./types";
import { RISK_COLOR, TYPE_STYLE, communityColor } from "./style";
import { TypeIcon } from "./SearchBox";

interface Props {
  node: NetNode;
  inGraph: (id: string) => boolean;
  onClose: () => void;
  onExpand: (id: string) => void;
  onHide: (id: string) => void;
  onPathFrom: (node: NetNode) => void;
  onFocus: (id: string) => void;
  /** Select an entity named in the profile: on the canvas if present, else add it. */
  onOpenEntity: (id: string) => void;
  onOpenSyndicate: (id: string) => void;
}

export default function EntityPanel({ node, inGraph, onClose, onExpand, onHide, onPathFrom, onFocus, onOpenEntity, onOpenSyndicate }: Props) {
  const cache = useRef(new Map<string, EntityProfile>());
  const [profile, setProfile] = useState<EntityProfile | null>(null);
  // Registry photos are remote; when one does not load, show the type icon rather
  // than a broken-image glyph.
  const [photoFailed, setPhotoFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const hit = cache.current.get(node.id);
    setProfile(hit || null);
    setError(null);
    if (hit) return;
    entity(node.id)
      .then((p) => {
        if (!alive) return;
        cache.current.set(node.id, p);
        setProfile(p);
      })
      .catch((e) => {
        if (!alive) return;
        setError(e instanceof ApiError && e.isForbidden
          ? "Your sign-in does not allow this profile."
          : e instanceof ApiError && e.status === 404
            ? "This entity is no longer in the graph (the corpus may have been reloaded)."
            : "The profile could not be loaded.");
      });
    return () => {
      alive = false;
    };
  }, [node.id]);

  const ts = TYPE_STYLE[node.type];
  const dossier = profile?.links?.find((l) => !l.href.startsWith("/map"));
  const mapLink = profile?.links?.find((l) => l.href.startsWith("/map"));

  return (
    <aside className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-black/10 bg-white/97 shadow-[0_20px_60px_-24px_rgba(17,20,32,0.35)] backdrop-blur-xl" aria-label="Selected entity">
      <div className="h-1 w-full shrink-0" style={{ background: ts?.color }} />
      <div className="flex items-start gap-3 px-4 pb-3 pt-3.5">
        {node.type === "Suspect" && !photoFailed && (profile?.suspect?.photo_url || node.photo) ? (
          // eslint-disable-next-line @next/next/no-img-element -- static export; remote registry photo
          <img
            src={(profile?.suspect?.photo_url || node.photo) as string}
            alt=""
            onError={() => setPhotoFailed(true)}
            className="h-12 w-12 shrink-0 rounded-xl border border-black/10 object-cover"
          />
        ) : (
          <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-black/[0.04]">
            <TypeIcon type={node.type} id={node.id} className="h-5 w-5" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="text-[10px] font-semibold uppercase tracking-[0.16em]" style={{ color: ts?.color }}>
            {ts?.label}
            {node.hop === 0 && <span className="ml-1.5 text-faint">· seed</span>}
          </div>
          <h3 className="mt-0.5 break-words text-[16px] font-bold leading-tight text-ink">{node.label}</h3>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted">{node.id}</p>
        </div>
        <button onClick={onClose} className="rounded-full border border-black/10 p-1.5 text-faint hover:text-ink" aria-label="Close profile">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="flex flex-wrap gap-1 px-4 pb-3">
        {node.warrant && <Chip tone="crimson">Active warrant</Chip>}
        {node.rowdy && <Chip tone="brand">Rowdy-sheeter</Chip>}
        {profile?.suspect?.flags.history_sheet && <Chip>History-sheeter</Chip>}
        {node.risk && <Chip style={{ color: RISK_COLOR[node.risk] }}>{node.risk} risk</Chip>}
        {node.gang && <Chip>{node.gang}</Chip>}
        {node.community && (
          <button onClick={() => onOpenSyndicate(node.community as string)} className="rounded-md border px-1.5 py-0.5 text-[10.5px] font-semibold hover:bg-black/[0.03]"
                  style={{ borderColor: communityColor(node.community), color: communityColor(node.community) }}
                  title="Load this syndicate">
            {node.community}
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-1.5 px-4 pb-3">
        <Action onClick={() => onExpand(node.id)} icon={Waypoints} primary>Expand</Action>
        {dossier ? (
          <Link prefetch={false} href={dossier.href} className="flex items-center justify-center gap-1.5 rounded-lg border border-black/10 bg-white px-2 py-2 text-[12px] font-semibold text-ink hover:border-brand/40 hover:text-brand">
            {dossier.label} <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        ) : (
          <span className="rounded-lg border border-dashed border-black/10 px-2 py-2 text-center text-[12px] text-faint">Loading…</span>
        )}
        {mapLink && (
          <Link prefetch={false} href={mapLink.href} className="flex items-center justify-center gap-1.5 rounded-lg border border-black/10 bg-white px-2 py-2 text-[12px] font-semibold text-ink hover:border-brand/40 hover:text-brand">
            <MapPin className="h-3.5 w-3.5" /> Show on map
          </Link>
        )}
        <Action onClick={() => onPathFrom(node)} icon={Route}>Find path from here</Action>
        <Action onClick={() => onFocus(node.id)} icon={Crosshair}>Centre</Action>
        <Action onClick={() => onHide(node.id)} icon={EyeOff}>Hide</Action>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto border-t border-black/[0.06] px-4 py-3.5 text-[12.5px]">
        {profile?.jurisdiction && !profile.jurisdiction.in_scope && (
          <div className="flex gap-2 rounded-lg border border-brand/30 bg-brand/[0.06] p-2.5 text-[11.5px] text-brand-deep">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {profile.jurisdiction.note}
          </div>
        )}
        {error && <div className="rounded-lg border border-crimson/25 bg-crimson/[0.05] p-2.5 text-[12px] text-crimson">{error}</div>}
        {!profile && !error && (
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <LoaderCircle className="h-4 w-4 animate-spin" /> Loading profile…
          </div>
        )}
        {profile && <Body p={profile} inGraph={inGraph} onOpenEntity={onOpenEntity} onOpenSyndicate={onOpenSyndicate} />}
      </div>
    </aside>
  );
}

function Body({ p, inGraph, onOpenEntity, onOpenSyndicate }: {
  p: EntityProfile; inGraph: (id: string) => boolean; onOpenEntity: (id: string) => void; onOpenSyndicate: (id: string) => void;
}) {
  if (p.suspect) {
    const s = p.suspect;
    return (
      <>
        <Grid>
          <Stat label="FIRs in graph" value={s.cases_count} />
          <Stat label="Open" value={s.open_cases} />
          <Stat label="Co-offenders" value={s.co_offenders_total} />
          <Stat label="Known associates" value={s.known_associates_total} />
          <Stat label="Past FIRs (registry)" value={s.past_fir_count ?? "—"} />
          <Stat label="Past arrests" value={s.past_arrests ?? "—"} />
        </Grid>
        <KV rows={[["Alias", s.alias], ["Gang (registry)", s.gang], ["Active years", s.active_years ? `${s.active_years[0]}–${s.active_years[1]}` : null]]} />
        {s.syndicate && (
          <button onClick={() => onOpenSyndicate(s.syndicate!.id)} className="flex w-full items-center gap-2 rounded-lg border border-black/10 p-2.5 text-left hover:border-brand/40">
            <Users className="h-4 w-4" style={{ color: communityColor(s.syndicate.id) }} />
            <span className="flex-1">
              <span className="block text-[12px] font-semibold text-ink">{s.syndicate.id}</span>
              <span className="block text-[11px] text-muted">{s.syndicate.size} members · {s.syndicate.risk_level} risk · load syndicate</span>
            </span>
          </button>
        )}
        <Recidivism r={p.recidivism} />
        <Bars title="Crime types" rows={s.crime_types} />
        <Bars title="Districts" rows={s.districts} />
        <EntityList title="Co-offenders (shared FIRs)" items={s.co_offenders.map((c) => ({ id: c.id, label: c.name, note: `${c.shared_cases} shared`, type: "Suspect" as const }))} inGraph={inGraph} onOpen={onOpenEntity} />
        <EntityList title="Known associates (registry)" items={s.known_associates.map((c) => ({ id: c.id, label: c.name, type: "Suspect" as const }))} inGraph={inGraph} onOpen={onOpenEntity} />
        <Cases title="Most recent FIRs" rows={s.recent_cases} inGraph={inGraph} onOpen={onOpenEntity} />
      </>
    );
  }
  if (p.case) {
    const c = p.case;
    return (
      <>
        <KV rows={[["Crime", c.crime_type], ["District", c.district], ["Station", c.station], ["Status", c.status],
                   ["Registered", c.registered], ["Severity", c.severity != null ? `${c.severity}/100` : null],
                   ["Sections", c.sections],
                   ["Fraud amount", c.fraud_amount != null ? `₹${Math.round(c.fraud_amount).toLocaleString("en-IN")}` : null]]} />
        {c.syndicate && (
          <button onClick={() => onOpenSyndicate(c.syndicate!)} className="text-[11.5px] font-semibold underline-offset-2 hover:underline" style={{ color: communityColor(c.syndicate) }}>
            Shared FIR of {c.syndicate} — load syndicate
          </button>
        )}
        <EntityList title="Accused" items={c.accused.map((a) => ({ id: a.id, label: a.name, note: [a.relationship, a.warrant ? "warrant" : null].filter(Boolean).join(" · "), type: "Suspect" as const }))} inGraph={inGraph} onOpen={onOpenEntity} />
        {c.officer && <EntityList title="Investigating officer" items={[{ id: c.officer.id, label: c.officer.name, note: c.officer.rank || undefined, type: "Officer" as const }]} inGraph={inGraph} onOpen={onOpenEntity} />}
        <EntityList title="Vehicles" items={c.vehicles.map((v) => ({ id: v, label: v, type: "Vehicle" as const }))} inGraph={inGraph} onOpen={onOpenEntity} />
        <EntityList title="Cyber identifiers" items={c.identifiers.map((v) => ({ id: v, label: v.split(":").slice(1).join(":"), note: v.split(":")[0], type: "CyberID" as const }))} inGraph={inGraph} onOpen={onOpenEntity} />
      </>
    );
  }
  if (p.officer) {
    const o = p.officer;
    return (
      <>
        <Grid>
          <Stat label="FIRs investigated" value={o.cases_investigated} />
          <Stat label="Open" value={o.open_cases} />
        </Grid>
        <KV rows={[["KGID", o.kgid], ["Rank", o.rank], ["Station", o.station], ["District", o.district], ["Specialisation", o.specialization]]} />
        <p className="text-[11px] leading-relaxed text-faint">An investigating officer is an administrative link. Two people whose FIRs this officer handled are not thereby associates.</p>
        <Bars title="Crime mix" rows={o.crime_types} />
        <Bars title="Districts" rows={o.districts} />
      </>
    );
  }
  if (p.vehicle) {
    const v = p.vehicle;
    return (
      <>
        <KV rows={[["Plate", v.plate], ["Make / model", v.make], ["Recovery", v.recovery_status], ["Stolen in", v.theft_district], ["Theft date", v.theft_date]]} />
        <Cases title="Linked FIRs" rows={v.cases} inGraph={inGraph} onOpen={onOpenEntity} />
      </>
    );
  }
  if (p.identifier) {
    const x = p.identifier;
    return (
      <>
        <Grid>
          <Stat label="FIRs" value={x.cases_count} />
          <Stat label="Victims" value={x.victims} />
          <Stat label="Reported fraud" value={`₹${x.total_fraud.toLocaleString("en-IN")}`} wide />
        </Grid>
        <KV rows={[["Kind", x.kind], ["Value", x.value]]} />
        <Bars title="Districts" rows={x.districts} />
        <EntityList title="Accused in these FIRs" items={x.accused.map((a) => ({ id: a.id, label: a.name, note: `${a.cases} FIRs`, type: "Suspect" as const }))} inGraph={inGraph} onOpen={onOpenEntity} />
        <Cases title="FIRs" rows={x.cases} inGraph={inGraph} onOpen={onOpenEntity} />
      </>
    );
  }
  return null;
}

function Recidivism({ r }: { r: EntityProfile["recidivism"] }) {
  if (!r) return null;
  if (r.withheld) {
    return (
      <div className="rounded-lg border border-dashed border-black/15 p-2.5 text-[11.5px] text-muted">
        <span className="font-semibold text-ink">Recidivism index withheld.</span> {r.note}
      </div>
    );
  }
  if (!r.available) return <div className="text-[11.5px] text-muted">{r.note}</div>;
  return (
    <div className="rounded-lg border border-black/10 p-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">Recidivism index</span>
        <span className="text-[15px] font-bold text-ink">{r.index?.toFixed(1)} <span className="text-[11px] font-semibold text-muted">· {r.band}</span></span>
      </div>
      <p className="mt-1 text-[10.5px] leading-snug text-faint">{r.note}</p>
    </div>
  );
}

function Chip({ children, tone, style }: { children: React.ReactNode; tone?: "crimson" | "brand"; style?: React.CSSProperties }) {
  const cls = tone === "crimson" ? "bg-crimson/10 text-crimson" : tone === "brand" ? "bg-brand/12 text-brand-deep" : "bg-black/[0.05] text-muted";
  return <span className={`rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold ${cls}`} style={style}>{children}</span>;
}

function Action({ children, onClick, icon: Icon, primary }: { children: React.ReactNode; onClick: () => void; icon: React.ComponentType<{ className?: string }>; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-2 text-[12px] font-semibold transition-colors ${
        primary ? "bg-ink text-white hover:bg-black" : "border border-black/10 bg-white text-ink hover:border-brand/40 hover:text-brand"}`}
    >
      <Icon className="h-3.5 w-3.5" /> {children}
    </button>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-1.5">{children}</div>;
}

function Stat({ label, value, wide }: { label: string; value: React.ReactNode; wide?: boolean }) {
  return (
    <div className={`rounded-lg bg-black/[0.03] px-2.5 py-2 ${wide ? "col-span-2" : ""}`}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">{label}</div>
      <div className="tabular mt-0.5 text-[15px] font-bold text-ink">{value}</div>
    </div>
  );
}

function KV({ rows }: { rows: [string, React.ReactNode | null | undefined][] }) {
  const shown = rows.filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-[11.5px] text-faint">{k}</dt>
          <dd className="min-w-0 break-words text-[12px] font-medium text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] text-faint">{title}</h4>
      {children}
    </section>
  );
}

function Bars({ title, rows }: { title: string; rows: CountRow[] }) {
  if (!rows?.length) return null;
  const max = Math.max(...rows.map((r) => r.count));
  return (
    <Section title={title}>
      <ul className="space-y-1">
        {rows.map((r) => (
          <li key={r.label} className="grid grid-cols-[1fr_auto] items-center gap-2">
            <div className="relative h-5 overflow-hidden rounded bg-black/[0.035]">
              <div className="absolute inset-y-0 left-0 rounded bg-azure/20" style={{ width: `${(100 * r.count) / max}%` }} />
              <span className="relative px-1.5 text-[11.5px] leading-5 text-ink">{r.label}</span>
            </div>
            <span className="tabular text-[11.5px] font-semibold text-muted">{r.count}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function EntityList({ title, items, inGraph, onOpen }: {
  title: string; items: { id: string; label: string; note?: string; type: NetNode["type"] }[];
  inGraph: (id: string) => boolean; onOpen: (id: string) => void;
}) {
  if (!items.length) return null;
  return (
    <Section title={title}>
      <ul className="space-y-0.5">
        {items.map((it) => (
          <li key={it.id}>
            <button onClick={() => onOpen(it.id)} className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-black/[0.04]"
                    title={inGraph(it.id) ? "Select on the canvas" : "Add to the canvas"}>
              <TypeIcon type={it.type} id={it.id} />
              <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-ink">{it.label}</span>
              {it.note && <span className="shrink-0 text-[10.5px] text-muted">{it.note}</span>}
              {!inGraph(it.id) && <span className="shrink-0 rounded bg-black/[0.05] px-1 text-[9.5px] font-semibold text-faint">+ add</span>}
            </button>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Cases({ title, rows, inGraph, onOpen }: { title: string; rows: CaseRow[]; inGraph: (id: string) => boolean; onOpen: (id: string) => void }) {
  if (!rows?.length) return null;
  return (
    <EntityList
      title={title}
      items={rows.map((r) => ({
        id: r.id,
        label: `…${r.id.slice(-6)} · ${r.crime_type || "FIR"}`,
        note: [r.district, r.year].filter(Boolean).join(" · "),
        type: "Case" as const,
      }))}
      inGraph={inGraph}
      onOpen={onOpen}
    />
  );
}
