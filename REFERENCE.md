# Gemini Data Analytics API — endpoint & payload reference

For the public **Conversational Analytics API**. Product docs:
https://cloud.google.com/gemini/docs/conversational-analytics-api

Base URL: `https://geminidataanalytics.googleapis.com/{version}`. `{parent}` =
`projects/{project}/locations/{location}` (location is usually `global`).

Versions: **`v1` = GA** (default; use this for external customers), `v1beta` =
BETA, `v1alpha` = ALPHA. REST bodies use **camelCase** field names (and query
params use camelCase too, e.g. `dataAgentId`, not `data_agent_id`).

## Endpoint availability

The endpoints documented below are available in `v1` (GA) unless noted. Prefer
`v1` for production; `v1beta` / `v1alpha` expose preview-only features.

## DataAgentService

| Method | HTTP | Path (after version) | Body |
| --- | --- | --- | --- |
| ListDataAgents | GET | `{parent}/dataAgents` | — |
| ListAccessibleDataAgents | GET | `{parent}/dataAgents:listAccessible` | — |
| GetDataAgent | GET | `{parent}/dataAgents/{id}` | — |
| CreateDataAgent | POST | `{parent}/dataAgents?dataAgentId={id}` | `DataAgent` (LRO) |
| CreateDataAgentSync | POST | `{parent}/dataAgents:createSync?dataAgentId={id}` | `DataAgent` |
| UpdateDataAgent | PATCH | `{parent}/dataAgents/{id}?updateMask=...` | `DataAgent` (LRO) |
| UpdateDataAgentSync | PATCH | `{parent}/dataAgents/{id}:updateSync?updateMask=...` | `DataAgent` |
| DeleteDataAgent | DELETE | `{parent}/dataAgents/{id}` | — (LRO) |
| DeleteDataAgentSync | DELETE | `{parent}/dataAgents/{id}:deleteSync` | — |
| GetIamPolicy | POST | `{parent}/dataAgents/{id}:getIamPolicy` | `GetIamPolicyRequest` |
| SetIamPolicy | POST | `{parent}/dataAgents/{id}:setIamPolicy` | `SetIamPolicyRequest` |

### DataAgent body

```json
{
  "displayName": "Sales agent",
  "description": "Answers questions about orders.",
  "labels": {"team": "sales"},
  "dataAnalyticsAgent": {
    "publishedContext": { /* Context, see below */ },
    "stagingContext":   { /* Context, optional */ }
  }
}
```

`name` is output/inferred — don't set it on create; pass the id via the
`dataAgentId` query param instead. On create the `publishedContext` is what the
Chat API uses in production.

## DataChatService — conversations

| Method | HTTP | Path | Body |
| --- | --- | --- | --- |
| CreateConversation | POST | `{parent}/conversations?conversationId={id}` | `Conversation` |
| GetConversation | GET | `{parent}/conversations/{id}` | — |
| ListConversations | GET | `{parent}/conversations?filter=...` | — |
| DeleteConversation | DELETE | `{parent}/conversations/{id}` | — |
| ListMessages | GET | `{parent}/conversations/{id}/messages` | — |

### Conversation body

```json
{
  "agents": ["projects/P/locations/global/dataAgents/my-agent"],
  "labels": {"surface": "cli"}
}
```

Set `agents` to attach a managed agent. `agents` currently accepts one agent.

## DataChatService — chat

| Method | HTTP | Path | Notes |
| --- | --- | --- | --- |
| Chat | POST | `{parent}:chat` | streaming; returns an array of `Message` objects |
| QueryData | POST | `{parent}:queryData` | NL → SQL + optional execution; **preview** (v1beta/v1alpha) |

The CLI uses `:chat` on `v1`. `:queryData` is preview-only — reach it via `raw`
on `v1beta`.

### ChatRequest body

```json
{
  "parent": "projects/P/locations/global",
  "messages": [{"userMessage": {"text": "How many orders last month?"}}],

  // exactly ONE context_provider:
  "conversationReference": {
    "conversation": "projects/P/locations/global/conversations/conv1",
    "dataAgentContext": {"dataAgent": "projects/P/locations/global/dataAgents/my-agent"}
  }
  // or "dataAgentContext": {"dataAgent": ".../dataAgents/my-agent"}
  // or "inlineContext": { /* Context */ }
}
```

- `conversationReference` → stateful chat (history persisted).
- `dataAgentContext` → stateless chat against an agent's context.
- `inlineContext` → fully stateless / agent-less, all context in the request.
- `dataAgentContext.contextVersion` may be `STAGING` or `PUBLISHED`.

## Context

Shared by agents, agent-less conversations, and inline chat.

```json
{
  "systemInstruction": "Revenue is net of refunds.",
  "datasourceReferences": {
    "bq": {
      "tableReferences": [
        {"projectId": "P", "datasetId": "sales", "tableId": "orders"}
      ]
    }
  },
  "options": {
    "analysis": {"python": {"enabled": true}}
    // "chart": {"image": {"svg": {}}}  // preview-only (v1beta/v1alpha), not GA
  },
  "exampleQueries": [
    {"naturalLanguageQuestion": "orders last month",
     "sqlQuery": "SELECT COUNT(*) FROM sales.orders WHERE ..."}
  ],
  "glossaryTerms": [
    {"displayName": "CTR", "description": "Click-through rate"}
  ]
}
```

`datasourceReferences` is a oneof — pick one:

- BigQuery: `"bq": {"tableReferences": [{"projectId","datasetId","tableId"}]}`
- Looker Studio: `"studio": { ... }`
- Looker Explores: `"looker": { ... }`

Only BigQuery is built by the CLI's `--bq-table` flag; for the others, hand-build
the body and send it via `gda.py ... raw`.

## Auth headers (what the CLI sends)

```
Authorization: Bearer <ADC access token>
x-goog-user-project: <project>
Content-Type: application/json
```

Token source: `gcloud auth application-default print-access-token`.
