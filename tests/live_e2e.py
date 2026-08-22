#!/usr/bin/env python3
"""Live end-to-end lifecycle against a real project (suite B of EVAL.md).

Needs real credentials, so it is NOT part of the default run. Usage:

    python3 tests/live_e2e.py --bq-table MYPROJ.dataset.orders

Credentials and the project are discovered the same way the CLI discovers them
(`python3 scripts/gda.py doctor` checks that); pass --project to override.

It creates an agent and a conversation, exercises all three chat modes, then
deletes everything it created (cleanup runs even if a step fails).
"""
import argparse, json, os, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIRST_ERROR = None
CLI = os.path.join(REPO, "scripts", "gda.py")
results = []


def gda(args, project, expect_ok=True):
    cmd = [sys.executable, CLI]
    if project:
        cmd += ["--project", project]
    if os.environ.get("GDA_HOST"):
        cmd += ["--host", os.environ["GDA_HOST"]]
    cmd += args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if p.returncode != 0 and expect_ok:
        global FIRST_ERROR
        if FIRST_ERROR is None:
            FIRST_ERROR = p.stderr.strip()[:600]
        print("    ! " + (p.stderr.strip().splitlines() or ["(no stderr)"])[0][:200])
    try:
        return p, json.loads(p.stdout) if p.stdout.strip() else None
    except json.JSONDecodeError:
        return p, None


def step(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"\n    {detail}" if detail and not ok else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="auto-detected when omitted")
    ap.add_argument("--bq-table", required=True, help="project.dataset.table you can read")
    # Agent delete is a SOFT delete: the id stays reserved for ~30 days, so a
    # fixed id would make the second run of this script fail with ALREADY_EXISTS.
    # Seconds alone collide when two runs start inside the same second — the
    # pid keeps them distinct without needing a clock.
    stamp = f"{int(time.time())}-{os.getpid()}"
    ap.add_argument("--agent-id", default="eval-agent-" + stamp)
    ap.add_argument("--conversation-id", default="eval-conv-" + stamp)
    a = ap.parse_args()
    P, AG, CV = a.project, a.agent_id, a.conversation_id

    try:
        # B1 create
        p, r = gda(["agents", "create", "--agent-id", AG, "--display-name", "Eval agent",
                    "--bq-table", a.bq_table,
                    "--system-instruction", "Answer tersely."], P)
        step("B1 agents create", r and r.get("name", "").endswith(f"/dataAgents/{AG}"), json.dumps(r)[:300])

        # B2 get
        p, r = gda(["agents", "get", "--agent-id", AG], P)
        ctx = ((r or {}).get("dataAnalyticsAgent") or {}).get("publishedContext") or {}
        tables = ((ctx.get("datasourceReferences") or {}).get("bq") or {}).get("tableReferences") or []
        step("B2 agents get returns the datasource",
             tables and tables[0]["tableId"] == a.bq_table.split(".")[-1], json.dumps(r)[:300])

        # B3 list
        p, r = gda(["agents", "list"], P)
        names = [x.get("name", "") for x in (r or {}).get("dataAgents", [])]
        step("B3 agents list includes it", any(n.endswith(f"/dataAgents/{AG}") for n in names), str(names)[:300])

        # B4 stateless inline chat
        p, r = gda(["chat", "--bq-table", a.bq_table, "--message", "How many rows are in this table?"], P)
        step("B4 inline-context chat answers", p.returncode == 0 and isinstance(r, list) and r,
             (p.stderr or "")[:300])

        # B5 stateless agent chat
        p, r = gda(["chat", "--agent-id", AG, "--message", "Show me a few sample rows.", "--answer-only"], P)
        step("B5 agent chat answers", p.returncode == 0 and p.stdout.strip(), (p.stderr or "")[:300])

        # B6 stateful chat
        p, r = gda(["conversations", "create", "--agent-id", AG, "--conversation-id", CV], P)
        step("B6a conversation created", r and r.get("name", "").endswith(f"/conversations/{CV}"), json.dumps(r)[:300])
        p, r = gda(["chat", "--agent-id", AG, "--conversation-id", CV,
                    "--message", "How many rows are there?", "--answer-only"], P)
        step("B6b stateful chat answers", p.returncode == 0 and p.stdout.strip(), (p.stderr or "")[:300])

        # B7 history
        p, r = gda(["conversations", "messages", "--conversation-id", CV], P)
        step("B7 conversation history persisted", r and r.get("messages"), json.dumps(r)[:300])

        # B8 update
        p, r = gda(["agents", "update", "--agent-id", AG, "--display-name", "Eval agent (updated)"], P)
        step("B8 agents update renames", r and r.get("displayName") == "Eval agent (updated)", json.dumps(r)[:300])

        # B10 GA-surface check
        p, _ = gda(["-v", "chat", "--agent-id", AG, "--message", "hi", "--answer-only"], P)
        line = next((l for l in p.stderr.splitlines() if l.startswith("# POST")), "")
        expect_host = os.environ.get("GDA_HOST", "https://geminidataanalytics.googleapis.com")
        step("B10 uses GA v1 + :chat on the expected host",
             f"{expect_host}/v1/" in line and line.endswith(":chat"), line or p.stderr[:200])
    finally:
        print("\n--- cleanup ---")
        p, _ = gda(["conversations", "delete", "--conversation-id", CV], P, expect_ok=False)
        step("B9a conversation deleted", p.returncode == 0)
        p, _ = gda(["agents", "delete", "--agent-id", AG], P, expect_ok=False)
        step("B9b agent deleted (soft delete)", p.returncode == 0)
        p, r = gda(["agents", "list"], P, expect_ok=False)
        names = [x.get("name", "") for x in (r or {}).get("dataAgents", [])]
        step("B9c agent excluded from list after delete",
             not any(n.endswith(f"/dataAgents/{AG}") for n in names), str(names)[:200])

    n, k = len(results), sum(1 for _, o in results if o)
    print(f"\n{'='*60}\n{k}/{n} live checks passed")

    # A compact block to paste back: everything needed to judge the run, and
    # nothing that needs scrolling. No credential values are included.
    print("\n----- PASTE THIS -----")
    print(f"result: {k}/{n} live checks passed")
    print(f"python: {sys.version.split()[0]}  platform: {sys.platform}")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if k != n:
        print("failures above; first error text:")
        for line in (FIRST_ERROR or "(none captured)").splitlines()[:6]:
            print("    " + line)
    print("----- END -----")
    return 0 if k == n else 1


if __name__ == "__main__":
    sys.exit(main())
