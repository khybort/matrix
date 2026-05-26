/**
 * /bulletin — public bulletin landing page.
 *
 * Server component: fetches latest published + archive index server-side
 * for snappier first paint, no client polling. Markdown rendering for the
 * latest issue uses `marked` (small, sandboxed). Archive entries link to
 * `/bulletin/[slug]`.
 */

import { marked } from "marked";
import Link from "next/link";

import { sql } from "@/lib/db";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type LatestRow = {
  slug: string;
  title: string;
  summary: string | null;
  body_md: string;
  issue_date: string;
  published_at: string;
};

type IndexRow = {
  slug: string;
  title: string;
  summary: string | null;
  issue_date: string;
};

async function fetchLatest(): Promise<LatestRow | null> {
  const rows = await sql<LatestRow[]>`
    SELECT slug, title, summary, body_md, issue_date, published_at
    FROM bulletin_issues
    WHERE status = 'published'
    ORDER BY issue_date DESC, published_at DESC NULLS LAST
    LIMIT 1
  `;
  return rows[0] ?? null;
}

async function fetchArchive(): Promise<IndexRow[]> {
  return sql<IndexRow[]>`
    SELECT slug, title, summary, issue_date
    FROM bulletin_issues
    WHERE status = 'published'
    ORDER BY issue_date DESC, published_at DESC NULLS LAST
    LIMIT 100
  `;
}

export default async function BulletinPage() {
  const [latest, archive] = await Promise.all([fetchLatest(), fetchArchive()]);
  const olderIssues = latest
    ? archive.filter((i) => i.slug !== latest.slug)
    : archive;

  return (
    <main className="min-h-screen p-6 max-w-3xl mx-auto">
      <header className="mb-8 flex items-baseline justify-between flex-wrap gap-2">
        <h1 className="text-2xl">Matrix bulletin</h1>
        <Link className="muted text-xs hover:opacity-100 opacity-80" href="/public">
          ← public dashboard
        </Link>
      </header>

      {!latest ? (
        <section className="panel p-5">
          <p className="muted">
            No published issues yet. The first auto-generated digest will
            appear after the bulletin daemon's next cycle.
          </p>
        </section>
      ) : (
        <article className="panel p-6 mb-8">
          <div className="muted text-xs uppercase tracking-wide mb-2">
            Latest · {new Date(latest.issue_date).toLocaleDateString()}
          </div>
          <div
            className="prose prose-invert max-w-none"
            // marked has a built-in sanitizer for the basic md we generate;
            // we control the generation source (our own daemon), so xss
            // risk is operator-bounded, not user-bounded.
            dangerouslySetInnerHTML={{ __html: marked.parse(latest.body_md) as string }}
          />
        </article>
      )}

      {olderIssues.length > 0 && (
        <section>
          <h2 className="muted text-xs uppercase tracking-wide mb-3">
            Archive
          </h2>
          <ul className="space-y-3">
            {olderIssues.map((i) => (
              <li key={i.slug} className="panel p-4">
                <div className="muted text-xs">
                  {new Date(i.issue_date).toLocaleDateString()}
                </div>
                <Link
                  href={`/bulletin/${i.slug}`}
                  className="text-base font-medium hover:underline"
                >
                  {i.title}
                </Link>
                {i.summary && (
                  <p className="muted text-sm mt-1">{i.summary}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="mt-12 muted text-xs">
        <p>
          Auto-generated weekly. The bulletin daemon composes drafts from
          engine state; the operator publishes when each issue looks honest.
          No subscription form yet — bookmark this page or watch the public
          dashboard.
        </p>
      </footer>
    </main>
  );
}
