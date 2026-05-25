import { NextResponse } from "next/server";
import { sql, sqlLocal } from "@/lib/db";

// AGE Cypher queries need the LOAD 'age' + SET search_path prelude on every
// transaction. asyncpg's JS cousin (postgres) accepts multi-statement strings
// fine for begin/commit, but we use postgres.js `.unsafe()` calls so we can
// inline the cypher() function.
async function ageCypher(query: string): Promise<unknown[]> {
  try {
    await sqlLocal.unsafe("LOAD 'age'");
    await sqlLocal.unsafe("SET search_path = ag_catalog, public");
    return await sqlLocal.unsafe(query);
  } catch (e) {
    console.warn("age query failed:", (e as Error).message);
    return [];
  }
}

function unquoteAgtype(v: unknown): string {
  const s = String(v ?? "").trim();
  if (s.startsWith('"') && s.endsWith('"')) return s.slice(1, -1);
  return s;
}

function toIntAgtype(v: unknown): number {
  const s = String(v ?? "0").trim().replace(/^"|"$/g, "");
  const n = parseInt(s, 10);
  return Number.isFinite(n) ? n : 0;
}

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
      SELECT p.id, p.symbol, p.side, p.asset_class, p.notional_usd,
             p.opened_price, p.opened_at,
             pr.strategy_id, pr.strategy_version, pr.close_by, pr.confidence
      FROM paper_positions p
      JOIN predictions pr ON pr.id = p.prediction_id
      WHERE p.status = 'open' AND p.wallet_id = ${walletRow.id}
      ORDER BY p.opened_at DESC
    `;

    const recentPredictions = await sql`
      SELECT id, strategy_id, strategy_version, symbol, asset_class, side,
             confidence, generated_at, close_by, status, thesis
      FROM predictions
      ORDER BY generated_at DESC
      LIMIT 25
    `;

    const recentOutcomes = await sql`
      SELECT o.id, o.observed_at, o.pnl_usd, o.pnl_pct, o.score, o.reason,
             p.strategy_id, p.strategy_version, p.symbol, p.asset_class, p.side
      FROM outcomes o
      JOIN predictions p ON p.id = o.prediction_id
      ORDER BY o.observed_at DESC
      LIMIT 25
    `;

    const strategyAgg = await sql`
      SELECT p.strategy_id, p.strategy_version, p.asset_class,
             COUNT(*) AS n, AVG(o.score)::numeric(8,4) AS avg_score,
             SUM(o.pnl_usd)::numeric(12,4) AS total_pnl_usd,
             SUM(CASE WHEN o.score > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*)::numeric AS win_rate
      FROM outcomes o
      JOIN predictions p ON p.id = o.prediction_id
      WHERE o.observed_at > NOW() - INTERVAL '24 hours'
      GROUP BY p.strategy_id, p.strategy_version, p.asset_class
      ORDER BY n DESC
    `;

    const mutationProposals = await sql`
      SELECT id, strategy_id, from_version, to_version, proposal_type, source,
             rationale, status, created_at
      FROM mutation_proposals
      ORDER BY created_at DESC
      LIMIT 10
    `;

    // Phase 5 live-execution gate visibility. Joins to strategy_configs so we
    // can flag rows where an active strategy version has NO cert at all
    // (gate would deny). `now()` comparison flips an expired-validity cert
    // into the same denial bucket as missing, matching what
    // matrix_shared.trading_safety.has_valid_certificate does at runtime.
    const certificates = await sql`
      WITH active_versions AS (
        SELECT strategy_id, asset_class, version
        FROM strategy_configs WHERE status = 'active'
      )
      SELECT
        av.strategy_id, av.asset_class, av.version,
        c.id              AS cert_id,
        c.status          AS cert_status,
        c.n_outcomes, c.observation_days,
        c.win_rate, c.total_pnl_usd, c.max_drawdown_pct,
        c.granted_at, c.granted_by, c.validity_until, c.revoked_reason,
        CASE
          WHEN c.id IS NULL THEN 'no_cert'
          WHEN c.status <> 'granted' THEN 'not_granted'
          WHEN c.validity_until IS NOT NULL AND c.validity_until < NOW() THEN 'expired'
          ELSE 'valid'
        END AS gate_verdict
      FROM active_versions av
      LEFT JOIN paper_trade_certificate c
        ON  c.strategy_id  = av.strategy_id
        AND c.asset_class  = av.asset_class
        AND c.version      = av.version
      ORDER BY av.strategy_id, av.asset_class, av.version
    `;

    const labLeaderboard = await sql`
      SELECT id, asset_class, generation, n_evaluations, n_signals, n_wins,
             total_score, fitness_score, status, params, created_at,
             parent_a_id, parent_b_id
      FROM lab_experiments
      WHERE status = 'active'
      ORDER BY fitness_score DESC NULLS LAST, n_evaluations DESC
      LIMIT 20
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

    // ---- BIST overview: universe size, freshest bar timestamps, position summary
    const [bistSymbolStats] = await sql`
      SELECT
        COUNT(*) FILTER (WHERE active) AS active,
        COUNT(*) FILTER (WHERE NOT active) AS inactive,
        MAX(last_refreshed_at) AS last_refreshed
      FROM bist_symbols
    `;
    const bistBarStats = await sqlLocal`
      SELECT interval, COUNT(*) AS n, MAX(ts) AS latest_ts
      FROM market_bars
      WHERE asset_class = 'bist'
      GROUP BY interval
      ORDER BY interval
    `;
    const [bistPositionStats] = await sql`
      SELECT
        COUNT(*) FILTER (WHERE status = 'open') AS open,
        COUNT(*) FILTER (WHERE status = 'closed') AS closed,
        COALESCE(SUM(pnl_usd) FILTER (WHERE status = 'closed'), 0)::numeric(18,4) AS realized_pnl_usd
      FROM paper_positions
      WHERE asset_class = 'bist'
    `;
    const [bistPredictionStats] = await sql`
      SELECT
        COUNT(*) FILTER (WHERE status = 'open') AS open,
        COUNT(*) FILTER (WHERE status = 'closed') AS closed
      FROM predictions
      WHERE asset_class = 'bist'
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

    // ---- LOCAL: AGE graph topology (node counts per label, edge counts per type)
    const entityLabels = ["Document", "Asset", "Company", "Person", "Event", "Concept"];
    const entityCounts: Record<string, number> = {};
    for (const label of entityLabels) {
      const rows = await ageCypher(
        `SELECT * FROM cypher('matrix_graph', $$ MATCH (n:${label}) RETURN count(n) $$) AS (n agtype)`,
      );
      entityCounts[label] = rows.length > 0 ? toIntAgtype((rows[0] as any).n) : 0;
    }

    const edgeTypes = [
      "MENTIONS", "IMPACTS", "EMPLOYS", "ANNOUNCES", "OWNS",
      "REGULATES", "PARTNERS_WITH", "COMPETES_WITH", "PARTICIPATES_IN", "RELATED_TO",
    ];
    const edgeCounts: Record<string, number> = {};
    for (const t of edgeTypes) {
      const rows = await ageCypher(
        `SELECT * FROM cypher('matrix_graph', $$ MATCH ()-[r:${t}]->() RETURN count(r) $$) AS (n agtype)`,
      );
      edgeCounts[t] = rows.length > 0 ? toIntAgtype((rows[0] as any).n) : 0;
    }

    // Top-mentioned entities (Asset + Company combined)
    const topMentioned: { kind: string; canonical: string; mentions: number }[] = [];
    const topRows = await ageCypher(
      `SELECT * FROM cypher('matrix_graph', $$
         MATCH (e)<-[r:MENTIONS]-(:Document)
         WHERE labels(e)[0] IN ['Asset','Company','Person','Concept','Event']
         RETURN labels(e)[0], e.canonical, count(r) AS n
         ORDER BY n DESC LIMIT 12
       $$) AS (kind agtype, canonical agtype, n agtype)`,
    );
    for (const row of topRows) {
      const r = row as any;
      topMentioned.push({
        kind: unquoteAgtype(r.kind),
        canonical: unquoteAgtype(r.canonical),
        mentions: toIntAgtype(r.n),
      });
    }

    // Sample of typed-edge instances (non-MENTIONS) — proof the agent is
    // reasoning over real relations, not just keyword mentions
    const typedEdgeSamples: {
      edge: string; src: string; src_label: string;
      tgt: string; tgt_label: string;
    }[] = [];
    const sampleRows = await ageCypher(
      `SELECT * FROM cypher('matrix_graph', $$
         MATCH (s)-[r]->(t)
         WHERE type(r) <> 'MENTIONS'
         RETURN type(r), labels(s)[0], s.canonical, labels(t)[0], t.canonical
         LIMIT 12
       $$) AS (edge agtype, src_label agtype, src agtype, tgt_label agtype, tgt agtype)`,
    );
    for (const row of sampleRows) {
      const r = row as any;
      typedEdgeSamples.push({
        edge: unquoteAgtype(r.edge),
        src_label: unquoteAgtype(r.src_label),
        src: unquoteAgtype(r.src),
        tgt_label: unquoteAgtype(r.tgt_label),
        tgt: unquoteAgtype(r.tgt),
      });
    }

    return NextResponse.json({
      ok: true,
      wallet: walletRow,
      equityCurve: equityCurve.slice().reverse(),
      openPositions,
      recentPredictions,
      recentOutcomes,
      strategyAgg,
      mutationProposals,
      certificates,
      labLeaderboard,
      labStats: labStats[0] ?? {},
      graphSignals,
      graphSignalsStats: graphSignalsStats[0] ?? {},
      graphTopology: {
        entityCounts,
        edgeCounts,
        topMentioned,
        typedEdgeSamples,
      },
      bist: {
        symbols: bistSymbolStats ?? {},
        bars: bistBarStats,
        positions: bistPositionStats ?? {},
        predictions: bistPredictionStats ?? {},
      },
      now: new Date().toISOString(),
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: String((e as Error).message) },
      { status: 500 },
    );
  }
}
