/** Stake -> payout math for a parlay, including the two promo types
 * sportsbooks actually offer.
 *
 * Decimal payout (what the API returns as combined_decimal_payout) is the
 * total return on a $1 stake *including* the stake itself, so a $1 stake at
 * 4.91x returns $4.91, of which $3.91 is profit.
 */

export const STAKE_PRESETS = [1, 2, 3, 5, 10, 20];

export interface PayoutOptions {
  /** Total return per $1 staked, including the stake. */
  decimalPayout: number;
  stake: number;
  /** Profit boost as a percentage, e.g. 50 for a +50% boost. 0 = no boost. */
  boostPct?: number;
  /** A free bet's stake is not returned on a win -- you receive profit only. */
  freeBet?: boolean;
}

export interface PayoutBreakdown {
  /** Profit before any boost is applied. */
  baseProfit: number;
  /** Extra profit contributed by the boost alone (0 when no boost). */
  boostBonus: number;
  /** Profit after the boost. */
  profit: number;
  /** Cash actually returned to you if every leg hits. */
  totalReturn: number;
  /** Your own money at risk (0 for a free bet). */
  atRisk: number;
}

export function calculatePayout({
  decimalPayout,
  stake,
  boostPct = 0,
  freeBet = false,
}: PayoutOptions): PayoutBreakdown {
  const baseProfit = stake * (decimalPayout - 1);

  // Boosts apply to profit, never to the stake -- a 50% boost on a $10 bet
  // returning $39.10 profit pays $58.65 profit, not $58.65 + a boosted stake.
  const profit = baseProfit * (1 + boostPct / 100);
  const boostBonus = profit - baseProfit;

  // On a winning free bet the book keeps the stake and pays only the profit,
  // which is why free bets favour longer odds far more strongly than cash:
  // the stake you "lose" on a win is a fixed cost regardless of the price.
  const totalReturn = freeBet ? profit : stake + profit;

  return {
    baseProfit,
    boostBonus,
    profit,
    totalReturn,
    atRisk: freeBet ? 0 : stake,
  };
}

export function formatMoney(value: number): string {
  return `$${value.toFixed(2)}`;
}
