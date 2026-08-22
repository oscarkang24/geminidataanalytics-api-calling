#!/usr/bin/env bash
# Offline suites (no credentials needed) + live route probes (network, no auth).
set -u
cd "$(dirname "$0")/.."
fail=0
for t in tests/test_requests.py tests/test_auth.py tests/test_docs.py tests/test_lifecycle.py tests/test_live_routes.py; do
  echo "=============== $t ==============="
  python3 "$t"
  rc=$?
  # 3 = the suite skipped itself (e.g. no network egress); not a failure.
  [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ] && fail=1
  echo
done
exit $fail
