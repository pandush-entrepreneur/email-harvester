# Email Harvester

Turns any contact list with **first name, last name, company domain** into verified work emails.

No per-contact data cost. Runs against a MailTester Ninja key. Yield depends on list quality, duplicates, and catch-all domains.

Built and proven across **208,391 processed lead rows**: **43,794 verified addresses from 207,841 MailTester requests**, with zero marginal data cost. See [RUN_HISTORY.md](RUN_HISTORY.md) for the aggregate record and per-run benchmarks. Update these cumulative figures after every completed enrichment.

---

## Read first

- **[CLEANUP.md](CLEANUP.md)** if this repo has ever been public with client data in it.
- **[PROMPTS.md](PROMPTS.md)** for the step-by-step run sequence.
- **[RUNBOOK.md](RUNBOOK.md)** for server setup, resuming, and troubleshooting.

---

## Run a client

```bash
# 1. Set up
cp .env.example .env && nano .env      # paste MailTester key
mkdir -p clients/acme
cp ~/acme-list.csv clients/acme/input.csv

# 2. Dry run. No API calls. Confirms columns, estimates requests.
python email_engine.py \
  --input clients/acme/input.csv \
  --output-dir clients/acme/output \
  --dry-run

# 3. Live sample. ~200 requests. Reveals catch-all rate.
python email_engine.py \
  --input clients/acme/input.csv \
  --output-dir clients/acme/output \
  --live-sample --domain-limit 50 --workers 3

# 4. Full run. Unattended, hours. Use tmux.
python email_engine.py \
  --input clients/acme/input.csv \
  --output-dir clients/acme/output \
  --full-run --workers 5

# 5. Progress, any time, read-only
python email_engine.py --output-dir clients/acme/output --status
```

---

## Outputs

| File | Contents |
|---|---|
| `verified_send_ready.csv` | Verified only. This goes to the sending tool. |
| `enriched_all.csv` | Every input row, all original columns, plus email and status. |
| `results.jsonl` | Append-only audit log. Enables resume. Never delete mid-run. |

Row count of `enriched_all.csv` always equals the input. Nothing is deleted, only labelled.

---

## Status values

| Status | Sendable | Meaning |
|---|---|---|
| `verified` | Yes | Mail server accepted it. |
| `catchall_guess` | **No** | Domain accepts everything. Pattern is a guess. |
| `check_failed` | Not yet | Probe blocked or timed out. Never actually tested. Retriable. |
| `person_invalid` | No | Domain pattern known, this person rejected. Usually left the company. |
| `pattern_unknown` | No | No pattern cracked. |
| `dead_domain` | No | No MX record. |
| `duplicate_contact` | No | Same address as an earlier row. Mailed once. |

---

## Input requirements

Any CSV with first name, last name, and company domain under any header names. Everything else is carried through unchanged.

Truncated LinkedIn surnames (`Jonathan K.`) are kept, not dropped — two of the eleven patterns need no surname.

---

## Expected yield

Latest comparable profile: TRI, a small B2B software list with 175,204 raw rows and 101,774 unique usable contacts.

| Status | Share of raw rows | Share of unique usable contacts |
|---|---:|---:|
| `verified` | 19.13% | 32.93% |
| `catchall_guess` | 21.19% | 36.48% |
| `check_failed` | 5.82% | 10.02% |
| `person_invalid` | 6.00% | 10.34% |
| other | 5.95% | 10.23% |

For similarly sourced lists, plan about **5.23 raw rows** or **3.04 unique usable contacts** per verified email. Recalculate from the latest comparable completed run before quoting a forecast. Catch-all rate drives the result and varies by segment.

---

## The one rule

**Never send to `catchall_guess`.** Those domains accept every address, so the verifier cannot confirm anything. About 40 to 47% of the guesses are right, and the rest land nowhere. They do not bounce, so the damage is a slow invisible drag on sender reputation rather than an obvious spike.

`verified_send_ready.csv` is pre-filtered. Use it.

---

## Client data never enters this repo

`clients/` and `.env` are gitignored. Lead lists are client property, and a key committed once is committed permanently. Delete client folders when an engagement ends.
