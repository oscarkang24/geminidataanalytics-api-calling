#!/usr/bin/env python3
"""A stand-in for the Conversational Analytics API.

Implements enough of DataAgentService and DataChatService to run the full
lifecycle, and enforces the parts of the contract the CLI must get right:
an Authorization header, x-goog-user-project, and exactly one context provider
on :chat. Used by tests/test_lifecycle.py.
"""
import json, re, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

AGENTS, CONVERSATIONS, MESSAGES = {}, {}, {}


def _status(code, status, msg):
    return code, {"error": {"code": code, "status": status, "message": msg}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _respond(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode()) if n else None
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = u.path

        if not (self.headers.get("Authorization") or "").startswith("Bearer "):
            return self._respond(*_status(401, "UNAUTHENTICATED", "missing bearer token"))
        if not self.headers.get("x-goog-user-project"):
            return self._respond(*_status(403, "PERMISSION_DENIED", "no quota project"))

        code, resp = self.route(self.command, path, q, body)
        self._respond(code, resp)

    def route(self, method, path, q, body):
        m = re.match(r"^/(v1[^/]*)/(projects/[^/]+/locations/[^/:]+)(.*)$", path)
        if not m:
            return _status(404, "NOT_FOUND", "bad path")
        _, parent, rest = m.groups()

        if rest == ":chat" and method == "POST":
            providers = [k for k in ("conversationReference", "dataAgentContext",
                                     "inlineContext") if k in (body or {})]
            if len(providers) != 1:
                return _status(400, "INVALID_ARGUMENT",
                               f"expected exactly one context provider, got {providers}")
            text = body["messages"][0]["userMessage"]["text"]
            if providers[0] == "conversationReference":
                cid = body["conversationReference"]["conversation"]
                MESSAGES.setdefault(cid, []).append({"userMessage": {"text": text}})
                MESSAGES[cid].append({"systemMessage": {"text": {
                    "parts": [f"Answer to: {text}"], "textType": "FINAL_RESPONSE"}}})
            return 200, [
                {"systemMessage": {"text": {"parts": ["Looking at the data"],
                                            "textType": "THOUGHT"}}},
                {"systemMessage": {"data": {"result": {
                    "schema": {"fields": [{"name": "n"}]}, "data": [{"n": 42}]}}}},
                {"systemMessage": {"text": {"parts": [f"Answer to: {text}"],
                                            "textType": "FINAL_RESPONSE"}}},
            ]

        # --- data agents ---
        if rest == "/dataAgents" and method == "GET":
            live = [a for a in AGENTS.values() if "deleteTime" not in a]
            return 200, {"dataAgents": live}
        if rest == "/dataAgents:listAccessible" and method == "GET":
            return 200, {"dataAgents": [a for a in AGENTS.values() if "deleteTime" not in a]}
        if rest == "/dataAgents:createSync" and method == "POST":
            aid = q.get("dataAgentId")
            if not aid:
                return _status(400, "INVALID_ARGUMENT", "dataAgentId required")
            if aid in AGENTS:
                return _status(409, "ALREADY_EXISTS", aid)
            agent = dict(body or {})
            agent["name"] = f"{parent}/dataAgents/{aid}"
            AGENTS[aid] = agent
            return 200, agent
        m = re.match(r"^/dataAgents/([^:/]+)(:updateSync|:deleteSync)?$", rest)
        if m:
            aid, verb = m.group(1), m.group(2)
            agent = AGENTS.get(aid)
            if not agent or (agent.get("deleteTime") and method == "GET" and not verb):
                if not agent:
                    return _status(404, "NOT_FOUND", f"dataAgent {aid}")
            if method == "GET" and not verb:
                return 200, agent
            if method == "PATCH" and verb == ":updateSync":
                if not q.get("updateMask"):
                    return _status(400, "INVALID_ARGUMENT", "updateMask required")
                for field in q["updateMask"].split(","):
                    if field == "display_name":
                        agent["displayName"] = body.get("displayName")
                    elif field == "description":
                        agent["description"] = body.get("description")
                    elif field.startswith("data_analytics_agent.published_context."):
                        leaf = field.rsplit(".", 1)[-1]
                        key = {"datasource_references": "datasourceReferences",
                               "system_instruction": "systemInstruction",
                               "analysis": "options"}[leaf]
                        src = (body.get("dataAnalyticsAgent") or {}).get("publishedContext") or {}
                        agent.setdefault("dataAnalyticsAgent", {}).setdefault(
                            "publishedContext", {})[key] = src.get(key)
                    else:
                        return _status(400, "INVALID_ARGUMENT", f"bad mask {field}")
                return 200, agent
            if method == "DELETE" and verb == ":deleteSync":
                # Real API soft-deletes: resource persists, excluded from list.
                agent["deleteTime"] = "2026-08-22T00:00:00Z"
                return 200, {}

        # --- conversations ---
        if rest == "/conversations" and method == "POST":
            cid = q.get("conversationId") or "auto-generated"
            conv = dict(body or {})
            conv["name"] = f"{parent}/conversations/{cid}"
            CONVERSATIONS[cid] = conv
            return 200, conv
        if rest == "/conversations" and method == "GET":
            return 200, {"conversations": list(CONVERSATIONS.values())}
        m = re.match(r"^/conversations/([^/]+)(/messages)?$", rest)
        if m:
            cid, msgs = m.group(1), m.group(2)
            conv = CONVERSATIONS.get(cid)
            if not conv:
                return _status(404, "NOT_FOUND", f"conversation {cid}")
            if msgs and method == "GET":
                return 200, {"messages": MESSAGES.get(conv["name"], [])}
            if method == "GET":
                return 200, conv
            if method == "DELETE":
                CONVERSATIONS.pop(cid)
                return 200, {}
        return _status(404, "NOT_FOUND", f"no handler for {method} {rest}")

    do_GET = do_POST = do_PATCH = do_DELETE = _handle


def start():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
