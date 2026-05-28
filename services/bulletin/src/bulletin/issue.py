"""Compose a markdown bulletin issue from a state snapshot.

LLM path uses matrix_shared.call_claude. If unavailable (no env or
import error), falls back to a deterministic template-built issue so the
daemon still ships something on every cadence — silence is worse than
plain prose.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal

from loguru import logger
from matrix_shared.subscription_llm import MODEL_SONNET

from bulletin.state import BulletinSnapshot

LLM_MODEL = MODEL_SONNET


@dataclass(slots=True)
class ComposedIssue:
    slug: str
    title: str
    summary: str
    body_md: str
    model: str | None
    model_cost_usd: Decimal | None


SYSTEM_PROMPT = (
    "You are the editor of a weekly research bulletin for Matrix, an autonomous "
    "trading research engine. The audience is technical traders + curious readers "
    "who want to follow the engine's progress honestly. "
    "Tone: candid, plain, no marketing fluff. Acknowledge losses; don't bury them. "
    "Avoid revealing exact strategy parameters or alpha — talk about behavior, not knobs. "
    "Markdown output. ~400-700 words. Sections: a short headline summary, "
    "what happened this week (performance), what the engine learned (mutations + "
    "cert state), what the news graph saw (top assets + polarity drift), "
    "and a brief 'next' paragraph."
)


def _user_prompt(snap: BulletinSnapshot) -> str:
    return (
        f"Compose this week's bulletin for issue date {snap.issue_date} (UTC).\n"
        f"State JSON (use these facts; do not invent numbers):\n\n```json\n"
        f"{json.dumps(asdict(snap), indent=2)}\n```\n\n"
        f"Output JSON with keys: "
        f'{{"title": "...", "summary": "...", "body_md": "..."}}'
    )


def _fallback_compose(snap: BulletinSnapshot) -> ComposedIssue:
    """Template-built fallback. Less polished, but verifiable + always works."""
    pnl_sign = "+" if Decimal(snap.total_pnl_usd_window) >= 0 else ""
    delta_sign = "+" if Decimal(snap.equity_delta_pct) >= 0 else ""
    title = f"Matrix bulletin · {snap.issue_date}"
    summary = (
        f"{snap.n_trades_window} scored trades over {snap.window_days}d, "
        f"win rate {Decimal(snap.win_rate_window) * 100:.1f}%, "
        f"net {pnl_sign}{snap.total_pnl_usd_window} USD, "
        f"equity Δ {delta_sign}{snap.equity_delta_pct}%."
    )
    lines = [
        f"# {title}",
        "",
        f"_{summary}_",
        "",
        "## What happened this week",
        "",
        f"- Trades scored: **{snap.n_trades_window}** ({snap.n_wins_window} wins, "
        f"win rate `{snap.win_rate_window}`).",
        f"- Net P&L over {snap.window_days}d: **{pnl_sign}{snap.total_pnl_usd_window} USD**.",
        f"- Equity now sits at **{snap.equity_usd} USD** "
        f"({delta_sign}{snap.equity_delta_pct}% from a {snap.starting_capital_usd} starting bet).",
        "",
        "Per strategy:",
        "",
    ]
    if snap.by_strategy:
        for s in snap.by_strategy:
            lines.append(
                f"- `{s['strategy_id']}` — {s['n_trades']} trades, "
                f"win {Decimal(s['win_rate']) * 100:.1f}%, pnl `{s['total_pnl_usd']}` USD."
            )
    else:
        lines.append("- _No strategies booked outcomes this window._")

    lines += ["", "## What the engine learned", ""]
    if snap.new_mutations:
        for m in snap.new_mutations[:5]:
            lines.append(
                f"- `{m['strategy_id']}` v{m['from_v']} → v{m['to_v']} "
                f"({m['type']}, source={m['source']}, status={m['status']})."
            )
    else:
        lines.append("- Reflection didn't propose any mutations this week.")
    if snap.cert_changes:
        lines.append("")
        for c in snap.cert_changes:
            lines.append(
                f"- 🎓 paper_trade_certificate **{c['event']}** "
                f"for `{c['strategy_id']}/{c['asset_class']}/v{c['version']}`."
            )

    lines += ["", "## What the news graph saw", ""]
    if snap.asset_signals:
        for a in snap.asset_signals:
            lines.append(
                f"- `{a['asset']}`: {a['mentions']} mentions, "
                f"polarity direct={a['direct_polarity']}, "
                f"contextual={a['contextual_polarity']}."
            )
    else:
        lines.append("- Graph signals went quiet this window — no fresh aggregates.")

    lines += [
        "",
        "## Next",
        "",
        "The system stays in paper mode until a strategy version earns its "
        "`paper_trade_certificate`. Reflection keeps proposing, labs keeps "
        "evolving, ingestion keeps logging. We'll see what the next 7 days say.",
    ]
    body = "\n".join(lines)
    return ComposedIssue(
        slug=f"weekly-{snap.issue_date}-{uuid.uuid4().hex[:6]}",
        title=title,
        summary=summary,
        body_md=body,
        model=None,
        model_cost_usd=None,
    )


async def compose(snap: BulletinSnapshot) -> ComposedIssue:
    """Try Haiku; on any failure fall back to the deterministic template."""
    try:
        from matrix_shared import call_claude_json, llm_enabled
    except ImportError:
        return _fallback_compose(snap)
    if not llm_enabled():
        logger.info("LLM disabled; falling back to template compose")
        return _fallback_compose(snap)

    try:
        parsed = await call_claude_json(
            system=SYSTEM_PROMPT,
            user=_user_prompt(snap),
            model=LLM_MODEL,
            max_tokens=1500,
            temperature=0.4,
        )
    except Exception as e:
        logger.warning(f"LLM compose failed ({e}); falling back to template")
        return _fallback_compose(snap)
    if not parsed or not isinstance(parsed, dict):
        return _fallback_compose(snap)

    title = str(parsed.get("title", "")).strip() or f"Matrix bulletin · {snap.issue_date}"
    summary = str(parsed.get("summary", "")).strip()
    body_md = str(parsed.get("body_md", "")).strip()
    if not body_md:
        return _fallback_compose(snap)

    return ComposedIssue(
        slug=f"weekly-{snap.issue_date}-{uuid.uuid4().hex[:6]}",
        title=title,
        summary=summary,
        body_md=body_md,
        model=LLM_MODEL,
        model_cost_usd=None,  # call_claude_json doesn't surface cost yet
    )
