/**
 * GET /api/public/stats — sanitized public-facing aggregate.
 *
 * Visible from anywhere, no auth. Every field below was chosen with the
 * "would I be comfortable an alpha-leaking competitor saw this" filter:
 *
 *   SAFE to expose                          DELIBERATELY HIDDEN
 *   ----------------------------------      --------------------------------
 *   - Equity change vs starting (%)         - Equity in USD, wallet UUID
 *   - 7d win rate aggregate                 - Per-strategy breakdown (alpha)
 *   - 7d trade count                        - Open positions, notional sizes
 *   - System uptime (days running)          - Mutation_proposals + weights
 *   - Phase label (Phase 3 paper, etc.)     - Strategy_configs.params
 *   - BTC/ETH coverage indicator            - API keys, chat IDs, broker
 *   - Last refresh timestamp                - Internal symbols beyond BTC/ETH
 *
 * If we ever want to publish more, default to "least information that
 * still tells the operational story" — operators reading marketing want
 * to know whether the system is alive and trending, not the order book.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";
export const revalidate = 0;

// Phase label is hand-curated; bumps when the operator decides we're in a
// new phase. Keeping it static avoids leaking decision-internal data.
const CURRENT_PHASE = {
  number: 3,
  name: "Phase 3 — paper-trade engine + strategy templates",
  detail: "Multi-strategy paper trade. Live capital still gated.",
};

export async function GET() {
  try {
    // --- equity curve as % change from starting capital ------------------
    const [walletRow] = await sql<
      { starting_capital_usd: string; created_at: string }[]
    >`
      SELECT starting_capital_usd, created_at
      FROM wallets ORDER BY created_at LIMIT 1
    `;
    const starting = walletRow ? Number(walletRow.starting_capital_usd) : 10000;
    const createdAt = walletRow ? new Date(walletRow.created_at) : new Date();

    const snapshots = await sql<
      { snapshot_ts: string; equity_usd: string }[]
    >`
      SELECT snapshot_ts, equity_usd
      FROM wallet_snapshots
      WHERE wallet_id = (SELECT id FROM wallets ORDER BY created_at LIMIT 1)
      ORDER BY snapshot_ts DESC
      LIMIT 200
    `;
    const equityCurvePct = snapshots
      .slice()
      .reverse()
      .map((s) => ({
        ts: s.snapshot_ts,
        pct: starting > 0
          ? ((Number(s.equity_usd) - starting) / starting) * 100
          : 0,
      }));

    // --- 7d aggregate win rate & trade count -----------------------------
    const [agg] = await sql<
      { n: string; wins: string }[]
    >`
      SELECT COUNT(*)::text AS n,
             SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)::text AS wins
      FROM outcomes
      WHERE observed_at > NOW() - INTERVAL '7 days'
    `;
    const nTrades7d = Number(agg?.n ?? "0");
    const winsLast7d = Number(agg?.wins ?? "0");
    const winRate7d = nTrades7d > 0 ? winsLast7d / nTrades7d : null;

    // --- 7d aggregate as a percentage of starting capital ----------------
    // Single number summarises "did the system make or lose money this week".
    // Reporting % keeps the scale identifiable without revealing notional.
    const [pnlAgg] = await sql<{ total: string | null }[]>`
      SELECT SUM(pnl_usd)::text AS total
      FROM outcomes
      WHERE observed_at > NOW() - INTERVAL '7 days'
    `;
    const pnlPctLast7d = starting > 0
      ? (Number(pnlAgg?.total ?? "0") / starting) * 100
      : 0;

    // --- coverage: are BTC and ETH actively being scored ------------------
    const coverage = await sql<{ asset: string; latest: string }[]>`
      SELECT asset,
             MAX(computed_at) AS latest
      FROM graph_signals
      WHERE asset IN ('BTC', 'ETH')
      GROUP BY asset
    `;

    // --- system uptime: oldest wallet row defines start --------------------
    const ageMs = Date.now() - createdAt.getTime();
    const ageDays = Math.floor(ageMs / (1000 * 60 * 60 * 24));

    return NextResponse.json({
      ok: true,
      phase: CURRENT_PHASE,
      uptime_days: ageDays,
      equity_curve_pct: equityCurvePct,
      win_rate_7d: winRate7d,
      n_trades_7d: nTrades7d,
      pnl_pct_7d: pnlPctLast7d,
      coverage: coverage.map((c) => ({
        asset: c.asset,
        last_signal_ts: c.latest,
      })),
      generated_at: new Date().toISOString(),
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
