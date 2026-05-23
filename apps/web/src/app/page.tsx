"use client";

import { useEffect, useState } from "react";

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
  now: string;
};

const REFRESH_MS = 5000;

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

  return (
    <main className="p-6 max-w-[1400px] mx-auto">
      <header className="flex items-baseline justify-between mb-6">
        <h1 className="text-2xl font-medium">
          <span className="accent">matrix</span> <span className="muted text-sm">/ trading agent dashboard</span>
        </h1>
        <span className="muted text-xs mono">{new Date(data.now).toLocaleTimeString()}</span>
      </header>

      <WalletCard wallet={data.wallet} curve={data.equityCurve} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-6">
        <Panel title="Strategy performance (24h)">
          <StrategyTable rows={data.strategyAgg} />
        </Panel>
        <Panel title="Mutation proposals (self-improvement)">
          <ProposalsTable rows={data.mutationProposals} />
        </Panel>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <Panel title={`Open positions (${data.openPositions.length})`}>
          <OpenPositionsTable rows={data.openPositions} />
        </Panel>
        <Panel title="Recent outcomes">
          <OutcomesTable rows={data.recentOutcomes} />
        </Panel>
      </div>

      <Panel title="Recent predictions" className="mt-4">
        <PredictionsTable rows={data.recentPredictions} />
      </Panel>

      <footer className="muted text-xs mt-8 text-center mono">
        refreshing every {REFRESH_MS / 1000}s · local-only
      </footer>
    </main>
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
        <tr><th>Strategy</th><th>v</th><th>N</th><th>Avg score</th><th>Win rate</th><th>PnL (USD)</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const sc = Number(r.avg_score);
          const pnl = Number(r.total_pnl_usd);
          return (
            <tr key={`${r.strategy_id}-${r.strategy_version}`}>
              <td className="mono">{r.strategy_id}</td>
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

function OpenPositionsTable({ rows }: { rows: any[] }) {
  if (!rows.length) return <p className="muted text-sm">No open positions.</p>;
  return (
    <table className="matrix">
      <thead><tr><th>Symbol</th><th>Side</th><th>Notional</th><th>Entry</th><th>Strategy</th><th>Closes by</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="mono">{r.symbol}</td>
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
      <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Strategy</th><th>Score</th><th>PnL</th></tr></thead>
      <tbody>
        {rows.map((r) => {
          const sc = Number(r.score);
          const pnl = Number(r.pnl_usd);
          return (
            <tr key={r.id}>
              <td className="mono">{new Date(r.observed_at).toLocaleTimeString()}</td>
              <td className="mono">{r.symbol}</td>
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
      <thead><tr><th>Time</th><th>Strategy</th><th>Symbol</th><th>Side</th><th>Conf</th><th>Status</th><th>Thesis</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="mono">{new Date(r.generated_at).toLocaleTimeString()}</td>
            <td className="mono">{r.strategy_id} v{r.strategy_version}</td>
            <td className="mono">{r.symbol}</td>
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
