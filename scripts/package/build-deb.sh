#!/bin/sh
# Build the Debian packages inside Docker (python:3.12-slim), for use on macOS or any host
# without a Linux Python 3.12. Arguments are passed through to scripts/package/build-deb.py
# (`--arch amd64|arm64`, `--wheels-dir DIR`, `--out DIR`). Outputs land in dist/ owned by
# the invoking user; pip's cache is kept in build/deb/pip-cache across runs.
set -eu

repo="$(cd "$(dirname "$0")/../.." && pwd)"
image="${CHINTU_DEB_IMAGE:-python:3.12-slim}"
mkdir -p "$repo/build/deb/pip-cache" "$repo/dist"

exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -e PIP_CACHE_DIR=/src/build/deb/pip-cache \
    ${SOURCE_DATE_EPOCH:+-e SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"} \
    -v "$repo:/src" -w /src \
    "$image" python3 scripts/package/build-deb.py "$@"
