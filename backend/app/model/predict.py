"""Orchestrates the Elo + market + weather pipeline into a stored Prediction row."""

import datetime as dt

from sqlalchemy.orm import Session

from app.config import settings
from app.model.coaching_change import coaching_change_uncertainty
from app.model.efficiency_stats import efficiency_adjustment
from app.model.elo import expected_win_prob, latest_rating
from app.model.injury_signal import injury_adjustment
from app.model.market import blend_line, blend_win_prob, market_home_win_prob
from app.model.pass_defense_stats import pass_defense_adjustment
from app.model.qb_elo import qb_elo_adjustment
from app.model.recalibration import get_tuned_value
from app.model.recent_form import recent_form_adjustment
from app.model.red_zone_stats import red_zone_adjustment
from app.model.scoring import ELO_POINTS_PER_ELO, matchup_expected_total
from app.model.stat_rankings import stat_ranking_adjustment
from app.model.team_home_field_advantage import team_hfa_adjustment
from app.model.venue import is_true_home_game
from app.model.weather_adjust import total_points_adjustment
from app.models import Game, OddsSnapshot, Prediction, WeatherSnapshot


def _latest_odds(db: Session, game_id: str) -> OddsSnapshot | None:
    return (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.game_id == game_id)
        .order_by(OddsSnapshot.fetched_at.desc())
        .first()
    )


def _latest_weather(db: Session, game_id: str) -> WeatherSnapshot | None:
    return (
        db.query(WeatherSnapshot)
        .filter(WeatherSnapshot.game_id == game_id)
        .order_by(WeatherSnapshot.fetched_at.desc())
        .first()
    )


def predict_game(db: Session, game: Game) -> Prediction:
    home_elo = latest_rating(db, game.home_team, game.season, game.week)
    away_elo = latest_rating(db, game.away_team, game.season, game.week)

    true_home_game = is_true_home_game(db, game)
    home_field_bonus = settings.elo_home_field_advantage if true_home_game else 0.0
    home_elo_adj = home_elo + home_field_bonus
    elo_home_prob = expected_win_prob(home_elo_adj, away_elo)
    elo_margin = (home_elo_adj - away_elo) / ELO_POINTS_PER_ELO

    odds = _latest_odds(db, game.game_id)
    weather = _latest_weather(db, game.game_id)

    market_prob = None
    market_spread = None  # home spread, negative = home favored
    market_total = None
    if odds is not None and odds.home_moneyline is not None and odds.away_moneyline is not None:
        market_prob = market_home_win_prob(odds.home_moneyline, odds.away_moneyline)
        market_spread = odds.spread_line
        market_total = odds.total_line

    tuned_blend_weight = get_tuned_value(db, "market_blend_weight", settings.market_blend_weight)
    home_win_prob = blend_win_prob(elo_home_prob, market_prob, weight=tuned_blend_weight)
    # margin_blend_weight/total_blend_weight are tuned separately from
    # market_blend_weight above: that weight is grid-searched purely for
    # win-probability Brier score, which isn't necessarily the weight that
    # best minimizes margin/total point error -- a distinct optimization
    # target. See model/recalibration.py.
    tuned_margin_blend_weight = get_tuned_value(db, "margin_blend_weight", settings.margin_blend_weight)
    tuned_total_blend_weight = get_tuned_value(db, "total_blend_weight", settings.total_blend_weight)

    # Small, capped nudge from each team's most recent game's keys-to-victory
    # record (turnover margin, rushing/passing yards, 3rd-down%) -- a signal
    # distinct from Elo's own margin-of-victory update (which only sees who
    # won and by how much, not how). See model/recent_form.py.
    home_form_delta, _home_form_note = recent_form_adjustment(db, game.home_team, game.season, game.week)
    away_form_delta, _away_form_note = recent_form_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped nudge from each team's season-to-date rank against the
    # rest of the league across the same tracked stat categories (rushing/
    # passing yards, turnover margin, 3rd-down%, points scored/allowed) --
    # distinct from home_form_delta above, which only looks at one prior
    # game. See model/stat_rankings.py.
    home_rank_delta, _home_rank_note = stat_ranking_adjustment(db, game.home_team, game.season, game.week)
    away_rank_delta, _away_rank_note = stat_ranking_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped downgrade from each team's own current-week starter
    # injury report -- previously injuries only reached the live model
    # indirectly through the market line, never directly. See
    # model/injury_signal.py.
    home_injury_delta, _home_injury_note = injury_adjustment(db, game.home_team, game.season, game.week)
    away_injury_delta, _away_injury_note = injury_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped nudge from each team's season-to-date EPA-per-dropback,
    # EPA-per-rush, EPA-per-target, and pressure-rate-allowed splits, ranked
    # against the rest of the league -- distinct from home_rank_delta above
    # (box-score yardage/3rd-down%) since EPA and pressure rate capture play
    # efficiency and pass protection that raw yardage totals can miss. See
    # model/efficiency_stats.py.
    home_efficiency_delta, _home_efficiency_note = efficiency_adjustment(db, game.home_team, game.season, game.week)
    away_efficiency_delta, _away_efficiency_note = efficiency_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped nudge from each team's own pass defense's season-to-date
    # rank on catch rate/yards-per-target/TD-rate/passer-rating allowed --
    # distinct from home_efficiency_delta above, which only looks at a
    # team's own offense. See model/pass_defense_stats.py.
    home_pass_d_delta, _home_pass_d_note = pass_defense_adjustment(db, game.home_team, game.season, game.week)
    away_pass_d_delta, _away_pass_d_note = pass_defense_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped nudge from each team's season-to-date red-zone TD rate
    # (offense) and red-zone TD rate allowed (defense), ranked against the
    # rest of the league. See model/red_zone_stats.py. (Man/zone coverage
    # rate was also requested alongside this, but it's informational only
    # -- see model/man_zone_stats.py's docstring for why it isn't wired in
    # here: nflverse's participation feed doesn't cover the in-progress
    # season at all yet, so it would be a guaranteed 0.0 no-op right now.)
    home_rz_delta, _home_rz_note = red_zone_adjustment(db, game.home_team, game.season, game.week)
    away_rz_delta, _away_rz_note = red_zone_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped nudge from the gap between each team's CURRENT starting
    # QB and its own season-long primary starter (by career EPA/dropback)
    # -- inspired by nfelo's QB-adjusted Elo. See model/qb_elo.py.
    home_qb_delta, _home_qb_note = qb_elo_adjustment(db, game.home_team, game.season, game.week)
    away_qb_delta, _away_qb_note = qb_elo_adjustment(db, game.away_team, game.season, game.week)

    # Small, capped nudge from the HOME team's own historical deviation
    # from the league-average home-field boost already baked into
    # home_field_bonus above -- inspired by nfelo's HFA Tracker. Home-side
    # only (there's no equivalent "away-field" concept to subtract), and
    # gated on true_home_game the same way home_field_bonus itself is: a
    # neutral-site game gets no home boost of any kind. See
    # model/team_home_field_advantage.py for why this is a layered
    # adjustment rather than a change to Elo's own home_field_advantage
    # constant.
    home_hfa_delta, _home_hfa_note = (
        team_hfa_adjustment(db, game.home_team, game.season, game.week) if true_home_game else (0.0, None)
    )

    home_win_prob = min(
        max(
            home_win_prob
            + home_form_delta - away_form_delta
            + home_rank_delta - away_rank_delta
            + home_injury_delta - away_injury_delta
            + home_efficiency_delta - away_efficiency_delta
            + home_pass_d_delta - away_pass_d_delta
            + home_rz_delta - away_rz_delta
            + home_qb_delta - away_qb_delta
            + home_hfa_delta,
            0.02,
        ),
        0.98,
    )

    market_margin = -market_spread if market_spread is not None else None  # spread is from home's perspective (negative = favored)
    predicted_margin = blend_line(elo_margin, market_margin, weight=tuned_margin_blend_weight)

    scoring_expected_total = matchup_expected_total(db, game.home_team, game.away_team, game.season, game.week)
    predicted_total = blend_line(scoring_expected_total, market_total, weight=tuned_total_blend_weight)

    weather_adjustment, weather_note = total_points_adjustment(weather)
    predicted_total = max(predicted_total - weather_adjustment, 20.0)

    tuned_margin_std = get_tuned_value(db, "margin_std_default", settings.margin_std_default)
    tuned_total_std = get_tuned_value(db, "total_std_default", settings.total_std_default)

    # A recently-logged coaching/play-calling change (see model/coaching_change.py)
    # widens the confidence range rather than moving the point estimate or
    # home_win_prob -- there's no honest evidence for which direction a
    # change tilts a team, only that it makes them less predictable than
    # usual until the new staff has a real track record.
    home_coaching_widen, _home_coaching_note = coaching_change_uncertainty(db, game.home_team, game.season, game.week)
    away_coaching_widen, _away_coaching_note = coaching_change_uncertainty(db, game.away_team, game.season, game.week)
    coaching_widen = max(home_coaching_widen, away_coaching_widen)

    predicted_home_score = (predicted_total + predicted_margin) / 2
    predicted_away_score = (predicted_total - predicted_margin) / 2

    prediction = Prediction(
        game_id=game.game_id,
        model_version=settings.model_version,
        created_at=dt.datetime.utcnow(),
        odds_snapshot_id=odds.id if odds else None,
        weather_snapshot_id=weather.id if weather else None,
        home_elo=home_elo,
        away_elo=away_elo,
        home_win_prob=home_win_prob,
        elo_win_prob=elo_home_prob,
        market_win_prob=market_prob,
        predicted_home_score=predicted_home_score,
        predicted_away_score=predicted_away_score,
        predicted_margin=predicted_margin,
        predicted_total=predicted_total,
        margin_range_low=predicted_margin - tuned_margin_std - coaching_widen,
        margin_range_high=predicted_margin + tuned_margin_std + coaching_widen,
        total_range_low=predicted_total - tuned_total_std - coaching_widen,
        total_range_high=predicted_total + tuned_total_std + coaching_widen,
        weather_note=weather_note,
    )
    db.add(prediction)
    return prediction


def predict_week(db: Session, season: int, week: int) -> list[Prediction]:
    # Excludes games already final: a week stays "current" (see
    # schedule_context.current_or_next_week) from its first kickoff until its
    # last game finishes, so run-routine calls this repeatedly across the
    # whole week. Re-predicting an already-decided game here would use Elo
    # ratings that build_ratings() already rebuilt from that exact game's own
    # result (a later run-routine step), silently overwriting its one real,
    # locked-in pre-game prediction with a hindsight-leaked one.
    games = (
        db.query(Game)
        .filter(Game.season == season, Game.week == week, Game.game_type == "REG", Game.status != "final")
        .all()
    )
    predictions = [predict_game(db, game) for game in games]
    db.commit()
    return predictions
