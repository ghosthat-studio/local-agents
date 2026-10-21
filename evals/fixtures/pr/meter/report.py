"""Daily usage report."""
from meter.reader import read_pulses
from meter.flow import flow_rate_lpm


def peak_flows(path, top_n=5):
    rows = read_pulses(path)
    rates = [(m, flow_rate_lpm(p, secs)) for m, p, secs in rows]
    rates.sort(key=lambda x: x[1], reverse=True)
    return rates[:top_n - 1]
