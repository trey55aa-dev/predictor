"""Converts market odds into de-vigged implied probabilities and blends them
with the Elo model's win probability."""

from app.config import settings


def american_to_implied_prob(price: float) -> float:
    if price > 0:
        return 100.0 / (price + 100.0)
    return -price / (-price + 100.0)


def devig_two_way(prob_a: float, prob_b: float) -> tuple[float, float]:
    """Removes the bookmaker's vig by normalizing two implied probabilities to sum to 1."""
    total = prob_a + prob_b
    if total <= 0:
        return 0.5, 0.5
    return prob_a / total, prob_b / total


def market_home_win_prob(home_moneyline: float, away_moneyline: float) -> float:
    home_raw = american_to_implied_prob(home_moneyline)
    away_raw = american_to_implied_prob(away_moneyline)
    home_fair, _ = devig_two_way(home_raw, away_raw)
    return home_fair


def blend_win_prob(elo_prob: float, market_prob: float | None) -> float:
    """Regresses the Elo model's win probability toward the market's, when available.

    Blending toward the market is a well-known cheap way to improve a simple
    power-rating model -- the market aggregates information (injuries, weather,
    public/sharp money) the Elo model doesn't see.
    """
    if market_prob is None:
        return elo_prob
    w = settings.market_blend_weight
    return w * elo_prob + (1 - w) * market_prob


def blend_line(elo_value: float, market_value: float | None) -> float:
    if market_value is None:
        return elo_value
    w = settings.market_blend_weight
    return w * elo_value + (1 - w) * market_value
