from app.model.elo import expected_win_prob, _mov_multiplier


def test_expected_win_prob_equal_ratings():
    assert expected_win_prob(1500, 1500) == 0.5


def test_expected_win_prob_favors_higher_rating():
    prob = expected_win_prob(1600, 1500)
    assert 0.5 < prob < 1.0


def test_expected_win_prob_symmetric():
    p1 = expected_win_prob(1600, 1400)
    p2 = expected_win_prob(1400, 1600)
    assert abs(p1 + p2 - 1.0) < 1e-9


def test_mov_multiplier_increases_with_margin():
    small = _mov_multiplier(3, 0)
    large = _mov_multiplier(28, 0)
    assert large > small


def test_mov_multiplier_dampened_for_expected_blowout():
    # A big favorite winning big should move ratings less than an underdog
    # winning by the same margin (both directions must stay well-behaved).
    fav_mult = _mov_multiplier(21, 400)
    dampened_only = _mov_multiplier(21, 0)
    assert fav_mult < dampened_only
