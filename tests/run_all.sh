#!/usr/bin/env bash
# Offline suites (no credentials needed) + live route probes (network, no auth).
set -u
cd "$(dirname "$0")/.."
fail=0
for t in tests/test_requests.py tests/test_docs.py tests/test_live_routes.py; do
  echo "=============== $t ==============="
  python3 "$t" || fail=1
  echo
done
exit $fail
