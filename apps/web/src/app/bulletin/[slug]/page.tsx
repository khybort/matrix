/**
 * /bulletin/[slug] — individual issue page.
 *
 * Server-rendered for SEO + initial paint speed. Issues remain accessible
 * by slug even if they're archived (status='archived') — only drafts get a
 * 404 for non-operator visitors.
 */

import { marked } from "marked";
import Link from "next/link";
import { notFound } from "next/navigation";

import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type Issue = {
  slug: string;
  title: string;
  summary: string | null;
  body_md: string;
  issue_date: string;
  status: string;
  generated_at: string;
  published_at: string | null;
  model: string | null;
};

async function fetchIssue(slug: string): Promise<Issue | null> {
  const rows = await sql<Issue[]>`
    SELECT slug, title, summary, body_md, issue_date, status,
           published_at, generated_at, model
    FROM bulletin_issues
    WHERE slug = ${slug}
    LIMIT 1
  `;
  return rows[0] ?? null;
}

export default async function BulletinIssuePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const issue = await fetchIssue(slug);
  if (!issue || issue.status === "draft") {
    notFound();
  }

  return (
    <main className="min-h-screen p-6 max-w-3xl mx-auto">
      <header className="mb-6 flex items-baseline justify-between flex-wrap gap-2">
        <Link
          href="/bulletin"
          className="muted text-xs opacity-80 hover:opacity-100"
        >
          ← all issues
        </Link>
        <div className="muted text-xs">
          {new Date(issue.issue_date).toLocaleDateString()}
          {issue.status === "archived" && " · archived"}
        </div>
      </header>

      <article className="panel p-6">
        <div
          className="prose prose-invert max-w-none"
          dangerouslySetInnerHTML={{ __html: marked.parse(issue.body_md) as string }}
        />
      </article>

      <footer className="mt-8 muted text-xs">
        Generated {new Date(issue.generated_at).toLocaleString()}
        {issue.model ? ` · ${issue.model}` : " · template fallback"}
      </footer>
    </main>
  );
}
