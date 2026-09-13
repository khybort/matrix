/**
 * POST /api/proposals/:id/apply — operator approves a pending MutationProposal.
 *
 * Mirrors `services/labs/src/labs/promote.py::apply_proposal` (2026-09-13):
 *   1. Load the proposal (must be pending)
 *   2. Merge after_params over the current active params (weights deep-merged)
 *   3. Challenger mode (default): insert as `shadow` beside the champion —
 *      reflection.efficacy cuts over / retires it; legacy mode retires the
 *      active row and inserts `active`
 *   4. Version = max(existing)+1 (to_version goes stale), record
 *      metrics_window.applied_version/challenger, mark proposal applied
 *
 * Risk caps stay enforced at the strategy layer; this route only touches
 * params (signal weights / thresholds / horizons). It does NOT grant a
 * paper_trade_certificate — live execution remains gated.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

// Mirrors labs/promote.py FORBIDDEN_FIELDS — risk caps and live flags can
// never ride along with a proposal, whoever applies it.
const FORBIDDEN_KEYS = new Set([
  "max_position_pct",
  "daily_loss_circuit_pct",
  "max_concurrent_positions",
  "live_capital_cap_usd",
  "live_execution_enabled",
]);

// Challenger mode (MATRIX_CHALLENGER_MODE, default on — same as labs): the
// applied version runs as a `shadow` beside the champion and only cuts over
// once reflection.efficacy measures it better on realised PnL.
const CHALLENGER_MODE =
  (process.env.MATRIX_CHALLENGER_MODE ?? "true").trim().toLowerCase() !== "false";

function scrubForbidden(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    if (FORBIDDEN_KEYS.has(k)) continue;
    out[k] = v && typeof v === "object" && !Array.isArray(v)
      ? scrubForbidden(v as Record<string, unknown>)
      : v;
  }
  return out;
}

// labs/promote._merge_strategy_params: patch over current params, weights deep-merged.
function mergeParams(
  current: Record<string, unknown>,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...current };
  for (const [k, v] of Object.entries(patch)) {
    if (k === "weights" && v && typeof v === "object" && !Array.isArray(v)) {
      out.weights = { ...((current.weights as Record<string, unknown>) ?? {}), ...(v as Record<string, unknown>) };
    } else {
      out[k] = v;
    }
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

      const patch = scrubForbidden(proposal.after_params ?? {});

      const currentRows = (await tx`
        SELECT params FROM strategy_configs
        WHERE strategy_id = ${proposal.strategy_id}
          AND asset_class = ${proposal.asset_class}
          AND status = 'active'
        ORDER BY version DESC LIMIT 1
      `) as unknown as { params: Record<string, unknown> | null }[];
      const merged = mergeParams(currentRows[0]?.params ?? {}, patch);

      if (CHALLENGER_MODE) {
        const shadowRows = (await tx`
          SELECT version FROM strategy_configs
          WHERE strategy_id = ${proposal.strategy_id}
            AND asset_class = ${proposal.asset_class}
            AND status = 'shadow' LIMIT 1
        `) as unknown as { version: number }[];
        if (shadowRows.length) {
          throw new Error(
            `a challenger (v${shadowRows[0].version}) is already running for this strategy; wait for efficacy to cut over or retire it`,
          );
        }
      } else {
        await tx`
          UPDATE strategy_configs
          SET status = 'retired'
          WHERE strategy_id = ${proposal.strategy_id}
            AND asset_class = ${proposal.asset_class}
            AND status = 'active'
        `;
      }

      // to_version is computed at proposal time and goes stale as other
      // promotions land (uq_strategy_configs_id_class_ver) — compute the real
      // next version here, exactly like labs/promote.apply_proposal.
      const maxRows = (await tx`
        SELECT COALESCE(MAX(version), 0) AS max_v FROM strategy_configs
        WHERE strategy_id = ${proposal.strategy_id}
          AND asset_class = ${proposal.asset_class}
      `) as unknown as { max_v: number }[];
      const newVersion = Math.max(Number(proposal.to_version), Number(maxRows[0]?.max_v ?? 0) + 1);

      const inserted = (await tx`
        INSERT INTO strategy_configs (
          id, strategy_id, asset_class, version, status, params, rationale, promoted_at
        ) VALUES (
          gen_random_uuid(),
          ${proposal.strategy_id}, ${proposal.asset_class}, ${newVersion},
          ${CHALLENGER_MODE ? "shadow" : "active"},
          ${sql.json(merged as never)},
          ${`Applied from proposal ${id} by operator${CHALLENGER_MODE ? " (challenger)" : ""}`},
          NOW()
        )
        RETURNING id, version, status
      `) as unknown as { id: string; version: number; status: string }[];

      await tx`
        UPDATE mutation_proposals
        SET status = 'applied', applied_at = NOW(),
            metrics_window = (COALESCE(metrics_window, '{}'::json)::jsonb
              || ${sql.json({ applied_version: newVersion, challenger: CHALLENGER_MODE, applied_by: "operator" } as never)}::jsonb)::json
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
