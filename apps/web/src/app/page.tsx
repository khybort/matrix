"use client";

import { useEffect, useState } from "react";

import { STRATEGY_TEMPLATES, type StrategyTemplate } from "@/lib/strategy-templates";

type Dashboard = {
  ok: boolean;
  wallet: any;
  equityCurve: { snapshot_ts: string; equity_usd: string; cash_usd: string;
    locked_usd: string; n_open_positions: number; unrealized_pnl_usd: string; }[];
  openPositions: any[];
  recentPredictions: any[];
  recentOutcomes: any[];
  strategyAgg: any[];
  mutationProposals: any[];
  agentLessons: {
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
  }[];
  certificates: {
    strategy_id: string; asset_class: string; version: number;
    cert_id: string | null; cert_status: string | null;
    n_outcomes: number | null; observation_days: number | null;
    win_rate: string | null; total_pnl_usd: string | null;
    max_drawdown_pct: string | null;
    granted_at: string | null; granted_by: string | null;
    validity_until: string | null; revoked_reason: string | null;
    gate_verdict: "valid" | "no_cert" | "not_granted" | "expired";
  }[];
  labLeaderboard: any[];
  labStats: {
    active?: number; retired?: number; promoted?: number;
    max_gen?: number; evals_open?: number; evals_scored?: number;
    evals_stale?: number;
  };
  graphSignals: any[];
  graphSignalsStats: {
    total_signals?: number; contributing_nodes?: number;
    distinct_assets?: number; latest_publish?: string;
    avg_compute_ms?: number;
  };
  graphTopology: {
    entityCounts: Record<string, number>;
    edgeCounts: Record<string, number>;
    topMentioned: { kind: string; canonical: string; mentions: number }[];
    typedEdgeSamples: { edge: string; src: string; src_label: string;
                        tgt: string; tgt_label: string }[];
  };
  bist: {
    symbols: { active?: number; inactive?: number; last_refreshed?: string };
    bars: { interval: string; n: number; latest_ts: string }[];
    positions: { open?: number; closed?: number; realized_pnl_usd?: string };
    predictions: { open?: number; closed?: number };
  };
  now: string;
};

const REFRESH_MS = 5000;

function formatTime(date: Date): string {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
}

export default function Page() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const res = await fetch("/api/dashboard", { cache: "no-store" });
        const j = (await res.json()) as Dashboard;
        if (!alive) return;
        if (!j.ok) {
          setErr(String((j as any).error));
        } else {
          setErr(null);
          setData(j);
        }
      } catch (e) {
        if (!alive) return;
        setErr(String((e as Error).message));
      }
    }
    tick();
    const id = setInterval(tick, REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (err) {
    return (
      <div className="p-8">
        <h1 className="text-2xl">Matrix</h1>
        <p className="neg mt-4">Error: {err}</p>
      </div>
    );
  }
  if (!data) {
    return <div className="p-8 muted">Loading…</div>;
  }

  const starting = Number(data.wallet.starting_capital_usd);
  const latest = data.equityCurve.at(-1);
  const equity = latest ? Number(latest.equity_usd) : Number(data.wallet.cash_usd) + Number(data.wallet.locked_usd);
  const totalPct = ((equity - starting) / starting) * 100;
  const circuitTripped = !!data.wallet.circuit_tripped_at;

  return (
    <div className="min-h-screen flex">
      <Sidebar
        equity={equity}
        totalPct={totalPct}
        circuitTripped={circuitTripped}
        now={data.now}
      />
      <main className="flex-1 min-w-0 px-6 lg:px-10 py-8 max-w-[1400px] mx-auto">
        <Section id="overview" title="Overview">
          <WalletCard wallet={data.wallet} curve={data.equityCurve} />
        </Section>

        <Section id="performance" title="Performance">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Panel title="Strategy performance (24h)">
              <StrategyTable rows={data.strategyAgg} />
            </Panel>
            <Panel title="Mutation proposals">
              <ProposalsTable rows={data.mutationProposals} />
            </Panel>
          </div>
          <Panel title="Live-execution gate — paper_trade_certificate" className="mt-4">
            <CertificatesTable rows={data.certificates} />
          </Panel>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
            <Panel title={`Open positions (${data.openPositions.length})`}>
              <OpenPositionsTable rows={data.openPositions} />
            </Panel>
            <Panel title="Recent outcomes">
              <OutcomesTable rows={data.recentOutcomes} />
            </Panel>
          </div>
        </Section>

        <Section id="learning" title="Learning">
          <Panel title="Agent lessons — distilled from the engine's own decisions">
            <AgentLessonsTable rows={data.agentLessons} />
          </Panel>
          <Panel title="Lab — evolutionary algorithm search" className="mt-4">
            <LabPanel rows={data.labLeaderboard} stats={data.labStats} />
          </Panel>
        </Section>

        <Section id="deploy" title="Deploy">
          <Panel title="Strategy templates — starter configs">
            <TemplatesPanel />
          </Panel>
          <Panel title="Strategy wizard — custom config" className="mt-4">
            <WizardPanel />
          </Panel>
        </Section>

        <Section id="markets" title="Markets">
          <Panel title="BIST — paper-only equities (Yahoo delayed feed)">
            <BistOverview bist={data.bist} />
          </Panel>
        </Section>

        <Section id="graph" title="Graph">
          <Panel title="Federated signals (per asset)">
            <GraphSignalsPanel rows={data.graphSignals} stats={data.graphSignalsStats} />
          </Panel>
          <Panel title="Topology — nodes + typed edges" className="mt-4">
            <GraphTopologyPanel topology={data.graphTopology} />
          </Panel>
        </Section>

        <Section id="predictions" title="Predictions">
          <Panel title="Recent predictions">
            <PredictionsTable rows={data.recentPredictions} />
          </Panel>
        </Section>

        <footer className="muted text-xs mt-12 text-center mono">
          refreshing every {REFRESH_MS / 1000}s · local-only · {formatTime(new Date(data.now))}
        </footer>
      </main>
    </div>
  );
}

const NAV_SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "performance", label: "Performance" },
  { id: "learning", label: "Learning" },
  { id: "deploy", label: "Deploy" },
  { id: "markets", label: "Markets" },
  { id: "graph", label: "Graph" },
  { id: "predictions", label: "Predictions" },
] as const;

function Sidebar({ equity, totalPct, circuitTripped, now }: {
  equity: number; totalPct: number; circuitTripped: boolean; now: string;
}) {
  const [active, setActive] = useState<string>("overview");
  useEffect(() => {
    const obs = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: "-20% 0px -60% 0px", threshold: [0, 0.25, 0.5, 1] }
    );
    NAV_SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) obs.observe(el);
    });
    return () => obs.disconnect();
  }, []);

  return (
    <aside className="hidden md:flex flex-col w-56 shrink-0 border-r border-[var(--border)] bg-[var(--panel)] sticky top-0 h-screen p-5">
      <div className="mb-6">
        <h1 className="text-lg font-medium tracking-tight">
          <span className="accent">matrix</span>
        </h1>
        <p className="muted text-xs mono mt-0.5">trading agent</p>
      </div>

      <div className="mb-6 space-y-1">
        <div className="muted text-[10px] uppercase tracking-widest">Equity</div>
        <div className="mono text-base">{fmtUsd(equity)}</div>
        <div className={`mono text-xs ${totalPct >= 0 ? "pos" : "neg"}`}>
          {totalPct >= 0 ? "+" : ""}{totalPct.toFixed(3)}%
        </div>
        <div className="mt-2">
          {circuitTripped ? (
            <span className="neg mono text-[10px] uppercase tracking-widest">● circuit tripped</span>
          ) : (
            <span className="pos mono text-[10px] uppercase tracking-widest">● live</span>
          )}
        </div>
      </div>

      <nav className="flex-1 space-y-1">
        {NAV_SECTIONS.map((s) => {
          const isActive = active === s.id;
          return (
            <a
              key={s.id}
              href={`#${s.id}`}
              className={`block px-3 py-1.5 text-sm rounded-md border-l-2 transition-colors ${
                isActive
                  ? "border-[var(--accent)] text-[var(--foreground)] bg-black/30"
                  : "border-transparent muted hover:text-[var(--foreground)] hover:bg-black/20"
              }`}
            >
              {s.label}
            </a>
          );
        })}
      </nav>

      <div className="mt-auto pt-4 border-t border-[var(--border)]">
        <div className="muted text-[10px] uppercase tracking-widest">Last refresh</div>
        <div className="mono text-xs">{formatTime(new Date(now))}</div>
      </div>
    </aside>
  );
}

function Section({ id, title, children }: {
  id: string; title: string; children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-6 mb-10">
      <h2 className="text-xs muted uppercase tracking-widest mb-3 border-b border-[var(--border)] pb-2">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Panel({ title, children, className = "" }: {
  title: string; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`panel p-4 ${className}`}>
      <h2 className="muted text-xs uppercase tracking-wide mb-3">{title}</h2>
      {children}
    </section>
  );
}

function WalletCard({ wallet, curve }: { wallet: any; curve: Dashboard["equityCurve"] }) {
  const starting = Number(wallet.starting_capital_usd);
  const cash = Number(wallet.cash_usd);
  const locked = Number(wallet.locked_usd);
  const latest = curve.at(-1);
  const equity = latest ? Number(latest.equity_usd) : cash + locked;
  const unrealized = latest ? Number(latest.unrealized_pnl_usd) : 0;
  const realized = cash + locked - starting;
  const totalPct = ((equity - starting) / starting) * 100;

  return (
    <section className="panel p-5">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Stat label="Equity" value={fmtUsd(equity)} sub={`${totalPct >= 0 ? "+" : ""}${totalPct.toFixed(3)}%`} subClass={totalPct >= 0 ? "pos" : "neg"} />
        <Stat label="Cash" value={fmtUsd(cash)} />
        <Stat label="Locked" value={fmtUsd(locked)} />
        <Stat label="Unrealized" value={fmtUsd(unrealized)} valueClass={unrealized >= 0 ? "pos" : "neg"} />
        <Stat label="Realized" value={fmtUsd(realized)} valueClass={realized >= 0 ? "pos" : "neg"} />
      </div>
      <div className="mt-4 muted text-xs flex justify-between">
        <span>
          Caps: max_pos={(Number(wallet.max_position_pct) * 100).toFixed(2)}% ·
          daily_circuit={(Number(wallet.daily_loss_circuit_pct) * 100).toFixed(1)}% ·
          max_concurrent={wallet.max_concurrent_positions}
        </span>
        <span>{wallet.circuit_tripped_at ? <span className="neg">CIRCUIT TRIPPED</span> : <span className="pos">circuit ok</span>}</span>
      </div>
      <EquityCurve curve={curve} starting={starting} />
    </section>
  );
}

function Stat({ label, value, sub, valueClass = "", subClass = "muted" }: {
  label: string; value: string; sub?: string; valueClass?: string; subClass?: string;
}) {
  return (
    <div>
      <div className="muted text-xs uppercase tracking-wide">{label}</div>
      <div className={`text-xl font-medium mono ${valueClass}`}>{value}</div>
      {sub && <div className={`${subClass} text-xs mono`}>{sub}</div>}
    </div>
  );
}

function EquityCurve({ curve, starting }: { curve: Dashboard["equityCurve"]; starting: number }) {
  if (!curve.length) return <div className="muted text-xs mt-4">no snapshots yet…</div>;
  const w = 1200, h = 120, pad = 8;
  const xs = curve.map((p) => new Date(p.snapshot_ts).getTime());
  const ys = curve.map((p) => Number(p.equity_usd));
  const minX = xs[0], maxX = xs[xs.length - 1];
  const allY = [...ys, starting];
  const minY = Math.min(...allY);
  const maxY = Math.max(...allY);
  const ySpan = Math.max(1, maxY - minY);
  const xSpan = Math.max(1, maxX - minX);
  const path = curve.map((p, i) => {
    const x = pad + ((xs[i] - minX) / xSpan) * (w - 2 * pad);
    const y = h - pad - ((Number(p.equity_usd) - minY) / ySpan) * (h - 2 * pad);
    return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const startY = h - pad - ((starting - minY) / ySpan) * (h - 2 * pad);
  const last = ys[ys.length - 1];
  const lineClass = last >= starting ? "pos" : "neg";
  return (
    <div className="mt-3">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-32 overflow-visible">
        <line x1={pad} x2={w - pad} y1={startY} y2={startY} stroke="#27272a" strokeDasharray="3,3" />
        <path d={path} fill="none" stroke="currentColor" className={lineClass} strokeWidth="1.5" />
      </svg>
    </div>
  );
}

function StrategyTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted text-sm">No outcomes in last 24h yet.</p>;
  return (
    <table className="matrix">
      <thead>
        <tr><th>Strategy</th><th>Cls</th><th>v</th><th>N</th><th>Avg score</th><th>Win rate</th><th>PnL (USD)</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const sc = Number(r.avg_score);
          const pnl = Number(r.total_pnl_usd);
          return (
            <tr key={`${r.strategy_id}-${r.strategy_version}-${r.asset_class}`}>
              <td className="mono">{r.strategy_id}</td>
              <td><AssetClassBadge cls={r.asset_class} /></td>
              <td className="mono">{r.strategy_version}</td>
              <td className="mono">{r.n}</td>
              <td className={`mono ${sc >= 0 ? "pos" : "neg"}`}>{sc.toFixed(4)}</td>
              <td className="mono">{(Number(r.win_rate) * 100).toFixed(1)}%</td>
              <td className={`mono ${pnl >= 0 ? "pos" : "neg"}`}>{pnl.toFixed(4)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function AssetClassBadge({ cls }: { cls?: string }) {
  const label = cls ?? "crypto";
  const tone =
    label === "bist"
      ? "border-amber-500 text-amber-300"
      : "border-emerald-500 text-emerald-300";
  return (
    <span className={`mono text-xs px-1.5 py-0.5 border rounded ${tone}`}>
      {label}
    </span>
  );
}

function BistOverview({ bist }: { bist: Dashboard["bist"] }) {
  const active = Number(bist.symbols.active ?? 0);
  const inactive = Number(bist.symbols.inactive ?? 0);
  const realized = Number(bist.positions.realized_pnl_usd ?? 0);
  const lastRefreshed = bist.symbols.last_refreshed
    ? new Date(bist.symbols.last_refreshed).toLocaleString()
    : "never";

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Stat label="Active symbols" value={String(active)} sub={inactive ? `${inactive} inactive` : undefined} />
        <Stat label="Universe refreshed" value={lastRefreshed} subClass="muted text-xs" />
        <Stat label="Open positions" value={String(bist.positions.open ?? 0)} />
        <Stat label="Closed positions" value={String(bist.positions.closed ?? 0)} />
        <Stat
          label="Realized PnL"
          value={fmtUsd(realized)}
          valueClass={realized >= 0 ? "pos" : "neg"}
        />
      </div>
      {bist.bars.length === 0 ? (
        <p className="muted text-sm">No bars yet — run <code className="mono">make bist-seed</code> then <code className="mono">make bist-poll</code> (or just wait for bist-ingestion).</p>
      ) : (
        <table className="matrix">
          <thead><tr><th>Interval</th><th>Bars</th><th>Latest bar ts</th></tr></thead>
          <tbody>
            {bist.bars.map((b) => (
              <tr key={b.interval}>
                <td className="mono">{b.interval}</td>
                <td className="mono">{b.n}</td>
                <td className="mono">{b.latest_ts ? new Date(b.latest_ts).toLocaleString() : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="muted text-xs">
        BIST predictions: {bist.predictions.open ?? 0} open · {bist.predictions.closed ?? 0} closed.
        Paper-only — no live execution.
      </div>
    </div>
  );
}

function ProposalsTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted text-sm">No mutation proposals yet.</p>;
  return (
    <table className="matrix">
      <thead><tr><th>Time</th><th>Strategy</th><th>Type</th><th>From → To</th><th>Source</th><th>Status</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="mono">{new Date(r.created_at).toLocaleTimeString()}</td>
            <td className="mono">{r.strategy_id}</td>
            <td className="mono">{r.proposal_type}</td>
            <td className="mono">v{r.from_version} → v{r.to_version}</td>
            <td className="mono">{r.source}</td>
            <td className={r.status === "applied" ? "accent" : "muted"}>{r.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CertificatesTable({ rows }: { rows: Dashboard["certificates"] }) {
  if (!rows.length) {
    return <p className="muted text-sm">No active strategy versions to certify.</p>;
  }
  // Visual: gate verdict is the ONLY thing an operator needs at a glance.
  // 'valid' green, anything else red — there is no middle ground for a
  // gate. Detail metrics shown so the reason is obvious without a query.
  return (
    <table className="matrix">
      <thead><tr>
        <th>Strategy</th><th>Cls</th><th>Ver</th>
        <th>Gate</th>
        <th>n_outcomes</th><th>Obs days</th>
        <th>Win rate</th><th>Total PnL</th><th>Max DD%</th>
        <th>Granted</th><th>Valid until</th>
      </tr></thead>
      <tbody>
        {rows.map((r) => {
          const isValid = r.gate_verdict === "valid";
          const verdictLabel: Record<typeof r.gate_verdict, string> = {
            valid: "ALLOW",
            no_cert: "NO CERT",
            not_granted: r.cert_status ? r.cert_status.toUpperCase() : "PENDING",
            expired: "EXPIRED",
          };
          return (
            <tr key={`${r.strategy_id}-${r.asset_class}-${r.version}`}>
              <td className="mono">{r.strategy_id}</td>
              <td><AssetClassBadge cls={r.asset_class} /></td>
              <td className="mono">v{r.version}</td>
              <td className={isValid ? "pos mono" : "neg mono"}>
                {verdictLabel[r.gate_verdict]}
              </td>
              <td className="mono">{r.n_outcomes ?? "—"}</td>
              <td className="mono">{r.observation_days ?? "—"}</td>
              <td className="mono">
                {r.win_rate != null ? (Number(r.win_rate) * 100).toFixed(1) + "%" : "—"}
              </td>
              <td className={r.total_pnl_usd != null && Number(r.total_pnl_usd) >= 0 ? "pos mono" : "neg mono"}>
                {r.total_pnl_usd != null ? "$" + Number(r.total_pnl_usd).toFixed(2) : "—"}
              </td>
              <td className="mono">
                {r.max_drawdown_pct != null ? (Number(r.max_drawdown_pct) * 100).toFixed(2) + "%" : "—"}
              </td>
              <td className="mono">
                {r.granted_at ? new Date(r.granted_at).toLocaleDateString() : "—"}
              </td>
              <td className="mono">
                {r.validity_until ? new Date(r.validity_until).toLocaleDateString() : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

type PreviewResult = {
  strategy_id: string;
  n_bars: number;
  n_positions_closed: number;
  total_pnl_usd: string;
  win_rate: string;
  max_drawdown_pct: string;
};

function AgentLessonsTable({ rows }: { rows: Dashboard["agentLessons"] }) {
  if (!rows.length) {
    return (
      <p className="muted text-sm">
        No lessons yet. The synthesizer runs hourly; come back after the engine
        has booked enough outcomes per pattern bucket.
      </p>
    );
  }
  return (
    <table className="matrix">
      <thead><tr>
        <th>Pattern</th>
        <th>Verdict</th>
        <th>n</th>
        <th>Win rate</th>
        <th>Total PnL</th>
        <th>Confidence</th>
        <th>Observed</th>
      </tr></thead>
      <tbody>
        {rows.map((r) => {
          const verdictClass = r.verdict === "avoid"
            ? "neg mono"
            : r.verdict === "prefer" ? "pos mono" : "mono muted";
          const wr = r.win_rate != null ? Number(r.win_rate) * 100 : null;
          return (
            <tr key={r.id}>
              <td>
                <div className="font-medium">{r.pattern_description}</div>
                <div className="muted text-xs">
                  {r.strategy_id} v{r.strategy_version} · {r.pattern_kind}
                </div>
              </td>
              <td className={verdictClass}>{r.verdict.toUpperCase()}</td>
              <td className="mono">{r.n_observations}</td>
              <td className={wr != null && wr < 50 ? "neg mono" : "pos mono"}>
                {wr != null ? wr.toFixed(2) + "%" : "—"}
              </td>
              <td className={r.total_pnl_usd != null && Number(r.total_pnl_usd) >= 0 ? "pos mono" : "neg mono"}>
                {r.total_pnl_usd != null ? "$" + Number(r.total_pnl_usd).toFixed(2) : "—"}
              </td>
              <td className="mono">
                {r.confidence != null ? (Number(r.confidence) * 100).toFixed(0) + "%" : "—"}
              </td>
              <td className="muted text-xs">
                {new Date(r.observed_until).toLocaleString()}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function TemplatesPanel() {
  const [busy, setBusy] = useState<string | null>(null);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [previews, setPreviews] = useState<Record<string, PreviewResult | { error: string }>>({});

  async function preview(t: StrategyTemplate) {
    setPreviewBusy(t.id);
    setPreviews((p) => {
      const next = { ...p };
      delete next[t.id];
      return next;
    });
    try {
      const r = await fetch("/api/strategy/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy: t.strategy_id,
          symbol: "BTCUSDT",
          asset_class: t.asset_class,
          days: 7,
          params: t.params,
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "preview failed");
      setPreviews((p) => ({ ...p, [t.id]: j.result }));
    } catch (e) {
      setPreviews((p) => ({ ...p, [t.id]: { error: String((e as Error).message) } }));
    } finally {
      setPreviewBusy(null);
    }
  }

  async function deploy(t: StrategyTemplate) {
    if (!confirm(
      `Deploy "${t.label}" as the next active version of ${t.strategy_id}/${t.asset_class}?\n\n`
      + `This retires the current active config and inserts a new strategy_configs row.\n`
      + `Live order submission stays blocked until a paper_trade_certificate is granted.`
    )) return;
    setBusy(t.id);
    setMsg(null);
    try {
      const r = await fetch("/api/strategy/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: t.strategy_id,
          asset_class: t.asset_class,
          params: t.params,
          rationale: `Deployed template "${t.label}" (${t.id}) from dashboard`,
          promoted_by: "dashboard-operator",
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "promote failed");
      setMsg({ kind: "ok", text: `Deployed ${t.strategy_id} v${j.config.version}` });
    } catch (e) {
      setMsg({ kind: "err", text: String((e as Error).message) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      {msg && (
        <div className={msg.kind === "ok" ? "pos text-sm" : "neg text-sm"}>{msg.text}</div>
      )}
      <table className="matrix">
        <thead><tr>
          <th>Template</th>
          <th>Strategy</th>
          <th>Class</th>
          <th>Risk</th>
          <th>Params</th>
          <th>Preview (7d BTC)</th>
          <th>Actions</th>
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
                <td className="mono">{t.asset_class}</td>
                <td className={t.risk === "conservative" ? "pos mono" : t.risk === "aggressive" ? "neg mono" : "mono"}>
                  {t.risk}
                </td>
                <td className="mono text-xs">
                  <pre className="whitespace-pre-wrap break-all">{JSON.stringify(t.params, null, 0)}</pre>
                </td>
                <td className="mono text-xs">
                  {pv === undefined && <span className="muted">— click Preview</span>}
                  {pv !== undefined && "error" in pv && (
                    <span className="neg">{pv.error}</span>
                  )}
                  {pv !== undefined && !("error" in pv) && (
                    <div className="space-y-0.5">
                      <div>
                        PnL: <span className={Number(pv.total_pnl_usd) >= 0 ? "pos" : "neg"}>
                          ${Number(pv.total_pnl_usd).toFixed(2)}
                        </span>
                        {" / "}
                        win: {(Number(pv.win_rate) * 100).toFixed(1)}%
                      </div>
                      <div className="muted">
                        n={pv.n_positions_closed}, dd={(Number(pv.max_drawdown_pct) * 100).toFixed(2)}%
                      </div>
                    </div>
                  )}
                </td>
                <td className="space-x-1 whitespace-nowrap">
                  <button
                    className="px-2 py-1 border border-current opacity-80 hover:opacity-100 disabled:opacity-40 text-xs"
                    onClick={() => preview(t)}
                    disabled={previewBusy !== null || busy !== null}
                  >
                    {previewBusy === t.id ? "Running…" : "Preview"}
                  </button>
                  <button
                    className="px-2 py-1 border border-current opacity-80 hover:opacity-100 disabled:opacity-40 text-xs"
                    onClick={() => deploy(t)}
                    disabled={busy !== null || previewBusy !== null}
                  >
                    {busy === t.id ? "Deploying…" : "Deploy"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---- Wizard ----------------------------------------------------------------
//
// Free-form strategy builder. Three strategies (grid / matrix_agent / dca)
// expose their tunable params here; the operator can preview against
// historical bars and then deploy a new strategy_configs version.
//
// Param defaults match the live runtime defaults so a Preview without
// touching anything reflects the current production behavior.

type WizardStrategy = "grid" | "matrix_agent" | "dca";

type WizardState = {
  strategy: WizardStrategy;
  symbol: string;
  asset_class: "crypto" | "bist";
  days: number;
  // grid params
  n_grids: number;
  price_band_pct: string;
  horizon_s: number;
  // matrix_agent params
  weight_trade_flow: string;
  weight_funding: string;
  weight_oi_delta: string;
  weight_ob_imbalance: string;
  weight_news: string;
  signal_threshold: string;
  // dca params
  interval_minutes: number;
};

const WIZARD_DEFAULTS: WizardState = {
  strategy: "grid",
  symbol: "BTCUSDT",
  asset_class: "crypto",
  days: 7,
  n_grids: 10,
  price_band_pct: "0.02",
  horizon_s: 300,
  weight_trade_flow: "0.35",
  weight_funding: "0.20",
  weight_oi_delta: "0.20",
  weight_ob_imbalance: "0.15",
  weight_news: "0.10",
  signal_threshold: "0.18",
  interval_minutes: 60,
};

function wizardParams(s: WizardState): Record<string, unknown> {
  if (s.strategy === "grid") {
    return {
      n_grids: s.n_grids,
      price_band_pct: s.price_band_pct,
      horizon_s: s.horizon_s,
    };
  }
  if (s.strategy === "matrix_agent") {
    return {
      weights: {
        trade_flow: s.weight_trade_flow,
        funding: s.weight_funding,
        oi_delta: s.weight_oi_delta,
        ob_imbalance: s.weight_ob_imbalance,
        news: s.weight_news,
      },
      signal_threshold: s.signal_threshold,
    };
  }
  // dca
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
    setBusy("preview");
    setPv(null);
    try {
      const r = await fetch("/api/strategy/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy: s.strategy,
          symbol: s.symbol,
          asset_class: s.asset_class,
          days: s.days,
          params: wizardParams(s),
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "preview failed");
      setPv(j.result);
    } catch (e) {
      setPv({ error: String((e as Error).message) });
    } finally {
      setBusy(null);
    }
  }

  async function deploy() {
    if (!confirm(
      `Deploy strategy "${s.strategy}" (${s.asset_class}) with these params?\n\n`
      + `Retires the current active config for ${s.strategy}/${s.asset_class} and inserts a new version.\n`
      + `Live order submission stays blocked until a paper_trade_certificate is granted.`
    )) return;
    setBusy("deploy");
    setMsg(null);
    try {
      const r = await fetch("/api/strategy/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy_id: s.strategy,
          asset_class: s.asset_class,
          params: wizardParams(s),
          rationale: `Deployed via wizard with params ${JSON.stringify(wizardParams(s))}`,
          promoted_by: "dashboard-wizard",
        }),
      });
      const j = await r.json();
      if (!j.ok) throw new Error(j.error ?? "promote failed");
      setMsg({ kind: "ok", text: `Deployed ${j.config.strategy_id} v${j.config.version}` });
    } catch (e) {
      setMsg({ kind: "err", text: String((e as Error).message) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3 text-sm">
      {msg && (
        <div className={msg.kind === "ok" ? "pos text-sm" : "neg text-sm"}>{msg.text}</div>
      )}

      {/* common fields */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <label className="flex flex-col gap-1">
          <span className="muted text-xs uppercase tracking-wide">Strategy</span>
          <select
            className="bg-transparent border border-current px-2 py-1"
            value={s.strategy}
            onChange={(e) => set("strategy", e.target.value as WizardStrategy)}
          >
            <option value="grid">grid</option>
            <option value="matrix_agent">matrix_agent</option>
            <option value="dca">dca</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="muted text-xs uppercase tracking-wide">Asset class</span>
          <select
            className="bg-transparent border border-current px-2 py-1"
            value={s.asset_class}
            onChange={(e) => set("asset_class", e.target.value as "crypto" | "bist")}
          >
            <option value="crypto">crypto</option>
            <option value="bist">bist</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="muted text-xs uppercase tracking-wide">Preview symbol</span>
          <input
            className="bg-transparent border border-current px-2 py-1 mono"
            value={s.symbol}
            onChange={(e) => set("symbol", e.target.value.toUpperCase())}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="muted text-xs uppercase tracking-wide">Preview days</span>
          <input
            type="number"
            min={1}
            max={30}
            className="bg-transparent border border-current px-2 py-1 mono"
            value={s.days}
            onChange={(e) => set("days", parseInt(e.target.value || "1", 10))}
          />
        </label>
      </div>

      {/* strategy-specific fields */}
      {s.strategy === "grid" && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <WizardNumber label="n_grids" value={s.n_grids} onChange={(v) => set("n_grids", v)} />
          <WizardText label="price_band_pct" value={s.price_band_pct} onChange={(v) => set("price_band_pct", v)} />
          <WizardNumber label="horizon_s" value={s.horizon_s} onChange={(v) => set("horizon_s", v)} />
        </div>
      )}
      {s.strategy === "matrix_agent" && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <WizardText label="weights.trade_flow" value={s.weight_trade_flow} onChange={(v) => set("weight_trade_flow", v)} />
          <WizardText label="weights.funding" value={s.weight_funding} onChange={(v) => set("weight_funding", v)} />
          <WizardText label="weights.oi_delta" value={s.weight_oi_delta} onChange={(v) => set("weight_oi_delta", v)} />
          <WizardText label="weights.ob_imbalance" value={s.weight_ob_imbalance} onChange={(v) => set("weight_ob_imbalance", v)} />
          <WizardText label="weights.news" value={s.weight_news} onChange={(v) => set("weight_news", v)} />
          <WizardText label="signal_threshold" value={s.signal_threshold} onChange={(v) => set("signal_threshold", v)} />
        </div>
      )}
      {s.strategy === "dca" && (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          <WizardNumber label="interval_minutes" value={s.interval_minutes} onChange={(v) => set("interval_minutes", v)} />
        </div>
      )}

      <div className="flex gap-2 items-center">
        <button
          className="px-3 py-1 border border-current opacity-80 hover:opacity-100 disabled:opacity-40 text-xs"
          onClick={preview}
          disabled={busy !== null}
        >
          {busy === "preview" ? "Running…" : "Preview"}
        </button>
        <button
          className="px-3 py-1 border border-current opacity-80 hover:opacity-100 disabled:opacity-40 text-xs"
          onClick={deploy}
          disabled={busy !== null}
        >
          {busy === "deploy" ? "Deploying…" : "Deploy"}
        </button>
        <span className="muted text-xs">
          Preview uses {s.days}d of {s.symbol} {s.asset_class} bars.
        </span>
      </div>

      {pv !== null && (
        <div className="mt-2 text-xs">
          {"error" in pv ? (
            <span className="neg">{pv.error}</span>
          ) : (
            <div className="space-y-0.5 mono">
              <div>
                PnL: <span className={Number(pv.total_pnl_usd) >= 0 ? "pos" : "neg"}>
                  ${Number(pv.total_pnl_usd).toFixed(2)}
                </span>
                {" / "}
                win: {(Number(pv.win_rate) * 100).toFixed(1)}%
                {" / "}
                dd: {(Number(pv.max_drawdown_pct) * 100).toFixed(2)}%
              </div>
              <div className="muted">
                n_bars={pv.n_bars}, n_positions={pv.n_positions_closed}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WizardNumber({ label, value, onChange }: {
  label: string; value: number; onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="muted text-xs uppercase tracking-wide">{label}</span>
      <input
        type="number"
        className="bg-transparent border border-current px-2 py-1 mono"
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value || "0", 10))}
      />
    </label>
  );
}

function WizardText({ label, value, onChange }: {
  label: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="muted text-xs uppercase tracking-wide">{label}</span>
      <input
        className="bg-transparent border border-current px-2 py-1 mono"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function OpenPositionsTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted text-sm">No open positions.</p>;
  return (
    <table className="matrix">
      <thead><tr><th>Symbol</th><th>Cls</th><th>Side</th><th>Notional</th><th>Entry</th><th>Strategy</th><th>Closes by</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="mono">{r.symbol}</td>
            <td><AssetClassBadge cls={r.asset_class} /></td>
            <td className={r.side === "long" ? "pos mono" : "neg mono"}>{r.side}</td>
            <td className="mono">${Number(r.notional_usd).toFixed(2)}</td>
            <td className="mono">{Number(r.opened_price).toFixed(2)}</td>
            <td className="mono">{r.strategy_id} v{r.strategy_version}</td>
            <td className="mono">{new Date(r.close_by).toLocaleTimeString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OutcomesTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted text-sm">No outcomes yet.</p>;
  return (
    <table className="matrix">
      <thead><tr><th>Time</th><th>Symbol</th><th>Cls</th><th>Side</th><th>Strategy</th><th>Score</th><th>PnL</th></tr></thead>
      <tbody>
        {rows.map((r) => {
          const sc = Number(r.score);
          const pnl = Number(r.pnl_usd);
          return (
            <tr key={r.id}>
              <td className="mono">{new Date(r.observed_at).toLocaleTimeString()}</td>
              <td className="mono">{r.symbol}</td>
              <td><AssetClassBadge cls={r.asset_class} /></td>
              <td className={r.side === "long" ? "pos mono" : "neg mono"}>{r.side}</td>
              <td className="mono">{r.strategy_id}</td>
              <td className={`mono ${sc >= 0 ? "pos" : "neg"}`}>{sc.toFixed(3)}</td>
              <td className={`mono ${pnl >= 0 ? "pos" : "neg"}`}>{pnl.toFixed(4)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function PredictionsTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted text-sm">No predictions.</p>;
  return (
    <table className="matrix">
      <thead><tr><th>Time</th><th>Strategy</th><th>Symbol</th><th>Cls</th><th>Side</th><th>Conf</th><th>Status</th><th>Thesis</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="mono">{new Date(r.generated_at).toLocaleTimeString()}</td>
            <td className="mono">{r.strategy_id} v{r.strategy_version}</td>
            <td className="mono">{r.symbol}</td>
            <td><AssetClassBadge cls={r.asset_class} /></td>
            <td className={r.side === "long" ? "pos mono" : r.side === "short" ? "neg mono" : "muted mono"}>{r.side}</td>
            <td className="mono">{Number(r.confidence).toFixed(2)}</td>
            <td className="mono muted">{r.status}</td>
            <td className="text-xs muted truncate max-w-[480px]" title={r.thesis}>{r.thesis}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function fmtUsd(n: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(n);
}

function GraphTopologyPanel({ topology }: { topology: Dashboard["graphTopology"] }) {
  const entityEntries = Object.entries(topology?.entityCounts || {});
  const edgeEntries = Object.entries(topology?.edgeCounts || {});
  const totalEntities = entityEntries.reduce((sum, [, v]) => sum + (v || 0), 0);
  const totalEdges = edgeEntries.reduce((sum, [, v]) => sum + (v || 0), 0);

  const typedEdges = topology?.typedEdgeSamples || [];
  const top = topology?.topMentioned || [];

  // Group typed edges by edge type for compact display
  const byEdgeType: Record<string, typeof typedEdges> = {};
  for (const e of typedEdges) {
    (byEdgeType[e.edge] ??= []).push(e);
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <Stat label="Total nodes" value={totalEntities.toString()} />
        <Stat label="Total edges" value={totalEdges.toString()} />
        <Stat
          label="Entity types"
          value={entityEntries.filter(([, v]) => v > 0).length.toString()}
        />
        <Stat
          label="Edge types"
          value={edgeEntries.filter(([, v]) => v > 0).length.toString()}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div>
          <div className="muted text-xs uppercase tracking-wide mb-2">Nodes by label</div>
          {entityEntries.length === 0 ? (
            <p className="muted text-sm">No nodes yet.</p>
          ) : (
            <div className="space-y-1">
              {entityEntries
                .sort(([, a], [, b]) => b - a)
                .map(([label, count]) => (
                  <div key={label} className="flex items-center gap-2 text-xs">
                    <span className="mono w-28 muted">{label}</span>
                    <div className="flex-1 bg-zinc-800 h-2 rounded overflow-hidden">
                      <div
                        className="h-full bg-cyan-400"
                        style={{
                          width: `${Math.max(2, Math.min(100, (count / Math.max(1, totalEntities)) * 100))}%`,
                        }}
                      />
                    </div>
                    <span className="mono w-12 text-right">{count}</span>
                  </div>
                ))}
            </div>
          )}
        </div>

        <div>
          <div className="muted text-xs uppercase tracking-wide mb-2">Edges by type</div>
          {edgeEntries.length === 0 ? (
            <p className="muted text-sm">No edges yet.</p>
          ) : (
            <div className="space-y-1">
              {edgeEntries
                .sort(([, a], [, b]) => b - a)
                .map(([edge, count]) => (
                  <div key={edge} className="flex items-center gap-2 text-xs">
                    <span className="mono w-32 muted">{edge}</span>
                    <div className="flex-1 bg-zinc-800 h-2 rounded overflow-hidden">
                      <div
                        className={`h-full ${edge === "MENTIONS" ? "bg-zinc-400" : "bg-amber-400"}`}
                        style={{
                          width: `${Math.max(2, Math.min(100, (count / Math.max(1, totalEdges)) * 100))}%`,
                        }}
                      />
                    </div>
                    <span className="mono w-12 text-right">{count}</span>
                  </div>
                ))}
            </div>
          )}
        </div>
      </div>

      <div>
        <div className="muted text-xs uppercase tracking-wide mb-2">
          Top-mentioned entities (any label)
        </div>
        {top.length === 0 ? (
          <p className="muted text-sm">No entities are mentioned yet.</p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {top.map((t) => (
              <span
                key={`${t.kind}:${t.canonical}`}
                className="mono text-xs px-2 py-0.5 bg-zinc-800 rounded"
                title={`${t.kind} · ${t.mentions} mentions`}
              >
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
        <div className="muted text-xs uppercase tracking-wide mb-2">
          Sample typed edges (non-MENTIONS)
        </div>
        {Object.keys(byEdgeType).length === 0 ? (
          <p className="muted text-sm">
            No typed relations yet. They appear once <span className="mono">AI_GATEWAY_API_KEY</span>{" "}
            is set and the graph extractor uses the LLM path.
          </p>
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

function GraphSignalsPanel({
  rows, stats,
}: { rows: any[]; stats: Dashboard["graphSignalsStats"] }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
        <Stat label="Signals (24h)" value={String(stats?.total_signals ?? 0)} />
        <Stat label="Contributing nodes" value={String(stats?.contributing_nodes ?? 0)} />
        <Stat label="Assets covered" value={String(stats?.distinct_assets ?? 0)} />
        <Stat
          label="Avg compute"
          value={`${stats?.avg_compute_ms ?? 0}ms`}
        />
        <Stat
          label="Latest publish"
          value={stats?.latest_publish ? new Date(stats.latest_publish).toLocaleTimeString() : "—"}
        />
      </div>

      {rows.length === 0 ? (
        <p className="muted text-sm">
          No graph signals yet. The graph service publishes federated aggregates
          every 120s when raw_documents exist for each tracked asset.
        </p>
      ) : (
        <table className="matrix">
          <thead>
            <tr>
              <th>Asset</th><th>Node</th><th>Mentions</th>
              <th>Polarity (direct / ctx)</th>
              <th>Related companies</th>
              <th>Co-mentioned assets</th>
              <th>At</th>
            </tr>
          </thead>
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
                  <td className="mono">{r.direct_mention_count}</td>
                  <td className="mono">
                    <span className={dpol >= 0 ? "pos" : "neg"}>{dpol.toFixed(2)}</span>
                    <span className="muted"> / </span>
                    <span className={cpol >= 0 ? "pos" : "neg"}>{cpol.toFixed(2)}</span>
                  </td>
                  <td className="text-xs">
                    {companies.length === 0 ? (
                      <span className="muted">—</span>
                    ) : (
                      companies.slice(0, 4).map((c) => (
                        <span
                          key={c}
                          className="inline-block mono mr-1 px-1.5 py-0.5 bg-zinc-800 rounded text-xs"
                        >
                          {c}
                        </span>
                      ))
                    )}
                  </td>
                  <td className="text-xs">
                    {coAssets.length === 0 ? (
                      <span className="muted">—</span>
                    ) : (
                      coAssets.slice(0, 4).map((a) => (
                        <span
                          key={a}
                          className="inline-block mono mr-1 px-1.5 py-0.5 bg-zinc-800 rounded text-xs"
                        >
                          {a}
                        </span>
                      ))
                    )}
                  </td>
                  <td className="mono muted text-xs">
                    {new Date(r.computed_at).toLocaleTimeString()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function LabPanel({ rows, stats }: { rows: any[]; stats: Dashboard["labStats"] }) {
  const top = rows[0];
  const topWeights: Record<string, string> = top?.params?.weights ?? {};
  const weightEntries = Object.entries(topWeights);

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 md:grid-cols-7 gap-3 text-xs">
        <Stat label="Generation" value={String(stats?.max_gen ?? 0)} />
        <Stat label="Active" value={String(stats?.active ?? 0)} />
        <Stat label="Retired" value={String(stats?.retired ?? 0)} />
        <Stat label="Evals open" value={String(stats?.evals_open ?? 0)} />
        <Stat label="Evals scored" value={String(stats?.evals_scored ?? 0)} />
        <Stat label="Evals stale" value={String(stats?.evals_stale ?? 0)} />
        <Stat label="Promoted" value={String(stats?.promoted ?? 0)} />
      </div>

      {rows.length === 0 ? (
        <p className="muted text-sm">No active experiments yet.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <table className="matrix">
            <thead><tr>
              <th>ID</th><th>Cls</th><th>Gen</th><th>Evals</th><th>Wins</th>
              <th>Fitness</th><th>Thr</th><th>Hor</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => {
                const fit = Number(r.fitness_score);
                return (
                  <tr key={r.id}>
                    <td className="mono">{r.id.slice(0, 6)}</td>
                    <td><AssetClassBadge cls={r.asset_class} /></td>
                    <td className="mono">{r.generation}</td>
                    <td className="mono">{r.n_evaluations}</td>
                    <td className="mono">{r.n_wins}</td>
                    <td className={`mono ${fit >= 0 ? "pos" : "neg"}`}>{fit.toFixed(4)}</td>
                    <td className="mono">{Number(r.params?.signal_threshold ?? 0).toFixed(3)}</td>
                    <td className="mono">{r.params?.horizon_seconds ?? "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <div>
            <div className="muted text-xs uppercase tracking-wide mb-2">
              Best genome weights ({top ? top.id.slice(0, 6) : "—"})
            </div>
            {weightEntries.length === 0 ? (
              <p className="muted text-sm">no weights</p>
            ) : (
              <div className="space-y-1">
                {weightEntries.map(([k, v]) => {
                  const pct = Math.round(Number(v) * 100);
                  return (
                    <div key={k} className="flex items-center gap-2 text-xs">
                      <span className="mono w-28 muted">{k}</span>
                      <div className="flex-1 bg-zinc-800 h-2 rounded overflow-hidden">
                        <div
                          className="h-full bg-cyan-400"
                          style={{ width: `${Math.max(2, pct)}%` }}
                        />
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
