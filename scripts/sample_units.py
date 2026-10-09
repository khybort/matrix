"""Sample-unit footer shared by the study reports.

Every study counts episodes, not rows (matrix_shared.edge_study.episode_groups):
a strategy that re-emits the same (symbol, side) inside its horizon has made one
bet. The formatted tables print `n`; this footer puts the raw row count and the
signals that could not be scored beside it, so a reader can see how much of a
strategy's volume was repetition and how much was unscorable (bar holes).
"""
from __future__ import annotations


def units_table(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = [f"\nsample units (n = independent episodes; n_raw = signal rows; unscorable = no clean bars)",
             f"{'strategy':<24}{'mkt':<7}{'n':>7}{'n_raw':>8}{'unscorable':>12}{'raw/ep':>8}"]
    for r in rows:
        n, raw = r.get("n") or 0, r.get("n_raw")
        unsc = r.get("n_unscorable")
        ratio = f"{raw / n:.1f}" if raw and n else "-"
        lines.append(f"{r.get('strategy', '?'):<24}{r.get('market', '?'):<7}{n:>7}"
                     f"{raw if raw is not None else '-':>8}{unsc if unsc is not None else '-':>12}{ratio:>8}")
    return "\n".join(lines)
