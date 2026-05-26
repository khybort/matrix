/**
 * GET /api/bulletin/latest — the most recent published bulletin issue.
 *
 * Returns the full body_md so the page can render markdown client-side.
 * Drafts are NOT served here — they're operator-internal until `--publish`.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [row] = await sql<{
      slug: string;
      title: string;
      summary: string | null;
      body_md: string;
      issue_date: string;
      published_at: string;
    }[]>`
      SELECT slug, title, summary, body_md, issue_date, published_at
      FROM bulletin_issues
      WHERE status = 'published'
      ORDER BY issue_date DESC, published_at DESC NULLS LAST
      LIMIT 1
    `;
    if (!row) {
      return NextResponse.json({ ok: true, issue: null });
    }
    return NextResponse.json({ ok: true, issue: row });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
