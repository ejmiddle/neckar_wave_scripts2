#!/usr/bin/env sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: $0 <encrypted-env-file> [output-file]" >&2
  exit 1
fi

encrypted_file=$1
output_file=${2:-}

if [ ! -f "$encrypted_file" ]; then
  echo "missing encrypted env file: $encrypted_file" >&2
  exit 1
fi

if [ ! -f "secrets.keys" ] && [ -z "${SOPS_AGE_KEY_FILE:-}" ]; then
  echo "missing secrets.keys and SOPS_AGE_KEY_FILE is not set" >&2
  exit 1
fi

export SOPS_AGE_KEY_FILE=${SOPS_AGE_KEY_FILE:-./secrets.keys}

if [ -n "$output_file" ]; then
  sops --decrypt --input-type dotenv --output-type dotenv "$encrypted_file" > "$output_file"
  echo "wrote $output_file"
else
  exec sops --decrypt --input-type dotenv --output-type dotenv "$encrypted_file"
fi
