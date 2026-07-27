---
name: lead-enrichment
description: Guide a new lead-list CSV from intake through server-side email discovery, verification, and retrieval of enriched outputs. Use whenever the user says “new file, let's enrich”, “start enrichment”, “start work on <name>”, `/enrich`, or attaches a lead CSV for email enrichment. Run a guided intake when no file is attached; do not require the user to know or write operational prompts.
---

# Lead enrichment

Treat a trigger phrase as the start of a guided server workflow. The user should only need to attach a CSV and answer questions that are genuinely missing or material.

## Intake

1. Look for an attached CSV or a new CSV in the workspace.
2. If none is available, say: “Ready. Attach the lead CSV; I’ll handle the rest.” Do not ask a long questionnaire.
3. If several plausible files exist, show their names and ask the user to select one.
4. Derive a client/list label from the filename. Confirm it only if ambiguous.
5. Check server access with the existing deployment key. If access is absent, ask for the server IP and guide the user through adding an SSH public key. Never request a root password or private key in chat.
6. Check that the MailTester key is configured on the server. If it is missing or rejected, guide the user to add it directly in the server console; never request the secret in chat.

## Default workflow: guided autopilot

Proceed automatically through these stages and send concise progress updates. Stop to ask only when there is a material ambiguity, missing authority, a cost/cap risk, or a failed safety check.

1. Upload the chosen CSV to the client folder on the server.
2. Run the no-cost dry run. Report raw rows, invalid rows, duplicates, unique usable contacts, expected request volume, daily-cap fit, and ETA.
3. Run a small live sample across 50 domains. Check catch-all rate, transient/error rate, API connection behavior, and sample verified yield. Catch-all domains are normal and must not be send-ready.
4. If the sample is operationally healthy, start the full run in a detached server session. Use unbuffered logging and a finalizer that packages the outputs after successful completion.
5. Monitor checkpoint progress with the engine’s read-only `--status` command or `results.jsonl`, not merely the existence of a tmux session.
6. Resume from `results.jsonl` after interruptions. Treat HTTP 408, 429, 5xx, and network failures as transient timeouts; do not discard completed work.
7. On completion, validate output row counts, download `enriched_all.csv` and `verified_send_ready.csv`, package the server output, and report the aggregate outcome.
8. Record aggregate-only metrics in the project’s `RUN_HISTORY.md`. Recalculate its cumulative proof point and update the matching `README.md` headline after every completed full run. Count processed input rows, verified send-ready addresses, and API requests across all recorded runs; retain each run’s separate unique-usable and duplicate rates. Rotate any API key that was exposed outside the server.

## Output rules

- `verified_send_ready.csv` contains only verified addresses. Never include `catchall_guess` rows.
- `enriched_all.csv` must preserve every input row with a status.
- Keep client data, keys, and private deployment keys out of git and chat.
- Use the latest comparable run’s measured conversion rates for future list-size forecasts. Read `references/benchmark.md` when planning a similar B2B software list.

## User experience

Accept short triggers such as:

```text
New file, let's enrich
/enrich
Start work on NSA
```

When the user has not attached anything, begin with the single next action: ask them to attach the CSV. Do not make them supply a server runbook, commands, or a multi-step prompt.
