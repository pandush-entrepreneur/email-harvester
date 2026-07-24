# Email Harvester

Turns any contact list with **first name, last name, company domain** into verified work emails.

No per-contact data cost. Runs against a MailTester Ninja key. Typical yield is around 30% fully verified at roughly 0.7 API requests per contact.

Built and proven on a 33,187-contact list: **10,282 verified addresses, 49,266 requests, zero marginal cost.** Paid enrichment for the same list quotes at $3,000 to $5,000.

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

Measured on 33,187 small B2B SaaS contacts:

| Status | Share |
|---|---|
| `verified` | 31% |
| `catchall_guess` | 34% |
| `check_failed` | 15% |
| `person_invalid` | 8% |
| other | 12% |

**Tell clients one third up front.** Catch-all rate drives it and varies by segment: very small companies on Google Workspace run 35 to 50%, larger companies lower.

---

## The one rule

**Never send to `catchall_guess`.** Those domains accept every address, so the verifier cannot confirm anything. About 40 to 47% of the guesses are right, and the rest land nowhere. They do not bounce, so the damage is a slow invisible drag on sender reputation rather than an obvious spike.

`verified_send_ready.csv` is pre-filtered. Use it.

---

## Client data never enters this repo

`clients/` and `.env` are gitignored. Lead lists are client property, and a key committed once is committed permanently. Delete client folders when an engagement ends.
