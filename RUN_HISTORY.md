# Run history

Store aggregate, non-identifying outcome data here after each completed client run. Do not put lead rows, API keys, or contact-level output in this file.

## TRI — 2026-07-25

| Metric | Count | Rate |
|---|---:|---:|
| Raw input rows | 175,204 | 100.00% of raw |
| Input-invalid rows | 4,099 | 2.34% of raw |
| Duplicate contacts | 69,331 | 39.57% of raw |
| Unique usable contacts | 101,774 | 58.09% of raw |
| Verified send-ready emails | 33,512 | 19.13% of raw; 32.93% of unique usable |
| Catch-all guesses | 37,125 | 21.19% of raw; 36.48% of unique usable |
| Check failed | 10,201 | 5.82% of raw; 10.02% of unique usable |

Other unique-usable outcomes: 10,520 person-invalid, 4,946 pattern-unknown, 4,431 dead-domain, and 1,039 unresolvable-pattern.

Planning benchmark for similarly sourced lists:

- Pull about **5.23 raw rows per verified email**.
- Or, after pre-verification deduplication and validity checks, plan **3.04 unique usable contacts per verified email**.
- These are benchmarks, not guarantees. Recalculate after each run; segment, company size, provider mix, and duplicate rate materially affect yield.

Operational findings: the run spent 158,575 requests, completed with a 40.48% domain catch-all rate, and encountered an HTTP 502. Treat transient HTTP 408/429/5xx and network failures as retryable timeouts so a job resumes from `results.jsonl` rather than failing.
