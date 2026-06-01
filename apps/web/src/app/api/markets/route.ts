// /api/markets — registered market adapters + live stats per market.
//
// Phase G surface. The registry itself is mirrored here statically (two
// markets, two rows — anything more dynamic would need a Python-side
// JSON endpoint we don't have yet). The interesting work is the live
// stats join: universe size, recent prediction count, recent paper
// position count, latest equity per market.

import { NextResponse } from "next/server";
import { sql, sqlLocal } from "@/lib/db";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type MarketDef = {
  name: string;
  assetClass: string;
  sessionDescription: string;
  allowsShort: boolean;
  settlementDays: number;
  liveExecutor: "wired" | "phase1-stub";
};

// Mirrors matrix_shared.markets.{crypto,bist} — kept short on purpose;
// when a third market lands the per-market description here gets a
// matching row.
const MARKETS: MarketDef[] = [
  {
    name: "crypto",
    assetClass: "crypto",
    sessionDescription: "24/7",
    allowsShort: true,
    settlementDays: 0,
    liveExecutor: "wired",
  },
  {
    name: "bist",
    assetClass: "bist",
    sessionDescription: "Mon-Fri 10:00-18:00 Europe/Istanbul",
    allowsShort: false,
    settlementDays: 2,
    liveExecutor: "phase1-stub",
  },
];

export async function GET() {
  try {
    const out = await Promise.all(
      MARKETS.map(async (m) => {
        const [predStats] = await sql`
          SELECT
            COUNT(*) FILTER (WHERE status = 'open')   AS predictions_open,
            COUNT(*) FILTER (WHERE generated_at > NOW() - INTERVAL '24 hours')
              AS predictions_24h
          FROM predictions WHERE asset_class = ${m.assetClass}
        `;
        const [posStats] = await sql`
          SELECT
            COUNT(*) FILTER (WHERE status = 'open')   AS positions_open,
            COUNT(*) FILTER (WHERE status = 'closed') AS positions_closed,
            COALESCE(
              SUM(pnl_usd) FILTER (WHERE status = 'closed'),
              0
            )::numeric(18,4) AS realized_pnl_usd
          FROM paper_positions WHERE asset_class = ${m.assetClass}
        `;
        const [walletStats] = await sql`
          SELECT
            COALESCE(SUM(cash_usd), 0)::numeric(18,4)   AS cash_usd,
            COALESCE(SUM(locked_usd), 0)::numeric(18,4) AS locked_usd,
            COUNT(*)                                    AS wallet_count
          FROM wallets WHERE asset_class = ${m.assetClass}
        `;
        let universeSize = 0;
        if (m.assetClass === "bist") {
          const [r] = await sqlLocal`
            SELECT COUNT(*) AS n FROM bist_symbols WHERE active = TRUE
          `;
          universeSize = Number(r?.n ?? 0);
        } else if (m.assetClass === "crypto") {
          // crypto universe is env-driven on the Python side; the DB
          // proxy here is distinct symbols seen in market_trades.
          const [r] = await sqlLocal`
            SELECT COUNT(DISTINCT symbol) AS n
            FROM market_trades
            WHERE trade_ts > NOW() - INTERVAL '6 hours'
          `;
          universeSize = Number(r?.n ?? 0);
        }
        return {
          ...m,
          universeSize,
          predictionsOpen: Number(predStats?.predictions_open ?? 0),
          predictions24h: Number(predStats?.predictions_24h ?? 0),
          positionsOpen: Number(posStats?.positions_open ?? 0),
          positionsClosed: Number(posStats?.positions_closed ?? 0),
          realizedPnlUsd: Number(posStats?.realized_pnl_usd ?? 0),
          walletCashUsd: Number(walletStats?.cash_usd ?? 0),
          walletLockedUsd: Number(walletStats?.locked_usd ?? 0),
          walletCount: Number(walletStats?.wallet_count ?? 0),
        };
      }),
    );
    return NextResponse.json({ markets: out });
  } catch (e) {
    console.error("/api/markets failed:", e);
    return NextResponse.json(
      { error: (e as Error).message, markets: [] },
      { status: 500 },
    );
  }
}
