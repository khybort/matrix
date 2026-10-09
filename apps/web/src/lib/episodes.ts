// One bet, one sample — the dashboard's copy of the episode rule.
//
// The single definition is matrix_shared.edge_study.episode_groups (Python);
// this file mirrors it because the web tier cannot import it. Keep the two in
// step: a signal opens a new episode only when no earlier episode of the same
// (strategy, market, symbol, side) is still inside the horizon of the signal
// that opened it; otherwise it joins that episode. A strategy that re-emits
// the same call every tick and is filled again has made one bet, so n and the
// win rate are counted per episode, with the raw fill count beside it.

export type FillRow = {
  strategy_id: string;
  asset_class: string;
  symbol: string;
  side: string;
  generated_at: string | Date;
  horizon_seconds: number | null;
  pnl_usd: string | number | null;
  score?: string | number | null;
  reason?: string | null;
};

export function episodeGroups<T extends FillRow>(rows: T[]): T[][] {
  const sorted = [...rows].sort(
    (a, b) => new Date(a.generated_at).getTime() - new Date(b.generated_at).getTime(),
  );
  const openUntil = new Map<string, number>();
  const current = new Map<string, T[]>();
  const out: T[][] = [];
  for (const r of sorted) {
    const key = `${r.strategy_id}|${r.asset_class}|${r.symbol}|${r.side}`;
    const at = new Date(r.generated_at).getTime();
    const until = openUntil.get(key);
    if (until !== undefined && at < until) {
      current.get(key)!.push(r);
      continue;
    }
    openUntil.set(key, at + (Number(r.horizon_seconds) || 600) * 1000);
    const g = [r];
    current.set(key, g);
    out.push(g);
  }
  return out;
}

export type EpisodeSummary = {
  n: number; // episodes
  n_raw: number; // scorable fills
  n_unscorable: number; // orphan flat-closes, not evidence
  wins: number; // episodes whose summed pnl_usd > 0
  win_rate: number | null;
  total_pnl_usd: number;
  avg_score: number | null; // mean over episodes of the episode's mean score
};

export function summarizeEpisodes(rows: FillRow[]): EpisodeSummary {
  const scorable = rows.filter((r) => r.reason !== "orphan_flat_close");
  const groups = episodeGroups(scorable);
  const pnls = groups.map((g) => g.reduce((s, r) => s + Number(r.pnl_usd ?? 0), 0));
  const scores = groups.map((g) => g.reduce((s, r) => s + Number(r.score ?? 0), 0) / g.length);
  const n = groups.length;
  const wins = pnls.filter((p) => p > 0).length;
  return {
    n,
    n_raw: scorable.length,
    n_unscorable: rows.length - scorable.length,
    wins,
    win_rate: n ? wins / n : null,
    total_pnl_usd: pnls.reduce((s, p) => s + p, 0),
    avg_score: n ? scores.reduce((s, x) => s + x, 0) / n : null,
  };
}
