"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Database,
  Shield,
  Zap,
  Server,
  Network,
  GitBranch,
  RefreshCw,
  Key,
  ShieldCheck,
  DownloadCloud,
  Check,
  Activity,
  Bot,
  Brain,
} from "lucide-react";
import { apiFetch, apiFetchRaw } from "@/lib/api";

export default function SettingsPage() {
  const [syncing, setSyncing] = useState(false);
  const [syncSuccess, setSyncSuccess] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [isUpdatingKey, setIsUpdatingKey] = useState(false);
  const [keySuccess, setKeySuccess] = useState(false);
  const [health, setHealth] = useState<any>(null);
  const [models, setModels] = useState<any>(null);

  const fetchHealth = useCallback(async () => {
    try {
      // Read the body whatever the status: /health answers 503 when degraded and
      // that response is the one carrying degraded_reasons.
      const res = await apiFetchRaw("/health");
      setHealth(await res.json());
    } catch {
      setHealth(null);
    }
    try {
      const res = await apiFetch("/stats");
      if (res.ok) setModels((await res.json()).models);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchHealth();
  }, [fetchHealth]);

  const handleSync = async () => {
    setSyncing(true);
    setSyncSuccess(false);
    try {
      const res = await apiFetch("/sync", { method: "POST" });
      if (res.ok) {
        setSyncSuccess(true);
        fetchHealth();
      }
    } finally {
      setSyncing(false);
    }
  };

  const handleUpdateKey = async () => {
    if (!apiKey.trim()) return;
    setIsUpdatingKey(true);
    setKeySuccess(false);
    try {
      const res = await apiFetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ NVIDIA_API_KEY: apiKey }),
      });
      if (res.ok) {
        setKeySuccess(true);
        setApiKey("");
        setTimeout(() => setKeySuccess(false), 3000);
      }
    } finally {
      setIsUpdatingKey(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="text-display flex items-center gap-3 text-4xl font-bold tracking-tight text-ink">
          <Server className="h-8 w-8 text-brand" strokeWidth={2} /> System Configuration
        </h1>
        <p className="mt-2 text-[15px] text-muted">
          Manage intelligence modules, API keys and database synchronisation.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* API Settings */}
        <div className="bezel lg:col-span-2">
          <div className="bezel-core p-7">
            <div className="mb-6 flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-azure/12 text-azure">
                <Key className="h-[18px] w-[18px]" />
              </span>
              <h2 className="text-[15px] font-bold text-ink">API Integrations</h2>
            </div>

            <div className="space-y-6">
              <div>
                <label
                  htmlFor="llm-key"
                  className="mb-2 block text-[11px] font-semibold uppercase tracking-wider text-faint"
                >
                  LLM Copilot Key (NVIDIA NIM)
                </label>
                <div className="flex gap-3">
                  <input
                    id="llm-key"
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="nvapi-•••• (enter a new key to update)"
                    className="flex-1 rounded-xl border border-black/[0.08] bg-black/[0.03] px-4 py-3 font-mono text-[13px] text-ink placeholder:text-faint focus:border-brand/40 focus:outline-none"
                  />
                  <button
                    onClick={handleUpdateKey}
                    disabled={isUpdatingKey || !apiKey.trim()}
                    className="flex items-center gap-2 rounded-xl border border-black/10 bg-black/[0.04] px-6 py-3 font-medium text-ink transition-colors hover:bg-black/[0.08] disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {isUpdatingKey ? (
                      <RefreshCw className="h-4 w-4 animate-spin" />
                    ) : keySuccess ? (
                      <Check className="h-4 w-4 text-emerald" />
                    ) : (
                      "Update"
                    )}
                  </button>
                </div>
                <p className="mt-2 flex items-center gap-2 text-[12px] text-faint">
                  <ShieldCheck className="h-3.5 w-3.5 text-emerald" /> Injected into the running backend. Groq is
                  used first for speed, with NVIDIA as fallback.
                </p>
              </div>

              <div>
                <span className="mb-2 block text-[11px] font-semibold uppercase tracking-wider text-faint">
                  Basemap
                </span>
                <div className="rounded-xl border border-black/[0.08] bg-black/[0.03] px-4 py-3">
                  <p className="text-[13px] font-medium text-ink">
                    Google Maps, falling back to Leaflet
                  </p>
                  <p className="mt-1 text-[12px] text-faint">
                    The map uses Google Maps when
                    <code className="mx-1 rounded bg-black/[0.05] px-1 py-0.5 font-mono text-[11px]">
                      NEXT_PUBLIC_GOOGLE_MAPS_API_KEY
                    </code>
                    is compiled into the client at build time, and Leaflet over keyless
                    Esri tiles otherwise — or if Google rejects the key at runtime. Both
                    draw the same layers, so the map is never blank. The key cannot be
                    changed from here; restrict it by HTTP referrer in the Google Cloud
                    console, since a browser key is public by construction.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* System Health */}
        <div className="bezel">
          <div className="bezel-core relative overflow-hidden p-7">
            <div className="mb-6 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="grid h-9 w-9 place-items-center rounded-xl bg-brand/12 text-brand">
                  <Zap className="h-[18px] w-[18px]" />
                </span>
                <h2 className="text-[15px] font-bold text-ink">Core Metrics</h2>
              </div>
              <button onClick={fetchHealth} className="text-faint transition-colors hover:text-ink" title="Refresh">
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3.5 text-[13px]">
              <div className="flex items-center justify-between">
                <span className="text-muted">Backend Status</span>
                <span
                  className={`rounded-lg border px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider ${
                    health ? "border-emerald/20 bg-emerald/10 text-emerald" : "border-crimson/20 bg-crimson/10 text-crimson"
                  }`}
                >
                  {health ? health.status : "Offline"}
                </span>
              </div>
              {[
                { label: "Cases Loaded", value: health?.total_cases?.toLocaleString() ?? "—", icon: Database },
                { label: "Tracked Suspects", value: health?.total_suspects?.toLocaleString() ?? "—", icon: Shield },
                { label: "Graph Nodes", value: health?.graph_nodes?.toLocaleString() ?? "—", icon: Network },
                { label: "Graph Edges", value: health?.graph_edges?.toLocaleString() ?? "—", icon: GitBranch },
                { label: "Anomalies", value: health?.anomalies_flagged ?? "—", icon: Activity },
              ].map((m) => (
                <div key={m.label} className="flex items-center justify-between">
                  <span className="flex items-center gap-2 text-muted">
                    <m.icon className="h-4 w-4 text-faint" /> {m.label}
                  </span>
                  <span className="tabular font-mono text-ink">{m.value}</span>
                </div>
              ))}
              <div className="flex items-center justify-between border-t border-black/[0.06] pt-3.5">
                <span className="flex items-center gap-2 text-muted">
                  <Bot className="h-4 w-4 text-faint" /> LLM Copilot
                </span>
                <span
                  className={`rounded-lg border px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider ${
                    health?.llm_connected ? "border-emerald/20 bg-emerald/10 text-emerald" : "border-black/15 bg-black/[0.03] text-muted"
                  }`}
                >
                  {health?.llm_connected ? "Connected" : "Local Mode"}
                </span>
              </div>
              {models && (
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-2 text-muted">
                    <Brain className="h-4 w-4 text-faint" /> ML Classifier
                  </span>
                  <span className="tabular font-mono text-emerald">
                    {models.classifier_accuracy != null ? `${Math.round(models.classifier_accuracy * 100)}%` : "—"}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Data Sync */}
        <div className="bezel lg:col-span-3">
          <div className="bezel-core p-7">
            <div className="mb-6 flex items-center gap-3">
              <span className="grid h-9 w-9 place-items-center rounded-xl bg-emerald/12 text-emerald">
                <Database className="h-[18px] w-[18px]" />
              </span>
              <h2 className="text-[15px] font-bold text-ink">Data Synchronisation</h2>
            </div>

            <div className="flex flex-col items-center justify-between gap-6 rounded-xl border border-black/[0.06] bg-black/[0.02] p-6 md:flex-row">
              <div>
                <h3 className="flex items-center gap-2 font-semibold text-ink">
                  Rebuild Intelligence Core
                  {syncSuccess && <span className="h-2 w-2 rounded-full bg-emerald shadow-[0_0_8px_rgba(52,211,153,1)]" />}
                </h3>
                <p className="mt-1 text-[13px] text-muted">
                  Re-read the case corpus from disk and rebuild the knowledge graph, hotspots
                  and derived analytics. This does <span className="font-semibold text-ink">not</span>{" "}
                  retrain the models — training is an offline step
                  (<code className="font-mono text-[11px]">python -m ml.train</code>) so that
                  published metrics always come from a reproducible run.
                </p>
                <p className="mt-3 font-mono text-[11px] text-faint">Last rebuilt: {syncSuccess ? "just now" : "on boot"}</p>
              </div>
              <button
                onClick={handleSync}
                disabled={syncing}
                className={`flex items-center gap-2 rounded-xl px-8 py-4 font-bold transition-all ${
                  syncing
                    ? "cursor-not-allowed border border-emerald/20 bg-emerald/10 text-emerald"
                    : "bg-gradient-to-b from-emerald/90 to-emerald text-black hover:brightness-105"
                }`}
              >
                {syncing ? (
                  <>
                    <RefreshCw className="h-5 w-5 animate-spin" /> Synchronising…
                  </>
                ) : (
                  <>
                    <DownloadCloud className="h-5 w-5" /> Reload corpus
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
