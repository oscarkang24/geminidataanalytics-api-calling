# Hero queries

Prompts to type at Claude Code with the skill installed. They are written the
way a real person asks, not as CLI invocations — the point is to test whether
Claude picks the right command, not whether you can.

Run 1–9 in order on a machine with credentials: together they are the live
end-to-end check (create → ask → converse → inspect → clean up), and they leave
nothing behind. 10–13 test judgement and cost nothing.

Substitute a BigQuery table you can read for `PROJECT.dataset.table`.

---

## Setup

**1. Preflight**
> Is my data agent setup working?

Good: runs `doctor`, reports credential source, project, and a real API call.
Bad: starts creating things before checking, or reports `[ok]` on credentials
without a call succeeding.

If this is not green, stop — everything below depends on it, and `doctor`'s
output names the fix.

## The core loop

**2. Create**
> Create a data agent called sales-bot over PROJECT.dataset.table. It should
> know that revenue is always net of refunds.

Good: uses `agents create` with `--agent-id sales-bot` (the id, not just the
display name) and puts the revenue rule in `--system-instruction`. Confirms the
billing project if it differs from the table's, rather than assuming.
Bad: silently picks a project; puts "sales-bot" only in the display name.

**3. Ask it something**
> Ask sales-bot how many orders we had last month.

Good: stateless `chat --agent-id sales-bot`; pins down whether "last month"
means the previous calendar month or a trailing 30 days, or says which it used.
Bad: creates a conversation for a one-off question.

**4. A conversation with memory**
> Start a saved conversation with sales-bot and ask it for the top 5 products by
> revenue. Then ask "and what about the month before?"

Good: `conversations create`, then `chat` with both `--agent-id` and
`--conversation-id`; the follow-up resolves against the first answer.
Bad: the second question is answered without history and misreads "the month
before".

**5. Ask without creating anything**
> Without saving anything, ask PROJECT.dataset.table how many rows it has.

Good: inline context — `chat --bq-table ...`, no agent, no conversation.
Bad: creates an agent first.

**6. Look around**
> What data agents do I have, and what's in the conversation I just started?

Good: `agents list` and `conversations messages`.

**7. Change it**
> Rename sales-bot to "Sales bot (v2)" and also give it access to
> PROJECT.dataset.other_table.

Good: `agents update` with a field mask per field, so renaming does not clobber
the system instruction. Verify the revenue rule survives with `agents get`.

**8. Show the wire**
> Ask sales-bot anything, and show me the exact HTTP request.

Good: adds `-v`; the URL is `https://geminidataanalytics.googleapis.com/v1/...`
and ends in `:chat`. A `/v1beta/` or `/v1alpha/` here is a bug — those are
preview surfaces.

**9. Clean up**
> Delete that conversation and the sales-bot agent.

Good: both deletes succeed, and Claude notes agent delete is a **soft** delete —
the id stays reserved ~30 days, so re-running #2 with the same id will hit
`ALREADY_EXISTS`.

## Judgement

**10. Under-specified — should ask, not build**
> I want my team to be able to ask questions about our sales data without
> knowing SQL.

Good: recognises this could be the CA API, Looker, a BI tool, or a custom app,
and asks which — in one message with a recommendation and the open questions
bundled. Creates nothing.
Bad: starts provisioning; or interrogates one question at a time.

**11. Should NOT trigger**
> Write me a BigQuery SQL query that counts orders per day.

> Create a Vertex AI Agent Builder app for our support docs.

Good: answers normally without reaching for this skill. The second is the one
that matters — "create a … agent" on Google Cloud is lexically close, and a
false trigger spends real credentials.

## Failure handling

**12. Break auth on purpose**
> ```
> export CLOUDSDK_AUTH_ACCESS_TOKEN=not-a-real-token
> ```
> Now list my data agents.

Good: `doctor` flags the value's *shape* as not-a-token, says the network is
fine, and points out the placeholder shadows every later source, so the fix is
`unset` rather than adding another credential. Stops there.
Bad: hunts the filesystem for keys, reads proxy docs, or reports `[ok]`.

Remember to `unset CLOUDSDK_AUTH_ACCESS_TOKEN` afterwards.

**13. A resource that isn't there**
> Ask the agent called no-such-agent what revenue was last quarter.

Good: a clean `NOT_FOUND`, no traceback, and a suggestion to run `agents list`.

---

## What to send back

For 1–9, the compact block from a live run is the most useful single artifact:

```bash
bash tests/run_live.sh PROJECT.dataset.table
```

It prints a `----- PASTE THIS -----` summary with pass/fail per step and no
credential values.
