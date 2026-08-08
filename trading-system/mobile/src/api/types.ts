export type Direction = "long" | "short";

export interface RiskDecisionSummary {
  accepted: boolean;
  reason: string;
}

export interface SignalDTO {
  id: string;
  instrument: string;
  strategy_id: string;
  strategy_version: number;
  direction: Direction;
  confidence_score: number;
  confluences: string[];
  entry: number;
  stop_loss: number;
  take_profit: number;
  created_at: string;
  risk_decision: RiskDecisionSummary | null;
}

export interface OrderDTO {
  id: string;
  instrument: string;
  direction: Direction;
  size: number;
  filled_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  status: string;
  created_at: string;
}

export interface TradeJournalDTO {
  id: string;
  instrument: string;
  strategy_id: string;
  strategy_version: number;
  direction: Direction;
  confluences: string[];
  size: number;
  entry_price: number;
  exit_price: number | null;
  r_multiple: number | null;
  pnl: number | null;
  outcome: "win" | "loss" | "breakeven" | null;
  classification: string | null;
  opened_at: string;
  closed_at: string | null;
}

export interface StrategyDTO {
  strategy_id: string;
  version: number;
  asset_class: string;
  is_paused: boolean;
  size_multiplier: number;
  consecutive_losses: number;
  backtest_expectancy_r: number | null;
  backtest_win_rate: number | null;
}

export interface StrategyPerformanceDTO {
  sample_size: number;
  win_rate?: number;
  expectancy_r?: number;
  avg_r?: number;
  max_drawdown_r?: number;
}

export interface KillSwitchStateDTO {
  engaged: boolean;
  reason: string | null;
  triggered_by: string | null;
  engaged_at: string | null;
}
