import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.keys_to_victory import build_game_breakdown
from app.models import Base, Game, Play, Prediction


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id="g1", home="SEA", away="NE", home_score=13, away_score=10, status="final"):
    game = Game(
        game_id=game_id, season=2026, week=1, game_type="REG", gameday="2026-09-09",
        home_team=home, away_team=away, home_score=home_score, away_score=away_score, status=status,
    )
    db.add(game)
    return game


def _prediction(db, game_id, home_win_prob):
    db.add(
        Prediction(
            game_id=game_id, model_version="test", created_at=dt.datetime.utcnow(),
            home_elo=1500, away_elo=1500, home_win_prob=home_win_prob,
            predicted_home_score=24, predicted_away_score=20, predicted_margin=4, predicted_total=44,
            margin_range_low=-9.5, margin_range_high=17.5, total_range_low=34, total_range_high=54,
        )
    )


def _play(db, game_id, posteam, defteam, play_type, yards_gained=0, down=None, ydstogo=None,
          interception=False, fumble_lost=False, sack=False, play_id=1):
    db.add(
        Play(
            play_key=f"{game_id}_{play_id}", game_id=game_id, season=2026, week=1,
            posteam=posteam, defteam=defteam, play_type=play_type, down=down, ydstogo=ydstogo,
            yards_gained=yards_gained, interception=interception, fumble_lost=fumble_lost, sack=sack,
        )
    )


def test_breakdown_unavailable_when_game_not_final(db):
    game = _game(db, status="scheduled", home_score=None, away_score=None)
    db.commit()

    result = build_game_breakdown(db, game)

    assert result["data_available"] is False
    assert "not final" in result["reason"].lower() or "isn't final" in result["reason"].lower()


def test_breakdown_unavailable_when_plays_not_ingested_yet(db):
    game = _game(db)
    db.commit()

    result = build_game_breakdown(db, game)

    assert result["data_available"] is False
    assert "ingested" in result["reason"].lower()


def test_real_stats_computed_from_plays_not_estimated(db):
    game = _game(db)  # SEA 13, NE 10 -- SEA (home) actually won
    _prediction(db, "g1", home_win_prob=0.65)  # model also favored SEA

    # SEA rushes for 100, NE rushes for 40.
    _play(db, "g1", "SEA", "NE", "run", yards_gained=100, play_id=1)
    _play(db, "g1", "NE", "SEA", "run", yards_gained=40, play_id=2)
    # SEA throws for 150, NE throws for 200 (NE wins passing yards).
    _play(db, "g1", "SEA", "NE", "pass", yards_gained=150, play_id=3)
    _play(db, "g1", "NE", "SEA", "pass", yards_gained=200, play_id=4)
    # NE throws an interception (SEA takeaway) -- SEA wins turnover margin.
    _play(db, "g1", "NE", "SEA", "pass", yards_gained=0, interception=True, play_id=5)
    # 3rd downs: SEA converts 1/1, NE converts 0/1.
    _play(db, "g1", "SEA", "NE", "run", yards_gained=5, down=3, ydstogo=4, play_id=6)
    _play(db, "g1", "NE", "SEA", "run", yards_gained=1, down=3, ydstogo=4, play_id=7)
    db.commit()

    result = build_game_breakdown(db, game)

    assert result["data_available"] is True
    assert result["actual_winner"] == "SEA"
    assert result["correct_winner"] is True
    assert result["home_stats"]["rushing_yards"] == 105  # includes the 3rd-down run below
    assert result["away_stats"]["rushing_yards"] == 41
    assert result["home_stats"]["passing_yards"] == 150
    assert result["away_stats"]["passing_yards"] == 200
    assert result["home_stats"]["turnovers"] == 0
    assert result["away_stats"]["turnovers"] == 1  # NE's interception

    keys_by_key = {k["key"]: k for k in result["keys"]}
    assert keys_by_key["rushing_yards"]["winner"] == "home"
    assert keys_by_key["passing_yards"]["winner"] == "away"
    assert keys_by_key["turnover_margin"]["winner"] == "home"  # SEA forced NE's pick

    # The narrative should credit SEA's real wins and note the passing loss.
    assert "SEA" in result["narrative"]
    assert "rushing" in result["narrative"].lower()


def test_sack_yardage_excluded_from_passing_yards(db):
    """A sack's negative yardage counts against total offense but not
    passing yards in a real box score -- this guards that distinction."""
    game = _game(db)
    _play(db, "g1", "SEA", "NE", "pass", yards_gained=-7, sack=True, play_id=1)
    _play(db, "g1", "SEA", "NE", "pass", yards_gained=20, play_id=2)
    db.commit()

    result = build_game_breakdown(db, game)

    assert result["home_stats"]["passing_yards"] == 20  # sack's -7 excluded


def test_fourth_down_flagged_no_signal_and_excluded_from_narrative(db):
    game = _game(db)
    _prediction(db, "g1", home_win_prob=0.6)
    # Give NE (the loser) the better 4th down rate -- if this leaked into
    # the narrative's reasoning, it would wrongly credit the loser.
    _play(db, "g1", "NE", "SEA", "run", yards_gained=5, down=4, ydstogo=3, play_id=1)
    _play(db, "g1", "SEA", "NE", "run", yards_gained=1, down=4, ydstogo=3, play_id=2)
    db.commit()

    result = build_game_breakdown(db, game)

    fourth = next(k for k in result["keys"] if k["key"] == "fourth_down_pct")
    assert fourth["no_signal"] is True
    assert fourth["winner"] == "away"  # NE converted, SEA didn't
    assert "4th down" not in result["narrative"]


def test_correct_winner_false_when_model_missed_the_call(db):
    game = _game(db, home="SEA", away="NE", home_score=13, away_score=10)
    _prediction(db, "g1", home_win_prob=0.2)  # model favored NE (away); SEA actually won
    _play(db, "g1", "SEA", "NE", "run", yards_gained=10, play_id=1)
    db.commit()

    result = build_game_breakdown(db, game)

    assert result["predicted_winner"] == "NE"
    assert result["actual_winner"] == "SEA"
    assert result["correct_winner"] is False
    assert "wrong" in result["narrative"].lower()
