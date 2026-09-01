from app.model.odds_math import american_to_decimal, combined_decimal_payout, combined_probability


def test_american_to_decimal_favorite():
    assert abs(american_to_decimal(-150) - (1 + 100 / 150)) < 1e-9


def test_american_to_decimal_underdog():
    assert abs(american_to_decimal(130) - 2.3) < 1e-9


def test_combined_probability_multiplies():
    assert abs(combined_probability([0.6, 0.5, 0.5]) - 0.15) < 1e-9


def test_combined_probability_single_leg():
    assert combined_probability([0.7]) == 0.7


def test_combined_decimal_payout_multiplies():
    assert abs(combined_decimal_payout([2.0, 1.5]) - 3.0) < 1e-9
