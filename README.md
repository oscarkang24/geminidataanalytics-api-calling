# geminidataanalytics skill

A [Claude Code](https://claude.com/claude-code) skill (plus a standalone Python
CLI) for the **Gemini Data Analytics / Conversational Analytics HTTP API**
(`geminidataanalytics.googleapis.com`).

It provides full CRUD over the two service surfaces:

- **DataAgentService** — reusable *data agents* that bundle a datasource + system
  instructions + context.
- **DataChatService** — *conversations* and `chat` (natural-language questions
  answered from your configured data).

The CLI (`scripts/gda.py`) is dependency-free (Python 3 stdlib only) and needs
no flags: it discovers credentials and the billing project the same way Google's
client libraries do — `$GOOGLE_APPLICATION_CREDENTIALS` service-account keys
(signed JWT grant), the gcloud ADC file (refresh-token grant), the GCE/Cloud Run
metadata server, then the `gcloud` CLI.

```bash
python3 scripts/gda.py doctor     # what will be used, and is it authorized?
```

## Prerequisites

1. Credentials from any of the sources above (on a workstation:
   `gcloud auth application-default login`)
2. API enabled: `gcloud services enable geminidataanalytics.googleapis.com --project PROJECT`
3. Caller has the relevant `geminidataanalytics.*` IAM permissions and can read
   the underlying BigQuery data

`doctor` reports which of these is missing.

## Quickstart

```bash
# Create an agent over a BigQuery table (--project is optional once detected).
python3 scripts/gda.py agents create \
    --agent-id my-agent --display-name "Sales agent" \
    --bq-table PROJECT.dataset.orders \
    --system-instruction "Revenue is net of refunds."

# Ask it a question (stateless).
python3 scripts/gda.py --project PROJECT chat --agent-id my-agent \
    --message "What were total sales last month?" --answer-only

# Fully stateless / agent-less: supply the datasource inline.
python3 scripts/gda.py --project PROJECT chat \
    --bq-table PROJECT.dataset.orders --message "How many orders yesterday?"
```

Global flags come **before** the resource:

```
python3 scripts/gda.py --project PROJECT [--location global] [--version v1] \
    [-v] <resource> <action> [flags]
```

- `--project` is optional — omit it to use the detected project.
- `--location` defaults to `global`.
- `--version` defaults to `v1` (GA). `v1beta` / `v1alpha` expose preview features.
- `-v` echoes the exact method / URL / body to stderr.
- `--access-token` (or `$GDA_ACCESS_TOKEN`) supplies an OAuth2 token directly,
  skipping credential discovery.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | The Claude Code skill definition (frontmatter + usage). |
| `scripts/gda.py` | The standalone CLI. |
| `REFERENCE.md` | Endpoint + payload reference for the API. |
| `EVAL.md` | Manual eval cases (skill triggering + end-to-end live). |
| `tests/` | Automated test suites (see below). |

## Tests

```bash
bash tests/run_all.sh      # 243 checks, no credentials needed
```

The harness is hermetic: every run starts from a scrubbed environment — ambient
credentials removed (no `$GOOGLE_APPLICATION_CREDENTIALS`, no ADC file, no metadata
server, a shadowed `gcloud`), plus a fixed `$COLUMNS`, UTF-8 mode regardless of
locale, and no proxy for loopback. Results do not depend on how the machine
running them happens to be configured. Verified against a deliberately hostile
environment combining all of those.

It ends with a per-suite `PASS` / `FAIL` / `SKIP` summary and names the
command to re-run any failing suite on its own. A suite that skips itself
(missing network, an `openssl` without the features it needs) is not a
failure and does not block the live run.

| Suite | What it checks | Needs |
| --- | --- | --- |
| `tests/test_requests.py` | Every command's exact method / path / query / headers / body, asserted against a capturing mock HTTP server, plus error handling and `--answer-only` rendering. | nothing |
| `tests/test_docs.py` | Every command shown in `SKILL.md` and `README.md` actually parses and runs. | nothing |
| `tests/test_auth.py` | Every credential and project source, against a mock OAuth endpoint, a mock metadata server, a real RSA service-account key, and a stub `gcloud` — plus precedence and failure modes. | nothing |
| `tests/test_tls.py` | Certificate handling: the certifi retry (against a real HTTPS server with a private CA), and that an explicitly-set `$GOOGLE_APPLICATION_CREDENTIALS` never falls through to another identity. | nothing |
| `tests/test_shell.py` | Shell portability — notably that no array is expanded unguarded under `set -u`, which aborts on the bash 3.2 macOS still ships. | nothing |
| `tests/test_lifecycle.py` | The full lifecycle with **zero flags** against `tests/fake_api.py`, with credentials from a simulated metadata server. Exercises `live_e2e.py` itself. | nothing |
| `tests/test_live_routes.py` | Each URL the CLI builds resolves to the expected RPC on the **real** API. Unauthenticated calls return 401 naming the resolved method, while a wrong path returns 404 — so this validates routing without credentials. Skips itself (exit 3) when `googleapis.com` is unreachable, rather than reporting phantom routing failures. | network egress, no credentials |
| `tests/live_e2e.py` | Full create → chat → converse → update → delete lifecycle (suite B of `EVAL.md`), with cleanup. | real credentials |
| `tests/run_live.sh` | One command: offline suites → `doctor` → live lifecycle, with fix-it guidance when `doctor` fails. | real credentials |

Against a real project, one command runs everything — offline suites, `doctor`,
then the live lifecycle. There is no token to generate; credentials are
discovered automatically:

```bash
bash tests/run_live.sh PROJECT.dataset.orders          # project auto-detected
bash tests/run_live.sh PROJECT.dataset.orders MYPROJ   # or name it explicitly

# Skip the offline suites and go straight to the live lifecycle:
GDA_SKIP_OFFLINE=1 bash tests/run_live.sh PROJECT.dataset.orders
```

It creates a data agent and a conversation, exercises all three chat modes, and
deletes both afterwards. Ids are stamped per run, so it is safe to re-run (agent
delete is a soft delete, and a fixed id would stay reserved for ~30 days).

## Using as a Claude Code skill

```bash
bash install.sh                 # -> ~/.claude/skills/geminidataanalytics
bash install.sh /some/dir       # or a different skills folder
```

It copies only what the skill needs at runtime (`SKILL.md`, `REFERENCE.md`,
`scripts/gda.py`), verifies the installed CLI runs, and leaves the tests and
evals behind. Restart Claude Code afterwards so it picks the skill up.

The skill activates on prompts like
"create a data agent", "chat with my data agent", or "ask a question over
BigQuery with the Conversational Analytics API".

## API docs

https://cloud.google.com/gemini/docs/conversational-analytics-api
