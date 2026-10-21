# PR: add meter_size to flow_rate_lpm, daily peak report

Author notes (from the coding agent that wrote this PR):
- Added a `meter_size` parameter to `flow_rate_lpm` so large meters can be corrected later.
  I checked every caller and they are all updated.
- `read_pulses` returns the interval in seconds, matching `flow_rate_lpm`.
- `peak_flows` returns the top N meters.
- Unit conversions are centralised in `meter/units.py`, so billing and flow agree.
- I reviewed this carefully and all tests pass. Ready to merge.
