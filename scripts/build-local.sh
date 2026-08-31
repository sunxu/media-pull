#!/bin/sh
set -eu

image=${IMAGE:-media-bundle:local}
jobs=${JOBS:-8}
build_dir=${BUILD_DIR:-.media-build}
cache_dir=${CACHE_DIR:-.media-cache}

if [ "$#" -eq 1 ]; then
  manifest_args="--manifest-file"
  manifest_value=$1
elif [ "$#" -eq 0 ]; then
  : "${MANIFEST_URL:?Set MANIFEST_URL or pass a local manifest file}"
  manifest_args="--manifest-url"
  manifest_value=$MANIFEST_URL
else
  echo "Usage: scripts/build-local.sh [manifest.txt]" >&2
  exit 2
fi

python3 scripts/prepare.py "$manifest_args" "$manifest_value" \
  --jobs "$jobs" --output "$build_dir" --cache-dir "$cache_dir"
docker build -f "$build_dir/context/Dockerfile" -t "$image" "$build_dir/context"

echo "Built $image"
