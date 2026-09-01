from app.model.market import (
    american_to_implied_prob,
    blend_line,
    blend_win_prob,
    devig_two_way,
    market_home_win_prob,
)


def test_american_to_implied_prob_favorite():
    # -200 favorite => implied prob 200/300 = 0.6667
    assert abs(american_to_implied_prob(-200) - (200 / 300)) < 1e-9


def test_american_to_implied_prob_underdog():
    # +150 underdog => implied prob 100/250 = 0.4
    assert abs(american_to_implied_prob(150) - 0.4) < 1e-9


def test_devig_two_way_sums_to_one():
    a, b = devig_two_way(0.55, 0.55)  # both sides overpriced due to vig
    assert abs(a + b - 1.0) < 1e-9
    assert abs(a - 0.5) < 1e-9


def test_market_home_win_prob_removes_vig():
    # Home -150, Away +130: raw implied probs sum to > 1 (vig); de-vigged should sum to 1.
    prob = market_home_win_prob(-150, 130)
    assert 0.5 < prob < 0.65


def test_blend_win_prob_with_no_market_returns_elo():
    assert blend_win_prob(0.7, None) == 0.7


def test_blend_win_prob_moves_toward_market():
    blended = blend_win_prob(0.9, 0.5)
    assert 0.5 < blended < 0.9


def test_blend_line_with_no_market_returns_first_value():
    assert blend_line(3.5, None) == 3.5
