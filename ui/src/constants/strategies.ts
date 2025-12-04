export type StrategyTag =
  | 'Trend'
  | 'DCA'
  | 'ML-Classifier'
  | 'ML-Regression'
  | 'Mean Reversion'
  | 'Relative Strength'
  | 'Vol Breakout'
  | 'Manual';

export const STRATEGY_TAGS: Record<string, StrategyTag> = {
  trend_core: 'Trend',
  dca_overlay: 'DCA',
  vol_breakout: 'Vol Breakout',
  majors_mean_rev: 'Mean Reversion',
  rs_rotation: 'Relative Strength',
  ai_predictor: 'ML-Classifier',
  ai_predictor_alt: 'ML-Classifier',
  ai_regression: 'ML-Regression',
  manual: 'Manual',
};
