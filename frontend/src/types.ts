export interface Weather {
  applicable: boolean;
  temp_f: number | null;
  wind_mph: number | null;
  precip_mm: number | null;
}

export interface GamePrediction {
  game_id: string;
  season: number;
  week: number;
  gameday: string;
  gametime_utc: string | null;
  home_team: string;
  away_team: string;
  status: string;
  home_score: number | null;
  away_score: number | null;

  home_win_prob: number | null;
  elo_win_prob: number | null;
  market_win_prob: number | null;
  predicted_home_score: number | null;
  predicted_away_score: number | null;
  predicted_margin: number | null;
  predicted_total: number | null;
  margin_range_low: number | null;
  margin_range_high: number | null;
  total_range_low: number | null;
  total_range_high: number | null;
  weather_note: string | null;

  market_spread_line: number | null;
  market_total_line: number | null;
  weather: Weather | null;

  correct_winner: boolean | null;
  model_version: string | null;

  is_upset_alert: boolean;
  upset_note: string | null;
  over_under_lean: string | null;
  over_under_note: string | null;
}

export interface Performance {
  graded_predictions: number;
  winner_accuracy: number | null;
  avg_brier_score: number | null;
  avg_abs_margin_error: number | null;
  avg_abs_total_error: number | null;
}

export interface CalibrationAdjustment {
  parameter_name: string;
  old_value: number;
  new_value: number;
  evidence: string;
  sample_size: number;
  created_at: string;
}

export interface SchemeFamily {
  id: string;
  side: "offense" | "defense";
  name: string;
  era: string;
  description: string;
  core_concepts: string[];
  team_season_count: number;
}

export interface TeamSeason {
  team_abbr: string;
  season: number;
  head_coach: string;
}

export interface PlayConcept {
  label: string;
  count: number;
  share: number;
}

export interface NotablePlay {
  game_id: string;
  season: number;
  week: number;
  posteam: string | null;
  defteam: string | null;
  desc: string | null;
  concept: string;
  explanation: string;
  epa: number | null;
  yards_gained: number | null;
}

export interface SchemeDetail extends SchemeFamily {
  team_seasons: TeamSeason[];
  top_concepts: PlayConcept[];
  notable_plays: NotablePlay[];
  sample_size: number;
}

export interface InjuryEntry {
  player_name: string;
  position: string | null;
  report_status: string;
  primary_injury: string | null;
  is_starter: boolean;
}

export interface DefensiveTendencies {
  sample_size: number;
  man_rate?: number | null;
  zone_rate?: number | null;
  blitz_rate?: number | null;
  pressure_rate?: number | null;
  avg_box_count?: number | null;
}

export interface SchemeMatchupStats {
  sample_size: number;
  avg_epa?: number | null;
  success_rate?: number | null;
}

export interface GamePlan {
  game_id: string;
  season: number;
  week: number;
  home_team: string;
  away_team: string;
  prediction: {
    home_win_prob: number | null;
    predicted_home_score: number | null;
    predicted_away_score: number | null;
  } | null;
  upset_alert: { is_upset_alert: boolean; note: string | null };
  over_under: { lean: string | null; edge: number | null; note: string | null };
  weather_note: string | null;
  injuries: { home: InjuryEntry[]; away: InjuryEntry[] };
  play_styles: {
    home_offense: PlayConcept[];
    away_offense: PlayConcept[];
    home_defense: DefensiveTendencies;
    away_defense: DefensiveTendencies;
  };
  scheme_matchups: {
    home_offense_vs_away_defense: SchemeMatchupStats;
    away_offense_vs_home_defense: SchemeMatchupStats;
  };
}

export interface ParlayLeg {
  leg_type: "game_winner" | "anytime_td";
  game_id: string;
  team: string;
  opponent: string;
  player_id: string | null;
  player_name: string | null;
  model_prob: number;
  /** The data-only Elo call, before the market blend. Null for player props. */
  elo_prob: number | null;
  market_prob: number | null;
  american_price: number | null;
  decimal_odds: number | null;
  edge: number | null;
}

export interface AccuracySource {
  key: "elo_only" | "market_only" | "blend";
  description: string;
  brier_score: number;
  winner_accuracy: number;
}

export interface AccuracyComparison {
  sample_size: number;
  sources: AccuracySource[];
  most_accurate?: "elo_only" | "market_only" | "blend";
}

export interface PlayerProjection {
  player_id: string;
  player_name: string;
  position: string;
  team: string;
  opponent: string;
  projected_rushing_yards: number;
  projected_receiving_yards: number;
  projected_passing_yards: number;
  rushing_td_prob: number;
  receiving_td_prob: number;
  passing_td_prob: number;
  anytime_td_prob: number;
  involvement: number;
  sample_size: number;
}

export interface PlayerProjectionsResponse {
  home: PlayerProjection[];
  away: PlayerProjection[];
}

export interface SimPropStat {
  mean_yards: number;
  p10: number;
  p90: number;
  td_probability: number;
}

export interface SimPlayerProp {
  player_id: string;
  player_name: string;
  team: string;
  /** Null when this player has no validated rushing role -- see SimPlayerProjection's
   * backend docstring. Not every player has both. */
  rushing: SimPropStat | null;
  receiving: SimPropStat | null;
  anytime_td_probability: number;
}

export interface SimPlayerPropsResponse {
  game_id: string;
  n_sims: number | null;
  home: SimPlayerProp[];
  away: SimPlayerProp[];
}

export interface ParlaySummary {
  legs: ParlayLeg[];
  combined_probability: number;
  combined_decimal_payout: number | null;
  caveat: string;
}

export interface ParlaysResponse {
  season: number;
  week: number;
  requested_legs: number;
  safest: ParlaySummary | null;
  best_money_move: ParlaySummary | null;
  best_money_move_note?: string;
}
