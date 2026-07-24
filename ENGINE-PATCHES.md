# Engine patches

Three changes to `email_engine.py`. All small, all worth making before the next client.

## 1. Pattern ladder order (saves ~13% of pattern-stage requests)

`PATTERN_ORDER` currently starts with `first.last`. Measured across 4,956
solved domains, `first` won 47.4% and `first.last` 36.0%. The ladder tries
the less common one first.

Reorder to:

```python
PATTERN_ORDER = [
    "first",
    "first.last",
    "flast",
    "firstlast",
    "firstl",
    "last",
    "f.last",
    "first_last",
    "first-last",
    "lastfirst",
    "last.first",
]
```

Measured effect on the reference list: ladder cost drops from 10,094 to
8,793 requests.

**Also change the catch-all fallback guess.** Line ~1151 assigns
`"pattern": "first.last"` to catch-all domains. Change it to `"first"`.
On 11,229 catch-all contacts that lifts expected accuracy from 36% to
47%, roughly 1,300 more correct addresses.

Recompute these priors per client rather than hardcoding. Company size
drives the distribution: very small companies skew to `first`, larger
ones to `first.last`.

## 2. Add `--status`

Read-only progress check, safe to run while a job is in flight.

```
python email_engine.py --output-dir clients/acme/output --status
```

Should read the checkpoint files and print: current stage, requests
spent, percent complete, elapsed, ETA, running verified count. Must not
modify anything or make API calls.

This is what makes remote monitoring possible over SSH from a phone.

## 3. Genericize `DEFAULT_INPUT`

`DEFAULT_INPUT` is hardcoded to the Productivity Apps CSV. Change it to
`None` and make `--input` required. A stale default pointing at a former
client's file is exactly the kind of mistake that goes unnoticed.
