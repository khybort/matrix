/**
 * POST /api/strategy/promote — operator-driven strategy_config write path.
 *
 * Inserts a new `strategy_configs` row at (existing_max_version + 1) for the
 * given (strategy_id, asset_class), and retires the previous active row.
 * Mirrors the SQL that `services/labs/promote.apply_proposal` runs server-
 * side, but does it without requiring a `mutation_proposal` row first — the
 * dashboard's templates panel needs a direct deploy path for the
 * pre-configured starters.
 *
 * Safety:
 * - This does NOT touch wallets, predictions, or risk caps.
 * - It does NOT grant a paper_trade_certificate. Live execution still
 *   requires the cert (see matrix_shared.trading_safety.has_valid_certificate).
 *   So deploying a template here only affects which params the live strategy
 *   modules consume — actual order submission stays gated.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

type PromoteBody = {
  strategy_id: string;
  asset_class?: string;
  params: Record<string, unknown>;
  rationale?: string;
  promoted_by?: string;
};

export async function POST(req: Request) {
  let body: PromoteBody;
  try {
    body = (await req.json()) as PromoteBody;
  } catch {
    return NextResponse.json(
      { ok: false, error: "invalid json body" },
      { status: 400 },
    );
  }

  const strategyId = (body.strategy_id ?? "").trim();
  const assetClass = (body.asset_class ?? "crypto").trim();
  const params = body.params;
  if (!strategyId || !params || typeof params !== "object") {
    return NextResponse.json(
      { ok: false, error: "strategy_id and params are required" },
      { status: 400 },
    );
  }

  const rationale =
    body.rationale ?? `Deployed from dashboard template by ${body.promoted_by ?? "operator"}`;

  try {
    // One transaction: retire current active, insert new at next version.
    const result = await sql.begin(async (tx) => {
      const [maxRow] = await tx<{ max_v: number | null }[]>`
        SELECT MAX(version) AS max_v
        FROM strategy_configs
        WHERE strategy_id = ${strategyId} AND asset_class = ${assetClass}
      `;
      const nextVersion = (maxRow?.max_v ?? 0) + 1;

      await tx`
        UPDATE strategy_configs
        SET status = 'retired'
        WHERE strategy_id = ${strategyId}
          AND asset_class = ${assetClass}
          AND status = 'active'
      `;

      // SQLAlchemy default=uuid.uuid4 lives on the model — raw SQL bypasses
      // that, so generate the uuid here. `gen_random_uuid()` is from pgcrypto.
      // postgres-js's `sql.json(...)` keeps `params` as a real JSON object on
      // the column; plain JSON.stringify would store it as a JSON-string-of-
      // a-JSON-object, which the SQLAlchemy reader can't unpack.
      const rows = (await tx`
        INSERT INTO strategy_configs (
          id, strategy_id, asset_class, version, status, params, rationale, promoted_at
        ) VALUES (
          gen_random_uuid(),
          ${strategyId}, ${assetClass}, ${nextVersion}, 'active',
          ${sql.json(params as never)}, ${rationale}, NOW()
        )
        RETURNING id, strategy_id, version, asset_class
      `) as unknown as {
        id: string;
        strategy_id: string;
        version: number;
        asset_class: string;
      }[];
      return rows[0];
    });

    return NextResponse.json({ ok: true, config: result });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
