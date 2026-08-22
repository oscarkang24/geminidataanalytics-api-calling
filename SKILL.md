---
name: geminidataanalytics
description: >
  Use for the Gemini Data Analytics / Conversational Analytics API
  (geminidataanalytics.googleapis.com, also called Data QnA or the CA API):
  create, list, get, update or delete dataAgents; create or manage
  conversations; and any request to ask, chat, or answer natural-language
  questions over BigQuery or Looker data through a data agent — e.g. "create a
  data agent on myproj.sales.orders", "list data agents", "ask my sales agent
  what revenue was last month". Also surface it when someone wants to query a
  warehouse in plain English, or wants their team asking questions of their
  data without writing SQL — but where it is still unclear what to build on
  (no product, table or existing agent identified, and nothing earlier in the
  conversation settles it), confirm they mean the Conversational Analytics API
  before creating anything. Read-only checks like `doctor` and `agents list`
  are fine to run first, and make for a better question. Wraps the REST
  endpoints in a dependency-free Python CLI using Application Default
  Credentials. Do NOT use for: writing or running
  plain BigQuery SQL, inspecting or altering table schemas, local CSV or pandas
  analysis, general-purpose or customer-support chatbots, or other Google agent
  products (Vertex AI Agent Builder / Agentspace, ADK / Agent Engine,
  Dialogflow) — those are separate APIs.
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

Run `python3 scripts/gda.py doctor` first — it reports which source supplied
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

`doctor` checks these in order and stops at the first failure, so re-run it
after each fix. It also reports whether the host is reachable without
credentials, which separates an auth problem from a network or proxy one.

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

`agents create` makes a durable cloud resource in one command, and the id it
takes cannot be reused for ~30 days after a delete. So when the target is still
unclear — which table, which billing project, or whether the user meant this
API at all — confirm before running it. Run `doctor` (and `agents list`) first:
they are read-only, and knowing the detected project and existing agents makes
the question concrete rather than asking the user to recall ids from memory.
Put the recommendation, the one-line alternatives and the open questions in a
single message, rather than interrogating one at a time.

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

## Choosing values

These are the decisions the user usually leaves implicit — ask rather than
guess when the answer changes what gets created:

- **`--project` is the billing/quota project, not necessarily the table's.**
  `--bq-table acme.retail.orders` says where the *data* lives; the agent is
  created in `--project`. They are often the same and often not. If the user
  named only a table, say which project you are billing, or ask.
- **`--agent-id` vs `--display-name`.** The id is the resource name — stable,
  used by every later command, and **it cannot be reused for ~30 days after a
  delete** (delete is a soft delete). The display name is free text and can be
  changed with `agents update`. When a user says "call it sales-bot", use it as
  the id.
- **Which chat mode.** Default to stateless `--agent-id` for a single
  question. Add `--conversation-id` only when follow-ups should see history
  ("and the month before?"). Use inline `--bq-table` when the user explicitly
  wants nothing persisted.
- **Ambiguous time ranges.** "Last month" may mean the previous calendar month
  or a trailing 30 days, and the answer differs. Pin it down in the question or
  in `systemInstruction`.
- **Business definitions.** A rule like "revenue is net of refunds" can go in
  `--system-instruction`. For term definitions the API also has
  `glossaryTerms`, and `exampleQueries` for known question/SQL pairs — neither
  has a CLI flag, so use `raw` with a hand-built `Context` (see `REFERENCE.md`).

## Troubleshooting

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `401 UNAUTHENTICATED`, `CREDENTIALS_MISSING` | No token was sent. | `doctor` — discovery found nothing. |
| `401 UNAUTHENTICATED`, `ACCESS_TOKEN_TYPE_UNSUPPORTED` | A token was sent but is not a usable OAuth token for this API (a placeholder, an API key, the wrong token type). | Check which source `doctor`/`-v` names, and replace it. |
| `403 PERMISSION_DENIED`, `SERVICE_DISABLED` | The API is not enabled on the project. | `gcloud services enable geminidataanalytics.googleapis.com --project PROJECT` |
| `403 PERMISSION_DENIED` (other) | The identity lacks `geminidataanalytics.*`, or cannot read the BigQuery table. | Grant the roles; confirm which identity is in use with `-v`. |
| `404` with an **HTML** body | Wrong path or API version — not a missing resource. | Check the path against `REFERENCE.md`; stay on `v1`. |
| `404` with a **JSON** `NOT_FOUND` | The resource really is absent. | Check the id; remember `agents list` hides soft-deleted agents. |
| `409 ALREADY_EXISTS` on create | The id is taken — including by a soft-deleted agent, for ~30 days. | Use a different `--agent-id`. |
| `connection failed` + TLS text | Python cannot verify the certificate (common on macOS). | The error names the fix; do not disable verification. |

Credentials are discovered, not configured — so when something looks wrong,
`doctor` is always the first command, and `-v` shows which identity and project
a given call actually used.

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
