import type { ParlaySummary } from "../types";

function formatPrice(price: number | null): string {
  if (price === null) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

interface Props {
  title: string;
  subtitle: string;
  summary: ParlaySummary | null;
  emptyNote?: string;
}

export default function ParlayCard({ title, subtitle, summary, emptyNote }: Props) {
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
          <p className="parlay-caveat">{summary.caveat}</p>
        </>
      )}
    </div>
  );
}
