#!/usr/bin/env python3
"""Suite 2: live route validation against geminidataanalytics.googleapis.com.

No credentials available, so every call comes back 401. That is still a real
signal: Google's API front end resolves the URL to an RPC *before* rejecting
auth, and echoes the resolved method in error.details[].metadata.method.
  - 401 UNAUTHENTICATED + expected method name  => the CLI's URL is correct
  - 404 NOT_FOUND / no method                   => the CLI's URL is wrong
"""
import json, os, re, subprocess, sys

CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "gda.py")
P = ["--project", "test-project-does-not-exist"]
results = []


def call(argv):
    env = dict(os.environ, GDA_ACCESS_TOKEN="fake-token-for-route-probe")
    p = subprocess.run([sys.executable, CLI] + P + argv,
                       capture_output=True, text=True, env=env, timeout=90)
    blob = p.stdout + p.stderr
    http = re.search(r"HTTP (\d+)", blob)
    code = int(http.group(1)) if http else None
    # The CLI prints a JSON error body starting on its own line; a non-JSON body
    # (the HTML 404 page) is flagged by _error_detail instead.
    err = {}
    # `:chat` is a streaming method: its error body is an ARRAY-wrapped Status.
    m = re.search(r"^[\[{].*", blob, re.M | re.S)
    if m and "non-JSON error body" not in blob:
        try:
            doc = json.loads(m.group(0))
        except json.JSONDecodeError:
            doc = None
        if isinstance(doc, list):
            doc = doc[0] if doc else {}
        if isinstance(doc, dict):
            err = doc.get("error", {})
    method = ""
    for d in err.get("details", []) or []:
        method = (d.get("metadata") or {}).get("method", "") or method
    return {"code": err.get("code") or code, "status": err.get("status"),
            "method": method, "http": code,
            "msg": (err.get("message") or "")[:80],
            "non_json": "non-JSON error body" in blob, "raw": blob[:200]}


def route(name, argv, expect_rpc):
    r = call(argv)
    short = r["method"].rsplit(".", 1)[-1] if r["method"] else ""
    ok = r["code"] == 401 and short == expect_rpc
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n"
          f"        -> HTTP {r['code']} {r['status']}  resolved={r['method'] or '(none)'}")
    if not ok:
        print(f"        expected 401 + {expect_rpc}; msg={r['msg']}")
    return r


print("=== DataAgentService routes (v1 GA) ===")
route("agents list        -> ListDataAgents", ["agents", "list"], "ListDataAgents")
route("agents list --accessible -> ListAccessibleDataAgents", ["agents", "list", "--accessible"], "ListAccessibleDataAgents")
route("agents get         -> GetDataAgent", ["agents", "get", "--agent-id", "probe"], "GetDataAgent")
route("agents create      -> CreateDataAgentSync", ["agents", "create", "--agent-id", "probe", "--bq-table", "p.d.t"], "CreateDataAgentSync")
route("agents update      -> UpdateDataAgentSync", ["agents", "update", "--agent-id", "probe", "--display-name", "x"], "UpdateDataAgentSync")
route("agents delete      -> DeleteDataAgentSync", ["agents", "delete", "--agent-id", "probe"], "DeleteDataAgentSync")

print("\n=== DataChatService routes (v1 GA) ===")
route("conversations create   -> CreateConversation", ["conversations", "create", "--agent-id", "a", "--conversation-id", "c"], "CreateConversation")
route("conversations get      -> GetConversation", ["conversations", "get", "--conversation-id", "c"], "GetConversation")
route("conversations list     -> ListConversations", ["conversations", "list"], "ListConversations")
route("conversations messages -> ListMessages", ["conversations", "messages", "--conversation-id", "c"], "ListMessages")
route("conversations delete   -> DeleteConversation", ["conversations", "delete", "--conversation-id", "c"], "DeleteConversation")
route("chat (agent)           -> Chat", ["chat", "--agent-id", "a", "--message", "hi"], "Chat")
route("chat (inline ctx)      -> Chat", ["chat", "--bq-table", "p.d.t", "--message", "hi"], "Chat")
route("chat (stateful)        -> Chat", ["chat", "--agent-id", "a", "--conversation-id", "c", "--message", "hi"], "Chat")

print("\n=== raw escape hatch: IAM (documented in REFERENCE.md) ===")
route("raw getIamPolicy   -> GetIamPolicy",
      ["raw", "POST", "{parent}/dataAgents/probe:getIamPolicy", "--body", '{"resource":"{parent}/dataAgents/probe"}'],
      "GetIamPolicy")

print("\n=== Negative control: a deliberately wrong path must NOT 401 ===")
r = call(["raw", "GET", "{parent}/bogusResource"])
ok = r["code"] == 404
results.append(("bogus path -> 404 (probe is discriminating)", ok))
print(f"[{'PASS' if ok else 'FAIL'}] bogus path -> 404, proving 401 means 'route exists'\n"
      f"        -> HTTP {r['code']}, non-JSON body={r['non_json']}")

print("\n=== Doc claim: queryData is preview-only (v1beta/v1alpha), not GA ===")
def probe_version(ver):
    r = call(["--version", ver, "raw", "POST", "{parent}:queryData", "--body", '{"parent":"{parent}"}'])
    return r["code"], r["status"] or ("non-JSON/HTML" if r["non_json"] else None)

v1 = probe_version("v1"); vb = probe_version("v1beta")
print(f"        v1     :queryData -> {v1}")
print(f"        v1beta :queryData -> {vb}")
ok = v1[0] == 404 and vb[0] == 401
results.append(("queryData: 404 on v1, 401 on v1beta (matches REFERENCE.md)", ok))
print(f"[{'PASS' if ok else 'FAIL'}] queryData absent from GA v1 but present in v1beta")

n = len(results); k = sum(1 for _, o in results if o)
print(f"\n{'='*62}\n{k}/{n} live route checks passed")
for nm, o in results:
    if not o:
        print("  FAIL:", nm)
sys.exit(0 if k == n else 1)
