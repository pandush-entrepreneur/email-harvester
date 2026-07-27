---
name: email-permutation-engine
description: "Build and run a domain-first email permutation and verification engine that turns a contact list (first name, last name, company domain) into verified work emails using the MailTester.Ninja API. Use this skill whenever the user wants to enrich a lead list with emails, guess or permutate email addresses, crack email patterns for a domain, verify emails in bulk, or process a Clay/Apollo/AI Ark export that has names and domains but no email addresses. Also use when the user mentions catch-all domains, email pattern detection, or bulk verification throttling."
---

# Email Permutation & Verification Engine

Turn a list of `first name + last name + company domain` into verified work emails.

Core principle: **an email pattern is a property of the domain, not the person.** Solve the pattern once per domain, then apply it to every other contact at that domain. Never brute-force every pattern against every contact.

This skill is list-agnostic. It works on any CSV with the required columns.

---

## Input Contract

Required columns (map user's headers to these internally):

| Internal field | Typical source header |
|---|---|
| `first_name` | First Name |
| `last_name` | Last Name |
| `domain` | Company Domain |

Optional, carried through to output untouched: company name, job title, location, LinkedIn URL, any other column.

Reject rows missing `first_name` or `domain`. Rows missing only `last_name` are **kept** and handled as `first_name_only` (see Stage 0, rule 7). Rejected rows still appear in the master output with status `input_invalid`. Do not silently drop rows.

---

## Hard Constraints (do not violate)

### API

```
GET https://happy.mailtester.ninja/ninja?email={email}&key={key}
```

Response:

```json
{"email":"john.doe@email.com","user":"John Doe","domain":"email.com",
 "mx":"mx.sender-email.com","code":"ok","message":"Accepted","connections":1}
```

`code` values:

| code | meaning |
|---|---|
| `ok` | valid |
| `ko` | invalid |
| `mb` | unverifiable |

`message` values: `Accepted`, `Limited`, `Rejected`, `Catch-All`, `No Mx`, `Mx Error`, `Timeout`, `SPAM Block`

### Rate limit

| Plan | Rate | Interval | Daily cap |
|---|---|---|---|
| Pro | 11 req / 10s | 865ms | 100k |
| **Ultimate** | **57 req / 10s** | **170ms** | **500k** |

Purchased quantity multiplies both the rate and the cap (Ultimate x QTY 2 = 1M/day). Confirm which plan and quantity is in use before sizing a run, and make the interval a config value rather than a hardcoded constant.

Rules:

- **Pace by interval, not by additive sleep.** A request takes roughly 500 to 600ms round trip. Sleeping the full interval *after* each request yields interval + latency, which on Ultimate caps you near 13 req/10s against an allowance of 57. Schedule each request at `previous_start + interval` and sleep only the remainder.
- **Sequential is only sufficient on Pro.** At 865ms the interval exceeds the latency, so one worker saturates the allowance. On Ultimate the target interval (175ms) is shorter than the latency, so reaching the paid rate requires roughly 3 to 4 concurrent workers sharing one global rate limiter. Start at 2 workers, confirm `connections` stays within expectations, then step up. Never exceed 4 without evidence from the provider about permitted concurrency.
- Keep the rate limiter global and in one place so no code path can bypass it, regardless of worker count.

### Never hammer a single mail server

Verification probes hit the **prospect's** mail server, not just the API. Running a full pattern ladder against one domain back to back sends up to 11 SMTP probes to the same host within seconds, which triggers greylisting and spam protection. The symptom is an elevated rate of `SPAM Block`, `Mx Error` and `Timeout` that looks like an API problem but is not.

Order the work **stage-major and round-based**, never domain-major:

1. Run all Stage 1 catch-all probes across every domain first. One request per domain, naturally interleaved.
2. Run Stage 2 as ranked rounds: pattern rank 1 against every unsolved domain, then rank 2 against those still unsolved, and so on. Consecutive requests then always hit different mail servers, and the ladder terminates globally as early as possible.
3. Run Stage 3 propagation shuffled across domains rather than grouped by domain.

Never send two consecutive requests to the same domain. If a queue ordering would do so, rotate to the next domain instead.

Measured effect of getting this wrong: 28% transient responses on a domain-major run versus roughly 3% on interleaved traffic.
- The `connections` field reports simultaneous connections on the key. A reading above 1 usually means something else is using the same key, such as a browser tab running the web verifier or finder. Do not halt on a single reading. Pause 30 seconds, then retry. Halt only if `connections` stays above 1 across **5 consecutive** requests, and report the offending value so the user can close the other client.
- Some responses carry undocumented `rate` and `limit` fields. The published schema is only `email`, `user`, `domain`, `mx`, `code`, `message`, `connections`. Treat anything else as informational. In particular, a `rate` that disagrees with the plan's expected value may reflect a base plan rate under a quantity multiplier rather than the real ceiling. Never change the throttle automatically on the strength of an undocumented field. Report the discrepancy and ask.
- Keep the throttle in exactly one place in the code so no path can bypass it.

### Classification of results

Treat these three groups differently. This is where naive implementations go wrong:

| message | treatment |
|---|---|
| `Accepted` | valid, pattern confirmed |
| `Rejected` | invalid, pattern ruled out |
| `Catch-All` | **unconfirmable**, quarantine the domain |
| `Limited`, `Timeout`, `SPAM Block`, `Mx Error` | **transient, retry later** |
| `No Mx` | domain has no mail server, dead, skip all its contacts |

`Timeout` and `SPAM Block` do not mean the address is bad. Never write them into the invalid bucket. Push them to a retry queue and re-run at the end of the pass, up to 2 retries with backoff.

Treat HTTP 408, 429, and 5xx responses plus network errors as transient `Timeout` results too. Persist the checkpoint, then let the retry queue handle them. A temporary provider 502 must never terminate an otherwise resumable run.

### Learn from completed runs

Save aggregate-only results to `RUN_HISTORY.md` after every full run: raw rows, invalid rows, duplicate contacts, unique usable contacts, status counts, request count, catch-all rate, and verified yield. Never place lead rows, outputs, or secrets in the history.

Use the latest comparable run to size future pulls:

```
raw leads needed = target verified / verified_from_raw
unique usable leads needed = target verified / verified_from_unique_usable
```

The TRI run (175,204 raw rows, 33,512 verified) established a 19.13% raw-to-verified and 32.93% unique-usable-to-verified benchmark. It also had a 39.57% duplicate-row rate; dedupe upstream before predicting paid verification volume.

---

## Pipeline

Run these stages in order. Each stage writes its own checkpoint file.

### Stage 0: Load and normalize

**Deduplication is at contact level, and never deletes a row.**

The dedup key is `(lower(first_name), lower(last_name), lower(domain))`. Multiple contacts sharing a domain are **not** duplicates and must all be retained. A domain with 50 people keeps all 50.

Do not use LinkedIn URL as the dedup key. The same person frequently holds two LinkedIn accounts (a vanity slug and an auto-generated one, e.g. `david-schloss-2a0654` and `dave-schloss-2a0654`), so URL-based dedup detects nothing while name+domain correctly catches them.

The rationale is that an email address is a pure function of first name, last name, and domain. Two rows sharing all three resolve to the identical address under every pattern, so they cannot be mailed separately even if they are genuinely different people.

Handling: keep **every** row in the output. Designate the first occurrence as primary. For each subsequent occurrence:

- Set `status` to `duplicate_contact` and populate a `duplicate_of` column with the primary row's LinkedIn URL.
- Copy the primary's `email`, `pattern_used`, `mt_code`, `mt_message` and `mx` across once resolved.
- Spend **zero** additional API requests on it.
- Exclude it from `verified_send_ready.csv` so the address is mailed once.

This preserves contact-level integrity, including both LinkedIn URLs, while guaranteeing one send per address.

Normalize names before permutating. Skipping this silently destroys match rate:

1. **Transliterate Unicode to ASCII.** `Cindrić` becomes `cindric`, `Sönmez` becomes `sonmez`, `Orléando` becomes `orleando`. Use `unicodedata.normalize('NFKD', s)` then strip combining marks, or the `unidecode` library.
2. **Lowercase everything.**
3. **Strip suffixes** from last name: `PhD`, `MBA`, `Jr`, `Sr`, `II`, `III`, `CPA`, `MSc`.
4. **Strip punctuation** except internal hyphens and apostrophes, then remove apostrophes (`O'Brien` becomes `obrien`).
5. **Multi-token last names** (`van der Berg`): generate two variants, the joined form (`vandenberg`) and the final token only (`berg`). Try both.
6. **Hyphenated last names** (`Bélisle-Dockrill`): generate four variants, the collapsed form (`belisledockrill`), the hyphen-preserved form (`belisle-dockrill`), and each half alone (`belisle`, `dockrill`). Holders of double-barrelled surnames frequently use only one half internally.
7. **Truncated surnames.** Do **not** discard a row because the last name is a single character, an initial, or empty, provided the first name and domain are valid. These are LinkedIn privacy-truncated surnames (`Jonathan K.`), not bad data, and they are numerous. On the reference list they account for roughly 1,780 rows, about 5.8% of contacts, skewed toward founders at small companies.

   Mark them `first_name_only` and handle them as follows:
   - Two patterns need no surname: `first` needs only the first name, and `firstl` needs the first name plus the last initial, which is precisely what a truncated surname gives you. Use the initial where present.
   - Never select a `first_name_only` contact as a Stage 2 probe contact.
   - If their domain's confirmed pattern is `first` or `firstl`, enrich them normally in Stage 3.
   - If the domain's pattern requires a full surname, set status `unresolvable_pattern`.
   - If they are alone on their domain, run a two-probe mini-ladder of `first` and `firstl` only, rather than the full ladder.

   Budget impact on the reference list is under 2,500 requests, negligible against Ultimate headroom.
8. **Initials-only first names** (`Clément M.`): flag as low confidence, still attempt.
9. Drop a row only when the **first name** is empty or unusable after cleaning, or the domain is missing. A missing surname alone is not grounds for dropping.

Also normalize the domain: lowercase, strip `www.`, strip protocol and any path.

### Stage 1: Domain-level pre-checks

Group all contacts by domain. For each unique domain, run **one** request:

Probe a random non-existent mailbox, e.g. `kq7x2mwp9z@{domain}`.

| Probe result | Domain status | Action |
|---|---|---|
| `Rejected` | normal | proceed to Stage 2 |
| `Accepted` or `Catch-All` | **catch-all** | quarantine, skip Stage 2 |
| `No Mx` / `Mx Error` | dead | skip all contacts on this domain |
| transient | retry | requeue |

This stage is non-negotiable and must run before any pattern probing.

**Expect a high catch-all rate.** Measured on a 50-domain live sample of small US/UK/CA B2B SaaS: **46% of domains were catch-all**, producing only ~34% verified contacts, with ~50% landing in `catchall_guess`. Most ran Google Workspace (`aspmx.l.google.com`).

Plan yield accordingly: roughly one third of any list of this type will be safely sendable. Tell the user this figure before Stage 3 rather than after, and never present the catch-all bucket as part of the deliverable headline count.

High catch-all rates make the run cheaper, not more expensive, since those domains exit after a single probe and skip the pattern ladder entirely.

Catch-all domains are not failures. They go to their own output bucket, marked unconfirmed, with the highest-probability pattern guess attached for optional separate handling.

### Stage 2: Pattern detection (one probe contact per domain)

For each non-catch-all domain, select **one** probe contact. Prefer the contact with the cleanest name: simple ASCII, single-token last name, no initials, length of both names 3 or more.

Test patterns against that one contact in the ranked order below. **Stop at the first `Accepted`.** Early exit is what keeps the request count down.

| Rank | Pattern | Example (John Doe, acme.com) |
|---|---|---|
| 1 | `first.last` | john.doe@acme.com |
| 2 | `first` | john@acme.com |
| 3 | `firstlast` | johndoe@acme.com |
| 4 | `flast` | jdoe@acme.com |
| 5 | `first_last` | john_doe@acme.com |
| 6 | `firstl` | johnd@acme.com |
| 7 | `f.last` | j.doe@acme.com |
| 8 | `first-last` | john-doe@acme.com |
| 9 | `lastfirst` | doejohn@acme.com |
| 10 | `last.first` | doe.john@acme.com |
| 11 | `last` | doe@acme.com |

Ranks 8 to 11 are rare. Include them only when running on Ultimate, where the request budget is not the binding constraint. On Pro, stop the ladder at rank 7.

**Second-probe fallback.** If all patterns fail for the probe contact and the domain has 2 or more contacts, run the full ladder again against a second contact before giving up. The most common cause of a fully failed ladder is that the probe person has left the company, not that the domain is unusual. This roughly halves the `pattern_unknown` rate for the cost of one extra ladder on a small subset of domains. Cap it at 2 probe contacts per domain.

Only after both probe contacts fail, mark the domain `pattern_unknown`.

Record the winning pattern against the domain in a `domain_patterns` checkpoint table.

### Stage 3: Propagation

For every remaining contact on a domain with a confirmed pattern, construct the single address implied by that pattern and verify it. One request per contact, not seven.

A confirmed pattern that fails for a specific person usually means that person has left or the name is spelled differently internally. Mark the row `pattern_known_person_invalid`. Do not re-probe the whole ladder.

### Stage 4: Retry queue

**Never retry inline.** When a transient message comes back, record it, append it to the retry queue, and move to the next request immediately. A worker must never sleep on backoff while holding a slot.

Inline retries are expensive and largely futile. A `SPAM Block` means the server refuses verification probes as policy, so an attempt two seconds later fails identically. Measured cost of getting this wrong: retry backoff consumed roughly a third of total worker capacity on a 3-worker run.

Run the queue as a separate pass after Stage 3 completes, with the same throttle and interleaving. Up to 2 attempts. Anything still transient becomes `check_failed`.

**Expected transient rates.** These have a floor set by which mail providers the prospects use, not by request pattern:

| Message | Typical | Reducible? |
|---|---|---|
| `Mx Error` | under 1% | Yes. Elevated rates mean domain-major ordering. |
| `SPAM Block` | ~10% | No. Provider policy, commonly Microsoft 365. |
| `Timeout` | ~4% | Partly. Slow or overloaded servers. |

A total transient rate around 15% is normal and healthy on interleaved traffic. Do not treat it as a failure or block a run on it. Only an elevated `Mx Error` rate indicates a fixable defect.

Contacts that finish as `check_failed` are strong candidates for the `website-email-harvester` route, since a server that blocks SMTP probes will keep blocking them.

### Stage 5: Output

The primary deliverable is **one CSV that mirrors the input file**, same rows, same columns, same order, with email columns appended. Do not fragment the deliverable across multiple files.

**`enriched_all.csv`** — every input row, including skipped and failed ones. All original columns preserved unchanged, then appended:

| Column | Contents |
|---|---|
| `email` | The verified address, or empty |
| `status` | See status values below |
| `pattern_used` | e.g. `first.last`, or empty |
| `mt_code` | `ok` / `ko` / `mb` |
| `mt_message` | `Accepted`, `Catch-All`, `Rejected`, etc. |
| `mx` | Mail server |
| `confidence` | `verified` / `unconfirmed` / `none` |
| `duplicate_of` | LinkedIn URL of the primary row, if this is a duplicate contact |
| `checked_at` | ISO timestamp |

Row count in `enriched_all.csv` must equal row count in the input file exactly. No row is ever removed, only labelled.

`status` values:

| status | confidence | meaning |
|---|---|---|
| `verified` | verified | Accepted by the mail server. Safe to send. |
| `catchall_guess` | unconfirmed | Domain accepts everything. Pattern is a guess. **Not safe to send.** |
| `pattern_unknown` | none | No pattern cracked for this domain. |
| `person_invalid` | none | Domain pattern known, this person's address rejected. |
| `unresolvable_pattern` | none | First-name-only contact on a domain needing a full surname. |
| `dead_domain` | none | No MX record. |
| `check_failed` | none | Transient errors (Timeout, SPAM Block, Limited) survived all retries. The address was never actually tested. Retriable in a later run. Do **not** record as `pattern_unknown` or `person_invalid`. |
| `input_invalid` | none | Missing or unusable input fields. |
| `duplicate_contact` | inherited | Same first name, last name and domain as an earlier row, so the same address. Row retained, result copied from the primary, mailed once. |

**`verified_send_ready.csv`** — filtered subset where `status = verified`, original columns plus `email` only. This is the file that goes into Instantly or Clay.

Keep both. The master file lets you audit, re-slice, and re-run later without touching the API again. The send-ready file is the one that touches a sending tool.

Never merge `catchall_guess` rows into the send-ready file, even though they will often look like plausible addresses. See the deliverability note below.

---

## Checkpointing and Resume

A full run takes hours even on Ultimate. Build resume support from the start, not as a later addition. It also makes iteration cheap: you can stop a run, fix a bug, and restart without re-spending requests.

- Append every single API result to a `results.jsonl` file immediately after the call returns. One line per request. Never buffer in memory and write at the end.
- On startup, read `results.jsonl` and build a set of already-checked addresses. Skip anything present.
- Persist the `domain_patterns` table separately so a restart does not re-probe solved domains.
- Track daily quota usage in a counter keyed by date. Stop cleanly when the daily cap is hit, print a resume instruction, and exit 0.
- Log progress every 100 requests: count, elapsed, requests remaining, projected finish time.

---

## Request Budget Math

Report this estimate to the user before starting a run.

For a list of `C` contacts across `D` unique domains, assuming roughly 20% catch-all and average 3.5 pattern probes before hitting:

```
Stage 1:  D                          catch-all probes
Stage 2:  D * 0.8 * 3.5              pattern probes
Stage 3:  (C - D) * 0.8              propagation
```

Worked example, the reference list this skill was built against: 32,563 usable contacts across 15,173 domains.

```
Stage 1:  15,173      catch-all probes
Stage 2:  ~42,500     pattern probes
Stage 3:  ~13,900     propagation
Total:    ~71,600 requests
```

| Plan | Runtime | Daily cap | Headroom |
|---|---|---|---|
| Pro (900ms) | ~17.9 h | 100k | 1.4x |
| Ultimate (180ms) | **~3.6 h** | 500k | 7.0x |

Add the second-probe fallback and the extended ladder and the realistic Ultimate figure is roughly 85k to 100k requests, still around 4 to 5 hours and still well inside a single day's cap.

Naive brute force (7 patterns x every contact) is ~228,000 requests: 57 hours on Pro, 11.4 hours on Ultimate. It fits in one Ultimate day, so on Ultimate the argument for domain-first is **accuracy**, not cost. A pattern confirmed once at the domain level and propagated is more reliable than seven independent guesses per person, and it produces a `pattern_used` field you can audit. Use domain-first regardless of plan.

---

## Failure Modes to Guard Against

1. **Skipping the catch-all probe.** Produces a large, confident, worthless list. The most expensive mistake available.
2. **Parallelizing without a global rate limiter.** Trips the API limit and risks the key. Concurrency is permitted on Ultimate, but only behind one shared limiter.
3. **Running domain-major.** Sending a whole pattern ladder to one mail server back to back triggers greylisting and spam defences on the prospect's server. Looks like an API fault, is not. Always interleave.
3. **Treating `Timeout` or `SPAM Block` as invalid.** Throws away good contacts.
4. **Not transliterating accented names.** Silent match-rate loss on every non-ASCII name.
5. **Buffering results in memory.** One crash at hour 14 loses the whole run.
6. **Probing the full ladder on every contact.** Triples the request count for no accuracy gain, and produces unauditable results. Propagate a confirmed domain pattern instead.
7. **Board members and advisors.** Contacts whose title contains `Board Member`, `Advisor`, `Investor`, or `Mentor` usually do not have a mailbox at the company domain. Never select one as a Stage 2 probe contact, since a failed ladder on an advisor wrongly condemns the whole domain. Enrich them normally in Stage 3 and expect a high failure rate. On Ultimate there is budget to attempt them fully; on Pro, defer them to a final low-priority pass.

---

## Deliverability Note

Pattern-derived addresses are lower quality than provider-sourced data even after verification. `verified_send_ready.csv` is a sending list. Rows with status `catchall_guess` are not, and must never be filtered into a sending list without separate confirmation, because the verifier cannot distinguish a real mailbox from a wildcard on those domains. Sending to unconfirmed catch-alls is one of the fastest ways to damage domain reputation.

If catch-all volume is high and those companies matter, confirm them out of band: check the company site or contact page for a published address, or verify a known individual by another route. Do not resolve the ambiguity by guessing and sending.

---

## Implementation Notes

- Python 3 with `requests` is sufficient. No framework needed.
- Single file is fine. Keep the throttle in one place so it cannot be bypassed.
- Read the API key from an environment variable, never hardcode it.
- Use the key without surrounding braces.
- Add `--dry-run` that runs Stages 0 and the budget math only, emitting the request estimate and a sample of generated permutations without calling the API. Always run this first and show the user the estimate before committing to a multi-hour run.
