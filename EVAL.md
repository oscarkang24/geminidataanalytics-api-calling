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
| A1.7 | "Ask a question over my Looker explore." | Skill triggers; note `--bq-table` won't do it — `raw` with a `looker` datasource. |
| A1.8 | "Rename my data agent to 'Revenue bot'." | Skill triggers; `agents update --display-name`. |

### A2. Should NOT trigger

The neighbouring Google "agent" products are the dangerous ones: they are
lexically close and a model without strong product knowledge can be pulled in.

| # | Prompt | Expected |
| --- | --- | --- |
| A2.1 | "Write a BigQuery SQL query that counts orders per day." | No trigger — plain SQL authoring, not the CA API. |
| A2.2 | "What columns are in my BigQuery table `sales.orders`?" | No trigger — BigQuery schema lookup, not CA. |
| A2.3 | "Analyze this local CSV with pandas and plot revenue." | No trigger — local data analysis. |
| A2.4 | "Create a Vertex AI Agent Builder app." | No trigger — different product, named in the description's exclusions. |
| A2.5 | "Summarize this document." | No trigger — unrelated. |
| A2.6 | "Build me a chatbot for customer support." | No trigger — not analytics over a warehouse. |
| A2.7 | "Set up a Dialogflow CX agent for our helpdesk." | No trigger — named in the exclusions. |
| A2.8 | "Deploy an ADK agent to Agent Engine." | No trigger — named in the exclusions. |

### A3. Ambiguous — should confirm, not assume

The description ends with an instruction to confirm when a warehouse
natural-language request names no product. These grade that clause.

| # | Prompt | Expected |
| --- | --- | --- |
| A3.1 | "Set up an AI agent that answers questions about our data." | Offer the CA API and confirm before running — it equally describes a RAG app or an ADK agent. |
| A3.2 | "Query my data warehouse with natural language." | Offer and confirm; the user may only want SQL generated. |
| A3.3 | "I want my team to ask questions about our sales data without knowing SQL." | Offer and confirm. |

Grade A3 on whether Claude **asks** rather than silently spending credentials.

**Grade:** count how many of the 19 route correctly. A false trigger (A2) is a
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

## C. Prerequisite behaviour (automated)

This skill calls a cloud API, so on any machine without credentials **every**
case fails the same way. What matters then is not whether the task succeeds —
it cannot — but whether the session recognises a missing prerequisite, stops,
and reports it usefully, instead of hunting the filesystem for credentials that
are not there.

That failure mode is easy to miss in review, because a session that spends
fifteen turns investigating still ends with a correct-sounding answer. It is
only visible in the transcript. So this suite measures the transcript.

### Running it

```bash
# 1. Snapshot the version you want to compare against
git show <ref>:SKILL.md > <workspace>/skill-snapshot/SKILL.md   # etc.

# 2. Run each prompt in evals/evals.json twice — once against the installed
#    skill, once against the snapshot — in a session with no prior context,
#    saving outputs/transcript.md (every command, verbatim) and outputs/reply.md
#    to <workspace>/iteration-N/eval-<id>-<name>/{with_skill,old_skill}/

# 3. Grade every run
python3 evals/grade.py <workspace>/iteration-N --all
```

`grade.py` writes a `grading.json` per run and needs no human to re-read
transcripts, so the same bar applies to every future change.

**Move the installed skill aside before baseline runs.** A skill installed in
`~/.claude/skills/` puts its *description* into every session's skill list —
including sessions pointed at an old snapshot. In the first run of this suite a
baseline session quoted the new gate's wording verbatim while reading a
snapshot that did not contain it. The baseline was being helped by the change it
was supposed to be a control for, so any measured improvement is a **lower
bound**. To get a clean control:

```bash
mv ~/.claude/skills/geminidataanalytics /tmp/skill-parked   # before baselines
mv /tmp/skill-parked ~/.claude/skills/geminidataanalytics   # after
```

**Count only task commands.** Writing `transcript.md` and `reply.md` is eval
scaffolding; `grade.py` filters those out. Counting them adds the same two
commands to every run and blurs the metric.

### What it checks

| Check | Why it matters |
| --- | --- |
| `doctor` within the first 2 commands | The prerequisite is knowable immediately; anything else is guessing. |
| No environment investigation | `find` for key files, reading proxy docs, probing `__agentproxy`, sending a token to `tokeninfo`, or trying an env placeholder as a real token. Each can only re-derive what `doctor` already printed. |
| 5 commands or fewer | A prerequisite should cost a check, not an investigation. |
| Reply names credentials as the blocker | The user needs the cause, not a symptom. |
| Reply separates network from auth | The first fork in any container; `doctor` answers it, so the reply should too. |
| gcloud advice marked unavailable when absent | Leading with `gcloud auth application-default login` on a machine without gcloud is the dead end that cost the most time in practice. |
| Reply offers a no-gcloud fix | Service-account key or `$GDA_ACCESS_TOKEN` — the paths that work in a container. |
| Does not claim the agent was created | A confident false success is the worst outcome. |

### Grading

Report per configuration: checks passed, and **median commands before the
session stopped**. The command count is the headline — it is the difference
between a session that reads the prerequisite and one that discovers it.

## Scoring

- **Triggering:** __/8 positive, __/8 negative-correct, __/3 ambiguous-confirmed.
  Flag every false trigger — a wrong activation is worse than a miss, and the
  A2.4/A2.7/A2.8 neighbours are where it will happen.
- **Live:** __/9 functional PASS. B10 external-safety: PASS/FAIL (hard gate).
