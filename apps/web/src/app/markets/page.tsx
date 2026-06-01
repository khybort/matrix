"use client";

import { useEffect, useState } from "react";
import {
  Sidebar, PageShell, Panel, EmptyHint, Stat, fmtUsd,
} from "@/components/shared";

type Market = {
  name: string;
  assetClass: string;
  sessionDescription: string;
  allowsShort: boolean;
  settlementDays: number;
  liveExecutor: "wired" | "phase1-stub";
  universeSize: number;
  predictionsOpen: number;
  predictions24h: number;
  positionsOpen: number;
  positionsClosed: number;
  realizedPnlUsd: number;
  walletCashUsd: number;
  walletLockedUsd: number;
  walletCount: number;
};

type Dashboard = {
  ok: boolean;
  wallet: { starting_capital_usd: string; cash_usd: string; locked_usd: string; circuit_tripped_at: string | null };
  equityCurve: { equity_usd: string }[];
  bist: {
    symbols: { active?: number; inactive?: number; last_refreshed?: string };
    bars: { interval: string; n: number; latest_ts: string }[];
    positions: { open?: number; closed?: number; realized_pnl_usd?: string };
    predictions: { open?: number; closed?: number };
  };
  now: string;
};

const REFRESH_MS = 30_000;

export default function MarketsPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const [d, m] = await Promise.all([
          fetch("/api/dashboard", { cache: "no-store" }).then((r) => r.json()),
          fetch("/api/markets", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ markets: [] })),
        ]);
        if (!alive) return;
        if (!d.ok) { setErr("dashboard api returned !ok"); return; }
        setErr(null);
        setData(d as Dashboard);
        setMarkets((m?.markets as Market[]) ?? []);
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

  return (
    <PageShell
      sidebar={<Sidebar equity={equity} totalPct={totalPct} circuitTripped={circuitTripped} now={data.now} />}
    >
      <div className="flex items-baseline justify-between pb-2" style={{ borderBottom: "1px solid var(--rule)" }}>
        <div>
          <div className="text-[10px] tracker dim">§ 04 · Connectivity</div>
          <h1 className="serif-i text-[34px] mt-1" style={{ lineHeight: 0.95 }}>
            Markets
            <span className="dim"> — </span>
            <span className="text-[var(--ink-2)]">where we&apos;re wired, what flows</span>
          </h1>
        </div>
      </div>

      <Panel title="Registered market adapters">
        {markets.length === 0 ? (
          <EmptyHint>Loading market registry…</EmptyHint>
        ) : (
          <div className="overflow-x-auto">
            <table className="matrix">
              <thead><tr>
                <th>Market</th><th>Session</th>
                <th>Short?</th><th>Settle</th><th>Live exec</th>
                <th className="text-right">Universe</th>
                <th className="text-right">Preds 24h</th>
                <th className="text-right">Pos open</th>
                <th className="text-right">Realized PnL</th>
              </tr></thead>
              <tbody>
                {markets.map((m) => (
                  <tr key={m.name}>
                    <td className="mono">{m.name}</td>
                    <td className="text-xs muted">{m.sessionDescription}</td>
                    <td className={m.allowsShort ? "pos mono" : "muted mono"}>{m.allowsShort ? "yes" : "no"}</td>
                    <td className="mono">T+{m.settlementDays}</td>
                    <td className={m.liveExecutor === "wired" ? "pos mono" : "muted mono"}>{m.liveExecutor}</td>
                    <td className="mono text-right">{m.universeSize}</td>
                    <td className="mono text-right">
                      {m.predictions24h}
                      {m.predictionsOpen ? <span className="muted text-xs"> ({m.predictionsOpen} open)</span> : null}
                    </td>
                    <td className="mono text-right">{m.positionsOpen}</td>
                    <td className={`mono text-right ${m.realizedPnlUsd >= 0 ? "pos" : "neg"}`}>
                      {fmtUsd(m.realizedPnlUsd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="BIST · paper-only equities (Yahoo delayed feed)">
        <BistOverview bist={data.bist} />
      </Panel>
    </PageShell>
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
        <Stat label="Realized PnL" value={fmtUsd(realized)} valueClass={realized >= 0 ? "pos" : "neg"} />
      </div>

      {bist.bars.length === 0 ? (
        <EmptyHint>
          No bars yet — run <code className="mono">make bist-seed</code> (dynamic discover)
          then wait for ingestion or <code className="mono">make bist-poll</code>.
        </EmptyHint>
      ) : (
        <div className="overflow-x-auto">
          <table className="matrix">
            <thead><tr>
              <th>Interval</th>
              <th className="text-right">Bars</th>
              <th>Latest bar ts</th>
            </tr></thead>
            <tbody>
              {bist.bars.map((b) => (
                <tr key={b.interval}>
                  <td className="mono">{b.interval}</td>
                  <td className="mono text-right">{b.n}</td>
                  <td className="mono muted">{b.latest_ts ? new Date(b.latest_ts).toLocaleString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="muted text-xs mono">
        BIST predictions: {bist.predictions.open ?? 0} open · {bist.predictions.closed ?? 0} closed.
        Paper-only — no live execution.
      </div>
    </div>
  );
}
