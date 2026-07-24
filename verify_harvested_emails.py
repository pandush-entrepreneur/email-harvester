import argparse
import csv
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path


API_URL = "https://happy.mailtester.ninja/ninja"
KNOWN_MESSAGES = {
    "Accepted",
    "Rejected",
    "Catch-All",
    "No Mx",
    "Limited",
    "Timeout",
    "SPAM Block",
    "Mx Error",
    "Greylisted",
    "Disposable",
}
TRANSIENT_MESSAGES = {"Limited", "Timeout", "SPAM Block", "Mx Error", "Greylisted", "Disposable"}


def canonical_message(message):
    if message == "No MX":
        return "No Mx"
    if message == "MX Error":
        return "Mx Error"
    return message


class GlobalRateLimiter:
    def __init__(self, interval_seconds):
        self.interval_seconds = interval_seconds
        self.lock = threading.Lock()
        self.next_start = None

    def wait_turn(self):
        with self.lock:
            now = time.monotonic()
            if self.next_start is None:
                scheduled = now
            else:
                scheduled = self.next_start
                if scheduled > now:
                    time.sleep(scheduled - now)
            actual = time.monotonic()
            self.next_start = actual + self.interval_seconds


class MailTesterVerifier:
    def __init__(self, output_dir, interval_ms, daily_cap, workers):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.results_path = self.output_dir / "harvest_mailtester_results.jsonl"
        self.usage_path = self.output_dir / "harvest_mailtester_daily_usage.json"
        self.key = (os.environ.get("MAILTESTER_KEY") or "").strip().strip("{}")
        if not self.key:
            raise SystemExit("MAILTESTER_KEY is not set. No API calls were made.")
        self.limiter = GlobalRateLimiter(interval_ms / 1000)
        self.daily_cap = daily_cap
        self.workers = workers
        self.lock = threading.Lock()
        self.history = self.load_history()
        self.usage = self.load_usage()
        self.started_at = time.monotonic()
        self.requests_spent = 0
        self.high_connection_streak = 0

    def load_history(self):
        history = {}
        if not self.results_path.exists():
            return history
        with self.results_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                item["message"] = canonical_message(item.get("message", ""))
                if item["message"] in KNOWN_MESSAGES:
                    history[item["email"].lower()] = item
        return history

    def load_usage(self):
        if not self.usage_path.exists():
            return {}
        return json.loads(self.usage_path.read_text(encoding="utf-8") or "{}")

    def save_usage(self):
        self.usage_path.write_text(json.dumps(self.usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def request_once(self, email, purpose):
        today = date.today().isoformat()
        with self.lock:
            used_today = int(self.usage.get(today, 0))
            if used_today >= self.daily_cap:
                print("Daily cap reached. Resume with the same command; cached results will be reused.")
                raise SystemExit(0)
        self.limiter.wait_turn()
        query = urllib.parse.urlencode({"email": email, "key": self.key})
        with urllib.request.urlopen(f"{API_URL}?{query}", timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
        message = canonical_message(raw.get("message", ""))
        if message not in KNOWN_MESSAGES:
            message = "Greylisted"
        result = {
            "email": email,
            "purpose": purpose,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "code": raw.get("code", ""),
            "message": message,
            "mx": raw.get("mx", ""),
            "connections": raw.get("connections"),
            "raw": raw,
        }
        with self.lock:
            with self.results_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(result, ensure_ascii=True) + "\n")
            self.history[email.lower()] = result
            self.usage[today] = int(self.usage.get(today, 0)) + 1
            self.save_usage()
            self.requests_spent += 1
            if self.requests_spent % 500 == 0:
                elapsed = time.monotonic() - self.started_at
                print(f"Progress: requests={self.requests_spent:,}, elapsed={format_duration(elapsed)}")
        if result["connections"] and int(result["connections"]) > 1:
            self.high_connection_streak += 1
            print(f"connections={result['connections']} for {email}; pausing 30s ({self.high_connection_streak}/5)")
            if self.high_connection_streak >= 5:
                raise SystemExit("Connections stayed above 1 for 5 consecutive readings. Halting.")
            time.sleep(30)
        else:
            self.high_connection_streak = 0
        return result

    def verify_batch(self, emails, attempt):
        tasks = [email for email in emails if email.lower() not in self.history or attempt > 0]
        results = {}
        def worker(email):
            return email, self.request_once(email, f"harvest_verify_attempt_{attempt + 1}")
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(worker, email): email for email in tasks}
            for future in as_completed(futures):
                email, result = future.result()
                results[email.lower()] = result
        for email in emails:
            if email.lower() not in results and email.lower() in self.history:
                results[email.lower()] = self.history[email.lower()]
        return results


def format_duration(seconds):
    seconds = round(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def status_from_result(original_status, result):
    message = result.get("message", "")
    if message == "Accepted":
        return "verified", "verified"
    if message == "Catch-All":
        return "catchall_observed", "unconfirmed"
    if message == "Rejected":
        return "email_invalid", "none"
    if message == "No Mx":
        return "dead_domain", "none"
    if message in TRANSIENT_MESSAGES:
        return "check_failed", "none"
    return original_status, "none"


def read_rows(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames or [], list(reader)


def write_rows(path, fieldnames, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="website_harvester_output/harvested_contacts_enriched_format.csv")
    parser.add_argument("--output", default="website_harvester_output/harvested_contacts_verified.csv")
    parser.add_argument("--output-dir", default="website_harvester_output")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--interval-ms", type=int, default=180)
    parser.add_argument("--daily-cap", type=int, default=500_000)
    return parser.parse_args()


def main():
    args = parse_args()
    fieldnames, rows = read_rows(Path(args.input))
    emails = list(dict.fromkeys(row["email"].strip().lower() for row in rows if row.get("email")))
    verifier = MailTesterVerifier(Path(args.output_dir), args.interval_ms, args.daily_cap, args.workers)
    results = verifier.verify_batch(emails, 0)
    retry_emails = [email for email, result in results.items() if result.get("message") in TRANSIENT_MESSAGES]
    for attempt in [1, 2]:
        if not retry_emails:
            break
        retry_results = verifier.verify_batch(retry_emails, attempt)
        results.update(retry_results)
        retry_emails = [email for email in retry_emails if results[email].get("message") in TRANSIENT_MESSAGES]

    for row in rows:
        email = row.get("email", "").strip().lower()
        result = results.get(email)
        if not result:
            continue
        row["mt_code"] = result.get("code", "")
        row["mt_message"] = result.get("message", "")
        row["mx"] = result.get("mx", "")
        row["checked_at"] = result.get("checked_at", "")
        row["status"], row["confidence"] = status_from_result(row.get("status", ""), result)

    write_rows(Path(args.output), fieldnames, rows)
    counts = Counter(row["status"] for row in rows)
    messages = Counter(row["mt_message"] for row in rows)
    print(f"Output: {args.output}")
    print(f"Rows: {len(rows):,}")
    print(f"Unique emails: {len(emails):,}")
    print(f"New MailTester requests spent this run: {verifier.requests_spent:,}")
    print("Status breakdown:")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count:,}")
    print("MailTester message breakdown:")
    for message, count in sorted(messages.items()):
        print(f"  {message}: {count:,}")


if __name__ == "__main__":
    main()
