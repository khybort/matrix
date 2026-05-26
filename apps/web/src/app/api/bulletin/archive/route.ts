/**
 * GET /api/bulletin/archive — index of published issues.
 *
 * Returns slug + title + summary + issue_date (no body) for the index
 * view; the per-issue page fetches the full body on demand.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const rows = await sql<{
      slug: string;
      title: string;
      summary: string | null;
      issue_date: string;
      published_at: string;
    }[]>`
      SELECT slug, title, summary, issue_date, published_at
      FROM bulletin_issues
      WHERE status = 'published'
      ORDER BY issue_date DESC, published_at DESC NULLS LAST
      LIMIT 100
    `;
    return NextResponse.json({ ok: true, issues: rows });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
