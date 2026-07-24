# Prompts

Copy-paste, in order. Replace `acme` with the client folder name.

`P1` to `P4` are the normal run. `P5` onward are recovery and troubleshooting.

The engine already exists and works. **Do not rebuild it per client.** Use `P0` only when the spec changes.

---

## P1 — Dry run

```
Run the dry run for this client:

  python email_engine.py \
    --input clients/acme/input.csv \
    --output-dir clients/acme/output \
    --dry-run

Report:
  - which input columns were detected as first name, last name, domain
  - total rows, usable rows, skipped rows with reasons
  - unique domains, contacts-per-domain distribution
  - projected request count per stage and estimated runtime
  - 15 sample normalized names, including any with accents, hyphens,
    multi-token surnames, or truncated surnames
  - the exact header row that enriched_all.csv will have

Then stop. No API calls.
```

**Before approving, check:**
- Accented names transliterate (`Cindrić` → `cindric`). If not, every such contact fails silently.
- Truncated surnames (`Jonathan K.`) are kept as `first_name_only`, not skipped.
- Skipped rows are a small share. A large count means column detection picked wrong fields.
- The header preserved all original columns, quoted correctly if any contain commas.

---

## P2 — Live sample

```
Run a 50-domain live sample:

  python email_engine.py \
    --input clients/acme/input.csv \
    --output-dir clients/acme/output \
    --live-sample --domain-limit 50 --workers 3

Report:
  - catch-all rate
  - pattern win distribution
  - transient rate broken down by message type
  - status breakdown
  - 20 sample rows
  - total requests spent

Then stop.
```

**Before approving, check:**
- **Catch-all rate.** This sets the client's expected yield. Tell them now, not later.
- `Mx Error` above 3% means domain-major ordering has crept back in. Halt and fix.
- `SPAM Block` near 10% and `Timeout` near 4% are the provider floor. Not fixable. Proceed.
- If you know a real email at any sampled company, confirm the engine derived the same address. This is the only true end-to-end check.

---

## P3 — Full run

```
Start the full run:

  tmux new -s acme
  python email_engine.py \
    --input clients/acme/input.csv \
    --output-dir clients/acme/output \
    --full-run --workers 5

Do not stop for approval on speed or transient rate. Halt only for:
  - Mx Error above 3%
  - connections above 1 on five consecutive requests
  - row-count assertion failure
  - daily cap reached

Log progress every 500 requests with count, elapsed, and ETA.

On completion report: final row count, status breakdown with
percentages, verified_send_ready.csv row count, catch-all rate,
pattern win distribution, total requests spent, and check_failed count.
```

Detach with `ctrl-b` then `d`. Reattach with `tmux attach -t acme`.

---

## P4 — Progress check

Read-only. Safe while a job is running.

```bash
python email_engine.py --output-dir clients/acme/output --status
```

From your phone:

```bash
ssh user@server "cd ~/harvester && source .venv/bin/activate && python email_engine.py --output-dir clients/acme/output --status"
```

---

## P5 — Resume after interruption

```
The run stopped. Resume it.

First confirm: how many results are already banked in results.jsonl and
how many requests remain. Nothing already checked should be re-spent.
Then resume with the same worker count and the same output dir.
```

Resume is automatic — the engine reads `results.jsonl` and skips anything already checked.

---

## P6 — Recover check_failed rows

Free. Run a day or more after the main job.

```
Re-run the check_failed rows for this client.

These were never actually tested; the probe was blocked or timed out.
SMTP blocking is often time-of-day dependent, so a fresh pass on a
different day resolves a real share of them.

Re-verify only rows with status check_failed. Update them in place in
enriched_all.csv and append newly verified rows to
verified_send_ready.csv. Report how many resolved and their new statuses.
```

On a 15% `check_failed` rate, expect roughly a third of the list's normal verify rate to come back. Worth doing before telling a client the number is final.

---

## P7 — Segment the deliverable

```
Split verified_send_ready.csv into campaign-ready segments:

  1. Remove titles containing Board Member, Board of Directors, Advisor,
     Advisory, Investor, or Mentor. They rarely hold a mailbox at the
     company and they tank reply rates.
  2. Split by geography (US / UK / Canada / other) — different send
     windows and different consent expectations.
  3. Split by seniority: founder/CEO/owner vs VP/Head/Director. These
     need different copy, not the same sequence.

Report row counts per segment and write each to its own CSV.
```

---

## P0 — Rebuild or modify the engine

Only when the spec changes. Not per client.

```
Read SKILL-Permutation.md in full before writing any code. Follow it
exactly. It is a spec, not a suggestion. If you think something in the
spec is wrong, stop and tell me instead of changing it.

[describe the change]

Then run P1 and P2 against a known client list and confirm the numbers
match the previous run before using it on anything new.
```

---

## P8 — Troubleshooting

**Slower than projected**

```
The run is slower than projected. Diagnose without stopping it if
possible. Report effective ms per request, whether workers are blocked
on retry backoff, and whether the rate limiter or network latency is
the binding constraint.

Two known causes:
  - Sleeping the full interval AFTER each request instead of pacing at
    previous_start + interval.
  - Retrying transients inline instead of deferring them to Stage 4.
```

**Transient rate above 20%**

```
Break the transient rate down by message type.

High Mx Error means the run has gone domain-major and is hammering
individual mail servers. Restructure to stage-major, round-based.

SPAM Block and Timeout are the provider floor. Not fixable. Continue.
```

**connections above 1**

```
Check whether anything else is using the key: a browser tab on
MailTester, another script, a second client run. Pause 30 seconds and
retry rather than halting on a single reading.
```

**Row count mismatch**

```
Count CSV records with csv.DictReader, not physical lines. Embedded
newlines inside quoted fields are common in Clay exports and will make
a correct file look wrong.
```
