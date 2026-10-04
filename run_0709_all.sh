#!/usr/bin/env bash
set -euo pipefail

cd /root/harvester
set -a
source ./.env
set +a

batch_dir="clients/batch-2026-09-07"

for client in red-buffer skyhigh tri; do
  client_dir="$batch_dir/$client"
  output_dir="$client_dir/output"
  log_file="$client_dir/full-run.log"

  if [ -f "$client_dir/$client-output.tar.gz" ] && \
     grep -q "Completed and packaged $client" "$log_file"; then
    printf '%s Skipping already completed and packaged %s.\n' \
      "$(date --iso-8601=seconds)" "$client" >>"$log_file"
    continue
  fi

  printf '%s Starting %s full run.\n' "$(date --iso-8601=seconds)" "$client" >>"$log_file"
  if .venv/bin/python -u email_engine.py \
    --input "$client_dir/input.csv" \
    --output-dir "$output_dir" \
    --full-run \
    --workers 4 \
    --interval-ms 44 \
    --daily-cap 800000 \
    >>"$log_file" 2>&1
  then
    tar -czf "$client_dir/$client-output.tar.gz" -C "$client_dir" output
    printf '%s Completed and packaged %s.\n' "$(date --iso-8601=seconds)" "$client" >>"$log_file"
  else
    status=$?
    printf '%s %s exited with status %s; resume with the same output directory.\n' \
      "$(date --iso-8601=seconds)" "$client" "$status" >>"$log_file"
    exit "$status"
  fi
done

.venv/bin/python "$batch_dir/combine_0709_outputs.py" \
  >"$batch_dir/combine.log" 2>&1
tar -czf "$batch_dir/batch-2026-09-07-combined-output.tar.gz" \
  -C "$batch_dir" combined-output
