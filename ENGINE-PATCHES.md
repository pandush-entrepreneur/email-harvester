# Engine improvements applied

## Completed after the TRI run

- Gate 1 now accepts any non-empty valid list; it no longer assumes the original 33k reference dataset.
- Gate 1 reads the selected output directory rather than a fixed legacy folder.
- `--input` is required for any run that reads a list, preventing accidental use of a former client's filename.
- `--status` reads checkpoints only. It reports recorded API results, current-day usage, and available output row counts without API calls or writes.
- HTTP 408, 429, 500, 502, 503, 504, and network failures are normalized to transient `Timeout` results so the retry queue and `results.jsonl` resume mechanism can handle them.

## Next experiment: per-segment pattern priors

The TRI final pattern distribution favoured `first` (7,601 solved domains) over `first.last` (4,953), but this can vary by company size and segment. Keep `PATTERN_ORDER` configurable and compare request cost against the next comparable completed run before changing the default ordering.

Catch-all guesses must remain excluded from `verified_send_ready.csv` regardless of the preferred fallback pattern.
