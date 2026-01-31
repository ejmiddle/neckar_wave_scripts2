#!/bin/sh
set -e

# Ensure storage dir exists (helps with mounted volumes)
mkdir -p /qdrant/storage

if [ -x /usr/local/bin/qdrant ]; then
  exec /usr/local/bin/qdrant
fi

exec /qdrant/qdrant
