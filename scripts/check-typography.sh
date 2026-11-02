#!/usr/bin/env bash
set -euo pipefail

PATTERN='\x{2014}|[\x{1F000}-\x{1FAFF}\x{2300}-\x{23FF}\x{2600}-\x{27BF}\x{2B00}-\x{2BFF}\x{FE0F}]'

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export LC_ALL=C.UTF-8

set +e
matches="$(grep -rnP "$PATTERN" frontend/src backend/app)"
grep_status=$?
set -e

if [[ $grep_status -gt 1 ]]; then
  echo "grep failed with status $grep_status" >&2
  exit "$grep_status"
fi

if [[ -n "$matches" ]]; then
  echo "$matches"
  echo >&2
  echo "Em-dashes or emojis found in UI or backend code. Use a comma, colon, period or lucide icon instead." >&2
  exit 1
fi

echo "Typography check passed."
