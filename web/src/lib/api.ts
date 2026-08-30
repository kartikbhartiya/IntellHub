/* The static client and the AppSail backend do NOT share a Catalyst origin.
 *
 * The client is served from <project>.development.catalystserverless.in; the API
 * only answers on ksp-intelhub-core-<id>.development.catalystappsail.in. Assuming
 * one origin and leaving this empty was verified wrong against the live site —
 * every `/serverless/api/...` path shape on the client domain returns 404, so every
 * panel rendered empty with nothing in the UI to say why.
 *
 * So the deployed bundle must carry the absolute API origin, set at build time via
 * NEXT_PUBLIC_API_URL, and the backend must allow that origin in ALLOWED_ORIGINS.
 * Leaving NEXT_PUBLIC_API_URL unset in a production build is a deployment error, not
 * a fallback worth having: an empty base is exactly the silent-404 case above.
 *
 * `??` rather than `||` so an explicitly-configured empty string is still honoured
 * for a genuinely same-origin deployment, instead of silently falling back to
 * localhost — which is what once baked `http://127.0.0.1:9000` into the bundle.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:9000");

export const api = (path: string) => `${API_URL}/serverless/api${path}`;

export type AgentAction =
  | "open_case"
  | "open_suspect"
  | "show_heatmap"
  | "show_network"
  | "show_dispatch"
  | "show_anomalies"
  | "show_analytics"
  | "show_models"
  | "recent_cases"
  | "show_registry"
  | "show_suspects"
  | "open_copilot"
  | "guide"
  | "answer";

export interface AgentSuggestion {
  label: string;
  query: string;
}

export interface AgentResult {
  action: AgentAction;
  params: {
    case_id?: string;
    suspect_id?: string;
    district?: string;
    crime_type?: string;
    time_of_day?: string;
    kind?: string;
    page?: string;
    q?: string;
    query?: string;
    target?: string;
    repeat_only?: boolean | string;
    rowdy_sheeter?: boolean | string;
    active_warrant?: boolean | string;
  };
  reply: string;
  suggestions?: AgentSuggestion[];
  transcript?: string;
  /** Identifier verification performed server-side. `grounded: false` means the
   *  answer named a case, offender or officer that is not in the corpus. */
  grounding?: {
    checked: number;
    verified: number;
    unknown: { kind: string; value: string }[];
    grounded: boolean;
  };
}

export type ChatTurn = { role: "user" | "assistant"; content: string };

export interface AgentContext {
  case_id?: string;
  suspect_id?: string;
}

export async function askAgent(
  query: string,
  history: ChatTurn[] = [],
  lang = "en",
  page?: string,
  context?: AgentContext
): Promise<AgentResult> {
  const res = await apiFetch("/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, history, lang, page, context }),
  });
  return res.json();
}

/* ------------------------------------------------------------------ auth
 * Person-level endpoints are role-gated server-side. The token below only attaches
 * the header; every access decision is made by the backend, and nothing here can
 * grant access the API has not already granted.
 *
 * Two shapes of refusal, deliberately different:
 *  - Aggregate surfaces that merely *contain* a person-level block (/stats,
 *    /analytics) stay readable and return that block pseudonymised, so the control
 *    is visible without credentials.
 *  - Dossiers and registries return 403. There is no partial version of an
 *    individual's file worth rendering.
 */
const TOKEN_KEY = "ksp.auth.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

/** An API failure that remembers its status, so callers can distinguish
 *  "you may not see this" from "the backend is down". Without the status the UI
 *  can only ever show one generic error for two very different situations. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message?: string) {
    super(message || `Request failed ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
  /** Refused for lack of a role, rather than unavailable. */
  get isForbidden() {
    return this.status === 403 || this.status === 401;
  }
}

export function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

/**
 * The only way to call the API. Attaches the bearer token and surfaces the status
 * as an ApiError, so a 403 from a role gate is distinguishable from a dead backend.
 *
 * Every raw `fetch` in the app used to bypass authHeaders(), which meant the six
 * routes gated in the access-control work returned 403 to signed-in users too.
 */
export async function apiFetch(path: string, init: RequestInit = {}) {
  const res = await apiFetchRaw(path, init);
  if (!res.ok) throw new ApiError(res.status);
  return res;
}

/**
 * Same, but returns the Response whatever the status.
 *
 * For endpoints whose *failure body is the point*: /health answers 503 when the
 * platform is serving but degraded, and that response carries `degraded_reasons`.
 * Throwing it away leaves the operator with a blank panel at precisely the moment
 * the panel has something to say.
 */
export async function apiFetchRaw(path: string, init: RequestInit = {}) {
  return fetch(api(path), {
    ...init,
    headers: { ...(init.headers || {}), ...authHeaders() },
  });
}

export interface Identity {
  identity: { user: string | null; role: string; authenticated: boolean };
  roles: string[];
  demo_mode: boolean;
  // Usernames and roles only — the API deliberately does not serve passwords.
  demo_accounts: { username: string; role: string }[];
  insecure_cleartext_accounts?: string[];
  policy: Record<string, string>;
  warning?: string | null;
}

export async function login(username: string, password: string) {
  const res = await fetch(api("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error("Invalid credentials");
  const data = await res.json();
  setToken(data.token);
  return data as { token: string; role: string; username: string };
}

export function logout() {
  setToken(null);
}

export async function whoami(): Promise<Identity> {
  return getJSON<Identity>("/auth/me");
}


export async function getJSON<T = any>(path: string): Promise<T> {
  const res = await fetch(api(path), { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status);

  // A 2xx with an empty body is a real thing here, not a theoretical one: the
  // Catalyst gateway answers 200 with no content while the AppSail container is
  // restarting. Calling res.json() on that throws the browser's own
  // "Unexpected end of JSON input", which surfaced to users as the reason a case
  // dossier failed to load — a message about JSON parsing when the actual cause
  // was a backend that was not up yet.
  const body = await res.text();
  if (!body.trim()) {
    throw new ApiError(
      503,
      "The intelligence core returned an empty response — it is most likely " +
        "restarting. Retry in a few seconds.");
  }
  try {
    return JSON.parse(body) as T;
  } catch {
    // Equally: an HTML error page from the gateway. Report what it was, not how
    // the parser failed.
    throw new ApiError(
      502,
      "The intelligence core returned a malformed response instead of JSON.");
  }
}
