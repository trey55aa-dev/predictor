import type { ParlaySummary } from "../types";
import type { StakeSettings } from "./StakeControls";
import { calculatePayout, formatMoney } from "../utils/payout";

function formatPrice(price: number | null): string {
  if (price === null) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

interface Props {
  title: string;
  subtitle: string;
  summary: ParlaySummary | null;
  stakeSettings: StakeSettings;
  emptyNote?: string;
}

export default function ParlayCard({ title, subtitle, summary, stakeSettings, emptyNote }: Props) {
  const payout =
    summary && summary.combined_decimal_payout !== null
      ? calculatePayout({
          decimalPayout: summary.combined_decimal_payout,
          stake: stakeSettings.stake,
          boostPct: stakeSettings.boostPct,
          freeBet: stakeSettings.freeBet,
        })
      : null;

  return (
    <div className="parlay-card">
      <h3>{title}</h3>
      <p className="parlay-subtitle">{subtitle}</p>

      {!summary && <p className="empty-note">{emptyNote ?? "Not enough data to build this parlay yet."}</p>}

      {summary && (
        <>
          <ul className="parlay-legs">
            {summary.legs.map((leg) => (
              <li key={leg.game_id}>
                <div className="parlay-leg-row">
                  <span className={`leg-type-badge ${leg.leg_type}`}>
                    {leg.leg_type === "anytime_td" ? "Anytime TD" : "Moneyline"}
                  </span>
                  <strong>{leg.leg_type === "anytime_td" ? leg.player_name : leg.team}</strong>
                  <span className="parlay-leg-opp">
                    {leg.leg_type === "anytime_td" ? `${leg.team} vs ${leg.opponent}` : `vs ${leg.opponent}`}
                  </span>
                  <span className="parlay-leg-price">{formatPrice(leg.american_price)}</span>
                </div>
                <div className="parlay-leg-meta">
                  model {Math.round(leg.model_prob * 100)}%
                  {/* loose != null so a backend that predates this field (mid-deploy) omits
                      the label instead of rendering "Elo NaN%" */}
                  {leg.elo_prob != null && ` · data-only Elo ${Math.round(leg.elo_prob * 100)}%`}
                  {leg.market_prob !== null && ` · market ${Math.round(leg.market_prob * 100)}%`}
                  {leg.edge !== null && ` · edge ${leg.edge >= 0 ? "+" : ""}${(leg.edge * 100).toFixed(1)}pt`}
                </div>
              </li>
            ))}
          </ul>

          <div className="parlay-summary-row">
            <span>Combined probability: {Math.round(summary.combined_probability * 100)}%</span>
            <span>
              Payout:{" "}
              {summary.combined_decimal_payout !== null
                ? `${summary.combined_decimal_payout.toFixed(2)}x`
                : "unavailable (missing odds)"}
            </span>
          </div>

          {payout && (
            <div className="payout-breakdown">
              <div className="payout-headline">
                <span>
                  {stakeSettings.freeBet ? "Free bet" : "Stake"} {formatMoney(stakeSettings.stake)}
                </span>
                <strong>{formatMoney(payout.totalReturn)} back</strong>
              </div>
              <div className="payout-detail">
                Profit {formatMoney(payout.profit)}
                {stakeSettings.boostPct > 0 &&
                  ` (includes ${formatMoney(payout.boostBonus)} from the ${stakeSettings.boostPct}% boost)`}
                {stakeSettings.freeBet && " — stake is kept by the book on a win, so only profit is returned"}
              </div>
              <div className="payout-detail payout-risk">
                At risk: {formatMoney(payout.atRisk)} ·{" "}
                {Math.round(summary.combined_probability * 100)}% chance this hits, so it misses roughly{" "}
                {Math.round((1 - summary.combined_probability) * 100)}% of the time
              </div>
            </div>
          )}

          {summary.combined_decimal_payout === null && (
            <p className="payout-detail">
              No payout can be calculated: at least one leg has no market price. Player props aren't priced by
              the free odds feed, so any parlay containing one can't be costed out.
            </p>
          )}

          <p className="parlay-caveat">{summary.caveat}</p>
        </>
      )}
    </div>
  );
}
