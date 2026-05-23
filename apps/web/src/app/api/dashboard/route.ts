import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  try {
    const [walletRow] = await sql`
      SELECT id, name, starting_capital_usd, cash_usd, locked_usd,
             max_position_pct, max_concurrent_positions, daily_loss_circuit_pct,
             circuit_tripped_at, day_start_equity
      FROM wallets ORDER BY created_at LIMIT 1
    `;

    const equityCurve = await sql`
      SELECT snapshot_ts, equity_usd, cash_usd, locked_usd, n_open_positions,
             realized_pnl_usd, unrealized_pnl_usd
      FROM wallet_snapshots
      WHERE wallet_id = ${walletRow.id}
      ORDER BY snapshot_ts DESC
      LIMIT 200
    `;

    const openPositions = await sql`
      SELECT p.id, p.symbol, p.side, p.notional_usd, p.opened_price, p.opened_at,
             pr.strategy_id, pr.strategy_version, pr.close_by, pr.confidence
      FROM paper_positions p
      JOIN predictions pr ON pr.id = p.prediction_id
      WHERE p.status = 'open' AND p.wallet_id = ${walletRow.id}
      ORDER BY p.opened_at DESC
    `;

    const recentPredictions = await sql`
      SELECT id, strategy_id, strategy_version, symbol, side, confidence,
             generated_at, close_by, status, thesis
      FROM predictions
      ORDER BY generated_at DESC
      LIMIT 25
    `;

    const recentOutcomes = await sql`
      SELECT o.id, o.observed_at, o.pnl_usd, o.pnl_pct, o.score, o.reason,
             p.strategy_id, p.strategy_version, p.symbol, p.side
      FROM outcomes o
      JOIN predictions p ON p.id = o.prediction_id
      ORDER BY o.observed_at DESC
      LIMIT 25
    `;

    const strategyAgg = await sql`
      SELECT p.strategy_id, p.strategy_version,
             COUNT(*) AS n, AVG(o.score)::numeric(8,4) AS avg_score,
             SUM(o.pnl_usd)::numeric(12,4) AS total_pnl_usd,
             SUM(CASE WHEN o.score > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*)::numeric AS win_rate
      FROM outcomes o
      JOIN predictions p ON p.id = o.prediction_id
      WHERE o.observed_at > NOW() - INTERVAL '24 hours'
      GROUP BY p.strategy_id, p.strategy_version
      ORDER BY n DESC
    `;

    const mutationProposals = await sql`
      SELECT id, strategy_id, from_version, to_version, proposal_type, source,
             rationale, status, created_at
      FROM mutation_proposals
      ORDER BY created_at DESC
      LIMIT 10
    `;

    return NextResponse.json({
      ok: true,
      wallet: walletRow,
      equityCurve: equityCurve.slice().reverse(),
      openPositions,
      recentPredictions,
      recentOutcomes,
      strategyAgg,
      mutationProposals,
      now: new Date().toISOString(),
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: String((e as Error).message) },
      { status: 500 },
    );
  }
}
