#!/usr/bin/env bash
# Verify the skill against a real project, in one command.
#
#   bash tests/run_live.sh PROJECT.dataset.table [PROJECT]
#
# No token to generate: the CLI discovers credentials itself. If you have run
# `gcloud auth application-default login` once (or are on a GCE/Cloud Run box,
# or have $GOOGLE_APPLICATION_CREDENTIALS set), this just works.
set -uo pipefail
cd "$(dirname "$0")/.."

BQ_TABLE="${1:-}"
PROJECT="${2:-}"
if [ -z "$BQ_TABLE" ]; then
  echo "usage: bash tests/run_live.sh PROJECT.dataset.table [PROJECT]" >&2
  echo "  (any BigQuery table you can read; the agent is created and deleted)" >&2
  exit 2
fi

PROJ_FLAG=()
[ -n "$PROJECT" ] && PROJ_FLAG=(--project "$PROJECT")
# $GDA_HOST points the whole run at a different endpoint (used by the tests).
HOST_FLAG=()
[ -n "${GDA_HOST:-}" ] && HOST_FLAG=(--host "$GDA_HOST")
# Expand arrays with the ${a[@]+"${a[@]}"} guard throughout: under `set -u`,
# bash 3.2 - still the /bin/bash macOS ships - treats "${empty[@]}" as an
# unbound variable and aborts the script.

if [ "${GDA_SKIP_OFFLINE:-}" != "1" ]; then
  echo "=============== 1/3  offline suites ==============="
  if ! bash tests/run_all.sh; then
    echo >&2
    echo "offline suites reported a failure — stopping before touching your" >&2
    echo "project. To run the live lifecycle anyway: GDA_SKIP_OFFLINE=1 $0 $*" >&2
    exit 1
  fi
fi

echo
echo "=============== 2/3  doctor ==============="
if ! python3 scripts/gda.py ${PROJ_FLAG[@]+"${PROJ_FLAG[@]}"} ${HOST_FLAG[@]+"${HOST_FLAG[@]}"} doctor; then
  cat >&2 <<'HINT'

doctor failed. The usual fixes:
  no credentials  ->  gcloud auth application-default login
  no project      ->  gcloud config set project PROJECT   (or pass it as arg 2)
  SERVICE_DISABLED->  gcloud services enable geminidataanalytics.googleapis.com \
                        --project PROJECT
  PERMISSION_DENIED-> grant the caller the geminidataanalytics.* roles
HINT
  exit 1
fi

echo
echo "=============== 3/3  live lifecycle ==============="
python3 tests/live_e2e.py ${PROJ_FLAG[@]+"${PROJ_FLAG[@]}"} --bq-table "$BQ_TABLE"
