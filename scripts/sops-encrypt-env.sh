#!/usr/bin/env sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <plain-env-file> <encrypted-output-file>" >&2
  exit 1
fi

plain_file=$1
encrypted_file=$2

if [ ! -f "$plain_file" ]; then
  echo "missing plaintext env file: $plain_file" >&2
  exit 1
fi

if [ ! -f ".sops.yaml" ]; then
  echo "missing .sops.yaml in repo root" >&2
  exit 1
fi

if [ ! -f "secrets.keys" ] && [ -z "${SOPS_AGE_KEY_FILE:-}" ]; then
  echo "missing secrets.keys and SOPS_AGE_KEY_FILE is not set" >&2
  exit 1
fi

export SOPS_AGE_KEY_FILE=${SOPS_AGE_KEY_FILE:-./secrets.keys}
sops --encrypt --filename-override "$encrypted_file" --input-type dotenv --output-type dotenv "$plain_file" > "$encrypted_file"
echo "wrote $encrypted_file"
