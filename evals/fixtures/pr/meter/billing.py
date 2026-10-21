"""Bills usage in gallons."""
LITRES_PER_GALLON = 3.79
RATE_PER_1000_GAL = 6.25


def bill(litres):
    gallons = litres / LITRES_PER_GALLON
    return round(gallons / 1000 * RATE_PER_1000_GAL, 2)
