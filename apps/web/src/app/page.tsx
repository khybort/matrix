"use client";

import { useEffect, useState } from "react";
import {
  Sidebar, PageShell, Panel, AssetBadge, MarketHeader, EmptyHint,
  fmtUsd, fmtMoney, formatClock, formatDuration, splitByMarket,
} from "@/components/shared";

type EquityPoint = {
  snapshot_ts: string;
  equity_usd: string;
  cash_usd: string;
  locked_usd: string;
  n_open_positions: number;
  unrealized_pnl_usd: string;
};

type OpenPosition = {
  id: string;
  symbol: string;
  side: "long" | "short";
  asset_class: string;
  notional_usd: string;
  opened_price: string;
  opened_at: string;
  strategy_id: string;
  strategy_version: number;
  close_by: string;
};

type Outcome = {
  id: string;
  observed_at: string;
  pnl_usd: string;
  pnl_pct: string | null;
  score: string;
  strategy_id: string;
  strategy_version: number;
  symbol: string;
  asset_class: string;
  side: "long" | "short";
};

type StrategyAgg = {
  strategy_id: string;
  strategy_version: number;
  asset_class: string;
  n: string;
  avg_score: string;
  total_pnl_usd: string;
  win_rate: string;
};

type Dashboard = {
  ok: boolean;
  wallet: {
    starting_capital_usd: string;
    cash_usd: string;
    locked_usd: string;
    max_position_pct: string;
    max_concurrent_positions: number;
    daily_loss_circuit_pct: string;
    circuit_tripped_at: string | null;
  };
  equityCurve: EquityPoint[];
  openPositions: OpenPosition[];
  recentOutcomes: Outcome[];
  strategyAgg: StrategyAgg[];
  now: string;
};

const REFRESH_MS = 5000;

export default function TradesPage() {
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
    return (
      <PageShell sidebar={<Sidebar />}>
        <p className="neg text-sm">⨯ {err}</p>
      </PageShell>
    );
  }
  if (!data) {
    return (
      <PageShell sidebar={<Sidebar />}>
        <p className="serif-i muted text-base">awaiting telemetry…</p>
      </PageShell>
    );
  }

  const starting = Number(data.wallet.starting_capital_usd);
  const cash = Number(data.wallet.cash_usd);
  const locked = Number(data.wallet.locked_usd);
  const latest = data.equityCurve.at(-1);
  const equity = latest ? Number(latest.equity_usd) : cash + locked;
  const unrealized = latest ? Number(latest.unrealized_pnl_usd) : 0;
  const realized = cash + locked - starting;
  const totalPct = ((equity - starting) / starting) * 100;
  const circuitTripped = !!data.wallet.circuit_tripped_at;
  const trades24h = data.strategyAgg.reduce((s, r) => s + Number(r.n), 0);

  const openByMkt = splitByMarket(data.openPositions);
  const outByMkt = splitByMarket(data.recentOutcomes);
  const aggByMkt = splitByMarket(data.strategyAgg);

  return (
    <PageShell
      sidebar={
        <Sidebar
          equity={equity}
          totalPct={totalPct}
          circuitTripped={circuitTripped}
          now={data.now}
        />
      }
    >
      {/* Hero strip: page label */}
      <div className="flex items-baseline justify-between pb-2" style={{ borderBottom: "1px solid var(--rule)" }}>
        <div>
          <div className="text-[10px] tracker dim">§ 01 · Live</div>
          <h1 className="serif-i text-[34px] mt-1" style={{ lineHeight: 0.95 }}>
            Trades
            <span className="dim"> — </span>
            <span className="text-[var(--ink-2)]">what the agent is doing right now</span>
          </h1>
        </div>
        <div className="text-right">
          <div className="text-[10px] tracker dim">P&amp;L vs start</div>
          <div className={`headline text-[34px] ${totalPct >= 0 ? "pos" : "neg"}`}>
            {totalPct >= 0 ? "+" : ""}{totalPct.toFixed(3)}%
          </div>
        </div>
      </div>

      {/* Equity + wallet ledger */}
      <section className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-5">
        <Panel title="Equity curve">
          <div className="scanlines -m-4 p-4">
            <EquityHero
              equity={equity}
              unrealized={unrealized}
              trades24h={trades24h}
              openCount={data.openPositions.length}
            />
            <EquityCurve curve={data.equityCurve} starting={starting} />
          </div>
        </Panel>

        <Panel title="Wallet ledger">
          <KpiRow k="Cash"        v={fmtUsd(cash)} />
          <KpiRow k="Locked"      v={fmtUsd(locked)} />
          <KpiRow k="Unrealized"  v={signed(unrealized)} tone={unrealized >= 0 ? "pos" : "neg"} />
          <KpiRow k="Realized"    v={signed(realized)}   tone={realized   >= 0 ? "pos" : "neg"} />
          <KpiRow k="Trades · 24h" v={String(trades24h)} />
          <div className="mt-4 pt-3" style={{ borderTop: "1px solid var(--rule)" }}>
            <div className="text-[9.5px] tracker dim mb-2">Risk gates</div>
            <div className="grid grid-cols-3 gap-2 text-[10.5px] tnum">
              <Gauge label="pos cap" value={`${(Number(data.wallet.max_position_pct) * 100).toFixed(1)}%`} />
              <Gauge label="kill" value={`${(Number(data.wallet.daily_loss_circuit_pct) * 100).toFixed(1)}%`} />
              <Gauge label="max open" value={String(data.wallet.max_concurrent_positions)} />
            </div>
          </div>
        </Panel>
      </section>

      {/* Open positions */}
      <Panel
        title="Open positions"
        action={<span>{data.openPositions.length} active</span>}
      >
        <div className="space-y-5">
          <div>
            <MarketHeader label="Crypto" count={openByMkt.crypto.length} tone="crypto" />
            {openByMkt.crypto.length === 0
              ? <EmptyHint>no crypto positions</EmptyHint>
              : <OpenPositionsTable rows={openByMkt.crypto} />}
          </div>
          <div>
            <MarketHeader label="BIST" count={openByMkt.bist.length} tone="bist" />
            {openByMkt.bist.length === 0
              ? <EmptyHint>no BIST positions</EmptyHint>
              : <OpenPositionsTable rows={openByMkt.bist} />}
          </div>
        </div>
      </Panel>

      {/* Recent trades */}
      <Panel
        title="Recent fills"
        action={<span>last {data.recentOutcomes.length}</span>}
      >
        <div className="space-y-5">
          <div>
            <MarketHeader label="Crypto" count={outByMkt.crypto.length} tone="crypto" />
            {outByMkt.crypto.length === 0
              ? <EmptyHint>no closed crypto trades</EmptyHint>
              : <OutcomesTable rows={outByMkt.crypto} />}
          </div>
          <div>
            <MarketHeader label="BIST" count={outByMkt.bist.length} tone="bist" />
            {outByMkt.bist.length === 0
              ? <EmptyHint>no closed BIST trades</EmptyHint>
              : <OutcomesTable rows={outByMkt.bist} />}
          </div>
        </div>
      </Panel>

      {/* Strategies 24h */}
      <Panel title="Strategies · last 24 hours">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div>
            <MarketHeader label="Crypto" count={aggByMkt.crypto.length} tone="crypto" />
            {aggByMkt.crypto.length === 0
              ? <EmptyHint>no crypto outcomes</EmptyHint>
              : <StrategyTable rows={aggByMkt.crypto} />}
          </div>
          <div>
            <MarketHeader label="BIST" count={aggByMkt.bist.length} tone="bist" />
            {aggByMkt.bist.length === 0
              ? <EmptyHint>no BIST outcomes</EmptyHint>
              : <StrategyTable rows={aggByMkt.bist} />}
          </div>
        </div>
      </Panel>

      <footer className="teletype text-center pt-3 pb-1">
        <span className="arrow">▸ </span>tick every {REFRESH_MS / 1000}s
        <span className="dim mx-2">·</span>
        <span className="arrow">▸ </span>{formatClock(new Date(data.now))}
      </footer>
    </PageShell>
  );
}

function signed(n: number): string {
  return (n >= 0 ? "+" : "") + fmtUsd(n);
}

// ── KPI row ──────────────────────────────────────────────────────────────

function KpiRow({ k, v, tone }: { k: string; v: string; tone?: "pos" | "neg" }) {
  return (
    <div className="kpi-row">
      <span className="k">{k}</span>
      <span className={`v ${tone ?? ""}`}>{v}</span>
    </div>
  );
}

function Gauge({ label, value }: { label: string; value: string }) {
  return (
    <div className="frame" style={{ background: "var(--panel-2)" }}>
      <div className="px-2 py-1.5">
        <div className="text-[8.5px] tracker dim">{label}</div>
        <div className="tnum mt-0.5">{value}</div>
      </div>
    </div>
  );
}

// ── Equity hero stat strip ───────────────────────────────────────────────

function EquityHero({ equity, unrealized, trades24h, openCount }: {
  equity: number; unrealized: number; trades24h: number; openCount: number;
}) {
  return (
    <div className="grid grid-cols-4 gap-4 pb-4 mb-3" style={{ borderBottom: "1px dotted var(--rule)" }}>
      <div className="col-span-2">
        <div className="text-[9.5px] tracker dim">Equity (USD)</div>
        <div className="headline text-[44px] mt-1">{fmtMoney(equity)}</div>
      </div>
      <div>
        <div className="text-[9.5px] tracker dim">Unrealized</div>
        <div className={`headline text-[26px] mt-1 ${unrealized >= 0 ? "pos" : "neg"}`}>
          {unrealized >= 0 ? "+" : "−"}{fmtMoney(Math.abs(unrealized))}
        </div>
      </div>
      <div>
        <div className="text-[9.5px] tracker dim">Flow · 24h</div>
        <div className="headline text-[26px] mt-1">
          {trades24h}
          <span className="dim text-[16px]"> · {openCount} open</span>
        </div>
      </div>
    </div>
  );
}

// ── Equity curve ─────────────────────────────────────────────────────────

function EquityCurve({ curve, starting }: { curve: EquityPoint[]; starting: number }) {
  if (!curve.length) {
    return <div className="dim text-center py-10 serif-i text-base">no snapshots yet</div>;
  }
  const w = 1200, h = 180, padL = 4, padR = 4, padT = 12, padB = 4;
  const xs = curve.map((p) => new Date(p.snapshot_ts).getTime());
  const ys = curve.map((p) => Number(p.equity_usd));
  const minX = xs[0]!;
  const maxX = xs[xs.length - 1]!;
  const allY = [...ys, starting];
  const minY = Math.min(...allY);
  const maxY = Math.max(...allY);
  const ySpan = Math.max(1, maxY - minY);
  const xSpan = Math.max(1, maxX - minX);

  const points = curve.map((p, i) => {
    const x = padL + ((xs[i]! - minX) / xSpan) * (w - padL - padR);
    const y = h - padB - ((Number(p.equity_usd) - minY) / ySpan) * (h - padT - padB);
    return { x, y };
  });
  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  const areaPath =
    `M${points[0]!.x.toFixed(2)},${h - padB} ` +
    points.map((p) => `L${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ") +
    ` L${points[points.length - 1]!.x.toFixed(2)},${h - padB} Z`;
  const startY = h - padB - ((starting - minY) / ySpan) * (h - padT - padB);
  const peakY = h - padB - ((maxY - minY) / ySpan) * (h - padT - padB);
  const last = points[points.length - 1]!;
  const lastValue = ys[ys.length - 1]!;
  const tone = lastValue >= starting ? "pos" : "neg";
  const toneColor = lastValue >= starting ? "var(--pos)" : "var(--neg)";

  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-44 block">
        <defs>
          <linearGradient id="eqfill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={toneColor} stopOpacity="0.22" />
            <stop offset="100%" stopColor={toneColor} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* High water mark */}
        <line
          x1={padL} x2={w - padR}
          y1={peakY} y2={peakY}
          stroke="var(--amber)" strokeOpacity="0.45"
          strokeWidth="1" strokeDasharray="1,4"
        />
        {/* Starting equity baseline */}
        <line
          x1={padL} x2={w - padR}
          y1={startY} y2={startY}
          stroke="var(--ink-3)" strokeOpacity="0.4"
          strokeWidth="1" strokeDasharray="3,3"
        />

        <path d={areaPath} fill="url(#eqfill)" />
        <path d={linePath} fill="none" stroke={toneColor} strokeWidth="1.4" />

        {/* Latest point marker */}
        <circle cx={last.x} cy={last.y} r="3.5" fill={toneColor} />
        <circle cx={last.x} cy={last.y} r="6" fill="none" stroke={toneColor} strokeOpacity="0.4" />
      </svg>

      <div className="flex justify-between mt-2 text-[10px] tracker-sm dim tnum">
        <span>{new Date(minX).toLocaleString([], { hour: "2-digit", minute: "2-digit", month: "short", day: "2-digit" })}</span>
        <span>
          base <span className="text-[var(--ink-2)]">${starting.toFixed(2)}</span>
          <span className="dim mx-1">·</span>
          peak <span className="accent">${maxY.toFixed(2)}</span>
        </span>
        <span className={tone}>now ${lastValue.toFixed(2)}</span>
      </div>
    </div>
  );
}

// ── Tables ───────────────────────────────────────────────────────────────

function OpenPositionsTable({ rows }: { rows: OpenPosition[] }) {
  const now = Date.now();
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th className="text-right">Notional</th>
            <th className="text-right">Entry</th>
            <th>Age</th>
            <th>Closes</th>
            <th>Strategy</th>
            <th>Cls</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const ageMs = now - new Date(r.opened_at).getTime();
            return (
              <tr key={r.id}>
                <td className="font-medium">{r.symbol}</td>
                <td>
                  <span className={`tracker-sm text-[10px] ${r.side === "long" ? "pos" : "neg"}`}>
                    {r.side === "long" ? "▲ long" : "▼ short"}
                  </span>
                </td>
                <td className="text-right tnum">${Number(r.notional_usd).toFixed(2)}</td>
                <td className="text-right tnum">{Number(r.opened_price).toFixed(2)}</td>
                <td className="dim tnum">{formatDuration(ageMs)}</td>
                <td className="dim tnum">{formatClock(new Date(r.close_by))}</td>
                <td className="dim">{r.strategy_id}<span className="faint"> v{r.strategy_version}</span></td>
                <td><AssetBadge cls={r.asset_class} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OutcomesTable({ rows }: { rows: Outcome[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead>
          <tr>
            <th>Time</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Strategy</th>
            <th className="text-right">Score</th>
            <th className="text-right">PnL</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const sc = Number(r.score);
            const pnl = Number(r.pnl_usd);
            return (
              <tr key={r.id}>
                <td className="dim tnum">{formatClock(new Date(r.observed_at))}</td>
                <td className="font-medium">{r.symbol}</td>
                <td>
                  <span className={`tracker-sm text-[10px] ${r.side === "long" ? "pos" : "neg"}`}>
                    {r.side === "long" ? "▲" : "▼"} {r.side}
                  </span>
                </td>
                <td className="dim">{r.strategy_id}</td>
                <td className={`text-right tnum ${sc >= 0 ? "pos" : "neg"}`}>
                  {sc >= 0 ? "+" : ""}{sc.toFixed(3)}
                </td>
                <td className={`text-right tnum font-medium ${pnl >= 0 ? "pos" : "neg"}`}>
                  {pnl >= 0 ? "+" : "−"}${Math.abs(pnl).toFixed(4)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StrategyTable({ rows }: { rows: StrategyAgg[] }) {
  const sorted = [...rows].sort((a, b) => Number(b.total_pnl_usd) - Number(a.total_pnl_usd));
  return (
    <div className="overflow-x-auto">
      <table className="matrix">
        <thead>
          <tr>
            <th>Strategy</th>
            <th className="text-right">N</th>
            <th className="text-right">Win</th>
            <th className="text-right">PnL</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const pnl = Number(r.total_pnl_usd);
            const wr = Number(r.win_rate);
            return (
              <tr key={`${r.strategy_id}-${r.strategy_version}-${r.asset_class}`}>
                <td>
                  <span className="font-medium">{r.strategy_id}</span>
                  <span className="faint"> v{r.strategy_version}</span>
                </td>
                <td className="text-right tnum">{r.n}</td>
                <td className="text-right tnum">{(wr * 100).toFixed(0)}%</td>
                <td className={`text-right tnum font-medium ${pnl >= 0 ? "pos" : "neg"}`}>
                  {pnl >= 0 ? "+" : "−"}${Math.abs(pnl).toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
