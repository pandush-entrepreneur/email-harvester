import argparse
import csv
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


REQUIRED_COLUMNS = {
    "first_name": "First Name",
    "last_name": "Last Name",
    "domain": "Company Domain",
}

ENRICHED_APPEND_COLUMNS = [
    "email",
    "status",
    "pattern_used",
    "mt_code",
    "mt_message",
    "mx",
    "confidence",
    "duplicate_of",
    "checked_at",
]

PATTERN_ORDER = [
    "first.last",
    "first",
    "firstlast",
    "flast",
    "first_last",
    "firstl",
    "f.last",
    "first-last",
    "lastfirst",
    "last.first",
    "last",
]

TRANSIENT_MESSAGES = {"Limited", "Timeout", "SPAM Block", "Mx Error", "Greylisted", "Disposable"}
DEAD_MESSAGES = {"No Mx"}
KNOWN_MESSAGES = {"Accepted", "Rejected", "Catch-All", "No Mx"} | TRANSIENT_MESSAGES
API_URL = "https://happy.mailtester.ninja/ninja"
SPECIAL_TRANSLITERATION = str.maketrans(
    {
        "\u0111": "d",
        "\u0110": "D",
        "\u00f0": "d",
        "\u00d0": "D",
        "\u00f8": "o",
        "\u00d8": "O",
        "\u0142": "l",
        "\u0141": "L",
        "\u00fe": "th",
        "\u00de": "Th",
        "\u00df": "ss",
        "\u00e6": "ae",
        "\u00c6": "Ae",
        "\u0153": "oe",
        "\u0152": "Oe",
        "\u0131": "i",
        "\u0130": "I",
    }
)


def repair_mojibake(value):
    if not value:
        return ""
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except UnicodeError:
        return value
    original_badness = sum(1 for char in value if char in "\u00c3\u00c4\u00c2\u00c5\u00ca\u00cb\u00d0\u00d8\u00e2")
    repaired_badness = sum(1 for char in repaired if char in "\u00c3\u00c4\u00c2\u00c5\u00ca\u00cb\u00d0\u00d8\u00e2")
    return repaired if original_badness and repaired_badness <= original_badness else value


def canonical_message(message):
    if message == "No MX":
        return "No Mx"
    if message == "MX Error":
        return "Mx Error"
    return message


def transliterate(value):
    repaired = repair_mojibake((value or "").strip()).translate(SPECIAL_TRANSLITERATION)
    normalized = unicodedata.normalize("NFKD", repaired)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def strip_last_suffixes(value):
    suffix_pattern = r"(?:ph\.?d\.?|m\.?sc\.?|mba|jr|sr|ii|iii|cpa)"
    while re.search(rf"(?:[\s,]+|^){suffix_pattern}\s*$", value):
        value = re.sub(rf"(?:[\s,]+|^){suffix_pattern}\s*$", "", value).strip()
    return value


def clean_name_text(value, is_last_name=False, preserve_hyphen=False):
    cleaned = transliterate(value).lower()
    if is_last_name:
        cleaned = strip_last_suffixes(cleaned)
    cleaned = re.sub(r"[\u2019`]", "'", cleaned).replace("'", "")
    allowed = r"[^a-z0-9\-\s]" if preserve_hyphen else r"[^a-z0-9\s]"
    cleaned = re.sub(allowed, " ", cleaned)
    return re.sub(r"\s+", " ", cleaned.strip())


def normalize_first_name(value):
    return clean_name_text(value).replace(" ", "")


def normalize_last_name_variants(value):
    cleaned = clean_name_text(value, is_last_name=True, preserve_hyphen=True)
    if not cleaned:
        return [], True

    parts = [part for part in re.split(r"[\s-]+", cleaned) if part]
    if not parts:
        return [], True

    collapsed_for_length = "".join(parts)
    first_name_only = len(collapsed_for_length) <= 1
    if first_name_only:
        return [collapsed_for_length[0]], True

    variants = []
    if "-" in cleaned:
        variants.append("".join(parts))
        variants.append("-".join(parts))
        variants.extend(parts)
    else:
        tokens = [token.replace("-", "") for token in cleaned.split() if token.replace("-", "")]
        joined = "".join(tokens)
        variants.append(joined)
        if len(tokens) > 1:
            variants.append(tokens[-1])
    return list(dict.fromkeys(variant for variant in variants if variant)), False


def normalize_domain(value):
    domain = (value or "").strip().lower()
    domain = re.sub(r"^[a-z][a-z0-9+\-.]*://", "", domain)
    domain = domain.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    domain = domain.strip().strip(".")
    return domain[4:] if domain.startswith("www.") else domain


def last_initial(contact):
    variants = contact.get("last_name_variants") or []
    return variants[0][0] if variants else ""


def candidate_email(first, last, initial, domain, pattern):
    if pattern == "first.last":
        return f"{first}.{last}@{domain}" if last else None
    if pattern == "first":
        return f"{first}@{domain}"
    if pattern == "firstlast":
        return f"{first}{last}@{domain}" if last else None
    if pattern == "flast":
        return f"{first[0]}{last}@{domain}" if last else None
    if pattern == "first_last":
        return f"{first}_{last}@{domain}" if last else None
    if pattern == "firstl":
        return f"{first}{initial}@{domain}" if initial else None
    if pattern == "f.last":
        return f"{first[0]}.{last}@{domain}" if last else None
    if pattern == "first-last":
        return f"{first}-{last}@{domain}" if last else None
    if pattern == "lastfirst":
        return f"{last}{first}@{domain}" if last else None
    if pattern == "last.first":
        return f"{last}.{first}@{domain}" if last else None
    if pattern == "last":
        return f"{last}@{domain}" if last else None
    raise ValueError(f"Unknown pattern: {pattern}")


def candidate_emails_for_pattern(contact, pattern):
    if contact["first_name_only"] and pattern not in {"first", "firstl"}:
        return []
    emails = []
    variants = contact["last_name_variants"] or [""]
    for last in variants:
        email = candidate_email(
            contact["first_name"],
            last,
            last_initial(contact),
            contact["domain"],
            pattern,
        )
        if email:
            emails.append(email)
    return list(dict.fromkeys(emails))


def best_guess_for_contact(contact):
    if contact["first_name_only"]:
        emails = candidate_emails_for_pattern(contact, "firstl") or candidate_emails_for_pattern(contact, "first")
        return emails[0] if emails else f"{contact['first_name']}@{contact['domain']}", "firstl" if last_initial(contact) else "first"
    return candidate_emails_for_pattern(contact, "first.last")[0], "first.last"


def load_csv_rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    missing = [source for source in REQUIRED_COLUMNS.values() if source not in fieldnames]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")
    return rows, fieldnames


def output_fieldnames(fieldnames, rows):
    if fieldnames and all((row.get(fieldnames[0]) or "") == "" for row in rows):
        return fieldnames[1:]
    return fieldnames


def filter_original(row, out_fields):
    return {field: row.get(field, "") for field in out_fields}


def stage0_load_normalize(input_path, output_dir):
    rows, fieldnames = load_csv_rows(input_path)
    out_fields = output_fieldnames(fieldnames, rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    usable = []
    skipped = []
    duplicates = []
    seen = set()
    primary_by_key = {}

    for input_index, row in enumerate(rows):
        first_raw = row.get(REQUIRED_COLUMNS["first_name"], "")
        last_raw = row.get(REQUIRED_COLUMNS["last_name"], "")
        domain_raw = row.get(REQUIRED_COLUMNS["domain"], "")
        missing = []
        if not first_raw or not first_raw.strip():
            missing.append("missing_first_name")
        if not domain_raw or not domain_raw.strip():
            missing.append("missing_domain")
        if missing:
            skipped.append({"input_index": input_index, "skip_reason": "+".join(missing), "original": row})
            continue

        first = normalize_first_name(first_raw)
        last_variants, first_name_only = normalize_last_name_variants(last_raw)
        domain = normalize_domain(domain_raw)
        unusable = []
        if len(first) <= 1:
            unusable.append("unusable_first_name")
        if not domain:
            unusable.append("unusable_domain")
        if unusable:
            skipped.append({"input_index": input_index, "skip_reason": "+".join(unusable), "original": row})
            continue

        primary_last = last_variants[0] if last_variants else ""
        key = (first.lower(), primary_last.lower(), domain.lower())
        if key in seen:
            primary = primary_by_key[key]
            duplicates.append(
                {
                    "input_index": input_index,
                    "primary_input_index": primary["input_index"],
                    "duplicate_of": primary["original"].get("LinkedIn Profile", ""),
                    "original": row,
                    "first_name": first,
                    "last_name_variants": last_variants,
                    "domain": domain,
                    "first_name_only": first_name_only,
                }
            )
            continue
        seen.add(key)

        contact = {
            "input_index": input_index,
            "row_number": input_index + 2,
            "original": row,
            "first_name": first,
            "last_name_variants": last_variants,
            "domain": domain,
            "first_name_only": first_name_only,
            "low_confidence_name": first_name_only,
            "has_accent_or_mojibake": has_accent_or_mojibake(first_raw, last_raw),
            "has_multi_token_last_name": " " in clean_name_text(last_raw, True, False),
            "has_hyphenated_last_name": "-" in transliterate(last_raw),
        }
        primary_by_key[key] = contact
        usable.append(contact)

    write_stage0_checkpoint(output_dir, usable)
    write_resume_files(output_dir)
    return rows, fieldnames, out_fields, usable, skipped, duplicates


def has_accent_or_mojibake(*values):
    joined = " ".join(value or "" for value in values)
    repaired = repair_mojibake(joined)
    return any(ord(char) > 127 for char in joined) or repaired != joined


def write_stage0_checkpoint(output_dir, usable):
    with (output_dir / "stage0_contacts.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for contact in usable:
            handle.write(json.dumps(contact, ensure_ascii=True) + "\n")


def write_resume_files(output_dir):
    defaults = {
        "results.jsonl": "",
        "domain_patterns.csv": "domain,pattern,domain_status,confidence,checked_at\n",
        "daily_usage.json": "{}\n",
    }
    for filename, content in defaults.items():
        path = output_dir / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def contacts_per_domain_distribution(usable):
    counts = Counter(contact["domain"] for contact in usable)
    return counts, Counter(counts.values())


def budget_math(contact_count, domain_count, interval_ms):
    stage1 = domain_count
    stage2 = domain_count * 0.8 * 3.5
    stage3 = max(contact_count - domain_count, 0) * 0.8
    total = stage1 + stage2 + stage3
    realistic_low = max(total, domain_count + (domain_count * 0.8 * 5.0) + stage3)
    realistic_high = max(total, domain_count + (domain_count * 0.8 * 6.5) + stage3)
    return {
        "stage1": stage1,
        "stage2": stage2,
        "stage3": stage3,
        "total": total,
        "realistic_low": realistic_low,
        "realistic_high": realistic_high,
        "runtime_seconds": total * interval_ms / 1000,
        "realistic_low_seconds": realistic_low * interval_ms / 1000,
        "realistic_high_seconds": realistic_high * interval_ms / 1000,
    }


def format_duration(seconds):
    return str(timedelta(seconds=round(seconds)))


def sample_normalized_names(usable, limit=15):
    buckets = [
        ([c for c in usable if c["has_hyphenated_last_name"]], 5),
        ([c for c in usable if c["first_name_only"]], 5),
        ([c for c in usable if c["has_accent_or_mojibake"] and not c["first_name_only"] and not c["has_hyphenated_last_name"]], 5),
    ]
    selected = []
    seen = set()
    for bucket, count in buckets:
        for contact in bucket[:count]:
            if contact["input_index"] not in seen:
                selected.append(contact)
                seen.add(contact["input_index"])
    for contact in usable:
        if len(selected) >= limit:
            break
        if contact["input_index"] not in seen:
            selected.append(contact)
    return selected[:limit]


def write_enriched_all(path, out_fields, rows_to_write):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=out_fields + ENRICHED_APPEND_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows_to_write)


def write_verified_send_ready(path, out_fields, enriched_rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=out_fields + ["email"], extrasaction="ignore")
        writer.writeheader()
        for row in enriched_rows:
            if row.get("status") == "verified" and not row.get("duplicate_of"):
                writer.writerow({**{field: row.get(field, "") for field in out_fields}, "email": row["email"]})


def write_dry_outputs(output_dir, out_fields, rows, skipped):
    skipped_by_index = {item["input_index"]: item for item in skipped}
    enriched_rows = []
    for index, row in enumerate(rows):
        output_row = filter_original(row, out_fields)
        if index in skipped_by_index:
            reason = skipped_by_index[index]["skip_reason"]
            output_row.update(
                {
                    "email": "",
                    "status": "duplicate" if reason == "duplicate" else "input_invalid",
                    "pattern_used": "",
                    "mt_code": "",
                    "mt_message": reason,
                        "mx": "",
                        "confidence": "none",
                        "duplicate_of": "",
                        "checked_at": "",
                    }
                )
        else:
            output_row.update({column: "" for column in ENRICHED_APPEND_COLUMNS})
        enriched_rows.append(output_row)
    write_enriched_all(output_dir / "enriched_all.csv", out_fields, enriched_rows)
    write_verified_send_ready(output_dir / "verified_send_ready.csv", out_fields, enriched_rows)


def print_gate1_report(rows, out_fields, usable, skipped, output_dir, interval_ms, daily_cap):
    domain_counts, distribution = contacts_per_domain_distribution(usable)
    budget = budget_math(len(usable), len(domain_counts), interval_ms)
    skip_reasons = Counter(item["skip_reason"] for item in skipped)
    first_name_only = [contact for contact in usable if contact["first_name_only"]]
    first_name_only_with_peer = sum(1 for contact in first_name_only if domain_counts[contact["domain"]] > 1)

    if not usable:
        raise SystemExit(
            "Gate 1 stopped: no usable contacts were detected. "
            "Check the input columns before making any API calls."
        )

    print("GATE 1 DRY RUN")
    print(f"Total rows: {len(rows):,}")
    print(f"Usable rows: {len(usable):,}")
    print(f"Skipped rows: {len(skipped):,}")
    print("Rows skipped and why:")
    for reason, count in sorted(skip_reasons.items()):
        print(f"  {reason}: {count:,}")
    print(f"Unique domains: {len(domain_counts):,}")
    print(f"Rows classified first_name_only: {len(first_name_only):,}")
    print(f"first_name_only rows on domains with at least one other contact: {first_name_only_with_peer:,}")
    print("Contacts-per-domain distribution:")
    for contacts_per_domain, domains in sorted(distribution.items())[:20]:
        print(f"  {contacts_per_domain} contact(s): {domains:,} domain(s)")
    if len(distribution) > 20:
        print(f"  ... {len(distribution) - 20} more bucket(s)")
    print("Projected request count:")
    print(f"  Stage 1 catch-all probes: {budget['stage1']:,.0f}")
    print(f"  Stage 2 pattern probes: {budget['stage2']:,.0f}")
    print(f"  Stage 3 propagation: {budget['stage3']:,.0f}")
    print(f"  Total estimate: {budget['total']:,.0f}")
    print(f"  Ultimate extended ladder / second-probe planning range: {budget['realistic_low']:,.0f}-{budget['realistic_high']:,.0f}")
    print(f"Estimated runtime at {interval_ms}ms/request:")
    print(f"  Formula estimate: {format_duration(budget['runtime_seconds'])}")
    print(f"  Ultimate planning range: {format_duration(budget['realistic_low_seconds'])}-{format_duration(budget['realistic_high_seconds'])}")
    print(f"Daily cap: {daily_cap:,}")
    print(f"Estimated cap headroom: {daily_cap / budget['total']:.1f}x")
    print("15 sample normalized names:")
    for contact in sample_normalized_names(usable):
        mini = ""
        if contact["first_name_only"]:
            ladder = candidate_emails_for_pattern(contact, "first") + candidate_emails_for_pattern(contact, "firstl")
            mini = f", first_name_only=true, mini_ladder={'|'.join(dict.fromkeys(ladder))}"
        original = contact["original"]
        print(
            f"  {original.get('First Name', '')} {original.get('Last Name', '')}"
            f" -> first={contact['first_name']}, variants={'|'.join(contact['last_name_variants'])}, domain={contact['domain']}{mini}"
        )
    print("enriched_all.csv header:")
    print(read_header_line(output_dir / "enriched_all.csv"))


def read_header_line(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.readline().rstrip("\r\n")


def print_status(output_dir):
    """Print checkpoint progress without modifying files or calling the API."""
    results_path = output_dir / "results.jsonl"
    usage_path = output_dir / "daily_usage.json"
    result_count = 0
    if results_path.exists():
        with results_path.open("r", encoding="utf-8") as handle:
            result_count = sum(1 for line in handle if line.strip())
    usage = {}
    if usage_path.exists():
        try:
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            usage = {}
    print("CHECKPOINT STATUS")
    print(f"Output directory: {output_dir}")
    print(f"Recorded API results: {result_count:,}")
    print(f"API results today: {int(usage.get(date.today().isoformat(), 0)):,}")
    for filename in ("enriched_all.csv", "verified_send_ready.csv", "domain_patterns.csv"):
        path = output_dir / filename
        if path.exists():
            print(f"{filename}: {count_csv_data_rows(path):,} rows")
        else:
            print(f"{filename}: not created yet")


def read_round_trip_rows(path, limit=3):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))[:limit]


def select_sample_domains(usable, domain_limit):
    by_domain = defaultdict(list)
    for contact in usable:
        by_domain[contact["domain"]].append(contact)

    selected = []

    def add(domains):
        for domain in domains:
            if domain not in selected and len(selected) < domain_limit:
                selected.append(domain)

    fno_domains = [d for d, contacts in by_domain.items() if any(c["first_name_only"] for c in contacts)]
    multi_domains = [d for d, contacts in by_domain.items() if len(contacts) >= 5]
    single_domains = [d for d, contacts in by_domain.items() if len(contacts) == 1]
    add(fno_domains[:8])
    add(multi_domains[:12])
    add(single_domains[:18])
    add(by_domain.keys())
    return selected[:domain_limit]


class MailTesterClient:
    def __init__(
        self,
        output_dir,
        interval_ms,
        daily_cap,
        progress_every=500,
        projected_total=None,
        allowed_connections=1,
    ):
        self.output_dir = output_dir
        self.interval_seconds = interval_ms / 1000
        self.daily_cap = daily_cap
        self.results_path = output_dir / "results.jsonl"
        self.usage_path = output_dir / "daily_usage.json"
        self.key = (os.environ.get("MAILTESTER_KEY") or "").strip().strip("{}")
        if not self.key:
            raise SystemExit("MAILTESTER_KEY is not set. No API calls were made.")
        self.history = self.load_history()
        self.usage = self.load_usage()
        self.requests_spent = 0
        self.started_at = time.monotonic()
        self.progress_every = progress_every
        self.projected_total = projected_total
        self.limiter = GlobalRateLimiter(interval_ms / 1000)
        self.io_lock = threading.Lock()
        self.connection_lock = threading.Lock()
        self.high_connection_streak = 0
        self.allowed_connections = max(1, allowed_connections)
        self.request_domains = []

    def load_history(self):
        history = defaultdict(list)
        if not self.results_path.exists():
            return history
        with self.results_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                result = json.loads(line)
                result["message"] = canonical_message(result.get("message", ""))
                if result.get("message") not in KNOWN_MESSAGES:
                    continue
                history[result["email"].lower()].append(result)
        return history

    def load_usage(self):
        if not self.usage_path.exists():
            return {}
        return json.loads(self.usage_path.read_text(encoding="utf-8") or "{}")

    def save_usage(self):
        self.usage_path.write_text(json.dumps(self.usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def latest(self, email):
        with self.io_lock:
            entries = list(self.history.get(email.lower(), []))
        return entries[-1] if entries else None

    def attempts(self, email):
        with self.io_lock:
            return len(self.history.get(email.lower(), []))

    def non_connection_attempts(self, email):
        with self.io_lock:
            return sum(1 for item in self.history.get(email.lower(), []) if not self.high_connection(item))

    def high_connection(self, result):
        try:
            return (
                result.get("connections") is not None
                and int(result.get("connections")) > self.allowed_connections
            )
        except (TypeError, ValueError):
            return False

    def cached_final(self, email):
        latest = self.latest(email)
        if not latest:
            return None
        if self.high_connection(latest):
            return None
        if latest.get("message") not in TRANSIENT_MESSAGES:
            return latest
        if self.non_connection_attempts(email) >= 3:
            return latest
        return None

    def request_once(self, email, purpose, domain=None):
        today = date.today().isoformat()
        with self.io_lock:
            used_today = int(self.usage.get(today, 0))
            if used_today >= self.daily_cap:
                print("Daily cap hit. Resume later with the same command; cached results will be skipped.")
                raise SystemExit(0)

        start_at = self.limiter.wait_turn(domain)
        if domain:
            with self.io_lock:
                self.request_domains.append(domain)
        query = urllib.parse.urlencode({"email": email, "key": self.key})
        try:
            with urllib.request.urlopen(f"{API_URL}?{query}", timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in {408, 429, 500, 502, 503, 504}:
                raise
            data = {"message": "Timeout", "code": f"http_{error.code}"}
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            data = {"message": "Timeout", "code": "network_error"}

        checked_at = datetime.now(timezone.utc).isoformat()
        message = canonical_message(data.get("message", ""))
        if message not in KNOWN_MESSAGES:
            error_path = self.output_dir / "api_errors.jsonl"
            with error_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(
                        {
                            "email": email,
                            "purpose": purpose,
                            "checked_at": checked_at,
                            "code": data.get("code", ""),
                            "message": message,
                            "raw": data,
                        },
                        ensure_ascii=True,
                    )
                    + "\n"
                )
            raise SystemExit(
                f"MailTester returned unexpected message={message!r}. "
                "Halting without caching it as a verification result."
            )
        result = {
            "email": email,
            "purpose": purpose,
            "checked_at": checked_at,
            "code": data.get("code", ""),
            "message": message,
            "mx": data.get("mx", ""),
            "connections": data.get("connections"),
            "raw": data,
        }
        with self.io_lock:
            with self.results_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(result, ensure_ascii=True) + "\n")
            self.history[email.lower()].append(result)
            self.usage[today] = int(self.usage.get(today, 0)) + 1
            self.save_usage()
            self.requests_spent += 1
            if self.requests_spent % self.progress_every == 0:
                elapsed = time.monotonic() - self.started_at
                projected = ""
                if self.projected_total and self.requests_spent:
                    finish_seconds = elapsed * (self.projected_total / self.requests_spent)
                    projected = f", ETA elapsed total={format_duration(finish_seconds)}"
                print(f"Progress: requests={self.requests_spent:,}, elapsed={format_duration(elapsed)}{projected}")
        return result

    def verify(self, email, purpose, domain=None):
        return self.check_once(email, purpose, domain)

    def check_once(self, email, purpose, domain):
        latest = self.latest(email)
        if latest and not self.high_connection(latest):
            return latest, False
        while True:
            result = self.request_once(email, purpose, domain=domain)
            if self.high_connection(result):
                with self.connection_lock:
                    self.high_connection_streak += 1
                    streak = self.high_connection_streak
                print(
                    f"connections={result.get('connections')} exceeded the configured worker count "
                    f"({self.allowed_connections}) for {email}; pausing 30s before retry ({streak}/5)."
                )
                if streak >= 5:
                    raise SystemExit(
                        "MailTester connections stayed above the configured worker count "
                        "for 5 consecutive readings. Halting."
                    )
                time.sleep(30)
                continue
            with self.connection_lock:
                self.high_connection_streak = 0
            return result, True

    def live_once(self, email, purpose, domain):
        while True:
            result = self.request_once(email, purpose, domain=domain)
            if self.high_connection(result):
                with self.connection_lock:
                    self.high_connection_streak += 1
                    streak = self.high_connection_streak
                print(
                    f"connections={result.get('connections')} exceeded the configured worker count "
                    f"({self.allowed_connections}) for {email}; pausing 30s before retry ({streak}/5)."
                )
                if streak >= 5:
                    raise SystemExit(
                        "MailTester connections stayed above the configured worker count "
                        "for 5 consecutive readings. Halting."
                    )
                time.sleep(30)
                continue
            with self.connection_lock:
                self.high_connection_streak = 0
            return result, True

    def valid_request_count(self):
        return sum(len(entries) for entries in self.history.values())


class GlobalRateLimiter:
    def __init__(self, interval_seconds):
        self.interval_seconds = interval_seconds
        self.lock = threading.Lock()
        self.next_start = None
        self.last_domain = None

    def wait_turn(self, domain=None):
        with self.lock:
            now = time.monotonic()
            if self.next_start is None:
                scheduled = now
            else:
                scheduled = self.next_start
                if domain and self.last_domain == domain:
                    scheduled = max(scheduled, now + self.interval_seconds)
                if scheduled > now:
                    time.sleep(scheduled - now)
            actual = time.monotonic()
            self.next_start = actual + self.interval_seconds
            self.last_domain = domain
            return actual


def clean_probe_score(contact):
    title = (contact["original"].get("Job Title", "") or "").lower()
    disallowed = any(term in title for term in ["board member", "advisor", "investor", "mentor"])
    simple_last = len(contact["last_name_variants"]) == 1 and "-" not in (contact["last_name_variants"][0] if contact["last_name_variants"] else "")
    return (
        contact["first_name_only"],
        not simple_last,
        len(contact["first_name"]) < 3,
        len(contact["last_name_variants"][0]) < 3 if contact["last_name_variants"] else True,
        contact["row_number"],
    )


def detect_pattern_for_domain(domain, contacts, client):
    def allowed_probe(contact):
        title = (contact["original"].get("Job Title", "") or "").lower()
        blocked = any(term in title for term in ["board member", "advisor", "investor", "mentor"])
        return not contact["first_name_only"] and not blocked

    eligible = [c for c in contacts if allowed_probe(c)]
    if not eligible:
        if len(contacts) == 1 and contacts[0]["first_name_only"]:
            contact = contacts[0]
            for pattern in ["first", "firstl"]:
                for email in candidate_emails_for_pattern(contact, pattern):
                    result, _ = client.verify(email, f"mini_ladder:{domain}:{pattern}")
                    if result["message"] == "Accepted":
                        return {"pattern": pattern, "status": "confirmed", "rescued": False, "probe_results": [result]}
                    if result["message"] in TRANSIENT_MESSAGES:
                        return {"pattern": "", "status": "transient_unresolved", "rescued": False, "probe_results": [result]}
        return {"pattern": "", "status": "pattern_unknown", "rescued": False, "probe_results": []}

    probes = sorted(eligible, key=clean_probe_score)[:2]
    probe_results = []
    for probe_index, contact in enumerate(probes):
        for pattern in PATTERN_ORDER:
            for email in candidate_emails_for_pattern(contact, pattern):
                result, _ = client.verify(email, f"pattern:{domain}:{pattern}:probe{probe_index + 1}")
                probe_results.append(result)
                if result["message"] == "Accepted":
                    return {
                        "pattern": pattern,
                        "status": "confirmed",
                        "rescued": probe_index == 1,
                        "probe_results": probe_results,
                    }
                if result["message"] in TRANSIENT_MESSAGES:
                    return {
                        "pattern": "",
                        "status": "transient_unresolved",
                        "rescued": False,
                        "probe_results": probe_results,
                    }
    return {"pattern": "", "status": "pattern_unknown", "rescued": False, "probe_results": probe_results}


def result_to_fields(result):
    return {
        "mt_code": result.get("code", ""),
        "mt_message": result.get("message", ""),
        "mx": result.get("mx", ""),
        "checked_at": result.get("checked_at", ""),
    }


def reorder_no_adjacent(tasks):
    pending = list(tasks)
    ordered = []
    last_domain = None
    while pending:
        idx = next((i for i, task in enumerate(pending) if task["domain"] != last_domain), None)
        if idx is None:
            print(
                "Scheduler warning: only one domain remains in this queue; "
                f"continuing with unavoidable repeated domain {last_domain}."
            )
            idx = 0
        task = pending.pop(idx)
        ordered.append(task)
        last_domain = task["domain"]
    return ordered


def execute_tasks(tasks, client, workers, force_live=False):
    ordered = reorder_no_adjacent(tasks)

    results = {}
    def run_task(task):
        if force_live:
            result, spent = client.live_once(task["email"], task["purpose"], task["domain"])
        else:
            result, spent = client.check_once(task["email"], task["purpose"], task["domain"])
        return task["id"], result, spent

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {pool.submit(run_task, task): task for task in ordered}
        for future in as_completed(future_map):
            task_id, result, spent = future.result()
            results[task_id] = result

    return results


def append_retry_task(retry_queue, task, result):
    if result.get("message") in TRANSIENT_MESSAGES:
        retry_queue.append(
            {
                "id": task["id"],
                "domain": task["domain"],
                "email": task["email"],
                "purpose": task["purpose"],
            }
        )


def run_retry_queue(retry_queue, client, workers, result_overrides):
    pending = list(retry_queue)
    for attempt in [1, 2]:
        if not pending:
            break
        tasks = []
        for task in pending:
            if client.non_connection_attempts(task["email"]) >= 3:
                continue
            retry = dict(task)
            retry["id"] = f"{task['id']}:retry{attempt}"
            retry["purpose"] = f"{task['purpose']}:retry{attempt}"
            tasks.append(retry)
        if not tasks:
            break
        results = execute_tasks(tasks, client, workers, force_live=True)
        next_pending = []
        for task in tasks:
            original_id = task["id"].rsplit(":retry", 1)[0]
            result = results[task["id"]]
            result_overrides[original_id] = result
            if result.get("message") in TRANSIENT_MESSAGES:
                next_pending.append(
                    {
                        "id": original_id,
                        "domain": task["domain"],
                        "email": task["email"],
                        "purpose": task["purpose"].rsplit(":retry", 1)[0],
                    }
                )
        pending = next_pending


def domain_pattern_path(output_dir):
    return output_dir / "domain_patterns.csv"


def load_domain_patterns(output_dir):
    path = domain_pattern_path(output_dir)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return {row["domain"]: row for row in reader if row.get("domain")}


def save_domain_patterns(output_dir, domain_statuses):
    fields = ["domain", "pattern", "domain_status", "confidence", "checked_at", "mt_code", "mt_message", "mx", "rescued"]
    with domain_pattern_path(output_dir).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for domain in sorted(domain_statuses):
            item = domain_statuses[domain]
            result = item.get("result", {})
            writer.writerow(
                {
                    "domain": domain,
                    "pattern": item.get("pattern", ""),
                    "domain_status": item.get("status", ""),
                    "confidence": item.get("confidence", ""),
                    "checked_at": result.get("checked_at", ""),
                    "mt_code": result.get("code", ""),
                    "mt_message": result.get("message", ""),
                    "mx": result.get("mx", ""),
                    "rescued": "true" if item.get("rescued") else "false",
                }
            )


def checkpoint_row_to_status(row):
    return {
        "status": row.get("domain_status", ""),
        "pattern": row.get("pattern", ""),
        "confidence": row.get("confidence", ""),
        "rescued": row.get("rescued") == "true",
        "result": {
            "code": row.get("mt_code", ""),
            "message": row.get("mt_message", ""),
            "mx": row.get("mx", ""),
            "checked_at": row.get("checked_at", ""),
        },
    }


def build_duplicate_map(duplicates):
    by_primary = defaultdict(list)
    by_index = {}
    for duplicate in duplicates:
        by_primary[duplicate["primary_input_index"]].append(duplicate)
        by_index[duplicate["input_index"]] = duplicate
    return by_primary, by_index


def allowed_probe_contact(contact):
    title = (contact["original"].get("Job Title", "") or "").lower()
    blocked = any(term in title for term in ["board member", "advisor", "investor", "mentor"])
    return not contact["first_name_only"] and not blocked


def probe_contacts_for_domain(contacts):
    return sorted([c for c in contacts if allowed_probe_contact(c)], key=clean_probe_score)[:2]


def run_pipeline(rows, out_fields, usable, skipped, duplicates, output_dir, interval_ms, daily_cap, domain_limit=None, workers=1):
    domains = select_sample_domains(usable, domain_limit) if domain_limit else sorted({c["domain"] for c in usable})
    selected_contacts = [contact for contact in usable if contact["domain"] in domains]
    by_domain = defaultdict(list)
    for contact in selected_contacts:
        by_domain[contact["domain"]].append(contact)

    budget = budget_math(len(selected_contacts), len(domains), interval_ms)
    client = MailTesterClient(
        output_dir,
        interval_ms,
        daily_cap,
        progress_every=500,
        projected_total=budget["total"],
        allowed_connections=workers,
    )
    primary_enriched = {}
    skipped_by_index = {item["input_index"]: item for item in skipped}
    duplicate_by_primary, duplicate_by_index = build_duplicate_map(duplicates)
    pattern_distribution = Counter()
    domain_statuses = {}
    checkpointed = load_domain_patterns(output_dir)
    transient_initial = 0
    transient_unresolved = 0
    second_probe_rescues = 0
    fno_outcomes = Counter()
    retry_queue = []
    retry_overrides = {}

    for domain in domains:
        if domain in checkpointed and checkpointed[domain].get("domain_status"):
            domain_statuses[domain] = checkpoint_row_to_status(checkpointed[domain])
            if domain_statuses[domain]["status"] == "confirmed" and domain_statuses[domain]["pattern"]:
                pattern_distribution[domain_statuses[domain]["pattern"]] += 1
            if domain_statuses[domain].get("rescued"):
                second_probe_rescues += 1
            continue

    stage1_tasks = [
        {
            "id": f"stage1:{domain}",
            "domain": domain,
            "email": f"kq7x2mwp9z@{domain}",
            "purpose": f"catch_all:{domain}",
        }
        for domain in domains
        if domain not in domain_statuses
    ]
    if stage1_tasks:
        stage1_results = execute_tasks(stage1_tasks, client, workers)
        for task in stage1_tasks:
            domain = task["domain"]
            catch_result = stage1_results[task["id"]]
            if catch_result["message"] in TRANSIENT_MESSAGES:
                transient_initial += 1
                append_retry_task(retry_queue, task, catch_result)
                domain_statuses[domain] = {
                    "status": "check_failed",
                    "pattern": "",
                    "confidence": "none",
                    "result": catch_result,
                    "rescued": False,
                }
                continue
            if catch_result["message"] in {"Accepted", "Catch-All"}:
                domain_statuses[domain] = {
                    "status": "catchall",
                    "pattern": "first.last",
                    "confidence": "unconfirmed",
                    "result": catch_result,
                    "rescued": False,
                }
                continue
            if catch_result["message"] in DEAD_MESSAGES:
                domain_statuses[domain] = {
                    "status": "dead_domain",
                    "pattern": "",
                    "confidence": "none",
                    "result": catch_result,
                    "rescued": False,
                }
                continue
            domain_statuses[domain] = {
                "status": "normal",
                "pattern": "",
                "confidence": "none",
                "result": catch_result,
                "rescued": False,
            }
        save_domain_patterns(output_dir, domain_statuses)

    unsolved = {
        domain for domain, info in domain_statuses.items()
        if info["status"] == "normal"
    }
    probes_by_domain = {domain: probe_contacts_for_domain(by_domain[domain]) for domain in unsolved}
    mini_domains = {
        domain for domain in unsolved
        if not probes_by_domain[domain] and len(by_domain[domain]) == 1 and by_domain[domain][0]["first_name_only"]
    }

    for probe_index in [0, 1]:
        for pattern in PATTERN_ORDER:
            round_domains = sorted(domain for domain in unsolved if domain not in mini_domains and len(probes_by_domain[domain]) > probe_index)
            if not round_domains:
                continue
            tasks = []
            task_domain = {}
            for domain in round_domains:
                contact = probes_by_domain[domain][probe_index]
                emails = candidate_emails_for_pattern(contact, pattern)
                if not emails:
                    continue
                task_id = f"stage2:{domain}:probe{probe_index + 1}:{pattern}"
                tasks.append({"id": task_id, "domain": domain, "email": emails[0], "purpose": f"pattern:{domain}:{pattern}:probe{probe_index + 1}"})
                task_domain[task_id] = domain
            if not tasks:
                continue
            results = execute_tasks(tasks, client, workers)
            for task in tasks:
                domain = task_domain[task["id"]]
                result = results[task["id"]]
                if result["message"] in TRANSIENT_MESSAGES:
                    transient_initial += 1
                    append_retry_task(retry_queue, task, result)
                    domain_statuses[domain] = {
                        "status": "check_failed",
                        "pattern": "",
                        "confidence": "none",
                        "result": result,
                        "rescued": False,
                    }
                    unsolved.discard(domain)
                elif result["message"] == "Accepted":
                    domain_statuses[domain] = {
                        "status": "confirmed",
                        "pattern": pattern,
                        "confidence": "verified",
                        "result": result,
                        "rescued": probe_index == 1,
                    }
                    pattern_distribution[pattern] += 1
                    if probe_index == 1:
                        second_probe_rescues += 1
                    unsolved.discard(domain)
            save_domain_patterns(output_dir, domain_statuses)

    for domain in sorted(mini_domains & unsolved):
        contact = by_domain[domain][0]
        tasks = []
        for pattern in ["first", "firstl"]:
            emails = candidate_emails_for_pattern(contact, pattern)
            if emails:
                tasks.append({"id": f"mini:{domain}:{pattern}", "domain": domain, "email": emails[0], "purpose": f"mini_ladder:{domain}:{pattern}"})
        if not tasks:
            domain_statuses[domain].update({"status": "pattern_unknown"})
            continue
        results = execute_tasks(tasks, client, workers)
        solved = False
        for task in tasks:
            result = results[task["id"]]
            pattern = task["id"].split(":")[-1]
            if result["message"] == "Accepted":
                domain_statuses[domain] = {"status": "confirmed", "pattern": pattern, "confidence": "verified", "result": result, "rescued": False}
                pattern_distribution[pattern] += 1
                solved = True
                break
            if result["message"] in TRANSIENT_MESSAGES:
                transient_initial += 1
                append_retry_task(retry_queue, task, result)
                domain_statuses[domain] = {"status": "check_failed", "pattern": "", "confidence": "none", "result": result, "rescued": False}
                solved = True
                break
        if not solved:
            domain_statuses[domain].update({"status": "pattern_unknown"})
        unsolved.discard(domain)
    for domain in sorted(unsolved):
        domain_statuses[domain].update({"status": "pattern_unknown"})
    save_domain_patterns(output_dir, domain_statuses)

    propagation_tasks = []
    propagation_contact_by_task = {}
    for contact in selected_contacts:
        status_info = domain_statuses[contact["domain"]]
        pattern = status_info.get("pattern", "")
        if status_info.get("status") == "confirmed":
            if contact["first_name_only"] and pattern not in {"first", "firstl"}:
                continue
            emails = candidate_emails_for_pattern(contact, pattern)
            if emails:
                task_id = f"stage3:{contact['input_index']}"
                propagation_tasks.append({"id": task_id, "domain": contact["domain"], "email": emails[0], "purpose": f"propagation:{contact['domain']}:{pattern}"})
                propagation_contact_by_task[task_id] = contact
    random.shuffle(propagation_tasks)
    propagation_results = execute_tasks(propagation_tasks, client, workers) if propagation_tasks else {}
    for task in propagation_tasks:
        result = propagation_results[task["id"]]
        if result.get("message") in TRANSIENT_MESSAGES:
            append_retry_task(retry_queue, task, result)

    run_retry_queue(retry_queue, client, workers, retry_overrides)
    for task_id, result in retry_overrides.items():
        if task_id.startswith("stage1:"):
            domain = task_id.split(":", 1)[1]
            if domain in domain_statuses:
                domain_statuses[domain]["result"] = result
                if result.get("message") not in TRANSIENT_MESSAGES:
                    if result["message"] in {"Accepted", "Catch-All"}:
                        domain_statuses[domain].update({"status": "catchall", "pattern": "first.last", "confidence": "unconfirmed"})
                    elif result["message"] in DEAD_MESSAGES:
                        domain_statuses[domain].update({"status": "dead_domain", "pattern": "", "confidence": "none"})
                    elif result["message"] == "Rejected":
                        domain_statuses[domain].update({"status": "pattern_unknown", "pattern": "", "confidence": "none"})
        elif task_id.startswith("stage2:") or task_id.startswith("mini:"):
            parts = task_id.split(":")
            domain = parts[1]
            if domain in domain_statuses:
                domain_statuses[domain]["result"] = result
                if result.get("message") == "Accepted":
                    pattern = parts[-1]
                    domain_statuses[domain].update({"status": "confirmed", "pattern": pattern, "confidence": "verified"})
        elif task_id.startswith("stage3:"):
            propagation_results[task_id] = result
    transient_unresolved = sum(1 for result in retry_overrides.values() if result.get("message") in TRANSIENT_MESSAGES)
    save_domain_patterns(output_dir, domain_statuses)

    for contact in selected_contacts:
        status_info = domain_statuses[contact["domain"]]
        base = filter_original(contact["original"], out_fields)
        base.update({column: "" for column in ENRICHED_APPEND_COLUMNS})
        pattern = status_info.get("pattern", "")
        domain_result = status_info.get("result", {})

        if status_info["status"] == "catchall":
            email, guessed_pattern = best_guess_for_contact(contact)
            base.update(result_to_fields(domain_result))
            base.update({"email": email, "status": "catchall_guess", "pattern_used": guessed_pattern, "confidence": "unconfirmed"})
        elif status_info["status"] == "dead_domain":
            base.update(result_to_fields(domain_result))
            base.update({"status": "dead_domain", "confidence": "none"})
        elif status_info["status"] == "check_failed":
            base.update(result_to_fields(domain_result))
            base.update({"status": "check_failed", "confidence": "none"})
        elif status_info["status"] != "confirmed":
            base.update(result_to_fields(domain_result))
            base.update({"status": "pattern_unknown", "confidence": "none"})
        elif contact["first_name_only"] and pattern not in {"first", "firstl"}:
            base.update({"status": "unresolvable_pattern", "pattern_used": pattern, "confidence": "none"})
            fno_outcomes["unresolvable_pattern"] += 1
        else:
            task_id = f"stage3:{contact['input_index']}"
            result = propagation_results.get(task_id)
            emails = candidate_emails_for_pattern(contact, pattern)
            if not result or not emails:
                base.update({"status": "unresolvable_pattern", "pattern_used": pattern, "confidence": "none"})
            else:
                base.update(result_to_fields(result))
                base["email"] = emails[0]
                base["pattern_used"] = pattern
                if result["message"] == "Accepted":
                    base.update({"status": "verified", "confidence": "verified"})
                    if contact["first_name_only"]:
                        fno_outcomes["resolved"] += 1
                elif result["message"] in TRANSIENT_MESSAGES:
                    transient_initial += 1
                    transient_unresolved += 1
                    base.update({"status": "check_failed", "confidence": "none"})
                else:
                    base.update({"status": "person_invalid", "confidence": "none"})
                    if contact["first_name_only"]:
                        fno_outcomes["unresolved_invalid"] += 1
        base.setdefault("duplicate_of", "")
        primary_enriched[contact["input_index"]] = base

    enriched_rows = []
    for index, row in enumerate(rows):
        if index in primary_enriched:
            output_row = dict(primary_enriched[index])
        elif index in duplicate_by_index:
            duplicate = duplicate_by_index[index]
            primary = primary_enriched.get(duplicate["primary_input_index"])
            output_row = filter_original(row, out_fields)
            output_row.update({column: "" for column in ENRICHED_APPEND_COLUMNS})
            if primary:
                for column in ["email", "pattern_used", "mt_code", "mt_message", "mx", "checked_at"]:
                    output_row[column] = primary.get(column, "")
                output_row["confidence"] = primary.get("confidence", "none")
            else:
                output_row["confidence"] = "none"
            output_row["status"] = "duplicate_contact"
            output_row["duplicate_of"] = duplicate["duplicate_of"]
        elif index in skipped_by_index:
            reason = skipped_by_index[index]["skip_reason"]
            output_row = filter_original(row, out_fields)
            output_row.update({column: "" for column in ENRICHED_APPEND_COLUMNS})
            output_row.update({"status": "input_invalid", "mt_message": reason, "confidence": "none", "duplicate_of": ""})
        else:
            if domain_limit is not None:
                continue
            raise SystemExit(f"Internal error: row {index + 2} was not classified.")
        enriched_rows.append(output_row)

    if domain_limit is None and len(enriched_rows) != len(rows):
        raise SystemExit(f"Hard assertion failed: enriched_all rows={len(enriched_rows):,}, input rows={len(rows):,}.")
    write_enriched_all(output_dir / "enriched_all.csv", out_fields, enriched_rows)
    write_verified_send_ready(output_dir / "verified_send_ready.csv", out_fields, enriched_rows)
    actual_domains = client.request_domains
    adjacent_domain_repeats = sum(1 for a, b in zip(actual_domains, actual_domains[1:]) if a == b)
    return {
        "domains": domains,
        "contacts": selected_contacts,
        "enriched_rows": enriched_rows,
        "pattern_distribution": pattern_distribution,
        "domain_statuses": domain_statuses,
        "transient_initial": transient_initial,
        "transient_unresolved": transient_unresolved,
        "second_probe_rescues": second_probe_rescues,
        "fno_outcomes": fno_outcomes,
        "requests_spent": client.requests_spent,
        "valid_requests_total": client.valid_request_count(),
        "adjacent_domain_repeats": adjacent_domain_repeats,
    }

def print_csv_roundtrip(output_dir):
    enriched_path = output_dir / "enriched_all.csv"
    print("New enriched_all.csv header:")
    print(read_header_line(enriched_path))
    print("3 csv.DictReader round-trip rows:")
    for row in read_round_trip_rows(enriched_path, 3):
        print(json.dumps(row, ensure_ascii=False))


def print_gate2_report(report):
    domain_status_counts = Counter(item["status"] for item in report["domain_statuses"].values())
    total_domains = len(report["domains"])
    catchall = domain_status_counts["catchall"]
    unknown = domain_status_counts["pattern_unknown"] + domain_status_counts["transient_unresolved"]
    status_counts = Counter(row["status"] for row in report["enriched_rows"])
    print("GATE 2 LIVE SAMPLE")
    print(f"Domains processed: {total_domains}")
    print(f"Contacts processed: {len(report['contacts'])}")
    print(f"Catch-all rate: {catchall}/{total_domains} ({catchall / total_domains:.1%})")
    print("Pattern distribution:")
    for pattern, count in sorted(report["pattern_distribution"].items()):
        print(f"  {pattern}: {count}")
    if not report["pattern_distribution"]:
        print("  none")
    print(f"Pattern_unknown rate: {unknown}/{total_domains} ({unknown / total_domains:.1%})")
    print(f"Second-probe fallback rescues: {report['second_probe_rescues']}")
    print(f"Transient results encountered: {report['transient_initial']}")
    print(f"Transient results unresolved after retries: {report['transient_unresolved']}")
    print("first_name_only outcomes:")
    print(f"  resolved: {report['fno_outcomes']['resolved']}")
    print(f"  unresolvable_pattern: {report['fno_outcomes']['unresolvable_pattern']}")
    print(f"Total requests actually spent: {report['requests_spent']}")
    print("Status counts in enriched_all.csv:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print("30 sample rows from enriched_all.csv:")
    sample = []
    seen_statuses = set()
    for row in report["enriched_rows"]:
        if row["status"] not in seen_statuses:
            sample.append(row)
            seen_statuses.add(row["status"])
    for row in report["enriched_rows"]:
        if len(sample) >= 30:
            break
        if row not in sample:
            sample.append(row)
    for row in sample[:30]:
        print(json.dumps(row, ensure_ascii=False))


def count_csv_data_rows(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def print_full_run_report(report, output_dir, input_row_count):
    enriched_path = output_dir / "enriched_all.csv"
    send_ready_path = output_dir / "verified_send_ready.csv"
    enriched_count = count_csv_data_rows(enriched_path)
    if enriched_count != input_row_count:
        raise SystemExit(
            f"Hard assertion failed after write: enriched_all.csv has {enriched_count:,} "
            f"data rows; input has {input_row_count:,}."
        )

    status_counts = Counter(row["status"] for row in report["enriched_rows"])
    domain_status_counts = Counter(item["status"] for item in report["domain_statuses"].values())
    total_rows = len(report["enriched_rows"])
    total_domains = len(report["domains"])
    catchall = domain_status_counts["catchall"]
    send_ready_count = count_csv_data_rows(send_ready_path)

    print("GATE 3 FULL RUN")
    print(f"Final enriched_all.csv row count: {enriched_count:,}")
    print("Full status breakdown:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count:,} ({count / total_rows:.2%})")
    print(f"verified_send_ready.csv row count: {send_ready_count:,}")
    print(f"Final catch-all rate across all domains: {catchall:,}/{total_domains:,} ({catchall / total_domains:.2%})")
    print("Pattern win distribution:")
    for pattern, count in sorted(report["pattern_distribution"].items()):
        print(f"  {pattern}: {count:,}")
    print(f"Total requests spent vs 71,155 projection: {report['valid_requests_total']:,} / 71,155")
    print(f"check_failed rows: {status_counts['check_failed']:,}")


def existing_result_emails(output_dir, limit):
    emails = []
    seen = set()
    path = output_dir / "results.jsonl"
    if not path.exists():
        return emails
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            result = json.loads(line)
            message = canonical_message(result.get("message", ""))
            email = (result.get("email") or "").lower()
            if message in KNOWN_MESSAGES and email and email not in seen:
                emails.append(email)
                seen.add(email)
            if len(emails) >= limit:
                break
    return emails


def run_burst_check(output_dir, interval_ms, daily_cap, count):
    emails = existing_result_emails(output_dir, count)
    if len(emails) < count:
        raise SystemExit(f"Only found {len(emails)} prior result emails; cannot run {count}-request burst.")
    client = MailTesterClient(output_dir, interval_ms, daily_cap, progress_every=500)
    messages = Counter()
    connections = Counter()
    high_connections = 0
    started = time.monotonic()
    for index, email in enumerate(emails, start=1):
        result = client.request_once(email, f"burst_check:{index}")
        messages[result["message"]] += 1
        connections[str(result.get("connections", ""))] += 1
        if client.high_connection(result):
            high_connections += 1
    elapsed = time.monotonic() - started
    bad_messages = {
        message: count
        for message, count in messages.items()
        if message in TRANSIENT_MESSAGES or message in {"Limited", "Disabled Key"}
    }
    clean = high_connections == 0 and not bad_messages
    print("200-request burst check")
    print(f"Requests attempted: {count}")
    print(f"Requests saved: {client.requests_spent}")
    print(f"Elapsed: {format_duration(elapsed)}")
    print(f"Connections distribution: {dict(connections)}")
    print(f"Message distribution: {dict(messages)}")
    print(f"Connections stayed at 1 for all {count}: {'yes' if high_connections == 0 else 'no'}")
    print(f"Rate-limit/Disabled/transient responses: {bad_messages if bad_messages else 'none'}")
    print(f"Burst clean: {'yes' if clean else 'no'}")
    return clean


def run_diagnostic(usable, output_dir, interval_ms, daily_cap, workers, count):
    domains = sorted({contact["domain"] for contact in usable})[:count]
    tasks = [
        {
            "id": f"diagnostic:{domain}",
            "domain": domain,
            "email": f"kq7x2mwp9z@{domain}",
            "purpose": f"diagnostic_stage1:{domain}",
        }
        for domain in domains
    ]
    client = MailTesterClient(
        output_dir,
        interval_ms,
        daily_cap,
        progress_every=500,
        projected_total=71_155,
        allowed_connections=workers,
    )
    started = time.monotonic()
    before = len(client.request_domains)
    results = execute_tasks(tasks, client, workers, force_live=True)
    elapsed = time.monotonic() - started
    actual_domains = client.request_domains[before:]
    adjacent_repeats = sum(1 for a, b in zip(actual_domains, actual_domains[1:]) if a == b)
    messages = Counter(result["message"] for result in results.values())
    connections = Counter(str(result.get("connections", "")) for result in results.values())
    transient_count = sum(messages[msg] for msg in TRANSIENT_MESSAGES)
    effective_ms = elapsed * 1000 / max(len(results), 1)
    projected_seconds = effective_ms * 71_155 / 1000
    transient_rate = transient_count / max(len(results), 1)
    mx_error_rate = messages["Mx Error"] / max(len(results), 1)
    sane_connections = all(
        key in {"", "0", "1", "None"} or int(key) <= workers
        for key in connections
    )
    clean = effective_ms < 300 and mx_error_rate <= 0.03 and adjacent_repeats == 0 and sane_connections
    print("300-request diagnostic")
    print(f"Workers: {workers}")
    print(f"Requests: {len(results)}")
    print(f"Elapsed: {format_duration(elapsed)}")
    print(f"Effective ms/request: {effective_ms:.1f}")
    print(f"Projected full-run time at this rate: {format_duration(projected_seconds)}")
    print("Transient rate by message type:")
    for message in sorted(TRANSIENT_MESSAGES):
        count_for_message = messages[message]
        print(f"  {message}: {count_for_message} ({count_for_message / len(results):.2%})")
    print(f"  total: {transient_count} ({transient_rate:.2%})")
    print(f"Connections distribution: {dict(connections)}")
    print(f"No two consecutive requests hit the same domain: {'yes' if adjacent_repeats == 0 else 'no'}")
    print(f"Mx Error above 3%: {'yes' if mx_error_rate > 0.03 else 'no'}")
    print(f"Diagnostic clean: {'yes' if clean else 'no'}")
    return {"clean": clean, "effective_ms": effective_ms, "mx_error_rate": mx_error_rate}


def parse_args():
    parser = argparse.ArgumentParser(description="Domain-first email permutation engine")
    parser.add_argument("--input")
    parser.add_argument("--output-dir", default="email_engine_output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live-sample", action="store_true")
    parser.add_argument("--full-run", action="store_true")
    parser.add_argument("--burst-check", type=int, default=0)
    parser.add_argument("--diagnostic", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--domain-limit", type=int, default=50)
    parser.add_argument("--interval-ms", type=int, default=180)
    parser.add_argument("--daily-cap", type=int, default=500_000)
    parser.add_argument("--roundtrip-only", action="store_true")
    parser.add_argument("--status", action="store_true", help="read checkpoint progress without API calls")
    return parser.parse_args()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    random.seed(42)
    args = parse_args()
    output_dir = Path(args.output_dir)
    if args.status:
        print_status(output_dir)
        return
    if not args.input:
        raise SystemExit("--input is required unless --status is used.")
    input_path = Path(args.input)
    rows, fieldnames, out_fields, usable, skipped, duplicates = stage0_load_normalize(input_path, output_dir)

    if args.roundtrip_only:
        write_dry_outputs(output_dir, out_fields, rows, skipped)
        print_csv_roundtrip(output_dir)
        return

    if args.burst_check:
        clean = run_burst_check(output_dir, args.interval_ms, args.daily_cap, args.burst_check)
        if not clean:
            raise SystemExit(2)
        return

    if args.diagnostic:
        diagnostic = run_diagnostic(usable, output_dir, args.interval_ms, args.daily_cap, args.workers, args.diagnostic)
        if diagnostic["effective_ms"] > 400 and args.workers != 5:
            print("3-worker diagnostic remained above 400ms/request; trying --workers 5 as requested.")
            run_diagnostic(usable, output_dir, args.interval_ms, args.daily_cap, 5, args.diagnostic)
            raise SystemExit(2)
        if not diagnostic["clean"]:
            raise SystemExit(2)
        report = run_pipeline(rows, out_fields, usable, skipped, duplicates, output_dir, args.interval_ms, args.daily_cap, None, workers=args.workers)
        print_full_run_report(report, output_dir, len(rows))
        print("Gate 3 complete.")
        return

    if args.dry_run:
        write_dry_outputs(output_dir, out_fields, rows, skipped)
        print_gate1_report(rows, out_fields, usable, skipped, output_dir, args.interval_ms, args.daily_cap)
        print("Gate 1 complete. Stopping for approval before any API calls.")
        return

    if args.live_sample:
        report = run_pipeline(rows, out_fields, usable, skipped, duplicates, output_dir, args.interval_ms, args.daily_cap, args.domain_limit, workers=args.workers)
        print_csv_roundtrip(output_dir)
        print_gate2_report(report)
        print("Gate 2 complete. Stopping for approval before full run.")
        return

    if args.full_run:
        report = run_pipeline(rows, out_fields, usable, skipped, duplicates, output_dir, args.interval_ms, args.daily_cap, None, workers=args.workers)
        print_full_run_report(report, output_dir, len(rows))
        print("Gate 3 complete.")
        return

    raise SystemExit("No live full-run path is enabled before Gate 3 approval.")


if __name__ == "__main__":
    main()
