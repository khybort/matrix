/**
 * POST /api/proposals/:id/reject — operator declines a pending MutationProposal.
 *
 * Sets status = 'rejected'. The proposal remains for audit; no strategy_config
 * row is touched. Idempotent for non-pending statuses (returns the prior state).
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  if (!id) {
    return NextResponse.json({ ok: false, error: "missing id" }, { status: 400 });
  }

  try {
    const rows = (await sql`
      UPDATE mutation_proposals
      SET status = 'rejected'
      WHERE id = ${id} AND status = 'pending'
      RETURNING id, status
    `) as unknown as { id: string; status: string }[];

    if (!rows.length) {
      const existing = (await sql`
        SELECT id, status FROM mutation_proposals WHERE id = ${id}
      `) as unknown as { id: string; status: string }[];
      if (!existing.length) {
        return NextResponse.json(
          { ok: false, error: "proposal not found" },
          { status: 404 },
        );
      }
      return NextResponse.json({
        ok: true,
        no_op: true,
        current_status: existing[0].status,
      });
    }

    return NextResponse.json({ ok: true, proposal: rows[0] });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
