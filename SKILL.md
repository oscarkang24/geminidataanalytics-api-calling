---
name: geminidataanalytics
description: >
  Use for the Gemini Data Analytics / Conversational Analytics API
  (geminidataanalytics.googleapis.com, also called Data QnA or the CA API):
  create, list, get, update or delete dataAgents; create or manage
  conversations; and any request to ask, chat, or answer natural-language
  questions over BigQuery or Looker data through a data agent — e.g. "create a
  data agent on myproj.sales.orders", "list data agents", "ask my sales agent
  what revenue was last month", "let the team query our warehouse in plain
  English". Wraps the REST endpoints in a dependency-free Python CLI that
  authenticates via Application Default Credentials. Do NOT use for: writing or
  running plain BigQuery SQL, inspecting table schemas, local CSV or pandas
  analysis, or other Google agent products (Vertex AI Agent Builder /
  Agentspace, ADK, Dialogflow) — those are separate APIs. If someone asks for
  natural-language querying of a warehouse without naming a product, confirm
  they mean the Conversational Analytics API before running anything.
---

# Gemini Data Analytics HTTP API

Full CRUD over the public Conversational Analytics API at
`https://geminidataanalytics.googleapis.com`. Two service surfaces:

- **DataAgentService** — manage `dataAgents` (reusable agents that bundle a
  datasource + system instructions + context).
- **DataChatService** — manage `conversations` and run `chat` (natural-language
  questions answered from the configured data).

Credentials and the billing project are discovered automatically, in the order
Google's own client libraries use, so **no flags are needed** on a configured
machine:

| | token | project |
| --- | --- | --- |
| 1 | `$GDA_ACCESS_TOKEN` | `--project` |
| 2 | `$GOOGLE_APPLICATION_CREDENTIALS` service-account key (signed JWT grant) | `$GDA_PROJECT` / `$GOOGLE_CLOUD_PROJECT` / `$GCLOUD_PROJECT` / `$CLOUDSDK_CORE_PROJECT` |
| 3 | gcloud ADC file (refresh-token grant) | ADC `quota_project_id` / key `project_id` |
| 4 | GCE / Cloud Run metadata server | metadata server |
| 5 | `gcloud auth [application-default] print-access-token` | `gcloud config get-value project` |

Setting `$GOOGLE_APPLICATION_CREDENTIALS` names the identity to use: if no token
can be obtained from it the CLI fails rather than quietly running as a different
principal. `--access-token` and `--project` override their chain. The project is sent in
`x-goog-user-project`.

Run `python3 scripts/gda.py doctor` first (or `bash tests/run_live.sh
PROJECT.dataset.table` to check the whole thing end to end) — it reports which source supplied
each, then makes a real call to confirm the API is enabled and authorized:

```bash
python3 scripts/gda.py doctor
# [ok]   credentials: ADC file (~/.config/gcloud/application_default_credentials.json)
# [ok]   project: my-proj (from $GOOGLE_CLOUD_PROJECT)
# [ok]   API reachable and authorized: 3 data agent(s) visible
```

## Prerequisites

1. Credentials from any source in the table above (on a workstation:
   `gcloud auth application-default login`).
2. The API enabled on the project:
   `gcloud services enable geminidataanalytics.googleapis.com --project PROJECT`
3. The caller has the relevant `geminidataanalytics.*` IAM permissions and can
   read the underlying BigQuery data.

`doctor` tells you which of these is missing.

## The CLI

Everything goes through `scripts/gda.py`. Global flags come **before** the
resource:

```
python3 scripts/gda.py --project PROJECT [--location global] [--version v1] \
    [-v] <resource> <action> [flags]
```

- `--project` is optional — omit it to use the detected project.
- `--location` defaults to `global`.
- `--version` defaults to `v1` (**GA** — the version external customers should
  use). `v1beta` (BETA) and `v1alpha` (ALPHA) expose preview-only features.
- `-v` echoes the method/URL/body to stderr — use it to show the user the exact
  HTTP call.
- `--access-token` (or `$GDA_ACCESS_TOKEN`) supplies an OAuth2 token directly,
  skipping credential discovery.
- `-v` also reports which source the token and project came from.
- Output is always the raw JSON response, pretty-printed.

See `REFERENCE.md` for the full endpoint and payload reference.

### Data agents

```bash
# Create an agent over a BigQuery table (createSync — returns the resource).
python3 scripts/gda.py --project P agents create \
    --agent-id my-agent --display-name "Sales agent" \
    --bq-table P.dataset.orders \
    --system-instruction "Revenue is net of refunds."

python3 scripts/gda.py --project P agents get    --agent-id my-agent
python3 scripts/gda.py --project P agents list                 # all in project
python3 scripts/gda.py --project P agents list --accessible    # caller-accessible
python3 scripts/gda.py --project P agents update --agent-id my-agent --display-name "New name"
python3 scripts/gda.py --project P agents delete --agent-id my-agent
```

`--bq-table` is repeatable to give an agent multiple tables. `--python` enables
Python analysis in the context (on `create` and `update` alike). `update` masks
each field individually, so changing the table never clobbers the system
instruction.

### Conversations

```bash
python3 scripts/gda.py --project P conversations create --agent-id my-agent --conversation-id conv1
python3 scripts/gda.py --project P conversations get      --conversation-id conv1
python3 scripts/gda.py --project P conversations list
python3 scripts/gda.py --project P conversations messages --conversation-id conv1
python3 scripts/gda.py --project P conversations delete   --conversation-id conv1
```

### Chat — three modes

```bash
# 1. Stateful: persisted conversation + agent (keeps history).
python3 scripts/gda.py --project P chat --agent-id my-agent --conversation-id conv1 \
    --message "What were total sales last month?"

# 2. Stateless against an agent (no conversation persistence).
python3 scripts/gda.py --project P chat --agent-id my-agent \
    --message "Top 5 products by revenue?"

# 3. Fully stateless / agent-less: context supplied inline.
python3 scripts/gda.py --project P chat --bq-table P.dataset.orders \
    --message "How many orders yesterday?"

# Print only the final answer text instead of the full message stream.
python3 scripts/gda.py --project P chat --agent-id my-agent \
    --message "Top 5 products by revenue?" --answer-only
```

`chat` calls the streaming `:chat` endpoint, which over REST returns a
**JSON array** of `Message` objects (thoughts, generated SQL, query results, and
the final answer). Add `--answer-only` to print just the `FINAL_RESPONSE` text.

An agent supplies its own context, so the inline-context flags (`--bq-table`,
`--system-instruction`, `--python`) **cannot** be combined with `--agent-id` /
`--conversation-id` — the CLI rejects that rather than silently answering from
the agent's table. Bake the change into the agent with `agents update` instead.

A stream that fails partway still returns HTTP 200 with an `error` element
appended to the array; the CLI prints the partial answer, reports the error on
stderr, and exits non-zero.

### Raw escape hatch

For public endpoints the CLI doesn't wrap (e.g. IAM policy):

```bash
# {parent} expands to projects/P/locations/LOCATION (works in path AND body).
python3 scripts/gda.py --project P raw POST '{parent}/dataAgents/my-agent:getIamPolicy' \
    --body '{"resource":"{parent}/dataAgents/my-agent"}'

python3 scripts/gda.py --project P raw GET '{parent}/dataAgents/my-agent' --body -   # stdin JSON
```

## Guidance

- **Prefer `createSync` / `updateSync` / `deleteSync`** (what the CLI uses) so you
  get the resource back directly instead of a long-running operation.
- When a call fails, re-run with `-v` and show the user the exact request; the
  API returns detailed `google.rpc.Status` error bodies (surfaced on stderr).
- The full endpoint + payload reference is in `REFERENCE.md` — consult it before
  hand-building a `raw` request.
- Datasources other than BigQuery (Looker, Looker Studio) use different
  `datasourceReferences` shapes; the CLI's `--bq-table` only builds BigQuery
  references. Use `raw` with a hand-built body for those, per `REFERENCE.md`.
- **Version:** stick to `v1` (GA) for production. Some features are preview-only
  — notably `queryData` (v1beta/v1alpha). See `REFERENCE.md` for details.
- A 404 with a non-JSON (HTML) body means the path or the API version is wrong,
  not that the resource is missing — real misses return a JSON `NOT_FOUND`.
