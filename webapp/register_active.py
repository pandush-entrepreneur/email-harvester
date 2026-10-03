import argparse
import json
from pathlib import Path

from app import db, now


parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True)
parser.add_argument("--slug", required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--pid", required=True, type=int)
parser.add_argument("--dry-log")
parser.add_argument("--sample-log")
parser.add_argument("--run-log")
args = parser.parse_args()
display_name = args.name.replace("~", " ")

timestamp = now()
with db() as conn:
    conn.execute(
        """INSERT INTO jobs
        (name, slug, state, input_file, output_dir, mapping_json, dry_log, sample_log, run_log, pid, external, created_at, updated_at)
        VALUES (?, ?, 'running_full', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET name=excluded.name, state='running_full', input_file=excluded.input_file,
        output_dir=excluded.output_dir, dry_log=excluded.dry_log, sample_log=excluded.sample_log,
        run_log=excluded.run_log, pid=excluded.pid, external=1, updated_at=excluded.updated_at""",
        (display_name, args.slug, str(Path(args.input)), str(Path(args.output)),
         json.dumps({"First Name": "First Name", "Last Name": "Last Name", "Company Domain": "Company Domain"}),
         args.dry_log, args.sample_log, args.run_log, args.pid, timestamp, timestamp),
    )
print(f"Registered {display_name}")
