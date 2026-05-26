"use client";

/**
 * Public-facing snapshot of the Matrix engine.
 *
 * Same browser path as the operator dashboard but consumes a different
 * API (`/api/public/stats`) that's been deliberately stripped of any
 * PII / alpha-leaking fields. See that route's docstring for what's in
 * and what's out.
 */

import { useEffect, useState } from "react";

type PublicStats = {
  ok: boolean;
  phase: { number: number; name: string; detail: string };
  uptime_days: number;
  equity_curve_pct: { ts: string; pct: number }[];
  win_rate_7d: number | null;
  n_trades_7d: number;
  pnl_pct_7d: number;
  coverage: { asset: string; last_signal_ts: string }[];
  generated_at: string;
};

const REFRESH_MS = 30_000; // public page polls slower than the operator one

export default function PublicPage() {
  const [data, setData] = useState<PublicStats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const r = await fetch("/api/public/stats", { cache: "no-store" });
        const j = (await r.json()) as PublicStats;
        if (!alive) return;
        if (!j.ok) setErr("stats unavailable");
        else { setData(j); setErr(null); }
      } catch (e) {
        if (!alive) return;
        setErr(String((e as Error).message));
      }
    }
    tick();
    const id = setInterval(tick, REFRESH_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return (
    <main className="min-h-screen p-6 max-w-4xl mx-auto">
      <header className="mb-6 flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-2xl">Matrix</h1>
        <p className="muted text-xs">
          Public read-only snapshot · refreshes every 30s
        </p>
      </header>

      {err && <p className="neg text-sm mb-4">{err}</p>}
      {!data ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="space-y-6">
          {/* phase + uptime */}
          <section className="panel p-5">
            <div className="muted text-xs uppercase tracking-wide mb-1">
              Where we are
            </div>
            <div className="text-lg font-medium">{data.phase.name}</div>
            <p className="muted text-sm mt-1">{data.phase.detail}</p>
            <p className="text-xs muted mt-2">
              System has been running for{" "}
              <span className="mono">{data.uptime_days}</span> day
              {data.uptime_days === 1 ? "" : "s"}.
            </p>
          </section>

          {/* aggregate numbers */}
          <section className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            <Stat
              label="Equity Δ (since start)"
              value={
                data.equity_curve_pct.at(-1)
                  ? `${data.equity_curve_pct.at(-1)!.pct.toFixed(3)}%`
                  : "—"
              }
              kind={
                data.equity_curve_pct.at(-1)?.pct ?? 0 >= 0 ? "pos" : "neg"
              }
            />
            <Stat
              label="Last 7d PnL"
              value={`${data.pnl_pct_7d.toFixed(3)}%`}
              kind={data.pnl_pct_7d >= 0 ? "pos" : "neg"}
            />
            <Stat
              label="Last 7d win rate"
              value={
                data.win_rate_7d != null
                  ? `${(data.win_rate_7d * 100).toFixed(1)}%`
                  : "—"
              }
            />
            <Stat
              label="Last 7d trades scored"
              value={data.n_trades_7d.toLocaleString()}
            />
            <Stat
              label="Live coverage"
              value={data.coverage.map((c) => c.asset).join(" · ") || "—"}
              sub={
                data.coverage.length > 0
                  ? `last signal ${new Date(
                      data.coverage
                        .map((c) => c.last_signal_ts)
                        .sort()
                        .at(-1)!
                    ).toLocaleTimeString()}`
                  : undefined
              }
            />
          </section>

          {/* equity curve */}
          <section>
            <h2 className="muted text-xs uppercase tracking-wide mb-2">
              Equity since start (Δ%)
            </h2>
            <EquityCurvePct curve={data.equity_curve_pct} />
          </section>

          <footer className="muted text-xs">
            <p>
              This page is intentionally sparse. Per-strategy detail, open
              positions, notional sizes, and tuning data are operator-only
              to avoid leaking the engine's research surface.
            </p>
            <p className="mt-2">
              Generated {new Date(data.generated_at).toLocaleString()}.
            </p>
          </footer>
        </div>
      )}
    </main>
  );
}

function Stat({
  label, value, sub, kind,
}: {
  label: string;
  value: string;
  sub?: string;
  kind?: "pos" | "neg";
}) {
  return (
    <div className="panel p-4">
      <div className="muted text-xs uppercase tracking-wide">{label}</div>
      <div className={`text-xl mono mt-1 ${kind ?? ""}`}>{value}</div>
      {sub && <div className="muted text-xs mt-1">{sub}</div>}
    </div>
  );
}

function EquityCurvePct({ curve }: { curve: { ts: string; pct: number }[] }) {
  if (curve.length < 2) {
    return <p className="muted text-sm">Not enough snapshots yet.</p>;
  }
  // Mini inline SVG sparkline. Pure presentation — no library needed.
  const w = 1200, h = 80, pad = 4;
  const xs = curve.map((p) => new Date(p.ts).getTime());
  const ys = curve.map((p) => p.pct);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(...ys, 0), ymax = Math.max(...ys, 0);
  const xspan = Math.max(xmax - xmin, 1);
  const yspan = Math.max(ymax - ymin, 0.0001);
  const sx = (t: number) => pad + ((t - xmin) / xspan) * (w - 2 * pad);
  const sy = (v: number) => h - pad - ((v - ymin) / yspan) * (h - 2 * pad);
  const path = curve
    .map((p, i) => `${i === 0 ? "M" : "L"}${sx(xs[i]).toFixed(2)},${sy(ys[i]).toFixed(2)}`)
    .join(" ");
  const zeroY = sy(0);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-20 panel">
      <line
        x1={0} x2={w} y1={zeroY} y2={zeroY}
        stroke="currentColor" strokeOpacity="0.2" strokeDasharray="4 4"
      />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
