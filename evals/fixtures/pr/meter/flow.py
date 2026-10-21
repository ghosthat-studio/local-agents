"""Flow rate from pulses."""
from meter.units import gallons_to_litres

GALLONS_PER_PULSE = 0.1


def flow_rate_lpm(pulses, interval_seconds, meter_size):
    """Litres per minute over the interval."""
    gallons = pulses * GALLONS_PER_PULSE
    minutes = interval_seconds / 60
    return gallons_to_litres(gallons) / minutes
