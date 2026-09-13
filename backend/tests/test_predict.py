import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.elo import expected_win_prob
from app.model.predict import predict_game, predict_week
from app.models import Base, Game, Prediction, Stadium, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_stadiums_and_teams(db):
    db.add(Stadium(stadium_id="LAX01", name="SoFi Stadium", roof_type="dome"))
    db.add(Stadium(stadium_id="SFO01", name="Levi's Stadium", roof_type="outdoor"))
    db.add(Stadium(stadium_id="MEL00", name="Melbourne Cricket Ground", roof_type="outdoor"))
    db.add(Team(team_abbr="LA", name="Rams", stadium_id="LAX01"))
    db.add(Team(team_abbr="SF", name="49ers", stadium_id="SFO01"))
    db.commit()


def test_predict_game_applies_home_field_advantage_for_a_real_home_game(db):
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="g_home", season=2026, week=2, game_type="REG", gameday="2026-09-17",
        home_team="LA", away_team="SF", stadium_id="LAX01",
    )
    db.add(game)
    db.commit()

    prediction = predict_game(db, game)

    expected = expected_win_prob(1500 + settings.elo_home_field_advantage, 1500)
    assert prediction.elo_win_prob == pytest.approx(expected)


def test_predict_game_withholds_home_field_advantage_for_a_neutral_site_game(db):
    """Reproduces the 2026_01_SF_LA game played at the Melbourne Cricket
    Ground: the Rams (LA) were still the schedule's nominal "home" team but
    the game was a neutral site -- LA got no real crowd advantage, and per
    real-world reporting LA actually had the worse end of the travel (a
    ~28-hour turnaround vs. SF arriving a week early). Elo should score this
    as a pick'em matchup between equal ratings, not hand LA the usual +60
    home-field Elo bump on top of an already-disadvantageous trip."""
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="2026_01_SF_LA", season=2026, week=1, game_type="REG", gameday="2026-09-05",
        home_team="LA", away_team="SF", stadium_id="MEL00",
    )
    db.add(game)
    db.commit()

    prediction = predict_game(db, game)

    assert prediction.elo_win_prob == pytest.approx(expected_win_prob(1500, 1500))
    assert prediction.elo_win_prob == pytest.approx(0.5)


def test_predict_week_does_not_regenerate_prediction_for_an_already_final_game(db):
    """Reproduces a real drift found in the 2026-09-13 automated review: the
    same week stays "current" (per schedule_context.current_or_next_week)
    from its first kickoff until its last game finishes, so run-routine's
    predict_week(season, week) call keeps re-running for the whole week --
    including games that already went final earlier that same week. Elo
    ratings get rebuilt (via build_ratings) after each grading pass, so a
    later predict_week call for an already-decided game uses ratings that
    have already absorbed that exact game's own result: a hindsight-leaked
    second Prediction row for a game that should have exactly one, logged
    before it was known. Confirmed live: 2026_01_SF_LA's and
    2026_01_NE_SEA's predicted_home_win_prob in review/model_state.json
    changed between the 2026-09-11 and 2026-09-13 automated-review runs with
    no recalibration event to explain it -- both games were already final in
    the earlier run."""
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="g1", season=2026, week=1, game_type="REG", gameday="2026-09-10",
        home_team="LA", away_team="SF", stadium_id="LAX01", status="scheduled",
    )
    db.add(game)
    db.commit()

    # First run-routine pass: the game hasn't been played yet, so it gets its
    # one real, locked-in pre-game prediction.
    predict_week(db, 2026, 1)
    assert db.query(Prediction).filter(Prediction.game_id == "g1").count() == 1

    # The game finishes; ingest_schedules (which runs earlier in the same
    # run-routine pass, see app/ingestion/schedules.py) flips it to final.
    game.home_score = 27
    game.away_score = 7
    game.status = "final"
    db.commit()

    # Week 1 is still "current" (its other games haven't kicked off yet), so
    # run-routine's next pass calls predict_week(season, 1) again. It must
    # not silently add a second, hindsight-informed prediction for a game
    # that's already been decided.
    predict_week(db, 2026, 1)
    predictions = db.query(Prediction).filter(Prediction.game_id == "g1").all()
    assert len(predictions) == 1
