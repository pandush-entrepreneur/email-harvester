# Run history

Store aggregate, non-identifying outcome data here after each completed client run. Do not put lead rows, API keys, or contact-level output in this file.

## Cumulative proof point

| Metric | Total |
|---|---:|
| Completed enrichment runs | 6 |
| Processed lead rows | 487,977 |
| Verified send-ready addresses | 120,582 |
| MailTester requests | 564,482 |
| Verified from processed rows | 24.71% |

After every completed full run, append its aggregate record below, recalculate this table, and update the matching proof-point sentence in `README.md`. Count processed input rows, not only unique usable contacts; report the latter separately in each run record.

## Reference run — prior benchmark

| Metric | Count |
|---|---:|
| Processed contacts | 33,187 |
| Verified addresses | 10,282 |
| MailTester requests | 49,266 |

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

## Skyhigh — 2026-07-28

| Metric | Count | Rate |
|---|---:|---:|
| Raw input rows | 88,020 | 100.00% of raw |
| Input-invalid rows | 1,542 | 1.75% of raw |
| Duplicate contacts | 24,310 | 27.62% of raw |
| Unique usable contacts | 62,168 | 70.63% of raw |
| Verified send-ready emails | 21,174 | 24.06% of raw; 34.06% of unique usable |
| Catch-all guesses | 18,148 | 20.62% of raw; 29.19% of unique usable |
| Check failed | 7,369 | 8.37% of raw; 11.85% of unique usable |

Other unique-usable outcomes: 7,227 person-invalid, 3,828 pattern-unknown, 2,492 dead-domain, and 1,930 unresolvable-pattern.

Planning benchmark for similarly sourced lists:

- Pull about **4.16 raw rows per verified email**.
- Or, after pre-verification deduplication and validity checks, plan **2.94 unique usable contacts per verified email**.
- Recalculate for materially different sources or segments.

Operational findings: the run spent 81,000 requests, completed with a 32.47% domain catch-all rate, and produced 21,174 verified send-ready rows. Validation confirmed all 88,020 input rows were preserved and no catch-all guesses entered the send-ready output.

## TRI — 2026-08-04

| Metric | Count | Rate |
|---|---:|---:|
| Raw input rows | 110,977 | 100.00% of raw |
| Input-invalid rows | 1,959 | 1.77% of raw |
| Duplicate contacts | 23,493 | 21.17% of raw |
| Unique usable contacts | 85,525 | 77.06% of raw |
| Verified send-ready rows | 31,789 | 28.64% of raw; 37.17% of unique usable |
| Unique verified email addresses | 31,666 | 28.53% of raw |
| Catch-all guesses | 28,425 | 25.61% of raw; 33.24% of unique usable |
| Check failed | 5,386 | 4.85% of raw; 6.30% of unique usable |

Other unique-usable outcomes: 9,799 person-invalid, 5,128 pattern-unknown, 3,647 dead-domain, and 1,351 unresolvable-pattern.

Planning benchmark for similarly sourced lists:

- Pull about **3.49 raw rows per verified email**.
- Or, after pre-verification deduplication and validity checks, plan **2.69 unique usable contacts per verified email**.
- Recalculate for materially different sources or segments.

Operational findings: the run spent 131,770 requests, completed with a 38.17% domain catch-all rate, and produced 31,789 verified rows. Validation confirmed all 110,977 input rows were preserved and every row in `verified_send_ready.csv` matched a verified result. Because 123 rows repeated an address verified for another source identity, `verified_send_ready_unique.csv` was added as the campaign-safe deliverable with 31,666 unique addresses. No catch-all guesses entered either send-ready file.

## Skyhigh — 2026-08-06

| Metric | Count | Rate |
|---|---:|---:|
| Raw input rows | 47,974 | 100.00% of raw |
| Input-invalid rows | 893 | 1.86% of raw |
| Duplicate contacts | 2,461 | 5.13% of raw |
| Unique usable contacts | 44,620 | 93.01% of raw |
| Verified send-ready rows | 13,615 | 28.38% of raw; 30.51% of unique usable |
| Unique verified email addresses | 13,539 | 28.22% of raw |
| Catch-all guesses | 12,715 | 26.50% of raw; 28.50% of unique usable |
| Check failed | 5,315 | 11.08% of raw; 11.91% of unique usable |

Other unique-usable outcomes: 4,607 person-invalid, 5,334 pattern-unknown, 2,620 dead-domain, and 414 unresolvable-pattern.

Planning benchmark for similarly sourced lists:

- Pull about **3.52 raw rows per verified email**.
- Or, after pre-verification deduplication and validity checks, plan **3.28 unique usable contacts per verified email**.
- Recalculate for materially different sources or segments.

Operational findings: the run spent 82,144 requests against a 71,155-request projection and completed with a 29.75% domain catch-all rate. Validation confirmed all 47,974 input rows were preserved and every row in `verified_send_ready.csv` matched a verified result. Because 76 rows repeated an address verified for another source identity, `verified_send_ready_unique.csv` was added as the campaign-safe deliverable with 13,539 unique addresses. No catch-all guesses entered either send-ready file.

## TRI — 2026-08-07

| Metric | Count | Rate |
|---|---:|---:|
| Raw input rows | 32,615 | 100.00% of raw |
| Input-invalid rows | 876 | 2.69% of raw |
| Duplicate contacts | 1,476 | 4.53% of raw |
| Unique usable contacts | 30,263 | 92.79% of raw |
| Verified send-ready rows | 10,210 | 31.30% of raw; 33.74% of unique usable |
| Unique verified email addresses | 10,180 | 31.21% of raw |
| Catch-all guesses | 12,105 | 37.11% of raw; 40.00% of unique usable |
| Check failed | 1,612 | 4.94% of raw; 5.33% of unique usable |

Other unique-usable outcomes: 1,932 person-invalid, 2,522 pattern-unknown, 1,553 dead-domain, and 329 unresolvable-pattern.

Planning benchmark for similarly sourced lists:

- Pull about **3.19 raw rows per verified email**.
- Or, after pre-verification deduplication and validity checks, plan **2.96 unique usable contacts per verified email**.
- Recalculate for materially different sources or segments.

Operational findings: the run spent 61,727 requests against a 71,155-request projection and completed with a 41.87% domain catch-all rate. Validation confirmed all 32,615 input rows were preserved and every row in `verified_send_ready.csv` matched a verified result. Because 30 rows repeated an address verified for another source identity, `verified_send_ready_unique.csv` was added as the campaign-safe deliverable with 10,180 unique addresses. No catch-all guesses entered either send-ready file.
