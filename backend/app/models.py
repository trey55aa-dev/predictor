import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Stadium(Base):
    __tablename__ = "stadiums"

    stadium_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    roof_type: Mapped[str] = mapped_column(String)  # outdoor | dome | retractable

    teams: Mapped[list["Team"]] = relationship(back_populates="stadium")


class Team(Base):
    __tablename__ = "teams"

    team_abbr: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    conference: Mapped[str | None] = mapped_column(String, nullable=True)
    division: Mapped[str | None] = mapped_column(String, nullable=True)
    stadium_id: Mapped[str | None] = mapped_column(ForeignKey("stadiums.stadium_id"), nullable=True)

    stadium: Mapped[Stadium | None] = relationship(back_populates="teams")


class Game(Base):
    __tablename__ = "games"

    game_id: Mapped[str] = mapped_column(String, primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    game_type: Mapped[str] = mapped_column(String)
    gameday: Mapped[str] = mapped_column(String)
    gametime_utc: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    home_team: Mapped[str] = mapped_column(ForeignKey("teams.team_abbr"))
    away_team: Mapped[str] = mapped_column(ForeignKey("teams.team_abbr"))
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stadium_id: Mapped[str | None] = mapped_column(ForeignKey("stadiums.stadium_id"), nullable=True)
    status: Mapped[str] = mapped_column(String, default="scheduled")  # scheduled | final
    home_coach: Mapped[str | None] = mapped_column(String, nullable=True)
    away_coach: Mapped[str | None] = mapped_column(String, nullable=True)

    # Historical closing lines, as embedded by nflverse (only populated for past games).
    close_spread_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_total_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_home_moneyline: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_away_moneyline: Mapped[float | None] = mapped_column(Float, nullable=True)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime)
    bookmaker: Mapped[str] = mapped_column(String)
    home_moneyline: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_moneyline: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_line: Mapped[float | None] = mapped_column(Float, nullable=True)  # home team spread
    total_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String, default="the-odds-api")


class WeatherSnapshot(Base):
    __tablename__ = "weather_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime)
    forecast_for_utc: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    temp_f: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_mph: Mapped[float | None] = mapped_column(Float, nullable=True)
    precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    applicable: Mapped[bool] = mapped_column(Boolean, default=True)


class TeamRating(Base):
    __tablename__ = "team_ratings"
    __table_args__ = (UniqueConstraint("team_abbr", "season", "week", name="uq_team_season_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_abbr: Mapped[str] = mapped_column(ForeignKey("teams.team_abbr"))
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    elo: Mapped[float] = mapped_column(Float)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    model_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    odds_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("odds_snapshots.id"), nullable=True)
    weather_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("weather_snapshots.id"), nullable=True)

    home_elo: Mapped[float] = mapped_column(Float)
    away_elo: Mapped[float] = mapped_column(Float)
    home_win_prob: Mapped[float] = mapped_column(Float)
    # Raw, unblended components -- kept so calibration can retroactively ask
    # "would a different blend weight have scored better," not just the
    # final blended number.
    elo_win_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_win_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    predicted_home_score: Mapped[float] = mapped_column(Float)
    predicted_away_score: Mapped[float] = mapped_column(Float)
    predicted_margin: Mapped[float] = mapped_column(Float)  # home - away
    predicted_total: Mapped[float] = mapped_column(Float)

    margin_range_low: Mapped[float] = mapped_column(Float)
    margin_range_high: Mapped[float] = mapped_column(Float)
    total_range_low: Mapped[float] = mapped_column(Float)
    total_range_high: Mapped[float] = mapped_column(Float)

    weather_note: Mapped[str | None] = mapped_column(String, nullable=True)

    # Filled in later by grade.py once the game is final.
    actual_home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correct_winner: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    margin_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier_component: Mapped[float | None] = mapped_column(Float, nullable=True)
    graded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Injury(Base):
    __tablename__ = "injuries"
    __table_args__ = (UniqueConstraint("season", "week", "gsis_id", name="uq_injury_player_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    team_abbr: Mapped[str] = mapped_column(String)
    gsis_id: Mapped[str] = mapped_column(String)
    player_name: Mapped[str] = mapped_column(String)
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    report_status: Mapped[str] = mapped_column(String)  # Out | Doubtful | Questionable
    practice_status: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_injury: Mapped[str | None] = mapped_column(String, nullable=True)
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime)


class SchemeFamily(Base):
    __tablename__ = "scheme_families"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    side: Mapped[str] = mapped_column(String)  # offense | defense
    name: Mapped[str] = mapped_column(String)
    era: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    core_concepts: Mapped[str] = mapped_column(String)  # JSON-encoded list[str]


class SchemeCoach(Base):
    __tablename__ = "scheme_coaches"
    __table_args__ = (UniqueConstraint("scheme_family_id", "coach_name", name="uq_scheme_coach"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scheme_family_id: Mapped[str] = mapped_column(ForeignKey("scheme_families.id"))
    coach_name: Mapped[str] = mapped_column(String)


class TeamSeasonScheme(Base):
    __tablename__ = "team_season_schemes"
    __table_args__ = (UniqueConstraint("team_abbr", "season", name="uq_team_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_abbr: Mapped[str] = mapped_column(ForeignKey("teams.team_abbr"))
    season: Mapped[int] = mapped_column(Integer)
    head_coach: Mapped[str] = mapped_column(String)
    offense_scheme_id: Mapped[str] = mapped_column(ForeignKey("scheme_families.id"))
    defense_scheme_id: Mapped[str] = mapped_column(ForeignKey("scheme_families.id"))


class Play(Base):
    __tablename__ = "plays"
    __table_args__ = (
        Index("ix_plays_offense_scheme_season", "offense_scheme_id", "season"),
        Index("ix_plays_defense_scheme_season", "defense_scheme_id", "season"),
        Index("ix_plays_scoring_play", "scoring_play"),
    )

    play_key: Mapped[str] = mapped_column(String, primary_key=True)  # f"{game_id}_{play_id}"
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    posteam: Mapped[str | None] = mapped_column(String, nullable=True)
    defteam: Mapped[str | None] = mapped_column(String, nullable=True)
    play_type: Mapped[str | None] = mapped_column(String, nullable=True)
    down: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ydstogo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yardline_100: Mapped[int | None] = mapped_column(Integer, nullable=True)
    desc: Mapped[str | None] = mapped_column(String, nullable=True)

    yards_gained: Mapped[float | None] = mapped_column(Float, nullable=True)
    epa: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    touchdown: Mapped[bool] = mapped_column(Boolean, default=False)
    scoring_play: Mapped[bool] = mapped_column(Boolean, default=False)
    scoring_type: Mapped[str | None] = mapped_column(String, nullable=True)  # rush_td | pass_td | field_goal
    interception: Mapped[bool] = mapped_column(Boolean, default=False)
    fumble_lost: Mapped[bool] = mapped_column(Boolean, default=False)
    sack: Mapped[bool] = mapped_column(Boolean, default=False)

    shotgun: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    no_huddle: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    run_location: Mapped[str | None] = mapped_column(String, nullable=True)
    run_gap: Mapped[str | None] = mapped_column(String, nullable=True)
    pass_length: Mapped[str | None] = mapped_column(String, nullable=True)
    pass_location: Mapped[str | None] = mapped_column(String, nullable=True)

    offense_formation: Mapped[str | None] = mapped_column(String, nullable=True)
    personnel_group: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "11", "12", "21"
    defenders_in_box: Mapped[float | None] = mapped_column(Float, nullable=True)
    was_pressure: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    man_zone: Mapped[str | None] = mapped_column(String, nullable=True)  # MAN_COVERAGE | ZONE_COVERAGE
    coverage_type: Mapped[str | None] = mapped_column(String, nullable=True)  # COVER_0..COVER_9 etc.
    time_to_throw: Mapped[float | None] = mapped_column(Float, nullable=True)

    is_play_action: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_screen_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_rpo: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_motion: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    n_blitzers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_pass_rushers: Mapped[int | None] = mapped_column(Integer, nullable=True)

    offense_scheme_id: Mapped[str | None] = mapped_column(ForeignKey("scheme_families.id"), nullable=True)
    defense_scheme_id: Mapped[str | None] = mapped_column(ForeignKey("scheme_families.id"), nullable=True)


class ParlayPick(Base):
    __tablename__ = "parlay_picks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    parlay_type: Mapped[str] = mapped_column(String)  # safest | best_money_move
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    combined_probability: Mapped[float] = mapped_column(Float)
    combined_decimal_payout: Mapped[float | None] = mapped_column(Float, nullable=True)
    graded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    all_legs_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    legs: Mapped[list["ParlayPickLeg"]] = relationship(back_populates="parlay_pick")


class ParlayPickLeg(Base):
    __tablename__ = "parlay_pick_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parlay_pick_id: Mapped[int] = mapped_column(ForeignKey("parlay_picks.id"))
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    leg_type: Mapped[str] = mapped_column(String, default="game_winner")  # game_winner | anytime_td
    team: Mapped[str] = mapped_column(String)
    player_id: Mapped[str | None] = mapped_column(String, nullable=True)
    player_name: Mapped[str | None] = mapped_column(String, nullable=True)
    model_prob: Mapped[float] = mapped_column(Float)
    market_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    american_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    parlay_pick: Mapped[ParlayPick] = relationship(back_populates="legs")


class PlayerGameStat(Base):
    __tablename__ = "player_game_stats"
    __table_args__ = (UniqueConstraint("player_id", "season", "week", name="uq_player_season_week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String)  # gsis_id
    player_name: Mapped[str] = mapped_column(String)
    position: Mapped[str] = mapped_column(String)
    team: Mapped[str] = mapped_column(String)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    game_id: Mapped[str | None] = mapped_column(String, nullable=True)

    carries: Mapped[float | None] = mapped_column(Float, nullable=True)
    rushing_yards: Mapped[float | None] = mapped_column(Float, nullable=True)
    rushing_tds: Mapped[float | None] = mapped_column(Float, nullable=True)
    targets: Mapped[float | None] = mapped_column(Float, nullable=True)
    receptions: Mapped[float | None] = mapped_column(Float, nullable=True)
    receiving_yards: Mapped[float | None] = mapped_column(Float, nullable=True)
    receiving_tds: Mapped[float | None] = mapped_column(Float, nullable=True)
    pass_attempts: Mapped[float | None] = mapped_column(Float, nullable=True)
    passing_yards: Mapped[float | None] = mapped_column(Float, nullable=True)
    passing_tds: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    air_yards_share: Mapped[float | None] = mapped_column(Float, nullable=True)


class PlayerProjection(Base):
    __tablename__ = "player_projections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"))
    player_id: Mapped[str] = mapped_column(String)
    player_name: Mapped[str] = mapped_column(String)
    position: Mapped[str] = mapped_column(String)
    team: Mapped[str] = mapped_column(String)
    opponent: Mapped[str] = mapped_column(String)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)

    projected_rushing_yards: Mapped[float] = mapped_column(Float)
    projected_receiving_yards: Mapped[float] = mapped_column(Float)
    projected_passing_yards: Mapped[float] = mapped_column(Float)
    rushing_td_prob: Mapped[float] = mapped_column(Float)
    receiving_td_prob: Mapped[float] = mapped_column(Float)
    passing_td_prob: Mapped[float] = mapped_column(Float)
    anytime_td_prob: Mapped[float] = mapped_column(Float)

    actual_rushing_yards: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_receiving_yards: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_passing_yards: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_tds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graded_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class CalibrationAdjustment(Base):
    """Audit log of every time a model constant was auto-tuned -- also the
    source of truth for its *current* value (latest row per parameter_name);
    config.py's constant is only the pre-any-tuning default."""

    __tablename__ = "calibration_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parameter_name: Mapped[str] = mapped_column(String)
    old_value: Mapped[float] = mapped_column(Float)
    new_value: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str] = mapped_column(String)
    sample_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
