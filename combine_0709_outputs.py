#!/usr/bin/env python3
"""Combine completed 07.09 client outputs without cross-client deduplication."""

import csv
from pathlib import Path


BATCH_DIR = Path(__file__).resolve().parent
CLIENTS = [
    ("red-buffer", "Red Buffer", 79_229),
    ("skyhigh", "SkyHigh", 109_349),
    ("tri", "The Revenue Inbox", 72_801),
]


def combine(filename, destination):
    fieldnames = None
    total = 0
    with destination.open("w", encoding="utf-8-sig", newline="") as output_handle:
        writer = None
        for folder, expected_client, expected_input_rows in CLIENTS:
            source = BATCH_DIR / folder / "output" / filename
            with source.open("r", encoding="utf-8-sig", newline="") as source_handle:
                reader = csv.DictReader(source_handle)
                if fieldnames is None:
                    fieldnames = reader.fieldnames
                    writer = csv.DictWriter(output_handle, fieldnames=fieldnames)
                    writer.writeheader()
                elif reader.fieldnames != fieldnames:
                    raise SystemExit(f"Header mismatch in {source}")

                client_rows = 0
                for row in reader:
                    if row.get("Client") != expected_client:
                        raise SystemExit(
                            f"Wrong client in {source}: expected {expected_client!r}, got {row.get('Client')!r}"
                        )
                    writer.writerow(row)
                    client_rows += 1
                    total += 1

                if filename == "enriched_all.csv" and client_rows != expected_input_rows:
                    raise SystemExit(
                        f"Row count mismatch in {source}: expected {expected_input_rows}, got {client_rows}"
                    )
    return total, fieldnames


def write_client_safe_unique(source, destination):
    seen = set()
    kept = 0
    removed = 0
    with source.open("r", encoding="utf-8-sig", newline="") as source_handle:
        reader = csv.DictReader(source_handle)
        with destination.open("w", encoding="utf-8-sig", newline="") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                email = (row.get("email") or "").strip().lower()
                client = (row.get("Client") or "").strip()
                if not email:
                    raise SystemExit("Blank email found in verified_send_ready.csv")
                key = (client, email)
                if key in seen:
                    removed += 1
                    continue
                seen.add(key)
                writer.writerow(row)
                kept += 1
    return kept, removed


def main():
    combined_dir = BATCH_DIR / "combined-output"
    combined_dir.mkdir(parents=True, exist_ok=True)

    enriched_rows, _ = combine("enriched_all.csv", combined_dir / "enriched_all.csv")
    if enriched_rows != 261_379:
        raise SystemExit(f"Combined enriched row count mismatch: {enriched_rows}")

    send_ready_rows, _ = combine(
        "verified_send_ready.csv", combined_dir / "verified_send_ready.csv"
    )
    unique_rows, removed = write_client_safe_unique(
        combined_dir / "verified_send_ready.csv",
        combined_dir / "verified_send_ready_unique_within_client.csv",
    )

    print(f"Combined enriched rows: {enriched_rows:,}")
    print(f"Combined verified send-ready rows: {send_ready_rows:,}")
    print(f"Unique within-client send-ready rows: {unique_rows:,}")
    print(f"Within-client duplicate email rows removed: {removed:,}")


if __name__ == "__main__":
    main()
