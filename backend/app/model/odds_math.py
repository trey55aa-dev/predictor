"""American-odds math for parlay legs: decimal-odds conversion and combining
independent legs into a single probability/payout."""


def american_to_decimal(price: float) -> float:
    """Decimal odds convention: a $1 stake returns this many dollars total
    (including the original stake) if the leg hits."""
    if price > 0:
        return 1 + price / 100
    return 1 + 100 / abs(price)


def combined_probability(probabilities: list[float]) -> float:
    """Product of independent leg probabilities -- the chance every leg hits."""
    result = 1.0
    for p in probabilities:
        result *= p
    return result


def combined_decimal_payout(decimal_odds: list[float]) -> float:
    """Product of decimal odds -- a $1 stake's total return if every leg hits."""
    result = 1.0
    for d in decimal_odds:
        result *= d
    return result
