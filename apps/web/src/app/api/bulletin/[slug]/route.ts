/**
 * GET /api/bulletin/[slug] — one bulletin issue's full content.
 *
 * Drafts are served too (operator preview); the page wrapper can decide
 * what to render. For purely public flows, gate on status='published'
 * at the page level. Archived issues remain accessible by slug.
 */

import { NextResponse } from "next/server";
import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { slug } = await params;
  try {
    const [row] = await sql<{
      slug: string;
      title: string;
      summary: string | null;
      body_md: string;
      issue_date: string;
      status: string;
      published_at: string | null;
      generated_at: string;
      model: string | null;
    }[]>`
      SELECT slug, title, summary, body_md, issue_date, status,
             published_at, generated_at, model
      FROM bulletin_issues
      WHERE slug = ${slug}
      LIMIT 1
    `;
    if (!row) {
      return NextResponse.json({ ok: false, error: "not found" }, { status: 404 });
    }
    return NextResponse.json({ ok: true, issue: row });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 500 });
  }
}
