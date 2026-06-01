"""Codebase index for dev_agent prompt context (Phase 1 text retrieval).

Populates `dev_codebase_nodes` with path metadata + short summaries.
Retrieval is ILIKE on path/summary (embeddings reserved for Phase 1+).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

INDEX_ROOTS = (
    "services",
    "packages",
    "apps",
    "scripts",
    "infra",
    "docs",
)
SKIP_DIR_NAMES = frozenset({
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "worktrees",
    ".next",
    "dist",
    "build",
})
SKIP_EXTENSIONS = frozenset({
    ".pyc",
    ".pyo",
    ".so",
    ".dylib",
    ".dll",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".lock",
})
MAX_FILE_BYTES = 64_000
MAX_SUMMARY_CHARS = 400


def _language(path: Path) -> str | None:
    ext = path.suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".md": "markdown",
        ".sql": "sql",
        ".sh": "shell",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".json": "json",
    }.get(ext)


def _summarize_file(path: Path) -> tuple[str, int]:
    try:
        raw = path.read_bytes()[:MAX_FILE_BYTES]
    except OSError:
        return "", 0
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", 0
    lines = text.splitlines()
    loc = len(lines)
    head = "\n".join(lines[:20]).strip()
    if len(head) > MAX_SUMMARY_CHARS:
        head = head[: MAX_SUMMARY_CHARS - 3] + "..."
    return head, loc


def _iter_files(repo_root: Path):
    for root_name in INDEX_ROOTS:
        base = repo_root / root_name
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            rel_dir = Path(dirpath).relative_to(repo_root).as_posix()
            yield rel_dir + "/", "dir", None, None, None, None
            for name in filenames:
                if name.startswith("."):
                    continue
                fp = Path(dirpath) / name
                if fp.suffix.lower() in SKIP_EXTENSIONS:
                    continue
                rel = fp.relative_to(repo_root).as_posix()
                summary, loc = _summarize_file(fp)
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=UTC)
                yield rel, "file", _language(fp), loc, mtime, summary


async def index_codebase(pool: asyncpg.Pool, repo_root: Path) -> int:
    """Walk repo and upsert dev_codebase_nodes. Returns rows touched."""
    repo_root = repo_root.resolve()
    count = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            for path, kind, language, loc, mtime, summary in _iter_files(repo_root):
                await conn.execute(
                    """
                    INSERT INTO dev_codebase_nodes
                      (path, kind, language, loc, last_modified, summary, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                    ON CONFLICT (path) DO UPDATE SET
                      kind = EXCLUDED.kind,
                      language = EXCLUDED.language,
                      loc = EXCLUDED.loc,
                      last_modified = EXCLUDED.last_modified,
                      summary = EXCLUDED.summary,
                      updated_at = NOW()
                    """,
                    path,
                    kind,
                    language,
                    loc,
                    mtime,
                    summary,
                )
                count += 1
    return count


async def search_codebase_context(
    pool: asyncpg.Pool,
    *,
    query: str,
    paths: list[str] | None = None,
    top_k: int = 10,
) -> str:
    """Format top matching codebase nodes for the system prompt."""
    paths = paths or []
    tokens = [t.strip() for t in query.replace("/", " ").split() if len(t.strip()) >= 3][:8]
    if not tokens and paths:
        tokens = [Path(p).name for p in paths if p][:5]
    if not tokens:
        tokens = ["main"]

    pattern = "%" + "%".join(tokens[:3]) + "%"
    rows = await pool.fetch(
        """
        SELECT path, kind, language, loc, summary,
               (CASE WHEN path = ANY($2::text[]) THEN 2
                     WHEN EXISTS (
                       SELECT 1 FROM unnest($2::text[]) AS hint(prefix)
                       WHERE dev_codebase_nodes.path LIKE hint.prefix || '%'
                     ) THEN 1
                     ELSE 0 END) AS path_boost
        FROM dev_codebase_nodes
        WHERE kind = 'file'
          AND (path ILIKE $1 OR COALESCE(summary, '') ILIKE $1)
        ORDER BY path_boost DESC, loc DESC NULLS LAST, path
        LIMIT $3
        """,
        pattern,
        paths,
        top_k,
    )
    if not rows:
        return ""

    lines = ["Relevant indexed files (path | lang | loc | excerpt):"]
    for r in rows:
        excerpt = (r["summary"] or "").replace("\n", " ")[:160]
        lines.append(
            f"- {r['path']} ({r['language'] or '?'}, {r['loc'] or 0} loc): {excerpt}"
        )
    return "\n".join(lines)
