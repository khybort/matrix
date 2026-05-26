/**
 * POST /api/strategy/preview — proxy to services/backtest-api.
 *
 * Body: { strategy, symbol, asset_class?, days?, params? }
 * Returns the BacktestResult JSON (positions count only, full list omitted
 * by default to keep the UI snappy).
 *
 * The backtest-api container is reachable inside the compose network at
 * http://backtest-api:8010. From a browser this URL is unreachable; the
 * proxy here is what makes the dashboard preview work without exposing
 * the Python service to the host.
 */

import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const BACKTEST_API_URL =
  process.env.BACKTEST_API_URL ?? "http://backtest-api:8010";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "invalid json body" },
      { status: 400 },
    );
  }

  try {
    const upstream = await fetch(`${BACKTEST_API_URL}/backtest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      // Backtests can take a few seconds for 7-day windows; give them
      // headroom but cap so a stuck upstream doesn't pile up requests.
      signal: AbortSignal.timeout(30000),
    });
    const text = await upstream.text();
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      return NextResponse.json(
        { ok: false, error: "backtest-api returned non-JSON", body: text.slice(0, 500) },
        { status: 502 },
      );
    }
    if (!upstream.ok) {
      return NextResponse.json(
        { ok: false, error: (parsed as { detail?: string }).detail ?? "backtest-api error", status: upstream.status },
        { status: upstream.status },
      );
    }
    return NextResponse.json({ ok: true, result: parsed });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json({ ok: false, error: msg }, { status: 502 });
  }
}
