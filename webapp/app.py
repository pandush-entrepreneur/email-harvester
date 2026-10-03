import csv
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename


BASE_DIR = Path(os.environ.get("HARVESTER_DIR", Path(__file__).resolve().parents[1])).resolve()
CLIENTS_DIR = BASE_DIR / "clients"
DB_PATH = Path(os.environ.get("HARVESTER_DB", BASE_DIR / "webapp" / "harvester.db"))
ENGINE = BASE_DIR / "email_engine.py"
VERIFIER = BASE_DIR / "verify_csv_emails.py"
DAILY_CAP = int(os.environ.get("HARVESTER_DAILY_CAP", "800000"))
WORKERS = int(os.environ.get("HARVESTER_WORKERS", "5"))

ALIASES = {
    "First Name": ["first name", "firstname", "first_name", "given name", "given_name"],
    "Last Name": ["last name", "lastname", "last_name", "surname", "family name", "family_name"],
    "Company Domain": ["company domain", "domain", "company website", "website", "company url", "company_domain"],
}
EMAIL_ALIASES = [
    "email", "email address", "email_address", "emailaddress", "work email",
    "work_email", "business email", "business_email", "email business",
    "business email address", "corporate email", "contact email",
]

app = Flask(__name__)
app.secret_key = os.environ.get("HARVESTER_SECRET_KEY", secrets.token_hex(32))
app.config.update(
    MAX_CONTENT_LENGTH=300 * 1024 * 1024,
    SESSION_COOKIE_SECURE=os.environ.get("HARVESTER_COOKIE_SECURE", "true").lower() == "true",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    """Open a connection, commit or roll back on exit, and always close it.

    A bare ``with sqlite3.connect(...)`` only handles the transaction. The
    connection object itself stayed alive, and the queue worker leaked one
    file descriptor per loop until it hit its 1024 fd limit and could no
    longer open the database at all.
    """
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                source_file TEXT,
                input_file TEXT,
                output_dir TEXT NOT NULL,
                mapping_json TEXT,
                dry_log TEXT,
                sample_log TEXT,
                run_log TEXT,
                pid INTEGER,
                external INTEGER NOT NULL DEFAULT 0,
                auto_full INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS verification_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                source_file TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                email_column TEXT,
                total_rows INTEGER NOT NULL DEFAULT 0,
                unique_emails INTEGER NOT NULL DEFAULT 0,
                log_file TEXT,
                pid INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT
            )
        """)
        job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "auto_full" not in job_columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN auto_full INTEGER NOT NULL DEFAULT 0")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def unique_slug(name, table="jobs"):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "list"
    slug = base
    counter = 2
    with db() as conn:
        while conn.execute(f"SELECT 1 FROM {table} WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base}-{counter}"
            counter += 1
    return slug


def read_headers(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        return next(reader, [])


def detect_mapping(headers):
    lowered = {header.strip().lower(): header for header in headers}
    mapping = {}
    for target, aliases in ALIASES.items():
        mapping[target] = next((lowered[a] for a in aliases if a in lowered), "")
    return mapping


def detect_email_column(headers):
    normalized = {header.strip().lower(): header for header in headers}
    exact = next((normalized[alias] for alias in EMAIL_ALIASES if alias in normalized), "")
    if exact:
        return exact
    excluded = {"provider", "status", "valid", "validation", "verified", "verification", "mx", "domain"}
    candidates = []
    for header in headers:
        words = set(re.findall(r"[a-z]+", header.lower()))
        if "email" in words and not words.intersection(excluded):
            candidates.append(header)
    return candidates[0] if len(candidates) == 1 else ""


def email_stats(path, email_column):
    total_rows = 0
    unique = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            total_rows += 1
            email = row.get(email_column, "").strip().lower() if email_column else ""
            if email:
                unique.add(email)
    return total_rows, len(unique)


def prepare_input(source, target, mapping):
    if len(set(mapping.values())) != 3 or any(not value for value in mapping.values()):
        raise ValueError("First name, last name, and company domain must map to three columns.")
    with source.open("r", encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        source_fields = list(reader.fieldnames or [])
        reverse = {value: key for key, value in mapping.items()}
        fields = [reverse.get(field, field) for field in source_fields]
        with target.open("w", encoding="utf-8-sig", newline="") as dst:
            writer = csv.DictWriter(dst, fieldnames=fields)
            writer.writeheader()
            count = 0
            for row in reader:
                writer.writerow({reverse.get(key, key): value for key, value in row.items()})
                count += 1
    return count


def run_command(args, log_path, env=None):
    command = [python_executable(), "-u", str(ENGINE)] + args
    with Path(log_path).open("w", encoding="utf-8") as log:
        return subprocess.run(command, cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, env=env).returncode


def parse_report(text):
    values = {}
    patterns = {
        "total_rows": r"Total rows: ([\d,]+)",
        "usable_rows": r"Usable rows: ([\d,]+)",
        "skipped_rows": r"Skipped rows: ([\d,]+)",
        "unique_domains": r"Unique domains: ([\d,]+)",
        "projected_requests": r"Total estimate: ([\d,]+)",
        "catchall_rate": r"Catch-all rate: .*?\(([^)]+)\)",
        "sample_requests": r"Total requests actually spent: ([\d,]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            values[key] = match.group(1)
    return values


def line_count(path):
    try:
        with Path(path).open("rb") as handle:
            return sum(1 for _ in handle)
    except FileNotFoundError:
        return 0


ROW_COUNT_CACHE = {}


def csv_rows(path):
    """Count CSV data records, including records with multiline fields.

    The dashboards recount the same output files on every poll. Results for
    an unchanged file are cached by path, size, and mtime so polling stays
    cheap once a run has finished.
    """
    path = Path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        ROW_COUNT_CACHE.pop(str(path), None)
        return 0
    cached = ROW_COUNT_CACHE.get(str(path))
    if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
        return cached[1]
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            count = sum(1 for _ in csv.DictReader(handle))
    except FileNotFoundError:
        ROW_COUNT_CACHE.pop(str(path), None)
        return 0
    ROW_COUNT_CACHE[str(path)] = ((stat.st_mtime_ns, stat.st_size), count)
    return count


def sample_completed_successfully(log_path):
    if not log_path:
        return False
    try:
        return "Gate 2 complete." in Path(log_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False


def full_run_completed(log_path):
    """True only when the engine itself reported a finished full run.

    The engine prints its final report and ``Gate 3 complete.`` after both
    output files are written. Relying on the pid alone marked crashed runs
    complete and missed runs whose process had already gone away.
    """
    if not log_path:
        return False
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return False
    return "GATE 3 FULL RUN" in text or "Gate 3 complete." in text


def final_request_count(log_path):
    """Requests the engine reported for a finished full run, 0 when absent."""
    if not log_path:
        return 0
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 0
    match = re.search(r"Total requests spent vs [\d,]+ projection: ([\d,]+)", text)
    return int(match.group(1).replace(",", "")) if match else 0


def verification_checkpoint_stats(output):
    progress_path = output / "verification_progress.json"
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        return int(progress.get("checked", 0)), int(progress.get("accepted", 0))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        pass
    checked = accepted = 0
    try:
        with (output / "verification_results.jsonl").open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    result = json.loads(line)
                except json.JSONDecodeError:
                    continue
                checked += 1
                accepted += int(result.get("message") == "Accepted")
    except FileNotFoundError:
        pass
    return checked, accepted


def harvest_checkpoint_stats(output, phase_started_at=None):
    """Derive live, phase-aware counts from the append-only API checkpoint."""
    requests = 0
    probed_domains = set()
    latest_by_contact = {}
    recent_pattern_checks = False
    recent_contact_checks = False
    try:
        with (output / "results.jsonl").open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    result = json.loads(line)
                except json.JSONDecodeError:
                    continue
                requests += 1
                purpose = str(result.get("purpose", ""))
                recent = not phase_started_at or str(result.get("checked_at", "")) >= phase_started_at
                if purpose.startswith("catch_all:"):
                    probed_domains.add(purpose.split(":", 1)[1])
                elif purpose.startswith(("pattern:", "mini_ladder:")):
                    email = str(result.get("email", "")).lower()
                    if email:
                        latest_by_contact[email] = result
                    if recent:
                        recent_pattern_checks = True
                elif purpose.startswith("propagation:"):
                    email = str(result.get("email", "")).lower()
                    if email:
                        latest_by_contact[email] = result
                    if recent:
                        recent_contact_checks = True
    except FileNotFoundError:
        pass

    verified_contacts = sum(result.get("message") == "Accepted" for result in latest_by_contact.values())
    return {
        "requests": requests,
        "domains": len(probed_domains),
        "verified": verified_contacts,
        "has_pattern_checks": recent_pattern_checks,
        "has_contact_checks": recent_contact_checks,
    }


def process_alive(pid):
    if not pid:
        return False
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists() and stat_path.read_text(encoding="utf-8", errors="ignore").split()[2] == "Z":
            return False
        os.kill(pid, 0)
        return True
    except (OSError, IndexError):
        return False


def python_executable():
    candidates = [BASE_DIR / ".venv" / "bin" / "python", BASE_DIR / ".venv" / "Scripts" / "python.exe"]
    return str(next((path for path in candidates if path.exists()), Path(sys.executable)))


def key_status():
    """Return the current MailTester owner. Queued work does not occupy the key."""
    with db() as conn:
        harvest = conn.execute(
            "SELECT id, name, state, pid FROM jobs WHERE state IN ('running_sample','running_full','pausing') ORDER BY id LIMIT 1"
        ).fetchone()
        verification = conn.execute(
            "SELECT id, name, state, pid FROM verification_jobs WHERE state IN ('running','stopping') ORDER BY id LIMIT 1"
        ).fetchone()
    if verification and process_alive(verification["pid"]):
        return {"available": False, "workflow": "verification", "name": verification["name"], "job_id": verification["id"]}
    if harvest and (harvest["pid"] is None or process_alive(harvest["pid"])):
        return {"available": False, "workflow": "harvester", "name": harvest["name"], "job_id": harvest["id"]}
    return {"available": True, "workflow": None, "name": None, "job_id": None}


def job_payload(row):
    output = Path(row["output_dir"])
    phase_started_at = row["updated_at"] if row["state"] in {"running_sample", "running_full"} else None
    complete = row["state"] == "complete"
    # A finished run's checkpoint never changes again, yet results.jsonl can be
    # hundreds of megabytes and re-reading it on every dashboard poll pinned
    # both web workers at 100% CPU. Completed jobs take their request total
    # from the engine's final report and scan the checkpoint only if that
    # report is missing.
    checkpoints = {"requests": 0, "verified": 0, "domains": 0}
    if complete:
        results = final_request_count(row["run_log"])
        if not results:
            checkpoints = harvest_checkpoint_stats(output, None)
            results = checkpoints["requests"]
    else:
        checkpoints = harvest_checkpoint_stats(output, phase_started_at)
        results = checkpoints["requests"]
    verified_output = csv_rows(output / "verified_send_ready.csv")
    verified = verified_output if complete else checkpoints["verified"]
    enriched = csv_rows(output / "enriched_all.csv")
    domains = max(csv_rows(output / "domain_patterns.csv"), checkpoints["domains"])
    report = {}
    for column in ("dry_log", "sample_log"):
        path = row[column]
        if path and Path(path).exists():
            report.update(parse_report(Path(path).read_text(encoding="utf-8", errors="replace")))
    projected = int(str(report.get("projected_requests", "0")).replace(",", "") or 0)
    # The forecast comes from the dry run and the sample, and real runs can
    # overshoot it (SkyHigh spent 294,234 against a 71,155 projection). Never
    # show 100% until the engine has actually reported a finished run.
    if row["state"] == "complete":
        percent = 100
    elif projected:
        percent = min(99.9, round(results * 100 / projected, 1))
    else:
        percent = 0
    unique_domains = int(str(report.get("unique_domains", "0")).replace(",", "") or 0)
    if row["state"] == "running_sample":
        stage = "Live sample: testing 50 domains"
    elif row["state"] == "running_full":
        if unique_domains and domains < unique_domains:
            stage = "Stage 1 of 3: checking company domains"
        elif checkpoints["has_contact_checks"]:
            stage = "Stage 3 of 3: verifying individual contacts"
        else:
            stage = "Stage 2 of 3: discovering email patterns"
    elif row["state"] == "complete":
        stage = "Complete"
    elif row["state"] in {"queued_sample", "queued_full"}:
        stage = "Waiting in queue"
    else:
        stage = row["state"].replace("_", " ").title()
    verified_note = "from the completed sample; contact verification comes in Stage 3" if row["state"] == "running_full" and "Stage 3" not in stage else "send-ready contacts"
    resumable = (
        row["state"] == "failed"
        and bool(row["run_log"])
        and bool(row["input_file"])
        and Path(row["input_file"]).exists()
    )
    return {
        "id": row["id"], "name": row["name"], "slug": row["slug"], "state": row["state"],
        "created_at": row["created_at"], "updated_at": row["updated_at"], "error": row["error"],
        "results": results, "verified": verified, "enriched": enriched, "domains": domains,
        "percent": percent, "stage": stage, "verified_note": verified_note,
        "report": report, "alive": process_alive(row["pid"]), "resumable": resumable,
        "downloads": {
            "verified": (output / "verified_send_ready.csv").exists(),
            "enriched": (output / "enriched_all.csv").exists(),
        },
    }


def verification_payload(row):
    output = Path(row["output_dir"])
    checked, accepted_so_far = verification_checkpoint_stats(output)
    accepted_output = output / "accepted_rows.csv"
    accepted = csv_rows(accepted_output) if accepted_output.exists() else accepted_so_far
    total = int(row["unique_emails"] or 0)
    percent = min(100, round(checked * 100 / total, 1)) if total else (100 if row["state"] == "complete" else 0)
    return {
        "id": row["id"], "name": row["name"], "slug": row["slug"], "state": row["state"],
        "email_column": row["email_column"], "total_rows": row["total_rows"],
        "unique_emails": total, "checked": checked, "accepted": accepted, "percent": percent,
        "created_at": row["created_at"], "updated_at": row["updated_at"], "error": row["error"],
        "alive": process_alive(row["pid"]), "download": accepted_output.exists(),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        supplied = request.form.get("password", "")
        expected = os.environ.get("HARVESTER_ADMIN_PASSWORD", "")
        if expected and secrets.compare_digest(supplied, expected):
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    with db() as conn:
        jobs = [job_payload(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id DESC")]
    return render_template("dashboard.html", jobs=jobs, daily_cap=DAILY_CAP, key=key_status(), section="harvester")


@app.route("/verification")
@login_required
def verification_dashboard():
    with db() as conn:
        jobs = [verification_payload(row) for row in conn.execute("SELECT * FROM verification_jobs ORDER BY id DESC")]
    return render_template("verification_dashboard.html", jobs=jobs, key=key_status(), section="verification")


@app.route("/verification/new", methods=["GET", "POST"])
@login_required
def new_verification():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            return render_template("new_verification.html", error="Choose a CSV file.", section="verification", key=key_status()), 400
        name = request.form.get("name", "").strip() or Path(upload.filename).stem
        slug = unique_slug(name, "verification_jobs")
        job_dir = CLIENTS_DIR / "verification" / slug
        job_dir.mkdir(parents=True)
        source = job_dir / (secure_filename(upload.filename) or "source.csv")
        upload.save(source)
        headers = read_headers(source)
        email_column = detect_email_column(headers)
        total_rows, unique_emails = email_stats(source, email_column)
        state = "queued" if email_column else "mapping"
        timestamp = now()
        with db() as conn:
            cursor = conn.execute(
                "INSERT INTO verification_jobs (name,slug,state,source_file,output_dir,email_column,total_rows,unique_emails,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, slug, state, str(source), str(job_dir / "output"), email_column, total_rows, unique_emails, timestamp, timestamp),
            )
            job_id = cursor.lastrowid
        return redirect(url_for("verification_detail", job_id=job_id) if email_column else url_for("map_verification", job_id=job_id))
    return render_template("new_verification.html", section="verification", key=key_status())


@app.route("/verification/<int:job_id>/map", methods=["GET", "POST"])
@login_required
def map_verification(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        abort(404)
    headers = read_headers(Path(row["source_file"]))
    error = None
    if request.method == "POST":
        email_column = request.form.get("email_column", "")
        if email_column not in headers:
            error = "Choose the column containing email addresses."
        else:
            _, unique_emails = email_stats(Path(row["source_file"]), email_column)
            with db() as conn:
                conn.execute("UPDATE verification_jobs SET email_column=?,unique_emails=?,state='queued',updated_at=? WHERE id=?",
                             (email_column, unique_emails, now(), job_id))
            return redirect(url_for("verification_detail", job_id=job_id))
    return render_template("map_verification.html", job=row, headers=headers, error=error, section="verification", key=key_status())


@app.route("/verification/<int:job_id>")
@login_required
def verification_detail(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        abort(404)
    log = ""
    if row["log_file"] and Path(row["log_file"]).exists():
        log = "\n".join(Path(row["log_file"]).read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
    return render_template("verification_job.html", job=verification_payload(row), log=log, key=key_status(), section="verification")


@app.post("/verification/<int:job_id>/pause")
@login_required
def pause_verification(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["pid"] and process_alive(row["pid"]):
            os.kill(row["pid"], 15)
            conn.execute("UPDATE verification_jobs SET state='stopping',updated_at=? WHERE id=?", (now(), job_id))
    return redirect(url_for("verification_detail", job_id=job_id))


@app.post("/verification/<int:job_id>/resume")
@login_required
def resume_verification(job_id):
    with db() as conn:
        conn.execute("UPDATE verification_jobs SET state='queued',error=NULL,updated_at=? WHERE id=? AND state IN ('paused','failed')", (now(), job_id))
    return redirect(url_for("verification_detail", job_id=job_id))


@app.route("/verification/<int:job_id>/download")
@login_required
def download_verification(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        abort(404)
    path = Path(row["output_dir"]) / "accepted_rows.csv"
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"{row['slug']}-verified-accepted.csv")


@app.route("/api/key-status")
@login_required
def api_key_status():
    return jsonify(key_status())


@app.route("/api/verification/<int:job_id>")
@login_required
def api_verification_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        abort(404)
    return jsonify(verification_payload(row))


@app.route("/jobs/new", methods=["GET", "POST"])
@login_required
def new_job():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        upload = request.files.get("file")
        if not name or not upload or not upload.filename.lower().endswith(".csv"):
            return render_template("new_job.html", error="Enter a list name and choose a CSV file.", section="harvester"), 400
        slug = unique_slug(name)
        client_dir = CLIENTS_DIR / slug
        client_dir.mkdir(parents=True)
        source = client_dir / (secure_filename(upload.filename) or "source.csv")
        upload.save(source)
        headers = read_headers(source)
        mapping = detect_mapping(headers)
        timestamp = now()
        with db() as conn:
            cursor = conn.execute(
                "INSERT INTO jobs (name, slug, state, source_file, output_dir, mapping_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, slug, "mapping", str(source), str(client_dir / "output"), json.dumps(mapping), timestamp, timestamp),
            )
            job_id = cursor.lastrowid
        return redirect(url_for("map_job", job_id=job_id))
    return render_template("new_job.html", section="harvester")


@app.route("/jobs/<int:job_id>/map", methods=["GET", "POST"])
@login_required
def map_job(job_id):
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        abort(404)
    headers = read_headers(Path(job["source_file"]))
    mapping = json.loads(job["mapping_json"] or "{}")
    error = None
    if request.method == "POST":
        mapping = {target: request.form.get(target, "") for target in ALIASES}
        try:
            target = Path(job["source_file"]).parent / "input.csv"
            prepare_input(Path(job["source_file"]), target, mapping)
            dry_log = Path(job["source_file"]).parent / "dry_run.log"
            code = run_command(["--input", str(target), "--output-dir", job["output_dir"], "--dry-run", "--daily-cap", str(DAILY_CAP)], dry_log)
            state = "dry_ready" if code == 0 else "failed"
            with db() as conn:
                conn.execute("UPDATE jobs SET input_file=?, mapping_json=?, dry_log=?, state=?, updated_at=?, error=? WHERE id=?",
                             (str(target), json.dumps(mapping), str(dry_log), state, now(), None if code == 0 else "Dry run failed", job_id))
            return redirect(url_for("job_detail", job_id=job_id))
        except Exception as exc:
            error = str(exc)
    return render_template("map_job.html", job=job, headers=headers, mapping=mapping, targets=ALIASES, error=error, section="harvester")


@app.route("/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        abort(404)
    payload = job_payload(row)
    logs = {}
    for name, column in (("Dry run", "dry_log"), ("Live sample", "sample_log"), ("Full run", "run_log")):
        path = row[column]
        if path and Path(path).exists():
            logs[name] = "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
    return render_template("job.html", job=payload, logs=logs, section="harvester")


@app.post("/jobs/<int:job_id>/approve-sample")
@login_required
def approve_sample(job_id):
    with db() as conn:
        conn.execute("UPDATE jobs SET state='queued_sample', auto_full=1, updated_at=?, error=NULL WHERE id=? AND state='dry_ready'", (now(), job_id))
    return redirect(url_for("job_detail", job_id=job_id))


@app.post("/jobs/<int:job_id>/approve-full")
@login_required
def approve_full(job_id):
    with db() as conn:
        conn.execute("UPDATE jobs SET state='queued_full', updated_at=?, error=NULL WHERE id=? AND state IN ('sample_ready','paused')", (now(), job_id))
    return redirect(url_for("job_detail", job_id=job_id))


@app.post("/jobs/<int:job_id>/resume-full")
@login_required
def resume_full(job_id):
    """Queue a full run that stopped early; the engine resumes from checkpoints."""
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["run_log"] and row["input_file"] and Path(row["input_file"]).exists():
            conn.execute("UPDATE jobs SET state='queued_full', error=NULL, updated_at=? WHERE id=? AND state='failed'", (now(), job_id))
    return redirect(url_for("job_detail", job_id=job_id))


@app.post("/jobs/<int:job_id>/pause")
@login_required
def pause_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["pid"] and process_alive(row["pid"]) and not row["external"]:
            os.kill(row["pid"], 15)
            conn.execute("UPDATE jobs SET state='pausing', updated_at=? WHERE id=?", (now(), job_id))
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/api/jobs")
@login_required
def api_jobs():
    with db() as conn:
        return jsonify([job_payload(row) for row in conn.execute("SELECT * FROM jobs ORDER BY id DESC")])


@app.route("/api/jobs/<int:job_id>")
@login_required
def api_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        abort(404)
    return jsonify(job_payload(row))


@app.route("/jobs/<int:job_id>/download/<kind>")
@login_required
def download(job_id, kind):
    names = {"verified": "verified_send_ready.csv", "enriched": "enriched_all.csv"}
    if kind not in names:
        abort(404)
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        abort(404)
    path = Path(row["output_dir"]) / names[kind]
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=f"{row['slug']}-{names[kind]}")


def reap_children():
    """Collect finished engine processes so they do not linger as zombies."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except (ChildProcessError, OSError):
            return
        if pid == 0:
            return


def reconcile_active_job(conn, active):
    """Record the final state of a webapp-managed job whose engine stopped.

    Returns True when the job was resolved. A job only counts as complete
    when its own log carries the engine's completion marker; a process that
    simply disappeared is reported as stopped, not as finished.
    """
    if active["state"] in {"running_full", "pausing"}:
        finished = full_run_completed(active["run_log"])
    else:
        finished = sample_completed_successfully(active["sample_log"])
    if not finished and process_alive(active["pid"]):
        return False
    error = None
    if active["state"] == "pausing":
        final = "complete" if finished else "paused"
    elif active["state"] == "running_sample":
        final = "queued_full" if finished and active["auto_full"] else ("sample_ready" if finished else "failed")
        if not finished:
            error = "The live sample stopped before completing successfully. Check the sample log."
    else:
        final = "complete" if finished else "failed"
        if not finished:
            error = "The full run stopped before finishing. Completed checkpoints are kept; resume to continue where it left off."
    conn.execute("UPDATE jobs SET state=?, pid=NULL, updated_at=?, error=? WHERE id=?", (final, now(), error, active["id"]))
    return True


def worker_loop():
    while True:
        try:
            reap_children()
            with db() as conn:
                active = conn.execute("SELECT * FROM jobs WHERE state IN ('running_sample','running_full','pausing') AND external=0 ORDER BY id LIMIT 1").fetchone()
                if active and reconcile_active_job(conn, active):
                    active = None
                external = conn.execute("SELECT * FROM jobs WHERE state='running_full' AND external=1").fetchall()
                for item in external:
                    if item["pid"] and not process_alive(item["pid"]):
                        conn.execute("UPDATE jobs SET state='complete', pid=NULL, updated_at=? WHERE id=?", (now(), item["id"]))
                verification_active = conn.execute("SELECT * FROM verification_jobs WHERE state IN ('running','stopping') ORDER BY id LIMIT 1").fetchone()
                if verification_active and not process_alive(verification_active["pid"]):
                    output_exists = (Path(verification_active["output_dir"]) / "accepted_rows.csv").exists()
                    stopped = verification_active["state"] == "stopping"
                    conn.execute("UPDATE verification_jobs SET state=?,pid=NULL,updated_at=?,error=? WHERE id=?",
                                 ("paused" if stopped else ("complete" if output_exists else "failed"), now(), None if stopped or output_exists else "Verification stopped before producing a result file.", verification_active["id"]))
                    verification_active = None
                any_running = conn.execute("SELECT 1 FROM jobs WHERE state IN ('running_sample','running_full','pausing') LIMIT 1").fetchone() or verification_active
                queued = None if any_running else conn.execute("SELECT * FROM jobs WHERE state IN ('queued_sample','queued_full') ORDER BY created_at LIMIT 1").fetchone()
                queued_verification = None if any_running else conn.execute("SELECT * FROM verification_jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
                if queued and queued_verification and queued_verification["created_at"] < queued["created_at"]:
                    queued = None
                elif queued:
                    queued_verification = None
            if queued:
                mode = "sample" if queued["state"] == "queued_sample" else "full"
                job_dir = Path(queued["input_file"]).parent
                log_path = job_dir / ("sample.log" if mode == "sample" else "run.log")
                args = ["--input", queued["input_file"], "--output-dir", queued["output_dir"], "--daily-cap", str(DAILY_CAP)]
                args += ["--live-sample", "--domain-limit", "50", "--workers", "3"] if mode == "sample" else ["--full-run", "--workers", str(WORKERS)]
                env = os.environ.copy()
                with log_path.open("w", encoding="utf-8") as log:
                    proc = subprocess.Popen([python_executable(), "-u", str(ENGINE)] + args,
                                            cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
                with db() as conn:
                    conn.execute(f"UPDATE jobs SET state=?, pid=?, {('sample_log' if mode == 'sample' else 'run_log')}=?, updated_at=? WHERE id=?",
                                 (f"running_{mode}", proc.pid, str(log_path), now(), queued["id"]))
            elif queued_verification:
                output_dir = Path(queued_verification["output_dir"])
                output_dir.mkdir(parents=True, exist_ok=True)
                log_path = Path(queued_verification["source_file"]).parent / "verification.log"
                args = ["--input", queued_verification["source_file"], "--email-column", queued_verification["email_column"],
                        "--output-dir", queued_verification["output_dir"], "--daily-cap", str(DAILY_CAP), "--workers", str(WORKERS)]
                with log_path.open("a", encoding="utf-8") as log:
                    proc = subprocess.Popen([python_executable(), "-u", str(VERIFIER)] + args, cwd=BASE_DIR,
                                            stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy(), start_new_session=True)
                with db() as conn:
                    conn.execute("UPDATE verification_jobs SET state='running',pid=?,log_file=?,updated_at=?,error=NULL WHERE id=? AND state='queued'",
                                 (proc.pid, str(log_path), now(), queued_verification["id"]))
        except Exception:
            traceback.print_exc()
        time.sleep(3)


init_db()


if __name__ == "__main__":
    threading.Thread(target=worker_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=8080)
