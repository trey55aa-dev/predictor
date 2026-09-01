import datetime as dt

from pydantic import BaseModel


class TeamOut(BaseModel):
    team_abbr: str
    name: str

    model_config = {"from_attributes": True}


class WeatherOut(BaseModel):
    applicable: bool
    temp_f: float | None = None
    wind_mph: float | None = None
    precip_mm: float | None = None

    model_config = {"from_attributes": True}


class OddsOut(BaseModel):
    home_moneyline: float | None = None
    away_moneyline: float | None = None
    spread_line: float | None = None
    total_line: float | None = None

    model_config = {"from_attributes": True}


class GamePredictionOut(BaseModel):
    game_id: str
    season: int
    week: int
    gameday: str
    gametime_utc: dt.datetime | None
    home_team: str
    away_team: str
    status: str
    home_score: int | None
    away_score: int | None

    home_win_prob: float | None = None
    predicted_home_score: float | None = None
    predicted_away_score: float | None = None
    predicted_margin: float | None = None
    predicted_total: float | None = None
    margin_range_low: float | None = None
    margin_range_high: float | None = None
    total_range_low: float | None = None
    total_range_high: float | None = None
    weather_note: str | None = None

    market_spread_line: float | None = None
    market_total_line: float | None = None
    weather: WeatherOut | None = None

    correct_winner: bool | None = None
    model_version: str | None = None

    is_upset_alert: bool = False
    upset_note: str | None = None
    over_under_lean: str | None = None
    over_under_note: str | None = None


class SchemeFamilyOut(BaseModel):
    id: str
    side: str
    name: str
    era: str
    description: str
    core_concepts: list[str]
    team_season_count: int


class TeamSeasonOut(BaseModel):
    team_abbr: str
    season: int
    head_coach: str


class PlayConceptOut(BaseModel):
    label: str
    count: int
    share: float


class NotablePlayOut(BaseModel):
    game_id: str
    season: int
    week: int
    posteam: str | None
    defteam: str | None
    desc: str | None
    concept: str
    explanation: str
    epa: float | None
    yards_gained: float | None


class SchemeDetailOut(SchemeFamilyOut):
    team_seasons: list[TeamSeasonOut]
    top_concepts: list[PlayConceptOut]
    notable_plays: list[NotablePlayOut]
    sample_size: int


class PerformanceOut(BaseModel):
    graded_predictions: int
    winner_accuracy: float | None = None
    avg_brier_score: float | None = None
    avg_abs_margin_error: float | None = None
    avg_abs_total_error: float | None = None
