import type { RiskPresetName } from '../services/api';

export type RiskPresetMeta = {
  label: string;
  tagline: string;
  maxDailyDrawdownPct: number;
  maxPortfolioRiskPct: number;
  perStrategyHighlights: string;
};

export const RISK_PRESET_META: Record<RiskPresetName, RiskPresetMeta> = {
  conservative: {
    label: 'Conservative',
    tagline: 'Capital preservation first.',
    maxDailyDrawdownPct: 5,
    maxPortfolioRiskPct: 5,
    perStrategyHighlights: 'Trend 25%, DCA 10%, AI 5%, others 5–10%.',
  },
  balanced: {
    label: 'Balanced',
    tagline: 'Growth with guardrails.',
    maxDailyDrawdownPct: 10,
    maxPortfolioRiskPct: 10,
    perStrategyHighlights: 'Trend 40%, DCA 20%, AI 10%, others 10–20%.',
  },
  aggressive: {
    label: 'Aggressive',
    tagline: 'Chasing upside with tighter stops.',
    maxDailyDrawdownPct: 15,
    maxPortfolioRiskPct: 15,
    perStrategyHighlights: 'Higher caps across trend, AI, breakout, rotation.',
  },
  degen: {
    label: 'Degen',
    tagline: 'Max throttle. Know what you’re doing.',
    maxDailyDrawdownPct: 25,
    maxPortfolioRiskPct: 25,
    perStrategyHighlights: 'High caps on AI, breakout, rotation. Very spicy.',
  },
};

export function formatPresetSummary(id: RiskPresetName): string {
  const meta = RISK_PRESET_META[id];
  return `${meta.label}: Max DD ${meta.maxDailyDrawdownPct}%, portfolio risk ${meta.maxPortfolioRiskPct}%. ${meta.perStrategyHighlights}`;
}
