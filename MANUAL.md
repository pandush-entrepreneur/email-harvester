# Manual

Start to finish for a new list. Nine steps, roughly 10 minutes of attention plus unattended runtime.

Replace `acme` with the client name throughout.

**Only steps 3 and 4 need real judgement.** Everything else is mechanical.

---

## Step 1 — Build the list

Build the audience in Clay, Apollo, AI Ark, or anywhere else. Filtering and exporting is free; you are only avoiding the paid email-enrichment step.

The export must contain, under any header names:

- First name
- Last name
- Company domain

Every other column rides along untouched into the output.

**Do not pay for email enrichment.** That is what this engine replaces.

---

## Step 2 — Drop it in

```bash
mkdir -p clients/acme
cp ~/Downloads/acme-export.csv clients/acme/input.csv
```

Or from your laptop to the server:

```bash
scp acme-export.csv user@server:~/harvester/clients/acme/input.csv
```

---

## Step 3 — Dry run

No API calls. Seconds.

```bash
python email_engine.py \
  --input clients/acme/input.csv \
  --output-dir clients/acme/output \
  --dry-run
```

### Prompt

```
Run the dry run for clients/acme and report:
  - which columns were detected as first name, last name, domain
  - total rows, usable rows, skipped rows with reasons
  - unique domains and contacts-per-domain distribution
  - projected request count and estimated runtime
  - 15 sample normalized names, including any with accents, hyphens,
    multi-token surnames, or truncated surnames
  - the exact header row enriched_all.csv will have

Then stop.
```

### Check before continuing

| Look for | Good | Bad |
|---|---|---|
| Column detection | The three fields you expect | Wrong columns → fix `config.yaml` |
| Skipped rows | A few percent | Large share → wrong columns |
| Accented names | `Cindrić` → `cindric` | Unchanged → silent failures ahead |
| Truncated surnames | `Jonathan K.` kept as `first_name_only` | Dropped → losing ~5% of the list |
| Header row | All original columns, commas quoted | Mangled → output unusable downstream |

---

## Step 4 — Live sample

About 200 requests. Two minutes. **This is the decision point.**

```bash
python email_engine.py \
  --input clients/acme/input.csv \
  --output-dir clients/acme/output \
  --live-sample --domain-limit 50 --workers 3
```

### Prompt

```
Run a 50-domain live sample for clients/acme and report:
  - catch-all rate
  - pattern win distribution
  - transient rate broken down by message type
  - status breakdown
  - 20 sample rows
  - total requests spent

Then stop.
```

### Check before continuing

**The catch-all rate is the client's yield.** Roughly: sendable share ≈ 100% minus catch-all minus about 20%.

| Catch-all rate | Expect sendable |
|---|---|
| 25% | ~50% |
| 40% | ~33% |
| 55% | ~20% |

Tell the client that number **now**, before promising volume.

| Signal | Meaning |
|---|---|
| `Mx Error` above 3% | Something is wrong. Halt and diagnose. |
| `SPAM Block` ~10% | Normal. Provider policy. Not fixable. |
| `Timeout` ~4% | Normal. |

If you know a real email at any sampled company, confirm the engine derived the same address. That is the only true end-to-end check, and it is free at this size.

---

## Step 5 — Full run

Hours. Unattended. Use tmux or it dies when SSH drops.

```bash
tmux new -s acme

python email_engine.py \
  --input clients/acme/input.csv \
  --output-dir clients/acme/output \
  --full-run --workers 5
```

Detach with `ctrl-b` then `d`.

### Prompt

```
Start the full run for clients/acme under tmux, 5 workers.

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

Rough timing: about 1.5 requests per contact, roughly 200ms each at 5 workers. A 30,000-contact list is around 3 to 4 hours.

---

## Step 6 — Check progress

Read-only. Safe while running. From anywhere.

```bash
python email_engine.py --output-dir clients/acme/output --status
```

From your phone:

```bash
ssh user@server "cd ~/harvester && source .venv/bin/activate && \
  python email_engine.py --output-dir clients/acme/output --status"
```

If it stopped, just re-run the Step 5 command. Resume is automatic and nothing is re-spent.

---

## Step 7 — Segment

Do not send the file as one blob.

### Prompt

```
Split clients/acme/output/verified_send_ready.csv into campaign-ready
segments:

  1. Remove titles containing Board Member, Board of Directors, Advisor,
     Advisory, Investor, or Mentor. They rarely hold a mailbox at the
     company domain and they tank reply rates.
  2. Split by geography (US / UK / Canada / other). Different send
     windows and different consent expectations.
  3. Split by seniority: founder/CEO/owner vs VP/Head/Director. These
     need different copy, not the same sequence.

Report row counts per segment and write each to its own CSV.
```

Founders and VPs are not one audience. A founder is not hiring an SDR team; a VP just inherited a number. Same offer, different angle.

---

## Step 8 — Send

Load `verified_send_ready.csv` into the sending tool.

**Never send `enriched_all.csv`.** It contains `catchall_guess` rows, which are unverifiable by design.

Ramp the volume. About 30 emails per inbox per day. A 10,000-contact list on a 3-touch sequence is roughly 30,000 sends, so about a month at 33 inboxes. Start below capacity for the first week and climb.

---

## Step 9 — Recover check_failed (a day or two later)

Free. Typically 15% of the list is `check_failed`, meaning the probe was blocked or timed out and the address was **never actually tested**.

SMTP blocking is often time-of-day dependent, so a fresh pass on another day resolves a real share.

### Prompt

```
Re-run the check_failed rows for clients/acme.

These were never actually tested - the probe was blocked or timed out.
Re-verify only rows with status check_failed. Update them in place in
enriched_all.csv and append newly verified rows to
verified_send_ready.csv.

Report how many resolved and their new statuses.
```

Do this before telling a client the number is final.

---

## Reading the output

| Status | Sendable | Meaning |
|---|---|---|
| `verified` | Yes | Mail server accepted it |
| `catchall_guess` | **No** | Domain accepts everything. Unverifiable. |
| `check_failed` | Not yet | Never tested. Retriable, see Step 9. |
| `person_invalid` | No | Pattern known, this person rejected. Usually left. |
| `pattern_unknown` | No | No pattern cracked |
| `dead_domain` | No | No MX record |
| `duplicate_contact` | No | Same address as an earlier row. Mailed once. |

### Why catch-alls stay out

About 40 to 47% of those guesses are correct, so it is tempting. But they do not bounce — the server accepts everything — so the damage is invisible: sustained low engagement quietly eroding sender reputation rather than an obvious spike you can react to.

If you ever do use them, put them on separate domains as their own segment, and watch reply rate rather than bounce rate.

---

## When something breaks

See RUNBOOK.md. Every row in its failure table actually happened during the build, with the fix that worked.

The three most common:

| Symptom | Fix |
|---|---|
| Much slower than projected | Retries running inline, or sleeping after each request instead of pacing by interval |
| `Mx Error` high | Run has gone domain-major and is hammering one mail server. Restructure to stage-major. |
| Row count mismatch | Count CSV records, not physical lines. Embedded newlines are common in Clay exports. |

---

## Client hygiene

When an engagement ends:

```bash
rm -rf clients/acme
```

Their list is their property. Holding it afterwards is liability with no upside.

Client data never enters git. `clients/` and `.env` are gitignored — keep it that way.
