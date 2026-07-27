# Runbook

Operating the engine on a server. Written for Hetzner, applies to any Linux box.

---

## One-time setup

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv tmux git
git clone git@github.com:pandush-entrepreneur/email-harvester.git ~/harvester
cd ~/harvester
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env            # paste the key
chmod 600 .env
```

**Never pass the key on the command line.** It lands in shell history on disk, and in your scrollback if you paste it anywhere.

---

## Running a client

```bash
cd ~/harvester && source .venv/bin/activate
mkdir -p clients/acme

# from your laptop:
scp acme-list.csv user@server:~/harvester/clients/acme/input.csv
```

## Planning and observing a run

Run the dry run first and save its aggregate totals in `RUN_HISTORY.md`. Do not use a fixed universal yield. The TRI benchmark was 19.13% verified from raw rows and 32.93% from unique usable contacts, but those rates vary by source and segment.

For a target number of verified emails, use the latest comparable benchmark:

```
raw leads needed = target verified / raw-to-verified rate
unique usable needed = target verified / usable-to-verified rate
```

Deduplicate before the live run whenever possible. It preserves every source row in `enriched_all.csv`, but avoids spending verification requests on repeat contacts.

Read progress without connecting to tmux or calling the API:

```bash
cd ~/harvester && .venv/bin/python email_engine.py --output-dir clients/acme/output --status
```

For unattended work, start the process from a small checked script or a quoted shell script, not a long inline SSH command. Use Python's unbuffered mode (`-u`) when redirecting logs. Confirm progress by watching the timestamp and line count of `results.jsonl`; a surviving tmux session alone does not prove that the worker is active.

Then P1 → P2 → P3 from PROMPTS.md.

**tmux is not optional.** Without it the job dies the moment SSH drops.

```bash
tmux new -s acme        # start
# ctrl-b then d         # detach
tmux attach -t acme     # reattach
tmux ls                 # list running jobs
```

Retrieve results:

```bash
scp user@server:~/harvester/clients/acme/output/verified_send_ready.csv .
```

---

## Several clients at once

One tmux session per client. **The MailTester rate limit is per key, not per process.** Two clients at 5 workers each is 10 concurrent requests on one key.

Split the worker budget rather than doubling it: two clients at 3 workers each, not 5 each. Beyond two, run sequentially — total throughput is capped by the key anyway, and sequential is easier to debug.

---

## Plan limits

| Plan | Rate | Interval | Daily cap |
|---|---|---|---|
| Pro | 11 / 10s | 865ms | 100k |
| Ultimate | 57 / 10s | 170ms | 500k |

Purchased quantity multiplies both. Ultimate × QTY 2 = 1M/day.

A 33,000-contact list costs roughly 50,000 requests, comfortably inside one Ultimate day. On Pro it spans two; the engine stops cleanly at the cap and resumes next day.

**Sequential execution cannot reach Ultimate's rate**, because a request takes longer than the interval. 5 workers behind one shared limiter is the practical ceiling. Six crosses it.

---

## Latency is usually the real constraint

Round-trip time, not the rate limit, is what binds. A datacenter connection typically halves it versus home broadband, roughly doubling throughput for free.

Measured reference: 580ms per request from home, contributing to a 15-hour projection. After fixing pacing and deferring retries, 343ms at 3 workers, and about 200ms at 5.

If a run is slow, measure effective ms per request before adding workers.

---

## Resuming

Every result is appended to `results.jsonl` the moment it returns. Interruptions cost wall-clock time, never work.

Resume is the default — the engine reads `results.jsonl` and skips anything already checked.

Migrating machines mid-run: copy `results.jsonl` across and resume. Nothing is re-spent.

**Never delete `results.jsonl` while a job is unfinished.** It is the only record of what has been paid for.

---

## Keeping the machine awake

Linux servers do not sleep. If you ever run this on a laptop:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

---

## Security

- `.env` gitignored, `chmod 600`.
- `clients/` gitignored. Client lead lists must never enter git history.
- Rotate the key if it is ever pasted into a terminal command, chat, or shared log.
- Delete client input and output when an engagement ends. Holding a former client's list is liability with no upside.

---

## Known failure modes

Every row here actually happened during the build, with the fix that worked.

| Symptom | Cause | Fix |
|---|---|---|
| Everything verifies on a domain | Catch-all not probed first | Stage 1 must run before Stage 2 |
| High `Mx Error` (13%) | Domain-major ordering hammering one mail server | Stage-major, round-based |
| `SPAM Block` ~10%, `Timeout` ~4% | Provider policy, commonly Microsoft 365 | Not fixable. Normal floor. |
| Run 4x slower than projected | Sleeping full interval after each request | Pace at `previous_start + interval` |
| A third of capacity idle | Retrying transients inline, blocking workers | Defer to Stage 4 queue |
| Row count mismatch | Counting physical lines | Count CSV records; embedded newlines are common |
| `Disabled Key` | Braces around the key | Strip them; key starts `sub_` |
| `connections` above 1 | Browser tab or second process on same key | Pause 30s, retry; halt only after 5 consecutive |
| Contacts silently missing | Accented names not transliterated | NFKD normalize, strip combining marks |
| ~5% of list dropped at load | Truncated LinkedIn surnames treated as invalid | Keep as `first_name_only`; `first` and `firstl` need no surname |
| Job dies on disconnect | No tmux | Always tmux |
| Job stops on HTTP 502/503/504 or network error | Transport exception was not classified | Record it as transient `Timeout`, retry in the retry queue, and resume from `results.jsonl` |
