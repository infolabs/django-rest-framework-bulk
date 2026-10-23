#!/bin/sh
set -eu
# Run from the repository root, including both modern and legacy regressions.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
exec python -m pytest "$@"
