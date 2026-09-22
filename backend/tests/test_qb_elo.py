import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.qb_elo import qb_elo_adjustment
from app.models import Base, Play, Stadium, Team, TeamRosterMembership


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_teams(db, *abbrs):
    for abbr in abbrs:
        db.add(Stadium(stadium_id=f"{abbr}01", name=f"{abbr} stadium", roof_type="outdoor"))
        db.add(Team(team_abbr=abbr, name=abbr, stadium_id=f"{abbr}01"))
    db.commit()


def _seed_qb_roster(db, team, *player_ids):
    for player_id in player_ids:
        db.add(
            TeamRosterMembership(
                season=2026, team=team, player_id=player_id, player_name=player_id, position="QB"
            )
        )
    db.commit()


def _dropback(db, game_id, week, passer_id, epa, play_id):
    db.add(
        Play(
            play_key=f"{game_id}_{play_id}", game_id=game_id, season=2026, week=week,
            posteam="SEA", defteam="NE", play_type="pass", epa=epa, passer_player_id=passer_id,
        )
    )


def test_no_adjustment_when_no_season_data_yet(db):
    _seed_teams(db, "SEA", "NE")
    delta, note = qb_elo_adjustment(db, "SEA", 2026, 1)
    assert delta == 0.0
    assert note is None


def test_no_adjustment_when_current_starter_is_the_primary_starter(db):
    _seed_teams(db, "SEA", "NE")
    _seed_qb_roster(db, "SEA", "QB1")
    for week in range(1, 4):
        for i in range(20):  # well past qb_elo_min_attempts across weeks
            _dropback(db, f"g{week}", week, "QB1", epa=0.1, play_id=i)
    db.commit()

    delta, note = qb_elo_adjustment(db, "SEA", 2026, 4)

    assert delta == 0.0
    assert note is None


def test_no_adjustment_when_backup_has_too_few_career_attempts(db):
    _seed_teams(db, "SEA", "NE")
    _seed_qb_roster(db, "SEA", "QB1", "QB2")
    for i in range(60):
        _dropback(db, "g1", 1, "QB1", epa=0.1, play_id=i)
    # QB2 takes over week 2 but has thrown almost nothing -- not enough to trust.
    for i in range(3):
        _dropback(db, "g2", 2, "QB2", epa=0.5, play_id=60 + i)
    db.commit()

    delta, note = qb_elo_adjustment(db, "SEA", 2026, 3)

    assert delta == 0.0
    assert note is None


def test_backup_much_worse_than_starter_produces_a_negative_capped_delta(db):
    _seed_teams(db, "SEA", "NE")
    _seed_qb_roster(db, "SEA", "QB1", "QB2")
    play_id = 0
    for i in range(65):
        _dropback(db, "g1", 1, "QB1", epa=0.3, play_id=play_id)  # elite starter, more attempts (stays "primary")
        play_id += 1
    for i in range(55):
        _dropback(db, "g2", 2, "QB2", epa=-0.3, play_id=play_id)  # much worse backup, now playing
        play_id += 1
    db.commit()

    delta, note = qb_elo_adjustment(db, "SEA", 2026, 3)

    assert delta == pytest.approx(-settings.qb_elo_max_adjustment)
    assert "SEA" in note


def test_backup_much_better_than_starter_produces_a_positive_capped_delta(db):
    _seed_teams(db, "SEA", "NE")
    _seed_qb_roster(db, "SEA", "QB1", "QB2")
    play_id = 0
    for i in range(65):
        _dropback(db, "g1", 1, "QB1", epa=-0.3, play_id=play_id)  # weak starter, more attempts (stays "primary")
        play_id += 1
    for i in range(55):
        _dropback(db, "g2", 2, "QB2", epa=0.3, play_id=play_id)  # much better backup, now playing
        play_id += 1
    db.commit()

    delta, note = qb_elo_adjustment(db, "SEA", 2026, 3)

    assert delta == pytest.approx(settings.qb_elo_max_adjustment)
