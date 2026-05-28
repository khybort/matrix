"use client";

import { useEffect, useState } from "react";
import {
  Sidebar, PageShell, Panel, AssetBadge, MarketHeader, EmptyHint, Stat,
  splitByMarket,
} from "@/components/shared";
import { STRATEGY_TEMPLATES, type StrategyTemplate } from "@/lib/strategy-templates";

type StrategyAgg = {
  strategy_id: string;
  strategy_version: number;
  asset_class: string;
  n: string;
  avg_score: string;
  total_pnl_usd: string;
  win_rate: string;
};

type Certificate = {
  strategy_id: string;
  asset_class: string;
  version: number;
  cert_id: string | null;
  cert_status: string | null;
  n_outcomes: number | null;
  observation_days: number | null;
  win_rate: string | null;
  total_pnl_usd: string | null;
  max_drawdown_pct: string | null;
  granted_at: string | null;
  granted_by: string | null;
  validity_until: string | null;
  revoked_reason: string | null;
  gate_verdict: "valid" | "no_cert" | "not_granted" | "expired";
};

type LabExperiment = {
  id: string;
  asset_class: string;
  generation: number;
  n_evaluations: number;
  n_signals: number;
  n_wins: number;
  total_score: string;
  fitness_score: string;
  status: string;
  params: any;
  created_at: string;
};

type Proposal = {
  id: string;
  strategy_id: string;
  from_version: number;
  to_version: number;
  proposal_type: string;
  source: string;
  rationale: string;
  status: string;
  created_at: string;
};

type Dashboard = {
  ok: boolean;
  wallet: { starting_capital_usd: string; cash_usd: string; locked_usd: string; circuit_tripped_at: string | null };
  equityCurve: { equity_usd: string }[];
  strategyAgg: StrategyAgg[];
  certificates: Certificate[];
  labLeaderboard: LabExperiment[];
  labStats: {
    active?: number; retired?: number; promoted?: number;
    max_gen?: number; evals_open?: number; evals_scored?: number; evals_stale?: number;
  };
  mutationProposals: Proposal[];
  now: string;
};

const REFRESH_MS = 10_000;

export default function StrategiesPage() {
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

  const aggByMkt = splitByMarket(data.strategyAgg);
  const certByMkt = splitByMarket(data.certificates);

  return (
    <PageShell
      sidebar={<Sidebar equity={equity} totalPct={totalPct} circuitTripped={circuitTripped} now={data.now} />}
    >
      <div className="flex items-baseline justify-between pb-2" style={{ borderBottom: "1px solid var(--rule)" }}>
        <div>
          <div className="text-[10px] tracker dim">§ 02 · Lifecycle</div>
          <h1 className="serif-i text-[34px] mt-1" style={{ lineHeight: 0.95 }}>
            Strategies
            <span className="dim"> — </span>
            <span className="text-[var(--ink-2)]">what lives, what's promotable</span>
          </h1>
        </div>
      </div>

      <Panel title="Strategy performance · last 24h">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div>
            <MarketHeader label="Crypto" count={aggByMkt.crypto.length} tone="crypto" />
            {aggByMkt.crypto.length === 0
              ? <EmptyHint>No crypto outcomes in 24h.</EmptyHint>
              : <StrategyTable rows={aggByMkt.crypto} />}
          </div>
          <div>
            <MarketHeader label="BIST" count={aggByMkt.bist.length} tone="bist" />
            {aggByMkt.bist.length === 0
              ? <EmptyHint>No BIST outcomes in 24h.</EmptyHint>
              : <StrategyTable rows={aggByMkt.bist} />}
          </div>
        </div>
      </Panel>

      <Panel title="Paper-trade certificates · live-execution gate">
        <div className="space-y-5">
          <div>
            <MarketHeader label="Crypto" count={certByMkt.crypto.length} tone="crypto" />
            {certByMkt.crypto.length === 0
              ? <EmptyHint>No crypto strategy versions to certify.</EmptyHint>
              : <CertificatesTable rows={certByMkt.crypto} />}
          </div>
          <div>
            <MarketHeader label="BIST" count={certByMkt.bist.length} tone="bist" />
            {certByMkt.bist.length === 0
              ? <EmptyHint>No BIST strategy versions to certify.</EmptyHint>
              : <CertificatesTable rows={certByMkt.bist} />}
          </div>
        </div>
      </Panel>

      <Panel title="Lab · evolutionary search">
        <LabPanel rows={data.labLeaderboard} stats={data.labStats} />
      </Panel>

      <Panel title="Mutation proposals · self-modification">
        <ProposalsTable rows={data.mutationProposals} />
      </Panel>

      <Panel title="Templates · starter configs">
        <TemplatesPanel />
      </Panel>

      <Panel title="Wizard · custom config">
        <WizardPanel />
      </Panel>
    </PageShell>
  );
}

function StrategyTable({ rows }: { rows: StrategyAgg[] }) {
  const sorted = [...rows].sort((a, b) => Number(b.total_pnl_usd) - Number(a.total_pnl_usd));
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead><tr>
          <th>Strategy</th><th>v</th>
          <th className="text-right">N</th>
          <th className="text-right">Avg score</th>
          <th className="text-right">Win</th>
          <th className="text-right">PnL</th>
        </tr></thead>
        <tbody>
          {sorted.map((r) => {
            const sc = Number(r.avg_score);
            const pnl = Number(r.total_pnl_usd);
            const wr = Number(r.win_rate);
            return (
              <tr key={`${r.strategy_id}-${r.strategy_version}-${r.asset_class}`}>
                <td className="mono">{r.strategy_id}</td>
                <td className="mono muted">v{r.strategy_version}</td>
                <td className="mono text-right">{r.n}</td>
                <td className={`mono text-right ${sc >= 0 ? "pos" : "neg"}`}>{sc.toFixed(4)}</td>
                <td className="mono text-right">{(wr * 100).toFixed(0)}%</td>
                <td className={`mono text-right ${pnl >= 0 ? "pos" : "neg"}`}>
                  {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CertificatesTable({ rows }: { rows: Certificate[] }) {
  const verdictLabel: Record<Certificate["gate_verdict"], string> = {
    valid: "ALLOW", no_cert: "NO CERT", not_granted: "PENDING", expired: "EXPIRED",
  };
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead><tr>
          <th>Strategy</th><th>v</th>
          <th>Gate</th>
          <th className="text-right">n_outc</th>
          <th className="text-right">Obs days</th>
          <th className="text-right">Win</th>
          <th className="text-right">Total PnL</th>
          <th className="text-right">Max DD</th>
          <th>Granted</th>
          <th>Valid until</th>
        </tr></thead>
        <tbody>
          {rows.map((r) => {
            const isValid = r.gate_verdict === "valid";
            const label =
              r.gate_verdict === "not_granted" && r.cert_status
                ? r.cert_status.toUpperCase()
                : verdictLabel[r.gate_verdict];
            return (
              <tr key={`${r.strategy_id}-${r.asset_class}-${r.version}`}>
                <td className="mono">{r.strategy_id}</td>
                <td className="mono">v{r.version}</td>
                <td className={isValid ? "pos mono" : "neg mono"}>{label}</td>
                <td className="mono text-right">{r.n_outcomes ?? "—"}</td>
                <td className="mono text-right">{r.observation_days ?? "—"}</td>
                <td className="mono text-right">
                  {r.win_rate != null ? (Number(r.win_rate) * 100).toFixed(1) + "%" : "—"}
                </td>
                <td className={`mono text-right ${r.total_pnl_usd != null && Number(r.total_pnl_usd) >= 0 ? "pos" : "neg"}`}>
                  {r.total_pnl_usd != null ? "$" + Number(r.total_pnl_usd).toFixed(2) : "—"}
                </td>
                <td className="mono text-right">
                  {r.max_drawdown_pct != null ? (Number(r.max_drawdown_pct) * 100).toFixed(2) + "%" : "—"}
                </td>
                <td className="mono muted">{r.granted_at ? new Date(r.granted_at).toLocaleDateString() : "—"}</td>
                <td className="mono muted">{r.validity_until ? new Date(r.validity_until).toLocaleDateString() : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LabPanel({ rows, stats }: { rows: LabExperiment[]; stats: Dashboard["labStats"] }) {
  const top = rows[0];
  const topWeights: Record<string, string> = top?.params?.weights ?? {};
  const weightEntries = Object.entries(topWeights);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 md:grid-cols-7 gap-3">
        <Stat label="Generation" value={String(stats?.max_gen ?? 0)} />
        <Stat label="Active" value={String(stats?.active ?? 0)} />
        <Stat label="Retired" value={String(stats?.retired ?? 0)} />
        <Stat label="Promoted" value={String(stats?.promoted ?? 0)} />
        <Stat label="Evals open" value={String(stats?.evals_open ?? 0)} />
        <Stat label="Evals scored" value={String(stats?.evals_scored ?? 0)} />
        <Stat label="Evals stale" value={String(stats?.evals_stale ?? 0)} />
      </div>

      {rows.length === 0 ? (
        <EmptyHint>No active experiments yet.</EmptyHint>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="overflow-x-auto">
            <table className="matrix">
              <thead><tr>
                <th>ID</th><th>Cls</th><th className="text-right">Gen</th>
                <th className="text-right">Evals</th><th className="text-right">Wins</th>
                <th className="text-right">Fitness</th><th className="text-right">Thr</th><th className="text-right">Hor</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => {
                  const fit = Number(r.fitness_score);
                  return (
                    <tr key={r.id}>
                      <td className="mono">{r.id.slice(0, 6)}</td>
                      <td><AssetBadge cls={r.asset_class} /></td>
                      <td className="mono text-right">{r.generation}</td>
                      <td className="mono text-right">{r.n_evaluations}</td>
                      <td className="mono text-right">{r.n_wins}</td>
                      <td className={`mono text-right ${fit >= 0 ? "pos" : "neg"}`}>{fit.toFixed(4)}</td>
                      <td className="mono text-right">{Number(r.params?.signal_threshold ?? 0).toFixed(3)}</td>
                      <td className="mono text-right">{r.params?.horizon_seconds ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div>
            <div className="muted text-[10px] uppercase tracking-widest mb-2">
              Best genome weights ({top ? top.id.slice(0, 6) : "—"})
            </div>
            {weightEntries.length === 0 ? (
              <EmptyHint>no weights</EmptyHint>
            ) : (
              <div className="space-y-1">
                {weightEntries.map(([k, v]) => {
                  const pct = Math.round(Number(v) * 100);
                  return (
                    <div key={k} className="flex items-center gap-2 text-xs">
                      <span className="mono w-28 muted">{k}</span>
                      <div className="flex-1 bg-zinc-800 h-2 rounded overflow-hidden">
                        <div className="h-full bg-cyan-400" style={{ width: `${Math.max(2, pct)}%` }} />
                      </div>
                      <span className="mono w-12 text-right">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ProposalsTable({ rows }: { rows: Proposal[] }) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  if (!rows.length) return <EmptyHint>No mutation proposals yet.</EmptyHint>;

  async function act(id: string, action: "apply" | "reject", label: string) {
    if (!window.confirm(`${label} proposal ${id.slice(0, 8)}…?`)) return;
    setBusyId(id); setMsg(null);
    try {
      const r = await fetch(`/api/proposals/${id}/${action}`, { method: "POST" });
      const j = await r.json();
      if (!j.ok) { setMsg(`error: ${j.error ?? "unknown"}`); setBusyId(null); return; }
      setMsg(`${label.toLowerCase()} ok`);
      window.location.reload();
    } catch (e) { setMsg(`error: ${(e as Error).message}`); setBusyId(null); }
  }

  return (
    <>
      <div className="overflow-x-auto">
        <table className="matrix">
          <thead><tr>
            <th>Time</th><th>Strategy</th><th>Type</th>
            <th>From → To</th><th>Source</th><th>Status</th><th>Actions</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="mono muted">{new Date(r.created_at).toLocaleTimeString()}</td>
                <td className="mono">{r.strategy_id}</td>
                <td className="mono">{r.proposal_type}</td>
                <td className="mono">v{r.from_version} → v{r.to_version}</td>
                <td className="mono">{r.source}</td>
                <td className={r.status === "applied" ? "accent" : "muted"}>{r.status}</td>
                <td>
                  {r.status === "pending" ? (
                    <span className="flex gap-2">
                      <button
                        className="btn-term good"
                        disabled={busyId === r.id}
                        onClick={() => act(r.id, "apply", "Approve")}
                      >Approve</button>
                      <button
                        className="btn-term danger"
                        disabled={busyId === r.id}
                        onClick={() => act(r.id, "reject", "Reject")}
                      >Reject</button>
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {msg && <p className="muted text-xs mt-2 mono">{msg}</p>}
    </>
  );
}

// ─── Templates ────────────────────────────────────────────────────────────

type PreviewResult = {
  strategy_id: string;
  n_bars: number;
  n_positions_closed: number;
  total_pnl_usd: string;
  win_rate: string;
  max_drawdown_pct: string;
};

function TemplatesPanel() {
  const [busy, setBusy] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [previews, setPreviews] = useState<Record<string, PreviewResult | { error: string }>>({});

  async function preview(t: StrategyTemplate) {
    setPreviewBusy(t.id);
    setPreviews((p) => { const n = { ...p }; delete n[t.id]; return n; });
    try {
      const r = await fetch("/api/strategy/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy: t.strategy_id, symbol: "BTCUSDT", asset_class: t.asset_class, days: 7, params: t.params }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "preview failed");
      setPreviews((p) => ({ ...p, [t.id]: j.result }));
    } catch (e) {
      setPreviews((p) => ({ ...p, [t.id]: { error: String((e as Error).message) } }));
    } finally { setPreviewBusy(null); }
  }

  async function deploy(t: StrategyTemplate) {
    if (!confirm(`Deploy "${t.label}" as the next active version of ${t.strategy_id}/${t.asset_class}?\n\nRetires current active config; live order submission stays blocked until paper_trade_certificate granted.`)) return;
    setBusy(t.id); setMsg(null);
    try {
      const r = await fetch("/api/strategy/promote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: t.strategy_id, asset_class: t.asset_class, params: t.params,
          rationale: `Deployed template "${t.label}" (${t.id}) from dashboard`,
          promoted_by: "dashboard-operator",
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "promote failed");
      setMsg({ kind: "ok", text: `Deployed ${t.strategy_id} v${j.config.version}` });
    } catch (e) { setMsg({ kind: "err", text: String((e as Error).message) }); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-3">
      {msg && <div className={msg.kind === "ok" ? "pos text-sm mono" : "neg text-sm mono"}>{msg.text}</div>}
      <div className="overflow-x-auto">
        <table className="matrix">
          <thead><tr>
            <th>Template</th><th>Strategy</th><th>Class</th><th>Risk</th>
            <th>Params</th><th>Preview (7d BTC)</th><th>Actions</th>
          </tr></thead>
          <tbody>
            {STRATEGY_TEMPLATES.map((t) => {
              const pv = previews[t.id];
              return (
                <tr key={t.id}>
                  <td>
                    <div className="font-medium">{t.label}</div>
                    <div className="muted text-xs">{t.short_description}</div>
                  </td>
                  <td className="mono">{t.strategy_id}</td>
                  <td><AssetBadge cls={t.asset_class} /></td>
                  <td className={t.risk === "conservative" ? "pos mono" : t.risk === "aggressive" ? "neg mono" : "mono"}>{t.risk}</td>
                  <td className="mono text-xs">
                    <pre className="whitespace-pre-wrap break-all">{JSON.stringify(t.params, null, 0)}</pre>
                  </td>
                  <td className="mono text-xs">
                    {pv === undefined && <span className="muted">— click Preview</span>}
                    {pv !== undefined && "error" in pv && <span className="neg">{pv.error}</span>}
                    {pv !== undefined && !("error" in pv) && (
                      <div className="space-y-0.5">
                        <div>
                          PnL: <span className={Number(pv.total_pnl_usd) >= 0 ? "pos" : "neg"}>
                            ${Number(pv.total_pnl_usd).toFixed(2)}
                          </span>{" / "}win: {(Number(pv.win_rate) * 100).toFixed(1)}%
                        </div>
                        <div className="muted">n={pv.n_positions_closed}, dd={(Number(pv.max_drawdown_pct) * 100).toFixed(2)}%</div>
                      </div>
                    )}
                  </td>
                  <td className="space-x-1 whitespace-nowrap">
                    <button
                      className="btn-term"
                      onClick={() => preview(t)}
                      disabled={previewBusy !== null || busy !== null}
                    >{previewBusy === t.id ? "Running…" : "Preview"}</button>
                    <button
                      className="btn-term"
                      onClick={() => deploy(t)}
                      disabled={busy !== null || previewBusy !== null}
                    >{busy === t.id ? "Deploying…" : "Deploy"}</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Wizard ──────────────────────────────────────────────────────────────

type WizardStrategy = "grid" | "matrix_agent" | "dca";

type WizardState = {
  strategy: WizardStrategy;
  symbol: string;
  asset_class: "crypto" | "bist";
  days: number;
  n_grids: number;
  price_band_pct: string;
  horizon_s: number;
  weight_trade_flow: string;
  weight_funding: string;
  weight_oi_delta: string;
  weight_ob_imbalance: string;
  weight_news: string;
  signal_threshold: string;
  interval_minutes: number;
};

const WIZARD_DEFAULTS: WizardState = {
  strategy: "grid", symbol: "BTCUSDT", asset_class: "crypto", days: 7,
  n_grids: 10, price_band_pct: "0.02", horizon_s: 300,
  weight_trade_flow: "0.35", weight_funding: "0.20", weight_oi_delta: "0.20",
  weight_ob_imbalance: "0.15", weight_news: "0.10", signal_threshold: "0.18",
  interval_minutes: 60,
};

function wizardParams(s: WizardState): Record<string, unknown> {
  if (s.strategy === "grid") {
    return { n_grids: s.n_grids, price_band_pct: s.price_band_pct, horizon_s: s.horizon_s };
  }
  if (s.strategy === "matrix_agent") {
    return {
      weights: {
        trade_flow: s.weight_trade_flow, funding: s.weight_funding,
        oi_delta: s.weight_oi_delta, ob_imbalance: s.weight_ob_imbalance, news: s.weight_news,
      },
      signal_threshold: s.signal_threshold,
    };
  }
  return { interval_minutes: s.interval_minutes };
}

function WizardPanel() {
  const [s, setS] = useState<WizardState>(WIZARD_DEFAULTS);
  const [busy, setBusy] = useState<"preview" | "deploy" | null>(null);
  const [pv, setPv] = useState<PreviewResult | { error: string } | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  function set<K extends keyof WizardState>(k: K, v: WizardState[K]) {
    setS((prev) => ({ ...prev, [k]: v }));
  }

  async function preview() {
    setBusy("preview"); setPv(null);
    try {
      const r = await fetch("/api/strategy/preview", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy: s.strategy, symbol: s.symbol, asset_class: s.asset_class, days: s.days, params: wizardParams(s) }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "preview failed");
      setPv(j.result);
    } catch (e) { setPv({ error: String((e as Error).message) }); }
    finally { setBusy(null); }
  }

  async function deploy() {
    if (!confirm(`Deploy strategy "${s.strategy}" (${s.asset_class})?\n\nRetires current active config; live order submission stays blocked until paper_trade_certificate granted.`)) return;
    setBusy("deploy"); setMsg(null);
    try {
      const r = await fetch("/api/strategy/promote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: s.strategy, asset_class: s.asset_class, params: wizardParams(s),
          rationale: `Deployed via wizard with params ${JSON.stringify(wizardParams(s))}`,
          promoted_by: "dashboard-wizard",
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "promote failed");
      setMsg({ kind: "ok", text: `Deployed ${j.config.strategy_id} v${j.config.version}` });
    } catch (e) { setMsg({ kind: "err", text: String((e as Error).message) }); }
    finally { setBusy(null); }
  }

  return (
    <div className="space-y-3 text-sm">
      {msg && <div className={msg.kind === "ok" ? "pos mono" : "neg mono"}>{msg.text}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Field label="Strategy">
          <select className="bg-transparent border border-current px-2 py-1" value={s.strategy}
            onChange={(e) => set("strategy", e.target.value as WizardStrategy)}>
            <option value="grid">grid</option>
            <option value="matrix_agent">matrix_agent</option>
            <option value="dca">dca</option>
          </select>
        </Field>
        <Field label="Asset class">
          <select className="bg-transparent border border-current px-2 py-1" value={s.asset_class}
            onChange={(e) => set("asset_class", e.target.value as "crypto" | "bist")}>
            <option value="crypto">crypto</option>
            <option value="bist">bist</option>
          </select>
        </Field>
        <Field label="Preview symbol">
          <input className="bg-transparent border border-current px-2 py-1 mono" value={s.symbol}
            onChange={(e) => set("symbol", e.target.value.toUpperCase())} />
        </Field>
        <Field label="Preview days">
          <input type="number" min={1} max={30} className="bg-transparent border border-current px-2 py-1 mono"
            value={s.days} onChange={(e) => set("days", parseInt(e.target.value || "1", 10))} />
        </Field>
      </div>

      {s.strategy === "grid" && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <NumField label="n_grids" value={s.n_grids} onChange={(v) => set("n_grids", v)} />
          <TextField label="price_band_pct" value={s.price_band_pct} onChange={(v) => set("price_band_pct", v)} />
          <NumField label="horizon_s" value={s.horizon_s} onChange={(v) => set("horizon_s", v)} />
        </div>
      )}
      {s.strategy === "matrix_agent" && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <TextField label="weights.trade_flow" value={s.weight_trade_flow} onChange={(v) => set("weight_trade_flow", v)} />
          <TextField label="weights.funding" value={s.weight_funding} onChange={(v) => set("weight_funding", v)} />
          <TextField label="weights.oi_delta" value={s.weight_oi_delta} onChange={(v) => set("weight_oi_delta", v)} />
          <TextField label="weights.ob_imbalance" value={s.weight_ob_imbalance} onChange={(v) => set("weight_ob_imbalance", v)} />
          <TextField label="weights.news" value={s.weight_news} onChange={(v) => set("weight_news", v)} />
          <TextField label="signal_threshold" value={s.signal_threshold} onChange={(v) => set("signal_threshold", v)} />
        </div>
      )}
      {s.strategy === "dca" && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <NumField label="interval_minutes" value={s.interval_minutes} onChange={(v) => set("interval_minutes", v)} />
        </div>
      )}

      <div className="flex gap-2 items-center">
        <button className="btn-term"
          onClick={preview} disabled={busy !== null}>
          {busy === "preview" ? "Running…" : "Preview"}
        </button>
        <button className="btn-term"
          onClick={deploy} disabled={busy !== null}>
          {busy === "deploy" ? "Deploying…" : "Deploy"}
        </button>
        <span className="muted text-xs">Preview uses {s.days}d of {s.symbol} {s.asset_class} bars.</span>
      </div>

      {pv !== null && (
        <div className="mt-2 text-xs">
          {"error" in pv ? (
            <span className="neg mono">{pv.error}</span>
          ) : (
            <div className="space-y-0.5 mono">
              <div>
                PnL: <span className={Number(pv.total_pnl_usd) >= 0 ? "pos" : "neg"}>
                  ${Number(pv.total_pnl_usd).toFixed(2)}
                </span>{" / "}win: {(Number(pv.win_rate) * 100).toFixed(1)}%{" / "}
                dd: {(Number(pv.max_drawdown_pct) * 100).toFixed(2)}%
              </div>
              <div className="muted">n_bars={pv.n_bars}, n_positions={pv.n_positions_closed}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="muted text-xs uppercase tracking-wide">{label}</span>
      {children}
    </label>
  );
}

function NumField({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <Field label={label}>
      <input type="number" className="bg-transparent border border-current px-2 py-1 mono"
        value={value} onChange={(e) => onChange(parseInt(e.target.value || "0", 10))} />
    </Field>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label}>
      <input className="bg-transparent border border-current px-2 py-1 mono"
        value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}
