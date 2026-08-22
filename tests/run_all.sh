#!/usr/bin/env bash
# Offline suites (no credentials) + live route probes (network, no credentials).
# Each suite: exit 0 = passed, 3 = skipped by itself, anything else = failed.
set -uo pipefail
cd "$(dirname "$0")/.."

# On a Mac without the Xcode command line tools, /usr/bin/python3 is a stub
# that opens an installer dialog and exits non-zero. Fail with a reason.
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || {
  echo "error: '$PY' not found. Install Python 3 (macOS: xcode-select --install)," >&2
  echo "       or set \$PYTHON to your interpreter." >&2
  exit 2
}

SUITES="tests/test_requests.py tests/test_auth.py tests/test_tls.py \
tests/test_shell.py tests/test_docs.py tests/test_lifecycle.py \
tests/test_live_routes.py"

names=(); codes=()
for t in $SUITES; do
  echo "=============== $t ==============="
  "$PY" "$t"
  codes+=("$?")
  names+=("$t")
  echo
done

echo "=============== summary ==============="
fail=0
for ((i = 0; i < ${#names[@]}; i++)); do
  case "${codes[$i]}" in
    0) printf '  PASS  %s\n'    "${names[$i]}" ;;
    3) printf '  SKIP  %s\n'    "${names[$i]}" ;;
    *) printf '  FAIL  %s (exit %s)\n' "${names[$i]}" "${codes[$i]}"; fail=1 ;;
  esac
done
if [ "$fail" -ne 0 ]; then
  echo
  echo "Re-run a failing suite on its own to see why, e.g.:"
  for ((i = 0; i < ${#names[@]}; i++)); do
    [ "${codes[$i]}" -ne 0 ] && [ "${codes[$i]}" -ne 3 ] && \
      echo "    $PY ${names[$i]}"
  done
fi
exit $fail
