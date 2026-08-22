#!/usr/bin/env python3
"""Request-construction suite: every command is run against a capturing mock
HTTP server and the exact method / path / query / headers / body is asserted.

No credentials and no network required.  Run:  python3 tests/test_requests.py
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import run, check, summary

P = ["--project", "myproj"]

print("--- Auth / transport ---")
p, c = run(P + ["agents", "get", "--agent-id", "a1"])
check("exit 0 on 200", p.returncode == 0, p.stderr)
check("Authorization: Bearer <token>", c and c["headers"].get("authorization") == "Bearer test-token-abc", str(c and c["headers"]))
check("x-goog-user-project header", c and c["headers"].get("x-goog-user-project") == "myproj", str(c and c["headers"]))
check("GET has no Content-Type/body", c and c["body_raw"] is None and "content-type" not in c["headers"], str(c))
check("stdout is pretty JSON", p.stdout.strip() == json.dumps({"ok": True}, indent=2), repr(p.stdout))

print("\n--- Data agents ---")
p, c = run(P + ["agents", "create", "--agent-id", "my-agent", "--display-name", "Sales agent",
               "--description", "desc", "--bq-table", "P.ds.orders",
               "--system-instruction", "Revenue is net of refunds."])
check("create: POST", c and c["method"] == "POST", str(c))
check("create: path :createSync", c and c["path"] == "/v1/projects/myproj/locations/global/dataAgents:createSync", str(c and c["path"]))
check("create: dataAgentId query", c and c["query"] == {"dataAgentId": "my-agent"}, str(c and c["query"]))
check("create: body shape", c and c["body"] == {
    "dataAnalyticsAgent": {"publishedContext": {
        "datasourceReferences": {"bq": {"tableReferences": [
            {"projectId": "P", "datasetId": "ds", "tableId": "orders"}]}},
        "systemInstruction": "Revenue is net of refunds."}},
    "displayName": "Sales agent", "description": "desc"}, json.dumps(c and c["body"]))
check("create: Content-Type json", c and c["headers"].get("content-type") == "application/json", str(c and c["headers"]))

p, c = run(P + ["agents", "create", "--agent-id", "m", "--bq-table", "P.ds.a", "--bq-table", "P.ds.b", "--python"])
tr = c and c["body"]["dataAnalyticsAgent"]["publishedContext"]["datasourceReferences"]["bq"]["tableReferences"]
check("create: repeatable --bq-table", tr == [{"projectId":"P","datasetId":"ds","tableId":"a"},{"projectId":"P","datasetId":"ds","tableId":"b"}], str(tr))
opts = c and c["body"]["dataAnalyticsAgent"]["publishedContext"].get("options")
check("create: --python -> options.analysis.python.enabled", opts == {"analysis": {"python": {"enabled": True}}}, str(opts))

p, c = run(P + ["agents", "create", "--agent-id", "m"])
check("create: missing --bq-table errors", p.returncode == 1 and "bq-table" in p.stderr, p.stderr.strip())
check("create: no HTTP call on validation error", c is None, str(c))

p, c = run(P + ["agents", "create", "--agent-id", "m", "--bq-table", "ds.orders"])
check("create: malformed --bq-table errors", p.returncode == 1 and "project.dataset.table" in p.stderr, p.stderr.strip())

p, c = run(P + ["agents", "get", "--agent-id", "a1"])
check("get: GET .../dataAgents/a1", c and (c["method"], c["path"]) == ("GET", "/v1/projects/myproj/locations/global/dataAgents/a1"), str(c))

p, c = run(P + ["agents", "list"])
check("list: GET .../dataAgents, no query", c and (c["method"], c["path"], c["raw_query"]) == ("GET", "/v1/projects/myproj/locations/global/dataAgents", ""), str(c))

p, c = run(P + ["agents", "list", "--accessible", "--page-size", "5", "--page-token", "tok=="])
check("list --accessible: :listAccessible", c and c["path"] == "/v1/projects/myproj/locations/global/dataAgents:listAccessible", str(c and c["path"]))
check("list: paging params passed+encoded", c and c["query"] == {"pageSize": "5", "pageToken": "tok=="}, str(c and c["query"]) + " raw=" + str(c and c["raw_query"]))

p, c = run(P + ["agents", "update", "--agent-id", "a1", "--display-name", "New name"])
check("update: PATCH :updateSync", c and (c["method"], c["path"]) == ("PATCH", "/v1/projects/myproj/locations/global/dataAgents/a1:updateSync"), str(c))
check("update: mask=display_name only", c and c["query"] == {"updateMask": "display_name"}, str(c and c["query"]))
check("update: body has only displayName", c and c["body"] == {"displayName": "New name"}, str(c and c["body"]))

p, c = run(P + ["agents", "update", "--agent-id", "a1", "--bq-table", "P.ds.t", "--system-instruction", "si", "--description", "d"])
check("update: nested masks for context fields",
      c and c["query"]["updateMask"] == "description,data_analytics_agent.published_context.datasource_references,data_analytics_agent.published_context.system_instruction",
      str(c and c["query"]))
check("update: nested body", c and c["body"]["dataAnalyticsAgent"]["publishedContext"] == {
    "datasourceReferences": {"bq": {"tableReferences": [{"projectId":"P","datasetId":"ds","tableId":"t"}]}},
    "systemInstruction": "si"}, json.dumps(c and c["body"]))

p, c = run(P + ["agents", "update", "--agent-id", "a1"])
check("update: no fields -> error, no call", p.returncode == 1 and c is None and "nothing to update" in p.stderr, p.stderr.strip())

p, c = run(P + ["agents", "update", "--agent-id", "a1", "--system-instruction", ""])
check("update: empty string systemInstruction still masked", c and c["query"]["updateMask"] == "data_analytics_agent.published_context.system_instruction", str(c and c["query"]))

p, c = run(P + ["agents", "delete", "--agent-id", "a1"])
check("delete: DELETE :deleteSync", c and (c["method"], c["path"]) == ("DELETE", "/v1/projects/myproj/locations/global/dataAgents/a1:deleteSync"), str(c))

print("\n--- Conversations ---")
p, c = run(P + ["conversations", "create", "--agent-id", "a1", "--conversation-id", "conv1"])
check("conv create: POST .../conversations", c and (c["method"], c["path"]) == ("POST", "/v1/projects/myproj/locations/global/conversations"), str(c))
check("conv create: conversationId query", c and c["query"] == {"conversationId": "conv1"}, str(c and c["query"]))
check("conv create: agents body", c and c["body"] == {"agents": ["projects/myproj/locations/global/dataAgents/a1"]}, str(c and c["body"]))

p, c = run(P + ["conversations", "create", "--agent-id", "a1"])
check("conv create: id optional (server-assigned)", c and c["raw_query"] == "", str(c and c["raw_query"]))

p, c = run(P + ["conversations", "get", "--conversation-id", "conv1"])
check("conv get", c and (c["method"], c["path"]) == ("GET", "/v1/projects/myproj/locations/global/conversations/conv1"), str(c))

p, c = run(P + ["conversations", "list", "--filter", "labels.key=value"])
check("conv list: filter encoded", c and c["path"].endswith("/conversations") and c["query"] == {"filter": "labels.key=value"},
      str(c and c["raw_query"]))

p, c = run(P + ["conversations", "messages", "--conversation-id", "conv1", "--page-size", "2"])
check("conv messages", c and c["path"] == "/v1/projects/myproj/locations/global/conversations/conv1/messages" and c["query"] == {"pageSize": "2"}, str(c))

p, c = run(P + ["conversations", "delete", "--conversation-id", "conv1"])
check("conv delete", c and (c["method"], c["path"]) == ("DELETE", "/v1/projects/myproj/locations/global/conversations/conv1"), str(c))

print("\n--- Chat (3 modes) ---")
p, c = run(P + ["chat", "--agent-id", "a1", "--conversation-id", "conv1", "--message", "Q?"], body=[])
check("chat: POST {parent}:chat", c and (c["method"], c["path"]) == ("POST", "/v1/projects/myproj/locations/global:chat"), str(c))
check("chat stateful: conversationReference", c and c["body"] == {
    "parent": "projects/myproj/locations/global",
    "messages": [{"userMessage": {"text": "Q?"}}],
    "conversationReference": {"conversation": "projects/myproj/locations/global/conversations/conv1",
                              "dataAgentContext": {"dataAgent": "projects/myproj/locations/global/dataAgents/a1"}}},
    json.dumps(c and c["body"]))

p, c = run(P + ["chat", "--agent-id", "a1", "--message", "Q?"], body=[])
check("chat stateless-agent: dataAgentContext only", c and set(c["body"]) == {"parent","messages","dataAgentContext"} and
      c["body"]["dataAgentContext"] == {"dataAgent": "projects/myproj/locations/global/dataAgents/a1"}, json.dumps(c and c["body"]))

p, c = run(P + ["chat", "--bq-table", "P.ds.orders", "--message", "Q?"], body=[])
check("chat inline: inlineContext only", c and set(c["body"]) == {"parent","messages","inlineContext"} and
      c["body"]["inlineContext"]["datasourceReferences"]["bq"]["tableReferences"][0]["tableId"] == "orders", json.dumps(c and c["body"]))

p, c = run(P + ["chat", "--conversation-id", "conv1", "--message", "Q?"])
check("chat: --conversation-id without --agent-id errors", p.returncode == 1 and c is None and "agent-id" in p.stderr, p.stderr.strip())

p, c = run(P + ["chat", "--message", "Q?"])
check("chat: no context at all errors", p.returncode == 1 and c is None, p.stderr.strip())

print("\n--- --answer-only rendering ---")
STREAM = [
  {"systemMessage": {"text": {"parts": ["Thinking..."], "textType": "THOUGHT"}}},
  {"systemMessage": {"text": {"parts": ["Total sales were", "$1,234."], "textType": "FINAL_RESPONSE"}}},
]
p, _ = run(P + ["chat", "--agent-id", "a1", "--message", "Q?", "--answer-only"], body=STREAM)
check("answer-only: prints FINAL_RESPONSE parts, drops THOUGHT", p.stdout.strip() == "Total sales were\n$1,234.", repr(p.stdout))

DATA = [
  {"systemMessage": {"text": {"parts": ["Here are the rows:"], "textType": "FINAL_RESPONSE"}}},
  {"systemMessage": {"data": {"result": {"schema": {"fields": [{"name": "product"}, {"name": "rev"}]},
                                          "data": [{"product": "A", "rev": 10}, {"product": "B", "rev": 20}]}}}},
]
p, _ = run(P + ["chat", "--agent-id", "a1", "--message", "Q?", "--answer-only"], body=DATA)
check("answer-only: appends data rows as TSV", p.stdout.strip() == "Here are the rows:\n\nproduct\trev\nA\t10\nB\t20", repr(p.stdout))

p, _ = run(P + ["chat", "--agent-id", "a1", "--message", "Q?", "--answer-only"], body=[{"systemMessage": {"data": {"weird": 1}}}])
check("answer-only: unknown data shape falls back to JSON (no silent loss)", '"weird"' in p.stdout, repr(p.stdout))

p, _ = run(P + ["chat", "--agent-id", "a1", "--message", "Q?", "--answer-only"], body=[{"systemMessage": {"schema": {"x": 1}}}])
check("answer-only: nothing recognized -> full stream", '"schema"' in p.stdout, repr(p.stdout))

p, _ = run(P + ["chat", "--agent-id", "a1", "--message", "Q?", "--answer-only"], body={"error": {"code": 3}})
check("answer-only: non-list response -> JSON, not crash", '"error"' in p.stdout, repr(p.stdout))

p, _ = run(P + ["chat", "--agent-id", "a1", "--message", "Q?"], body=STREAM)
check("no --answer-only: full stream printed", '"THOUGHT"' in p.stdout, repr(p.stdout[:200]))

print("\n--- raw escape hatch ---")
p, c = run(P + ["raw", "POST", "{parent}/dataAgents/a1:getIamPolicy", "--body", '{"resource":"{parent}/dataAgents/a1"}'])
check("raw: {parent} expands in path", c and c["path"] == "/v1/projects/myproj/locations/global/dataAgents/a1:getIamPolicy", str(c and c["path"]))
check("raw: {parent} expands in body", c and c["body"] == {"resource": "projects/myproj/locations/global/dataAgents/a1"}, str(c and c["body"]))

p, c = run(P + ["raw", "get", "/{parent}/dataAgents"])
check("raw: lowercase method + leading slash tolerated", c and (c["method"], c["path"]) == ("GET", "/v1/projects/myproj/locations/global/dataAgents"), str(c))

p, c = run(P + ["raw", "POST", "{parent}:queryData", "--body", "-"],
           stdin='{"project":"{project}","loc":"{location}"}')
check("raw: --body - reads stdin, {project}/{location} expand",
      c and c["body"] == {"project": "myproj", "loc": "global"}, str(c and c["body"]))

p, c = run(P + ["raw", "POST", "{parent}:x", "--body", "{not json"])
check("raw: invalid JSON body fails loudly", p.returncode != 0 and c is None, (p.stderr or "")[-200:])

print("\n--- Global flags ---")
p, c = run(P + ["--location", "us-central1", "--version", "v1beta", "agents", "list"])
check("--version/--location reflected in URL", c and c["path"] == "/v1beta/projects/myproj/locations/us-central1/dataAgents", str(c and c["path"]))

p, c = run(P + ["-v", "agents", "create", "--agent-id", "a1", "--bq-table", "P.d.t"])
check("-v echoes METHOD + URL to stderr", "# POST" in p.stderr and ":createSync?dataAgentId=a1" in p.stderr, repr(p.stderr))
check("-v echoes body to stderr", "# body:" in p.stderr and "tableReferences" in p.stderr, repr(p.stderr))
check("-v keeps stdout clean JSON", json.loads(p.stdout) == {"ok": True}, repr(p.stdout))

p, c = run(["agents", "list"], env_extra={v: "" for v in
           ("GDA_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT")})
check("--project optional: falls back to detection, errors clearly if none found",
      p.returncode == 1 and c is None and "could not determine the project" in p.stderr,
      p.stderr.strip()[:200])

p, c = run(P + ["--access-token", "flag-token", "agents", "list"])
check("--access-token beats $GDA_ACCESS_TOKEN", c and c["headers"]["authorization"] == "Bearer flag-token", str(c and c["headers"].get("authorization")))
p, c = run(P + ["agents", "list", "--access-token", "flag-token"])
check("global flag after resource -> argparse error (documented ordering)", p.returncode == 2 and c is None, p.stderr.strip()[:120])

p, c = run(P + ["agents", "list"], env_extra={"GDA_ACCESS_TOKEN": ""})
check("no token + no gcloud -> clear error, no call", p.returncode == 1 and c is None and "gcloud" in p.stderr, p.stderr.strip()[:200])

print("\n--- Response / error handling ---")
p, c = run(P + ["agents", "get", "--agent-id", "a1"], status=403,
           body={"error": {"code": 403, "message": "Permission denied", "status": "PERMISSION_DENIED"}})
check("HTTP 403 -> exit 1", p.returncode == 1, str(p.returncode))
check("HTTP error body surfaced on stderr", "PERMISSION_DENIED" in p.stderr and "403" in p.stderr, p.stderr.strip()[:200])

p, c = run(P + ["agents", "delete", "--agent-id", "a1"], body="")
check("empty 200 body -> {}", p.returncode == 0 and json.loads(p.stdout) == {}, repr(p.stdout))

p, c = run(P + ["agents", "get", "--agent-id", "a1"], body="not json at all")
check("non-JSON 200 body -> returned, not crash", p.returncode == 0 and "not json" in p.stdout, repr(p.stdout))

p, c = run(P + ["agents", "get", "--agent-id", "a1"], status=404,
           body={"error": {"code": 404, "status": "NOT_FOUND"}})
check("HTTP 404 -> exit 1 + body", p.returncode == 1 and "NOT_FOUND" in p.stderr, p.stderr.strip()[:200])

print("\n--- Unicode / quoting ---")
p, c = run(P + ["chat", "--agent-id", "a1", "--message", "¿Cuántos pedidos? 100% & más"], body=[])
check("unicode/special chars survive round-trip in body",
      c and c["body"]["messages"][0]["userMessage"]["text"] == "¿Cuántos pedidos? 100% & más", str(c and c["body"]))

p, c = run(P + ["conversations", "list", "--filter", "a b&c=d"])
check("query values are percent-encoded (no injection)",
      c and c["query"] == {"filter": "a b&c=d"}, "raw=" + str(c and c["raw_query"]))


print('\n=== Regression checks for fixed defects ===')
NOTOK = {"GDA_ACCESS_TOKEN": ""}

print("--- Fix 1: validation errors no longer masked by auth errors ---")
p, c = run(P + ["agents", "update", "--agent-id", "a"], env_extra=NOTOK)
check("bad flags beat missing-creds error", "nothing to update" in p.stderr and "gcloud" not in p.stderr, p.stderr.strip())
p, c = run(P + ["chat", "--message", "x"], env_extra=NOTOK)
check("chat no-context error beats auth error", "bq-table" in p.stderr and "gcloud" not in p.stderr, p.stderr.strip())
p, c = run(P + ["agents", "list"], env_extra=NOTOK)
check("a valid command still reports missing creds", p.returncode == 1 and "gcloud" in p.stderr, p.stderr.strip()[:80])
p, c = run(P + ["agents", "list"])
check("token still sent on the wire", c and c["headers"]["authorization"] == "Bearer test-token-abc", str(c and c["headers"].get("authorization")))

print("\n--- Fix 2: non-JSON error bodies trimmed, JSON ones kept whole ---")
HTML = "<!DOCTYPE html><html><head><title>Error 404 (Not Found)!!1</title>" + "<style>x{y:1}</style>" * 200 + "</html>"
p, c = run(P + ["agents", "get", "--agent-id", "a"], status=404, body=HTML)
check("HTML 404 trimmed to a hint", "non-JSON error body" in p.stderr and len(p.stderr) < 400, f"len={len(p.stderr)}")
check("HTML 404 still says HTTP 404", "HTTP 404" in p.stderr, p.stderr[:120])
p, c = run(P + ["agents", "get", "--agent-id", "a"], status=403,
           body={"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [{"reason": "IAM_PERMISSION_DENIED"}]}})
check("JSON google.rpc.Status still shown in full", "IAM_PERMISSION_DENIED" in p.stderr and "non-JSON" not in p.stderr, p.stderr[:200])

print("\n--- Fix 3: agents update --python now applied ---")
p, c = run(P + ["agents", "update", "--agent-id", "a", "--python"])
check("update --python sends options.analysis",
      c and c["body"]["dataAnalyticsAgent"]["publishedContext"]["options"] == {"analysis": {"python": {"enabled": True}}},
      json.dumps(c and c["body"]))
check("update --python masks options.analysis only",
      c and c["query"]["updateMask"] == "data_analytics_agent.published_context.options.analysis", str(c and c["query"]))
p, c = run(P + ["agents", "update", "--agent-id", "a", "--display-name", "n", "--python"])
check("update --python composes with other fields",
      c and c["query"]["updateMask"] == "display_name,data_analytics_agent.published_context.options.analysis", str(c and c["query"]))

print("\n--- Fix 4: silently-dropped chat context flags now rejected ---")
for flags, label in ((["--bq-table", "x.y.z"], "--bq-table"),
                     (["--system-instruction", "rule"], "--system-instruction"),
                     (["--python"], "--python")):
    p, c = run(P + ["chat", "--agent-id", "a1", "--message", "Q"] + flags, body=[])
    check(f"chat --agent-id + {label} -> error, no request", p.returncode == 1 and c is None and label in p.stderr, p.stderr.strip()[:160])
p, c = run(P + ["chat", "--agent-id", "a1", "--conversation-id", "c1", "--bq-table", "x.y.z", "--message", "Q"], body=[])
check("stateful chat + --bq-table -> error", p.returncode == 1 and c is None, p.stderr.strip()[:120])
p, c = run(P + ["chat", "--agent-id", "a1", "--message", "Q"], body=[])
check("plain agent chat still works", p.returncode == 0 and c and "dataAgentContext" in c["body"], p.stderr[:120])
p, c = run(P + ["chat", "--bq-table", "x.y.z", "--python", "--system-instruction", "s", "--message", "Q"], body=[])
check("inline chat with all context flags still works",
      p.returncode == 0 and c and set(c["body"]["inlineContext"]) == {"datasourceReferences", "systemInstruction", "options"},
      json.dumps(c and c["body"]))

print("\n--- Fix 5: --help version text ---")
p, _ = run(["--project", "p", "--help"])
check("--help says v1 is the default", "v1 (GA, default)" in p.stdout and "v1beta (default)" not in p.stdout,
      [l for l in p.stdout.splitlines() if "--version" in l])

print("\n--- connection-error diagnosis ---")
import ssl as _ssl, urllib.error as _ue, importlib.util as _ilu
_spec = _ilu.spec_from_file_location("gda", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "gda.py"))
_gda = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gda)
_tls = _gda._connection_error(_ue.URLError(_ssl.SSLCertVerificationError(
    1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")))
check("TLS failure is named as a Python trust problem, not an outage",
      "TLS trust problem in Python" in _tls and "Install Certificates" in _tls
      and "SSL_CERT_FILE" in _tls, _tls[:200])
check("TLS advice never suggests disabling verification",
      "Do not disable certificate verification" in _tls
      and "verify=False" not in _tls and "_create_unverified" not in _tls, _tls[:200])
_dns = _gda._connection_error(_ue.URLError("Name or service not known"))
check("non-TLS connection errors stay terse",
      "TLS trust problem" not in _dns and "Name or service not known" in _dns, _dns)

print("\n--- doctor ---")
p, c = run(["doctor"], body={"dataAgents": [{"name": "a"}, {"name": "b"}]},
           env_extra={"GOOGLE_CLOUD_PROJECT": "doc-project"})
check("doctor reports sources and agent count",
      p.returncode == 0 and "credentials: $GDA_ACCESS_TOKEN" in p.stdout
      and "doc-project (from $GOOGLE_CLOUD_PROJECT)" in p.stdout
      and "2 data agent(s) visible" in p.stdout, p.stdout[-300:])
p, c = run(["doctor"], body=[], env_extra={"GOOGLE_CLOUD_PROJECT": "doc-project"})
check("doctor survives an unexpected response shape (no traceback)",
      p.returncode == 0 and "Traceback" not in p.stderr, (p.stderr or p.stdout)[-300:])
p, c = run(["doctor"], status=403, env_extra={"GOOGLE_CLOUD_PROJECT": "doc-project"},
           body={"error": {"code": 403, "status": "SERVICE_DISABLED",
                           "message": "geminidataanalytics.googleapis.com is not enabled"}})
check("doctor surfaces the API's own diagnosis",
      p.returncode == 1 and "SERVICE_DISABLED" in p.stderr, p.stderr[-200:])

print('\n--- Mid-stream error detection ---')
PARTIAL = [{"systemMessage": {"text": {"parts": ["Sales were 100"], "textType": "FINAL_RESPONSE"}}},
           {"error": {"code": 13, "status": "INTERNAL", "message": "stream broke"}}]
p, _ = run(P + ["chat", "--agent-id", "a", "--message", "Q", "--answer-only"], body=PARTIAL)
check("mid-stream error: partial answer still printed", "Sales were 100" in p.stdout, repr(p.stdout))
check("mid-stream error: exits non-zero", p.returncode == 1, str(p.returncode))
check("mid-stream error: reported on stderr", "incomplete" in p.stderr and "stream broke" in p.stderr, p.stderr[:200])
p, _ = run(P + ["chat", "--agent-id", "a", "--message", "Q"], body=PARTIAL)
check("mid-stream error caught in full-stream mode too", p.returncode == 1 and "INTERNAL" in p.stderr, p.stderr[:120])
CLEAN = [{"systemMessage": {"text": {"parts": ["All good"], "textType": "FINAL_RESPONSE"}}}]
p, _ = run(P + ["chat", "--agent-id", "a", "--message", "Q", "--answer-only"], body=CLEAN)
check("clean stream still exits 0", p.returncode == 0 and p.stdout.strip() == "All good", f"{p.returncode} {p.stdout!r}")
p, _ = run(P + ["agents", "get", "--agent-id", "a"], body={"name": "x"})
check("non-chat commands unaffected", p.returncode == 0, p.stderr[:120])
sys.exit(summary())
