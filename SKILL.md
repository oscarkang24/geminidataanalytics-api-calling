---
name: geminidataanalytics
description: >
  Use when calling the Gemini Data Analytics / Conversational Analytics HTTP API
  (geminidataanalytics.googleapis.com) — triggers include "create a data agent",
  "list data agents", "start a conversation", "ask a question over BigQuery with
  the CA API / Data QnA", "chat with a data agent", or any CRUD on dataAgents /
  conversations. Wraps the REST endpoints with a dependency-free Python CLI that
  authenticates via Application Default Credentials.
---

# Gemini Data Analytics HTTP API

Full CRUD over the public Conversational Analytics API at
`https://geminidataanalytics.googleapis.com`. Two service surfaces:

- **DataAgentService** — manage `dataAgents` (reusable agents that bundle a
  datasource + system instructions + context).
- **DataChatService** — manage `conversations` and run `chat` (natural-language
  questions answered from the configured data).

All requests authenticate with an ADC token
(`gcloud auth application-default print-access-token`) and send the billing
project in the `x-goog-user-project` header.

## Prerequisites

1. ADC configured: `gcloud auth application-default login`
2. The API enabled on the project:
   `gcloud services enable geminidataanalytics.googleapis.com --project PROJECT`
3. The caller has the relevant `geminidataanalytics.*` IAM permissions and can
   read the underlying BigQuery data.

## The CLI

Everything goes through `scripts/gda.py`. Global flags come **before** the
resource:

```
python3 scripts/gda.py --project PROJECT [--location global] [--version v1] \
    [-v] <resource> <action> [flags]
```

- `--location` defaults to `global`.
- `--version` defaults to `v1` (**GA** — the version external customers should
  use). `v1beta` (BETA) and `v1alpha` (ALPHA) expose preview-only features.
- `-v` echoes the method/URL/body to stderr — use it to show the user the exact
  HTTP call.
- `--access-token` (or `$GDA_ACCESS_TOKEN`) supplies an OAuth2 token directly,
  bypassing gcloud ADC.
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

`--bq-table` is repeatable to give an agent multiple tables.

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
