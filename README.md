# geminidataanalytics skill

A [Claude Code](https://claude.com/claude-code) skill (plus a standalone Python
CLI) for the **Gemini Data Analytics / Conversational Analytics HTTP API**
(`geminidataanalytics.googleapis.com`).

It provides full CRUD over the two service surfaces:

- **DataAgentService** — reusable *data agents* that bundle a datasource + system
  instructions + context.
- **DataChatService** — *conversations* and `chat` (natural-language questions
  answered from your configured data).

The CLI (`scripts/gda.py`) is dependency-free (Python 3 stdlib only) and
authenticates with Application Default Credentials.

## Prerequisites

1. ADC configured: `gcloud auth application-default login`
2. API enabled: `gcloud services enable geminidataanalytics.googleapis.com --project PROJECT`
3. Caller has the relevant `geminidataanalytics.*` IAM permissions and can read
   the underlying BigQuery data.

## Quickstart

```bash
# Create an agent over a BigQuery table.
python3 scripts/gda.py --project PROJECT agents create \
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

- `--location` defaults to `global`.
- `--version` defaults to `v1` (GA). `v1beta` / `v1alpha` expose preview features.
- `-v` echoes the exact method / URL / body to stderr.
- `--access-token` (or `$GDA_ACCESS_TOKEN`) supplies an OAuth2 token directly,
  bypassing gcloud ADC.

## Files

| File | Purpose |
| --- | --- |
| `SKILL.md` | The Claude Code skill definition (frontmatter + usage). |
| `scripts/gda.py` | The standalone CLI. |
| `REFERENCE.md` | Endpoint + payload reference for the API. |
| `EVAL.md` | Manual eval cases (skill triggering + end-to-end live). |

## Using as a Claude Code skill

Drop this directory into your Claude Code skills folder (e.g.
`~/.claude/skills/geminidataanalytics/`). The skill activates on prompts like
"create a data agent", "chat with my data agent", or "ask a question over
BigQuery with the Conversational Analytics API".

## API docs

https://cloud.google.com/gemini/docs/conversational-analytics-api
