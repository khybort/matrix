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

    const labLeaderboard = await sql`
      SELECT id, generation, n_evaluations, n_signals, n_wins,
             total_score, fitness_score, status, params, created_at,
             parent_a_id, parent_b_id
      FROM lab_experiments
      WHERE status = 'active'
      ORDER BY fitness_score DESC NULLS LAST, n_evaluations DESC
      LIMIT 10
    `;

    const labStats = await sql`
      SELECT
        COUNT(*) FILTER (WHERE status = 'active') AS active,
        COUNT(*) FILTER (WHERE status = 'retired') AS retired,
        COUNT(*) FILTER (WHERE status = 'promoted') AS promoted,
        MAX(generation) AS max_gen,
        (SELECT COUNT(*) FROM lab_evaluations WHERE status = 'open') AS evals_open,
        (SELECT COUNT(*) FROM lab_evaluations WHERE status = 'scored') AS evals_scored,
        (SELECT COUNT(*) FROM lab_evaluations WHERE status = 'stale') AS evals_stale
      FROM lab_experiments
    `;

    // Federated graph_signals: latest row per asset across all nodes
    const graphSignals = await sql`
      SELECT DISTINCT ON (asset)
        asset, node_id, computed_at, direct_mention_count,
        direct_polarity, contextual_polarity, recency_weight,
        n_contextual_documents, related_companies, co_mentioned_assets,
        computed_in_ms
      FROM graph_signals
      WHERE computed_at > NOW() - INTERVAL '6 hours'
      ORDER BY asset, computed_at DESC
    `;

    const graphSignalsStats = await sql`
      SELECT
        COUNT(*) AS total_signals,
        COUNT(DISTINCT node_id) AS contributing_nodes,
        COUNT(DISTINCT asset) AS distinct_assets,
        MAX(computed_at) AS latest_publish,
        AVG(computed_in_ms)::int AS avg_compute_ms
      FROM graph_signals
      WHERE computed_at > NOW() - INTERVAL '24 hours'
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
      labLeaderboard,
      labStats: labStats[0] ?? {},
      graphSignals,
      graphSignalsStats: graphSignalsStats[0] ?? {},
      now: new Date().toISOString(),
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: String((e as Error).message) },
      { status: 500 },
    );
  }
}
