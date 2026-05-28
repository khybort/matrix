"use client";

import { useEffect, useState } from "react";
import {
  Sidebar, PageShell, Panel, AssetBadge, MarketHeader, EmptyHint, Stat,
  splitByMarket,
} from "@/components/shared";

type Prediction = {
  id: string;
  strategy_id: string;
  strategy_version: number;
  symbol: string;
  asset_class: string;
  side: "long" | "short" | "flat";
  confidence: string;
  generated_at: string;
  close_by: string;
  status: string;
  thesis: string;
};

type Lesson = {
  id: string;
  strategy_id: string;
  strategy_version: number;
  pattern_kind: string;
  pattern_description: string;
  n_observations: number;
  win_rate: string | null;
  total_pnl_usd: string | null;
  verdict: "avoid" | "prefer" | "neutral";
  confidence: string | null;
  observed_until: string;
  generated_at: string;
};

type GraphSignal = {
  asset: string;
  node_id: string;
  computed_at: string;
  direct_mention_count: number;
  direct_polarity: string;
  contextual_polarity: string;
  recency_weight: string;
  n_contextual_documents: number;
  related_companies: string[];
  co_mentioned_assets: string[];
  computed_in_ms: number;
};

type Topology = {
  entityCounts: Record<string, number>;
  edgeCounts: Record<string, number>;
  topMentioned: { kind: string; canonical: string; mentions: number }[];
  typedEdgeSamples: { edge: string; src: string; src_label: string; tgt: string; tgt_label: string }[];
};

type Dashboard = {
  ok: boolean;
  wallet: { starting_capital_usd: string; cash_usd: string; locked_usd: string; circuit_tripped_at: string | null };
  equityCurve: { equity_usd: string }[];
  recentPredictions: Prediction[];
  agentLessons: Lesson[];
  graphSignals: GraphSignal[];
  graphSignalsStats: {
    total_signals?: number; contributing_nodes?: number; distinct_assets?: number;
    latest_publish?: string; avg_compute_ms?: number;
  };
  graphTopology: Topology;
  now: string;
};

const REFRESH_MS = 10_000;

export default function ResearchPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const r = await fetch("/api/dashboard", { cache: "no-store" });
        const j = (await r.json()) as Dashboard;
        if (!alive) return;
        if (!j.ok) setErr("dashboard api returned !ok");
        else { setErr(null); setData(j); }
      } catch (e) { if (alive) setErr(String((e as Error).message)); }
    }
    tick();
    const id = setInterval(tick, REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (err && !data) {
    return <PageShell sidebar={<Sidebar />}><p className="neg mono text-sm">api error: {err}</p></PageShell>;
  }
  if (!data) {
    return <PageShell sidebar={<Sidebar />}><p className="muted mono text-sm">loading…</p></PageShell>;
  }

  const starting = Number(data.wallet.starting_capital_usd);
  const cash = Number(data.wallet.cash_usd);
  const locked = Number(data.wallet.locked_usd);
  const latest = data.equityCurve.at(-1);
  const equity = latest ? Number(latest.equity_usd) : cash + locked;
  const totalPct = ((equity - starting) / starting) * 100;
  const circuitTripped = !!data.wallet.circuit_tripped_at;

  const predByMkt = splitByMarket(data.recentPredictions);

  return (
    <PageShell
      sidebar={<Sidebar equity={equity} totalPct={totalPct} circuitTripped={circuitTripped} now={data.now} />}
    >
      <div className="flex items-baseline justify-between pb-2" style={{ borderBottom: "1px solid var(--rule)" }}>
        <div>
          <div className="text-[10px] tracker dim">§ 03 · Cognition</div>
          <h1 className="serif-i text-[34px] mt-1" style={{ lineHeight: 0.95 }}>
            Research
            <span className="dim"> — </span>
            <span className="text-[var(--ink-2)]">what the system sees &amp; learns</span>
          </h1>
        </div>
      </div>

      <Panel title={`Predictions · stream`}>
        <div className="space-y-5">
          <div>
            <MarketHeader label="Crypto" count={predByMkt.crypto.length} tone="crypto" />
            {predByMkt.crypto.length === 0
              ? <EmptyHint>No crypto predictions.</EmptyHint>
              : <PredictionsTable rows={predByMkt.crypto} />}
          </div>
          <div>
            <MarketHeader label="BIST" count={predByMkt.bist.length} tone="bist" />
            {predByMkt.bist.length === 0
              ? <EmptyHint>No BIST predictions.</EmptyHint>
              : <PredictionsTable rows={predByMkt.bist} />}
          </div>
        </div>
      </Panel>

      <Panel title="Agent lessons · distilled verdicts">
        <AgentLessonsTable rows={data.agentLessons} />
      </Panel>

      <Panel title="Federated graph signals">
        <GraphSignalsPanel rows={data.graphSignals} stats={data.graphSignalsStats} />
      </Panel>

      <Panel title="Graph topology · nodes + typed edges">
        <GraphTopologyPanel topology={data.graphTopology} />
      </Panel>
    </PageShell>
  );
}

function PredictionsTable({ rows }: { rows: Prediction[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead><tr>
          <th>Time</th><th>Strategy</th><th>Symbol</th>
          <th>Side</th><th className="text-right">Conf</th>
          <th>Status</th><th>Thesis</th>
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="mono muted">{new Date(r.generated_at).toLocaleTimeString()}</td>
              <td className="mono">{r.strategy_id} <span className="muted">v{r.strategy_version}</span></td>
              <td className="mono">{r.symbol}</td>
              <td className={r.side === "long" ? "pos mono" : r.side === "short" ? "neg mono" : "muted mono"}>{r.side}</td>
              <td className="mono text-right">{Number(r.confidence).toFixed(2)}</td>
              <td className="mono muted">{r.status}</td>
              <td className="text-xs muted truncate max-w-[420px]" title={r.thesis}>{r.thesis}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AgentLessonsTable({ rows }: { rows: Lesson[] }) {
  if (!rows.length) {
    return <EmptyHint>No lessons yet. Synthesizer runs hourly; wait until enough outcomes per pattern bucket.</EmptyHint>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead><tr>
          <th>Pattern</th><th>Verdict</th>
          <th className="text-right">n</th>
          <th className="text-right">Win</th>
          <th className="text-right">PnL</th>
          <th className="text-right">Confidence</th>
          <th>Observed</th>
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const verdictClass = r.verdict === "avoid" ? "neg mono"
              : r.verdict === "prefer" ? "pos mono" : "mono muted";
            const wr = r.win_rate != null ? Number(r.win_rate) * 100 : null;
            return (
              <tr key={r.id}>
                <td>
                  <div className="font-medium">{r.pattern_description}</div>
                  <div className="muted text-xs mono">{r.strategy_id} v{r.strategy_version} · {r.pattern_kind}</div>
                </td>
                <td className={verdictClass}>{r.verdict.toUpperCase()}</td>
                <td className="mono text-right">{r.n_observations}</td>
                <td className={`mono text-right ${wr != null && wr < 50 ? "neg" : "pos"}`}>
                  {wr != null ? wr.toFixed(2) + "%" : "—"}
                </td>
                <td className={`mono text-right ${r.total_pnl_usd != null && Number(r.total_pnl_usd) >= 0 ? "pos" : "neg"}`}>
                  {r.total_pnl_usd != null ? "$" + Number(r.total_pnl_usd).toFixed(2) : "—"}
                </td>
                <td className="mono text-right">
                  {r.confidence != null ? (Number(r.confidence) * 100).toFixed(0) + "%" : "—"}
                </td>
                <td className="muted text-xs mono">{new Date(r.observed_until).toLocaleString()}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function GraphSignalsPanel({ rows, stats }: { rows: GraphSignal[]; stats: Dashboard["graphSignalsStats"] }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Stat label="Signals 24h" value={String(stats?.total_signals ?? 0)} />
        <Stat label="Contributing nodes" value={String(stats?.contributing_nodes ?? 0)} />
        <Stat label="Assets covered" value={String(stats?.distinct_assets ?? 0)} />
        <Stat label="Avg compute" value={`${stats?.avg_compute_ms ?? 0}ms`} />
        <Stat label="Latest publish"
          value={stats?.latest_publish ? new Date(stats.latest_publish).toLocaleTimeString() : "—"} />
      </div>

      {rows.length === 0 ? (
        <EmptyHint>No graph signals yet. Graph service publishes every 120s when raw_documents exist.</EmptyHint>
      ) : (
        <div className="overflow-x-auto">
          <table className="matrix">
            <thead><tr>
              <th>Asset</th><th>Node</th>
              <th className="text-right">Mentions</th>
              <th>Polarity (direct/ctx)</th>
              <th>Related companies</th>
              <th>Co-mentioned</th>
              <th>At</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => {
                const dpol = Number(r.direct_polarity);
                const cpol = Number(r.contextual_polarity);
                const companies: string[] = r.related_companies || [];
                const coAssets: string[] = r.co_mentioned_assets || [];
                return (
                  <tr key={`${r.asset}-${r.node_id}`}>
                    <td className="mono font-medium">{r.asset}</td>
                    <td className="mono muted text-xs">{r.node_id}</td>
                    <td className="mono text-right">{r.direct_mention_count}</td>
                    <td className="mono">
                      <span className={dpol >= 0 ? "pos" : "neg"}>{dpol.toFixed(2)}</span>
                      <span className="muted"> / </span>
                      <span className={cpol >= 0 ? "pos" : "neg"}>{cpol.toFixed(2)}</span>
                    </td>
                    <td className="text-xs">
                      {companies.length === 0 ? <span className="muted">—</span> :
                        companies.slice(0, 4).map((c) => (
                          <span key={c} className="inline-block mono mr-1 px-1.5 py-0.5 bg-zinc-800 rounded text-xs">{c}</span>
                        ))}
                    </td>
                    <td className="text-xs">
                      {coAssets.length === 0 ? <span className="muted">—</span> :
                        coAssets.slice(0, 4).map((a) => (
                          <span key={a} className="inline-block mono mr-1 px-1.5 py-0.5 bg-zinc-800 rounded text-xs">{a}</span>
                        ))}
                    </td>
                    <td className="mono muted text-xs">{new Date(r.computed_at).toLocaleTimeString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function GraphTopologyPanel({ topology }: { topology: Topology }) {
  const entityEntries = Object.entries(topology?.entityCounts || {});
  const edgeEntries = Object.entries(topology?.edgeCounts || {});
  const totalEntities = entityEntries.reduce((sum, [, v]) => sum + (v || 0), 0);
  const totalEdges = edgeEntries.reduce((sum, [, v]) => sum + (v || 0), 0);
  const typedEdges = topology?.typedEdgeSamples || [];
  const top = topology?.topMentioned || [];

  const byEdgeType: Record<string, typeof typedEdges> = {};
  for (const e of typedEdges) (byEdgeType[e.edge] ??= []).push(e);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Total nodes" value={totalEntities.toString()} />
        <Stat label="Total edges" value={totalEdges.toString()} />
        <Stat label="Entity types" value={entityEntries.filter(([, v]) => v > 0).length.toString()} />
        <Stat label="Edge types" value={edgeEntries.filter(([, v]) => v > 0).length.toString()} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <div className="muted text-[10px] uppercase tracking-widest mb-2">Nodes by label</div>
          {entityEntries.length === 0 ? <EmptyHint>No nodes yet.</EmptyHint> : (
            <div className="space-y-1">
              {entityEntries.sort(([, a], [, b]) => b - a).map(([label, count]) => (
                <div key={label} className="flex items-center gap-2 text-xs">
                  <span className="mono w-28 muted">{label}</span>
                  <div className="flex-1 bg-zinc-800 h-2 rounded overflow-hidden">
                    <div className="h-full bg-cyan-400"
                      style={{ width: `${Math.max(2, Math.min(100, (count / Math.max(1, totalEntities)) * 100))}%` }} />
                  </div>
                  <span className="mono w-12 text-right">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <div className="muted text-[10px] uppercase tracking-widest mb-2">Edges by type</div>
          {edgeEntries.length === 0 ? <EmptyHint>No edges yet.</EmptyHint> : (
            <div className="space-y-1">
              {edgeEntries.sort(([, a], [, b]) => b - a).map(([edge, count]) => (
                <div key={edge} className="flex items-center gap-2 text-xs">
                  <span className="mono w-32 muted">{edge}</span>
                  <div className="flex-1 bg-zinc-800 h-2 rounded overflow-hidden">
                    <div className={`h-full ${edge === "MENTIONS" ? "bg-zinc-400" : "bg-amber-400"}`}
                      style={{ width: `${Math.max(2, Math.min(100, (count / Math.max(1, totalEdges)) * 100))}%` }} />
                  </div>
                  <span className="mono w-12 text-right">{count}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div>
        <div className="muted text-[10px] uppercase tracking-widest mb-2">Top-mentioned entities</div>
        {top.length === 0 ? <EmptyHint>No entities mentioned yet.</EmptyHint> : (
          <div className="flex flex-wrap gap-1">
            {top.map((t) => (
              <span key={`${t.kind}:${t.canonical}`}
                className="mono text-xs px-2 py-0.5 bg-zinc-800 rounded"
                title={`${t.kind} · ${t.mentions} mentions`}>
                <span className="muted">{t.kind}</span>
                <span className="mx-1">·</span>
                {t.canonical}
                <span className="muted ml-1">{t.mentions}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="muted text-[10px] uppercase tracking-widest mb-2">Sample typed edges (non-MENTIONS)</div>
        {Object.keys(byEdgeType).length === 0 ? (
          <EmptyHint>No typed relations yet.</EmptyHint>
        ) : (
          <div className="space-y-1">
            {Object.entries(byEdgeType).map(([edge, samples]) => (
              <div key={edge} className="text-xs">
                <span className="mono accent">{edge}</span>
                <span className="muted"> · </span>
                {samples.slice(0, 4).map((s, i) => (
                  <span key={i} className="mono mr-2">
                    <span className="muted">{s.src_label}:</span>
                    {s.src}
                    <span className="muted"> → </span>
                    <span className="muted">{s.tgt_label}:</span>
                    {s.tgt}
                  </span>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
