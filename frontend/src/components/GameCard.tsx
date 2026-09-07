import { useState } from "react";
import type { GamePrediction } from "../types";
import GamePlanPanel from "./GamePlanPanel";

function pct(p: number | null): string {
  if (p === null) return "—";
  return `${Math.round(p * 100)}%`;
}

function round1(n: number | null): string {
  if (n === null) return "—";
  return n.toFixed(1);
}

function formatSpread(spread: number | null, team: string): string {
  if (spread === null) return "—";
  const sign = spread > 0 ? "+" : "";
  return `${team} ${sign}${spread.toFixed(1)}`;
}

export default function GameCard({ prediction }: { prediction: GamePrediction }) {
  const {
    game_id,
    home_team,
    away_team,
    gameday,
    status,
    home_score,
    away_score,
    home_win_prob,
    elo_win_prob,
    market_win_prob,
    predicted_home_score,
    predicted_away_score,
    predicted_total,
    total_range_low,
    total_range_high,
    predicted_margin,
    margin_range_low,
    margin_range_high,
    market_spread_line,
    market_total_line,
    weather,
    weather_note,
    correct_winner,
    is_upset_alert,
    upset_note,
    over_under_lean,
    over_under_note,
  } = prediction;

  const [showPlan, setShowPlan] = useState(false);

  const homeFavored = (home_win_prob ?? 0.5) >= 0.5;
  const modelSpreadForHome = predicted_margin === null ? null : -predicted_margin;

  return (
    <div className="game-card">
      <div className="game-card-header">
        <span className="matchup">
          {away_team} @ {home_team}
        </span>
        <span className="gameday">{gameday}</span>
      </div>

      {(is_upset_alert || over_under_lean) && (
        <div className="badge-row" title={[upset_note, over_under_note].filter(Boolean).join(" ")}>
          {is_upset_alert && <span className="badge upset-badge">🚨 Upset Alert</span>}
          {over_under_lean && over_under_lean !== "push" && (
            <span className="badge ou-badge">Lean {over_under_lean === "over" ? "Over" : "Under"}</span>
          )}
        </div>
      )}

      {status === "final" ? (
        <div className="final-score">
          Final: {away_team} {away_score} — {home_team} {home_score}
          {correct_winner !== null && (
            <span className={`grade-badge ${correct_winner ? "correct" : "incorrect"}`}>
              {correct_winner ? "✓ model called it" : "✗ model missed"}
            </span>
          )}
        </div>
      ) : (
        <div className="win-prob-row">
          <div className={`team-prob ${homeFavored ? "favored" : ""}`}>
            <span className="team">{home_team}</span>
            <span className="prob">{pct(home_win_prob)}</span>
          </div>
          <div className={`team-prob ${!homeFavored ? "favored" : ""}`}>
            <span className="team">{away_team}</span>
            <span className="prob">{home_win_prob === null ? "—" : pct(1 - home_win_prob)}</span>
          </div>
        </div>
      )}

      <div className="prediction-details">
        <div className="detail-row">
          <span className="label">Predicted score</span>
          <span>
            {home_team} {round1(predicted_home_score)} — {away_team} {round1(predicted_away_score)}
          </span>
        </div>
        <div className="detail-row">
          <span className="label">Total range</span>
          <span>
            {round1(total_range_low)}–{round1(total_range_high)} (model: {round1(predicted_total)}
            {market_total_line !== null ? `, market: ${round1(market_total_line)}` : ""})
          </span>
        </div>
        {elo_win_prob != null && market_win_prob != null && (
          <div className="detail-row">
            <span className="label">Win prob: data-only vs. market</span>
            <span>
              Elo {Math.round(elo_win_prob * 100)}% · market {Math.round(market_win_prob * 100)}% ({home_team})
            </span>
          </div>
        )}
        <div className="detail-row">
          <span className="label">Margin range</span>
          <span>
            {round1(margin_range_low)} to {round1(margin_range_high)} pts ({home_team})
          </span>
        </div>
        <div className="detail-row">
          <span className="label">Spread (model vs. market)</span>
          <span>
            {formatSpread(modelSpreadForHome, home_team)}
            {market_spread_line !== null ? ` vs. ${formatSpread(market_spread_line, home_team)}` : " (no market line)"}
          </span>
        </div>
        {weather?.applicable && (
          <div className="detail-row">
            <span className="label">Weather</span>
            <span>
              {weather.temp_f !== null ? `${Math.round(weather.temp_f)}°F` : ""}
              {weather.wind_mph !== null ? `, wind ${Math.round(weather.wind_mph)} mph` : ""}
            </span>
          </div>
        )}
        {weather_note && <div className="weather-note">⚠️ {weather_note}</div>}
      </div>

      <button className="plan-toggle" onClick={() => setShowPlan((v) => !v)}>
        {showPlan ? "Hide game plan ▲" : "View game plan ▼"}
      </button>
      {showPlan && <GamePlanPanel gameId={game_id} />}
    </div>
  );
}
