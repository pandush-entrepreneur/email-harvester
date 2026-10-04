#!/usr/bin/env python3
"""Normalize the 07.09 client exports into one master and per-client run inputs."""

import csv
from collections import Counter
from pathlib import Path


SOURCE_DIR = Path("07.09")
OUTPUT_DIR = SOURCE_DIR / "merged"

OUTPUT_FIELDS = [
    "First Name",
    "Last Name",
    "Full Name",
    "Job Title",
    "Company",
    "City",
    "State or Province",
    "Country",
    "LinkedIn Profile",
    "Company Domain",
    "Client",
]

SOURCES = [
    ("Red Buffer 06.09.csv", "Red Buffer", "red-buffer-input.csv"),
    ("Red Buffer Signals List 07.09.csv", "Red Buffer", "red-buffer-input.csv"),
    ("SkyHigh 06.09.csv", "SkyHigh", "skyhigh-input.csv"),
    ("TRI 06.09.csv", "The Revenue Inbox", "tri-input.csv"),
]

INPUT_TO_OUTPUT = {
    "First Name": "First Name",
    "Last Name": "Last Name",
    "Full Name": "Full Name",
    "Job Title": "Job Title",
    "Company": "Company",
    "City": "City",
    "State or Province": "State or Province",
    "Country": "Country",
    "LinkedIn Profile": "LinkedIn Profile",
    "Domain": "Company Domain",
}


def normalized_row(row, client):
    output = {field: "" for field in OUTPUT_FIELDS}
    for source, target in INPUT_TO_OUTPUT.items():
        output[target] = (row.get(source) or "").strip()
    output["Client"] = client
    return output


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    master_path = OUTPUT_DIR / "07.09-all-clients-master.csv"
    split_handles = {}
    split_writers = {}
    counts = Counter()

    try:
        for _, _, split_name in SOURCES:
            if split_name in split_handles:
                continue
            handle = (OUTPUT_DIR / split_name).open("w", encoding="utf-8-sig", newline="")
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            split_handles[split_name] = handle
            split_writers[split_name] = writer

        with master_path.open("w", encoding="utf-8-sig", newline="") as master_handle:
            master_writer = csv.DictWriter(master_handle, fieldnames=OUTPUT_FIELDS)
            master_writer.writeheader()

            for filename, client, split_name in SOURCES:
                source_path = SOURCE_DIR / filename
                with source_path.open("r", encoding="utf-8-sig", newline="") as source_handle:
                    reader = csv.DictReader(source_handle)
                    headers = set(reader.fieldnames or [])
                    missing = sorted(set(INPUT_TO_OUTPUT) - headers)
                    if missing:
                        raise SystemExit(f"{filename}: missing required columns: {', '.join(missing)}")

                    for source_row in reader:
                        output_row = normalized_row(source_row, client)
                        master_writer.writerow(output_row)
                        split_writers[split_name].writerow(output_row)
                        counts[filename] += 1
                        counts[client] += 1
                        counts["total"] += 1
    finally:
        for handle in split_handles.values():
            handle.close()

    print(f"Master: {master_path} ({counts['total']:,} rows)")
    for filename, client, _ in SOURCES:
        print(f"  {filename}: {counts[filename]:,} rows -> {client}")
    for client in ("Red Buffer", "SkyHigh", "The Revenue Inbox"):
        print(f"  {client} total: {counts[client]:,} rows")


if __name__ == "__main__":
    main()
