import type { MutableRefObject } from "react";

/**
 * Shapes returned by appsail/network_intel.py. Kept in one place so the canvas,
 * the panels and the page agree on what a node is; every field the server always
 * sends is typed as present, and nullable where the corpus genuinely lacks it.
 */

export type EntityType = "Suspect" | "Case" | "Officer" | "Vehicle" | "CyberID";

export const ENTITY_TYPES: EntityType[] = ["Suspect", "Case", "Officer", "Vehicle", "CyberID"];

export interface NetNode {
  id: string;
  type: EntityType;
  label: string;
  short: string;
  sub: string | null;
  district: string | null;
  crime_type: string | null;
  risk: string | null;
  gang: string | null;
  /** Syndicate id (Louvain over repeat co-offending), or null when in none. */
  community: string | null;
  rowdy: boolean;
  warrant: boolean;
  photo: string | null;
  year: number | null;
  status: string | null;
  severity: number | null;
  /** Degree in the whole knowledge graph. */
  degree: number;
  /** PageRank within the view that returned it, scaled to 0..1. */
  score: number;
  hop: number;
  view_degree: number;
  // Written by the force engine.
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
  fz?: number;
}

export type LinkKind = "recorded" | "derived" | "predicted";

export interface NetLink {
  source: string | NetNode;
  target: string | NetNode;
  relationship: string;
  weight: number;
  derived: boolean;
  kind: LinkKind;
  shared_cases?: number;
  shared_cases_off_screen?: number;
  case_ids?: string[];
  score?: number;
  common_associates?: number;
  in_global_top?: boolean;
}

export interface Predicted {
  source: string;
  target: string;
  score: number;
  common_associates: number;
  in_global_top: boolean;
}

export interface ViewStats {
  nodes: number;
  links: number;
  derived_links?: number;
  by_type?: Partial<Record<EntityType, number>>;
  communities?: number;
  cap?: number;
  depth?: number;
  considered?: number;
  left_out?: number;
  syndicates_shown?: number;
  syndicates_total?: number;
  score?: string;
}

export type Focus =
  | { kind: "overview"; syndicates?: string[] }
  | { kind: "seed"; ids: string[] }
  | { kind: "syndicate"; id: string; size?: number; risk_level?: string }
  | { kind: "query"; q: string; ids: string[] };

export interface ExploreResponse {
  nodes: NetNode[];
  links: Omit<NetLink, "kind">[];
  predicted: Predicted[];
  truncated: boolean;
  stats: ViewStats;
  focus: Focus;
  note: string | null;
}

export interface SearchResult {
  id: string;
  type: EntityType;
  label: string;
  sub: string | null;
  risk: string | null;
  rowdy: boolean;
  warrant: boolean;
  matched: string | null;
  score: number;
}

export interface Facet {
  field: "crime_type" | "district" | "station";
  label: string;
  value: string;
  count: number;
}

export interface PathHop {
  from: string;
  to: string;
  relationship: string;
  from_label: string;
  to_label: string;
  explanation: string;
}

export interface PathItem {
  length: number;
  nodes: string[];
  hops: PathHop[];
  summary: string;
}

export interface PathResponse {
  from: NetNode;
  to: NetNode;
  paths: PathItem[];
  graph: { nodes: NetNode[]; links: Omit<NetLink, "kind">[] };
  search: { k: number; max_hops: number; via_officers: boolean; elapsed_ms: number; stopped: string };
  note: string | null;
}

export interface CountRow {
  label: string;
  count: number;
}

export interface CaseRow {
  id: string;
  crime_type: string | null;
  district: string | null;
  year: number | null;
  status: string | null;
  relationship: string | null;
}

export interface EntityProfile {
  entity: NetNode;
  neighbours_by_type: Partial<Record<EntityType, number>>;
  links: { label: string; href: string }[];
  jurisdiction?: { in_scope: boolean; audited?: boolean; note?: string };
  recidivism?: {
    withheld: boolean;
    available?: boolean;
    index?: number;
    band?: string;
    note?: string;
    required_permission?: string;
  };
  suspect?: {
    name: string;
    alias: string | null;
    gang: string | null;
    risk_level: string | null;
    photo_url: string | null;
    flags: { rowdy_sheeter: boolean; history_sheet: boolean; active_warrant: boolean };
    past_fir_count: number | null;
    past_arrests: number | null;
    cases_count: number;
    open_cases: number;
    crime_types: CountRow[];
    districts: CountRow[];
    active_years: [number, number] | null;
    co_offenders: { id: string; name: string; shared_cases: number; risk: string | null }[];
    co_offenders_total: number;
    known_associates: { id: string; name: string }[];
    known_associates_total: number;
    syndicate: { id: string; size: number; risk_level: string | null } | null;
    recent_cases: CaseRow[];
  };
  case?: {
    crime_type: string | null;
    district: string | null;
    station: string | null;
    status: string | null;
    registered: string | null;
    severity: number | null;
    sections: string | null;
    accused: { id: string; name: string; relationship: string | null; risk: string | null; warrant: boolean }[];
    officer: { id: string; name: string; rank: string | null } | null;
    vehicles: string[];
    identifiers: string[];
    fraud_amount: number | null;
    syndicate: string | null;
  };
  officer?: {
    kgid: string;
    name: string;
    rank: string | null;
    station: string | null;
    district: string | null;
    specialization: string | null;
    cases_investigated: number;
    open_cases: number;
    crime_types: CountRow[];
    districts: CountRow[];
  };
  vehicle?: {
    plate: string;
    make: string | null;
    recovery_status: string | null;
    theft_district: string | null;
    theft_date: string | null;
    cases: CaseRow[];
  };
  identifier?: {
    kind: string;
    value: string;
    cases_count: number;
    total_fraud: number;
    victims: number;
    districts: CountRow[];
    accused: { id: string; name: string; cases: number }[];
    cases: CaseRow[];
  };
}

export interface Syndicate {
  syndicate_id: string;
  size: number;
  risk_level: string | null;
  member_ids: string[];
  members: { id: string; name: string; risk: string | null; rowdy: boolean; warrant: boolean; gang: string | null }[];
  shared_cases: number;
  cases_total: number;
  open_cases: number;
  districts: CountRow[];
  crime_mix: CountRow[];
  years: [number, number] | null;
  legacy_gangs: string[];
  rowdy_members: number;
  warrant_members: number;
}

export interface NetworkMeta {
  types: { type: EntityType; count: number }[];
  districts: string[];
  crime_types: string[];
  years: [number, number] | null;
  graph: { nodes: number; edges: number };
  syndicates: number;
  limits: { max_nodes: number; default_seeded: number; overview: number };
}

export interface Filters {
  types: EntityType[];
  depth: 1 | 2;
  district: string;
  crimeType: string;
  yearFrom: string;
  yearTo: string;
  rowdy: boolean;
  warrant: boolean;
  /** Node cap for a load; 0 lets the server choose (150 seeded, 350 overview). */
  maxNodes: number;
}

export const DEFAULT_FILTERS: Filters = {
  types: [...ENTITY_TYPES],
  depth: 2,
  district: "",
  crimeType: "",
  yearFrom: "",
  yearTo: "",
  rowdy: false,
  warrant: false,
  maxNodes: 0,
};

export type ColorBy = "type" | "community" | "risk";

/** What the canvas imperatively exposes to the toolbar. */
export interface GraphControls {
  zoomIn: () => void;
  zoomOut: () => void;
  /** Fit everything, or only the given ids (used to frame a path). */
  fit: (ids?: string[]) => void;
  focusNode: (id: string) => void;
  /** Restart the layout after pins change. */
  reheat: () => void;
  exportPNG: (filename: string) => void;
}

export const linkEnd = (end: string | NetNode): string =>
  typeof end === "object" ? end.id : end;

export const linkKey = (a: string, b: string, kind: LinkKind) =>
  a < b ? `${a}|${b}|${kind}` : `${b}|${a}|${kind}`;

/** Props shared by the 2D canvas and the 3D scene, so the page can swap them. */
export interface GraphViewProps {
  width: number;
  height: number;
  nodes: NetNode[];
  links: NetLink[];
  predicted: Predicted[];
  showPredicted: boolean;
  colorBy: ColorBy;
  selectedId: string | null;
  /** Path mode: nodes and node-pairs to keep lit; everything else dims. */
  highlight: { nodes: Set<string>; pairs: Set<string> } | null;
  /** Bumped by the page whenever the view should be refitted after it settles. */
  fitToken: number;
  /** When set with a new fitToken, frame only these nodes (a found path). */
  fitIds: string[] | null;
  /** Pixels covered on the right by the profile panel, so fits frame the visible part. */
  insetRight?: number;
  showLabels: boolean;
  onSelect: (n: NetNode | null) => void;
  onExpand: (n: NetNode) => void;
  onContext: (n: NetNode, x: number, y: number) => void;
  controlRef: MutableRefObject<GraphControls | null>;
}
