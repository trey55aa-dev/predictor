from app.model.weather_adjust import total_points_adjustment
from app.models import WeatherSnapshot


def test_no_weather_snapshot_no_adjustment():
    adjustment, note = total_points_adjustment(None)
    assert adjustment == 0.0
    assert note is None


def test_inapplicable_dome_no_adjustment():
    w = WeatherSnapshot(applicable=False)
    adjustment, note = total_points_adjustment(w)
    assert adjustment == 0.0
    assert note is None


def test_calm_clear_weather_no_adjustment():
    w = WeatherSnapshot(applicable=True, wind_mph=5, precip_mm=0, temp_f=65)
    adjustment, note = total_points_adjustment(w)
    assert adjustment == 0.0
    assert note is None


def test_high_wind_produces_penalty_and_note():
    w = WeatherSnapshot(applicable=True, wind_mph=25, precip_mm=0, temp_f=60)
    adjustment, note = total_points_adjustment(w)
    assert adjustment > 0
    assert "wind" in note.lower()


def test_precipitation_and_cold_stack():
    w = WeatherSnapshot(applicable=True, wind_mph=5, precip_mm=2.0, temp_f=25)
    adjustment, note = total_points_adjustment(w)
    assert adjustment > 0
    assert "precipitation" in note.lower()
    assert "cold" in note.lower()
