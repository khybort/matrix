/**
 * POST /api/proposals/:id/apply — operator approves a pending MutationProposal.
 *
 * Mirrors `services/labs/src/labs/promote.py::apply_proposal`:
 *   1. Load the proposal (must be pending)
 *   2. Retire the active strategy_configs row for (strategy_id, asset_class)
 *   3. Insert a new strategy_configs row at to_version with after_params
 *   4. Mark mutation_proposals.status = 'applied', applied_at = NOW()
 *
 * Risk caps stay enforced at the strategy layer; this route only touches
 * params (signal weights / thresholds / horizons). It does NOT grant a
 * paper_trade_certificate — live execution remains gated.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

const FORBIDDEN_KEYS = new Set([
  "max_position_pct",
  "daily_loss_circuit_pct",
  "max_concurrent_positions",
]);

function scrubForbidden(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (!FORBIDDEN_KEYS.has(k)) out[k] = v;
  }
  return out;
}

export async function POST(
  _req: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  if (!id) {
    return NextResponse.json({ ok: false, error: "missing id" }, { status: 400 });
  }

  try {
    const result = await sql.begin(async (tx) => {
      const rows = (await tx`
        SELECT id, strategy_id, asset_class, to_version, after_params, status
        FROM mutation_proposals
        WHERE id = ${id}
        FOR UPDATE
      `) as unknown as {
        id: string;
        strategy_id: string;
        asset_class: string;
        to_version: number;
        after_params: Record<string, unknown>;
        status: string;
      }[];

      if (!rows.length) throw new Error("proposal not found");
      const proposal = rows[0];
      if (proposal.status !== "pending") {
        throw new Error(`proposal status is '${proposal.status}', expected 'pending'`);
      }

      const cleanParams = scrubForbidden(proposal.after_params ?? {});

      await tx`
        UPDATE strategy_configs
        SET status = 'retired'
        WHERE strategy_id = ${proposal.strategy_id}
          AND asset_class = ${proposal.asset_class}
          AND status = 'active'
      `;

      const inserted = (await tx`
        INSERT INTO strategy_configs (
          id, strategy_id, asset_class, version, status, params, rationale, promoted_at
        ) VALUES (
          gen_random_uuid(),
          ${proposal.strategy_id}, ${proposal.asset_class}, ${proposal.to_version},
          'active',
          ${sql.json(cleanParams as never)},
          ${`Applied from proposal ${id}`},
          NOW()
        )
        RETURNING id, version
      `) as unknown as { id: string; version: number }[];

      await tx`
        UPDATE mutation_proposals
        SET status = 'applied', applied_at = NOW()
        WHERE id = ${id}
      `;

      return {
        proposal_id: id,
        strategy_id: proposal.strategy_id,
        asset_class: proposal.asset_class,
        new_config: inserted[0],
      };
    });

    return NextResponse.json({ ok: true, ...result });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
