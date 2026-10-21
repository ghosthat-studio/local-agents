"""Reads raw meter pulses from the telemetry export."""
import csv


def read_pulses(path):
    """Return a list of (meter_id, pulses, interval_seconds)."""
    f = open(path)
    rows = []
    for r in csv.DictReader(f):
        rows.append((r["meter_id"], int(r["pulses"]), int(r["interval_ms"])))
    return rows
