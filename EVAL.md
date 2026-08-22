# geminidataanalytics skill — evaluation cases

Manual prompt/response eval for the **skill** (how Claude behaves when the skill
is available), not for the CLI's request construction. Run each case against
Claude with this skill installed, then grade against the expectation.

Two suites:

- **A. Triggering** — no API calls. Checks that the skill's `description`
  activates it on the right prompts and stays quiet on the wrong ones.
- **B. End-to-end live** — real calls against a real project. Checks that once
  triggered, Claude drives `gda.py` correctly and only over public/GA endpoints.

Grade each case **PASS / FAIL** and note what actually happened.

---

## A. Triggering (offline)

Say the prompt to Claude in a fresh session. Observe whether the
`geminidataanalytics` skill is selected. Do **not** approve any tool calls — you
are only grading the routing decision.

### A1. Should trigger

| # | Prompt | Expected |
| --- | --- | --- |
| A1.1 | "Create a data agent over my BigQuery table `myproj.sales.orders`." | Skill triggers; proposes `agents create`. |
| A1.2 | "List the data agents in project `myproj`." | Skill triggers; proposes `agents list`. |
| A1.3 | "Ask my sales data agent what total revenue was last month." | Skill triggers; proposes `chat --agent-id ...`. |
| A1.4 | "Start a conversation with agent `sales` and ask a question over BigQuery with the Conversational Analytics API." | Skill triggers; conversations + chat. |
| A1.5 | "Chat with a data agent about the top 5 products." | Skill triggers; `chat`. |
| A1.6 | "Delete the data agent `old-agent`." | Skill triggers; `agents delete`. |

### A2. Should NOT trigger

| # | Prompt | Expected |
| --- | --- | --- |
| A2.1 | "Write a BigQuery SQL query that counts orders per day." | No trigger — plain SQL authoring, not the CA API. |
| A2.2 | "What columns are in my BigQuery table `sales.orders`?" | No trigger — BigQuery schema lookup, not CA. |
| A2.3 | "Analyze this local CSV with pandas and plot revenue." | No trigger — local data analysis. |
| A2.4 | "Create a Vertex AI Agent Builder app." | No trigger — different product. |
| A2.5 | "Summarize this document." | No trigger — unrelated. |

**Grade:** count how many of the 11 route correctly. A false trigger (A2) is a
worse failure than a miss (A1) for an external skill — note them separately.

---

## B. End-to-end live

Suite B is automated end-to-end by `tests/live_e2e.py` — run that to check the
CLI mechanics, and use the table below when grading *Claude's* behaviour.

### Prerequisites

1. `gcloud auth application-default login`
2. `gcloud services enable geminidataanalytics.googleapis.com --project $PROJECT`
3. Caller can read the BigQuery table used below.

Fill these in and use them consistently:

```
PROJECT   = <your gcp project>
BQ_TABLE  = <project.dataset.table you can read>
AGENT_ID  = eval-agent
CONV_ID   = eval-conv
```

Ask Claude the natural-language prompt; Claude should run the command shown.
Verify the **observable** before moving on. Grade PASS/FAIL.

| # | Prompt to Claude | Command Claude should run | Verify |
| --- | --- | --- | --- |
| B1 | "Create a data agent `$AGENT_ID` over `$BQ_TABLE` that answers questions tersely." | `agents create --agent-id $AGENT_ID --bq-table $BQ_TABLE --system-instruction "..."` | Response JSON has `name` ending `/dataAgents/$AGENT_ID` and a `publishedContext` with the bq table. |
| B2 | "Show me that agent." | `agents get --agent-id $AGENT_ID` | Same resource returned; datasource matches. |
| B3 | "List my data agents." | `agents list` | The created agent appears in `dataAgents`. |
| B4 | "Without saving anything, ask that table how many rows it has." | `chat --bq-table $BQ_TABLE --message "..."` (inline context) | Response is a JSON array of messages ending in a `FINAL_RESPONSE` with a plausible answer. |
| B5 | "Ask the `$AGENT_ID` agent for its top few records by some metric." | `chat --agent-id $AGENT_ID --message "..."` | Answer returned; used `dataAgentContext`, not inline. |
| B6 | "Start a saved conversation `$CONV_ID` with that agent and ask a question." | `conversations create ...` then `chat --agent-id $AGENT_ID --conversation-id $CONV_ID --message "..."` | Answer returned; uses `conversationReference`. |
| B7 | "Show the messages in that conversation." | `conversations messages --conversation-id $CONV_ID` | Prior turn(s) persisted — history is there. |
| B8 | "Rename the agent to 'Eval agent (updated)'." | `agents update --agent-id $AGENT_ID --display-name "Eval agent (updated)"` | Response shows new `displayName`. |
| B9 | "Clean up: delete the conversation and the agent." | `conversations delete ...` then `agents delete ...` | Both deletes succeed. Note: agent delete is a **soft delete** — the resource keeps existing with a `deleteTime`/`purgeTime` (~30 days out) and is excluded from `agents list`. Verify it's gone from `agents list`; do **not** assert `agents get` returns NOT_FOUND. |

### B10. External-safety spot check

Re-run one chat case with verbose so the real HTTP call is visible:

> "Ask the `$AGENT_ID` agent a question, and show me the exact HTTP request."

Claude should add `-v`. In the stderr line, verify **all** of:

- Host is `https://geminidataanalytics.googleapis.com`.
- Version segment is `/v1/` (GA) — not `/v1beta/` or `/v1alpha/`.
- Endpoint is `:chat`, not any preview-only method.

Any non-GA endpoint here is an automatic FAIL for the production constraint.

---

## Scoring

- **Triggering:** __/6 positive, __/5 negative-correct. Flag every false trigger.
- **Live:** __/9 functional PASS. B10 external-safety: PASS/FAIL (hard gate).
