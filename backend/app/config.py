from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./football.db"
    odds_api_key: str = ""

    # Prediction model tuning constants.
    elo_k_factor: float = 20.0
    elo_home_field_advantage: float = 60.0
    elo_start_rating: float = 1500.0
    elo_season_regression: float = 1.0 / 3.0
    market_blend_weight: float = 0.4  # weight given to the Elo model vs. the market

    # Weather adjustment thresholds (outdoor games only).
    weather_wind_threshold_mph: float = 10.0
    weather_wind_max_penalty: float = 4.0  # points shaved off the total at high wind
    weather_precip_penalty: float = 1.5
    weather_cold_threshold_f: float = 32.0
    weather_cold_penalty: float = 1.0

    # Initial (pre-backtest) estimates of prediction error spread, used to express
    # a 1-std-dev confidence range around point predictions. Historically, NFL
    # score-margin predictions run roughly +/-13-14 points std dev and totals
    # roughly +/-10 points. `grade.py` accumulates real error data over time so
    # these can be replaced with measured values as the model is graded on more games.
    margin_std_default: float = 13.5
    total_std_default: float = 10.0

    model_version: str = "elo-market-weather-v1"


settings = Settings()
