import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.model.parlays import build_parlays, grade_parlays, log_parlays
from app.models import (
    Base,
    Game,
    OddsSnapshot,
    ParlayPick,
    ParlayPickLeg,
    PlayerGameStat,
    PlayerProjection,
    Prediction,
    Team,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def db_with_fk_enforcement():
    """SQLite doesn't enforce foreign keys unless told to; production
    Postgres always does. Real bug this reproduces: a bulk .delete() on
    ParlayPick doesn't cascade to ParlayPickLeg (no ORM cascade fires on a
    bulk delete, and the FK itself has no ON DELETE CASCADE), so re-running
    log_parlays while the prior pick was still ungraded raised a live
    ForeignKeyViolation in production -- invisible on a plain sqlite test
    engine until FK enforcement is switched on here to match."""
    engine = create_engine("sqlite:///:memory:")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id, home, away, season=2024, week=1, home_score=None, away_score=None):
    game = Game(
        game_id=game_id,
        season=season,
        week=week,
        game_type="REG",
        gameday="2024-09-08",
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        status="final" if home_score is not None else "scheduled",
    )
    db.add(game)
    return game


def _prediction(db, game_id, home_win_prob, odds_snapshot_id=None):
    pred = Prediction(
        game_id=game_id,
        model_version="test",
        created_at=dt.datetime.utcnow(),
        odds_snapshot_id=odds_snapshot_id,
        home_elo=1500,
        away_elo=1500,
        home_win_prob=home_win_prob,
        predicted_home_score=24,
        predicted_away_score=20,
        predicted_margin=4,
        predicted_total=44,
        margin_range_low=-9.5,
        margin_range_high=17.5,
        total_range_low=34,
        total_range_high=54,
    )
    db.add(pred)
    return pred


def _odds(db, game_id, home_ml, away_ml):
    odds = OddsSnapshot(
        game_id=game_id,
        fetched_at=dt.datetime.utcnow(),
        bookmaker="consensus",
        home_moneyline=home_ml,
        away_moneyline=away_ml,
    )
    db.add(odds)
    db.flush()
    return odds


def test_safest_picks_highest_model_probability(db):
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.85)
    _game(db, "g2", "SF", "SEA")
    _prediction(db, "g2", 0.55)
    db.commit()

    result = build_parlays(db, 2024, 1, legs=2)
    assert result["safest"] is not None
    teams = [leg["team"] for leg in result["safest"]["legs"]]
    assert teams[0] == "KC"  # highest model prob picked first


def test_no_odds_means_no_best_money_move(db):
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.8)
    db.commit()

    result = build_parlays(db, 2024, 1, legs=1)
    assert result["best_money_move"] is None
    assert "best_money_move_note" in result


def test_best_money_move_requires_positive_edge(db):
    _game(db, "g1", "KC", "DEN")
    odds = _odds(db, "g1", -110, -110)  # market ~50/50 each way after vig removal
    _prediction(db, "g1", 0.65, odds_snapshot_id=odds.id)  # model likes KC more than market does
    db.commit()

    result = build_parlays(db, 2024, 1, legs=1)
    assert result["best_money_move"] is not None
    leg = result["best_money_move"]["legs"][0]
    assert leg["team"] == "KC"
    assert leg["edge"] > 0


def test_combined_probability_and_payout(db):
    _game(db, "g1", "KC", "DEN")
    odds1 = _odds(db, "g1", -200, 170)
    _prediction(db, "g1", 0.7, odds_snapshot_id=odds1.id)
    db.commit()

    result = build_parlays(db, 2024, 1, legs=1)
    safest = result["safest"]
    assert abs(safest["combined_probability"] - 0.7) < 1e-9
    assert safest["combined_decimal_payout"] is not None
    assert "caveat" in safest


def test_fewer_legs_than_requested_notes_it(db):
    _game(db, "g1", "KC", "DEN")
    odds1 = _odds(db, "g1", -200, 170)
    _prediction(db, "g1", 0.7, odds_snapshot_id=odds1.id)
    db.commit()

    result = build_parlays(db, 2024, 1, legs=3)
    assert result["best_money_move"] is not None
    assert len(result["best_money_move"]["legs"]) == 1
    assert "best_money_move_note" in result


def _player_projection(db, game_id, player_id, player_name, team, opponent, anytime_td_prob, season=2024, week=1):
    db.add(
        PlayerProjection(
            game_id=game_id,
            player_id=player_id,
            player_name=player_name,
            position="RB",
            team=team,
            opponent=opponent,
            season=season,
            week=week,
            model_version="test",
            created_at=dt.datetime.utcnow(),
            projected_rushing_yards=50,
            projected_receiving_yards=10,
            projected_passing_yards=0,
            rushing_td_prob=anytime_td_prob,
            receiving_td_prob=0.0,
            passing_td_prob=0.0,
            anytime_td_prob=anytime_td_prob,
        )
    )


def test_anytime_td_leg_can_win_safest_slot(db):
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.55)  # mediocre game-winner confidence
    _player_projection(db, "g1", "p1", "Star Back", "KC", "DEN", anytime_td_prob=0.85)  # much safer
    db.commit()

    result = build_parlays(db, 2024, 1, legs=1)
    leg = result["safest"]["legs"][0]
    assert leg["leg_type"] == "anytime_td"
    assert leg["player_name"] == "Star Back"


def test_one_leg_per_game_enforced(db):
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.9)
    _player_projection(db, "g1", "p1", "Star Back", "KC", "DEN", anytime_td_prob=0.99)
    _game(db, "g2", "SF", "SEA")
    _prediction(db, "g2", 0.6)
    db.commit()

    result = build_parlays(db, 2024, 1, legs=2)
    game_ids = [leg["game_id"] for leg in result["safest"]["legs"]]
    assert len(game_ids) == len(set(game_ids))  # no duplicate game_id
    # g1's TD leg (0.99) should beat g1's own game-winner leg (0.9) for that game's single slot
    assert any(leg["game_id"] == "g1" and leg["leg_type"] == "anytime_td" for leg in result["safest"]["legs"])


def test_grade_anytime_td_leg_hit(db):
    _game(db, "g1", "KC", "DEN", home_score=27, away_score=20)  # game_winner style already covers final score path
    _prediction(db, "g1", 0.55)
    _player_projection(db, "g1", "p1", "Star Back", "KC", "DEN", anytime_td_prob=0.9)
    db.add(
        PlayerGameStat(
            player_id="p1", player_name="Star Back", position="RB", team="KC",
            season=2024, week=1, game_id="g1", rushing_tds=1, receiving_tds=0, passing_tds=0,
        )
    )
    db.commit()

    log_parlays(db, 2024, 1, legs=1)
    grade_parlays(db, 2024, 1)

    pick = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").first()
    assert pick.all_legs_hit is True


def test_grade_anytime_td_leg_miss_when_no_stat_line(db):
    _game(db, "g1", "KC", "DEN", home_score=27, away_score=20)
    _prediction(db, "g1", 0.55)
    _player_projection(db, "g1", "p1", "Star Back", "KC", "DEN", anytime_td_prob=0.9)
    db.commit()  # no PlayerGameStat row for p1 -- didn't record a stat line

    log_parlays(db, 2024, 1, legs=1)
    grade_parlays(db, 2024, 1)

    pick = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").first()
    assert pick.all_legs_hit is False


def test_log_parlays_replaces_an_already_logged_ungraded_pick_without_crashing(db_with_fk_enforcement):
    """The bug this guards against: production has real foreign-key
    enforcement (SQLite doesn't, by default), so deleting a ParlayPick row
    while its ParlayPickLeg children still reference it raised
    IntegrityError/ForeignKeyViolation the second time log_parlays ran for
    the same still-ungraded week -- confirmed live in the GitHub Actions
    cloud routine."""
    db = db_with_fk_enforcement
    db.add(Team(team_abbr="KC", name="Kansas City Chiefs"))
    db.add(Team(team_abbr="DEN", name="Denver Broncos"))
    db.flush()  # teams are seeded well before any game in the real pipeline; match that here
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.7)
    db.commit()

    log_parlays(db, 2024, 1, legs=1)
    first_pick_id = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").first().id

    # Re-running before the first pick is ever graded is the exact
    # production scenario (a routine run earlier the same day already
    # logged this week's pick; nothing has kicked off/finished yet).
    log_parlays(db, 2024, 1, legs=1)

    picks = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").all()
    assert len(picks) == 1  # replaced, not duplicated
    assert db.query(ParlayPickLeg).filter(ParlayPickLeg.parlay_pick_id == first_pick_id).count() > 0
    assert db.query(ParlayPickLeg).count() == len(picks[0].legs)  # no orphaned legs left behind
