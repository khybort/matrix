/**
 * Pre-configured strategy templates.
 *
 * Each template is a starting point — params can be tuned by the AI
 * Suggester (`make suggest`) before deploy. Risk labels mirror
 * `backtest.suggester.RISK_WEIGHTS` so an operator picks intent rather
 * than poking at individual numbers.
 *
 * Templates target the existing crypto strategy modules:
 *   matrix_agent   — LLM blender; weights + signal_threshold
 *   grid           — bar-based grid bot
 *   dca            — recurring buyer
 *
 * The selectable params are exactly what the strategy module reads at
 * runtime; tightening or broadening this list requires touching the
 * module + a new template version, not just a label change.
 */

export type RiskLabel = "conservative" | "balanced" | "aggressive";

export type StrategyTemplate = {
  id: string;
  strategy_id: string;
  asset_class: "crypto" | "bist";
  label: string;
  short_description: string;
  risk: RiskLabel;
  params: Record<string, unknown>;
};

export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  {
    id: "matrix-agent-conservative-v1",
    strategy_id: "matrix_agent",
    asset_class: "crypto",
    label: "matrix_agent — conservative blend",
    short_description:
      "Heavy oi_delta + funding weights, news dampened, signal_threshold tightened. Reflection's preferred shape after the first auto-mutation.",
    risk: "conservative",
    params: {
      weights: {
        trade_flow: "0.30",
        funding: "0.25",
        oi_delta: "0.25",
        ob_imbalance: "0.15",
        news: "0.05",
      },
      signal_threshold: "0.25",
    },
  },
  {
    id: "matrix-agent-balanced-v1",
    strategy_id: "matrix_agent",
    asset_class: "crypto",
    label: "matrix_agent — balanced blend",
    short_description:
      "Even weights, moderate threshold. Closest to the seed config that ran for the first 24h of paper-trade history.",
    risk: "balanced",
    params: {
      weights: {
        trade_flow: "0.35",
        funding: "0.20",
        oi_delta: "0.20",
        ob_imbalance: "0.15",
        news: "0.10",
      },
      signal_threshold: "0.18",
    },
  },
  {
    id: "grid-conservative-v1",
    strategy_id: "grid",
    asset_class: "crypto",
    label: "grid — tight band, slow horizon",
    short_description:
      "n_grids=10 inside a ±1% band, 10-minute horizon. Few signals, modest expected pnl per trade.",
    risk: "conservative",
    params: {
      n_grids: 10,
      price_band_pct: "0.01",
      horizon_s: 600,
    },
  },
  {
    id: "grid-aggressive-v1",
    strategy_id: "grid",
    asset_class: "crypto",
    label: "grid — wide band, fast horizon",
    short_description:
      "n_grids=20 inside a ±5% band, 2-minute horizon. More fires, more slippage exposure.",
    risk: "aggressive",
    params: {
      n_grids: 20,
      price_band_pct: "0.05",
      horizon_s: 120,
    },
  },
  {
    id: "dca-default-v1",
    strategy_id: "dca",
    asset_class: "crypto",
    label: "dca — hourly accumulator",
    short_description:
      "Long-only fixed-cadence buys. Doesn't read market state beyond price; pairs well with a satellite signal strategy.",
    risk: "balanced",
    params: {
      interval_minutes: 60,
      symbols: ["BTCUSDT", "ETHUSDT"],
    },
  },
];
