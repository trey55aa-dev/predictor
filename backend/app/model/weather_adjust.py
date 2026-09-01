"""Simple, documented heuristics for adjusting a projected total based on
game-day weather. Outdoor-stadium games only. These are MVP heuristics,
explicitly meant to be validated/refined later using the grading loop's
accuracy data -- not physics-grade modeling.
"""

from app.config import settings
from app.models import WeatherSnapshot


def total_points_adjustment(weather: WeatherSnapshot | None) -> tuple[float, str | None]:
    """Returns (points_to_subtract_from_total, human-readable note)."""
    if weather is None or not weather.applicable:
        return 0.0, None

    adjustment = 0.0
    notes: list[str] = []

    if weather.wind_mph is not None and weather.wind_mph > settings.weather_wind_threshold_mph:
        excess = weather.wind_mph - settings.weather_wind_threshold_mph
        wind_penalty = min(excess * 0.3, settings.weather_wind_max_penalty)
        adjustment += wind_penalty
        notes.append(f"wind {weather.wind_mph:.0f} mph")

    if weather.precip_mm is not None and weather.precip_mm > 0.5:
        adjustment += settings.weather_precip_penalty
        notes.append("precipitation expected")

    if weather.temp_f is not None and weather.temp_f < settings.weather_cold_threshold_f:
        adjustment += settings.weather_cold_penalty
        notes.append(f"cold ({weather.temp_f:.0f}°F)")

    note = None
    if notes:
        note = "Weather may suppress scoring: " + ", ".join(notes) + "."

    return adjustment, note
