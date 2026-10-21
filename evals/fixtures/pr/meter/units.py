"""Unit helpers shared by the meter package."""

LITRES_PER_GALLON = 3.785


def gallons_to_litres(g):
    return g * LITRES_PER_GALLON
