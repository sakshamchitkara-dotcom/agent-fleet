def compound(principal, rate_percent, years):
    """Balance after `years` of yearly compounding at `rate_percent` % per year."""
    return round(principal * (1 + rate_percent) ** years, 2)
