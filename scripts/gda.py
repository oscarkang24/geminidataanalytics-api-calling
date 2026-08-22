#!/usr/bin/env python3
"""CLI for the Gemini Data Analytics (Conversational Analytics) HTTP API.

Talks to https://geminidataanalytics.googleapis.com using Application Default
Credentials. Covers full CRUD over Data Agents and Conversations, plus the
stateful and stateless Chat surfaces.

Auth: by default an ADC access token is fetched via
`gcloud auth application-default print-access-token`. Override it with
`--access-token` or the `$GDA_ACCESS_TOKEN` env var. The billing/quota project
is sent in the `x-goog-user-project` header.

No third-party dependencies (urllib only).
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_HOST = "https://geminidataanalytics.googleapis.com"
DEFAULT_VERSION = "v1"  # GA. Use v1beta for preview-only features (e.g. queryData).
DEFAULT_LOCATION = "global"


def _die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def get_token():
    try:
        out = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()
    except FileNotFoundError:
        _die("gcloud not found on PATH")
    except subprocess.CalledProcessError as e:
        _die(
            "failed to obtain ADC token. Run "
            "`gcloud auth application-default login` first.\n" + e.stderr.strip()
        )


class Client:
    def __init__(self, project, location, version, host, token=None, verbose=False):
        self.project = project
        self.location = location
        self.version = version
        self.host = host.rstrip("/")
        self._token = token
        self.verbose = verbose

    @property
    def token(self):
        # Fetched lazily: a bad-flags error should surface before an auth error.
        if not self._token:
            self._token = get_token()
        return self._token

    @property
    def parent(self):
        return f"projects/{self.project}/locations/{self.location}"

    def url(self, path):
        # path is everything after the version, without a leading slash.
        return f"{self.host}/{self.version}/{path}"

    def request(self, method, path, body=None, query=None):
        url = self.url(path)
        if query:
            pairs = []
            for k, v in query.items():
                if v is None:
                    continue
                pairs.append(f"{k}={urllib.parse.quote(str(v))}")
            if pairs:
                url += "?" + "&".join(pairs)
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "x-goog-user-project": self.project,
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.verbose:
            print(f"# {method} {url}", file=sys.stderr)
            if body is not None:
                print("# body: " + json.dumps(body), file=sys.stderr)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            _die(f"HTTP {e.code} {e.reason}\n{_error_detail(detail)}")
        except urllib.error.URLError as e:
            _die(f"connection failed: {e.reason}")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


def _error_detail(body):
    """Render an HTTP error body.

    JSON `google.rpc.Status` bodies pass through in full. Anything else — most
    often the multi-KB HTML page the API front end serves for an unknown path —
    is trimmed to a hint, so the real cause isn't buried in markup.
    """
    body = body.strip()
    try:
        json.loads(body)
    except ValueError:
        return ("non-JSON error body (usually a wrong path or API version)\n"
                + body[:200])
    return body


def out(obj):
    print(json.dumps(obj, indent=2))


# ---------------------------------------------------------------------------
# Context / datasource builders
# ---------------------------------------------------------------------------

def parse_bq_table(spec):
    """`project.dataset.table` -> BigQueryTableReference dict."""
    parts = spec.split(".")
    if len(parts) != 3:
        _die(f"--bq-table must be project.dataset.table, got '{spec}'")
    return {"projectId": parts[0], "datasetId": parts[1], "tableId": parts[2]}


def build_context(args):
    """Build a Context proto from --bq-table / --system-instruction flags."""
    tables = [parse_bq_table(t) for t in (args.bq_table or [])]
    if not tables:
        _die("at least one --bq-table is required to build inline context")
    ctx = {
        "datasourceReferences": {"bq": {"tableReferences": tables}},
    }
    if args.system_instruction:
        ctx["systemInstruction"] = args.system_instruction
    if getattr(args, "python", False):
        ctx.setdefault("options", {})["analysis"] = {"python": {"enabled": True}}
    return ctx


# ---------------------------------------------------------------------------
# Data Agents
# ---------------------------------------------------------------------------

def agents_create(c, args):
    body = {
        "dataAnalyticsAgent": {
            "publishedContext": build_context(args),
        }
    }
    if args.display_name:
        body["displayName"] = args.display_name
    if args.description:
        body["description"] = args.description
    # createSync returns the resource directly (no LRO).
    path = f"{c.parent}/dataAgents:createSync"
    out(c.request("POST", path, body=body, query={"dataAgentId": args.agent_id}))


def agents_get(c, args):
    out(c.request("GET", f"{c.parent}/dataAgents/{args.agent_id}"))


def agents_list(c, args):
    q = {"pageSize": args.page_size, "pageToken": args.page_token}
    suffix = ":listAccessible" if args.accessible else ""
    out(c.request("GET", f"{c.parent}/dataAgents{suffix}", query=q))


def agents_update(c, args):
    agent = {}
    mask = []
    if args.display_name is not None:
        agent["displayName"] = args.display_name
        mask.append("display_name")
    if args.description is not None:
        agent["description"] = args.description
        mask.append("description")
    # Mask individual fields *inside* publishedContext, so changing one (e.g. the
    # table) never clobbers a sibling like systemInstruction.
    ctx = {}
    if args.bq_table:
        tables = [parse_bq_table(t) for t in args.bq_table]
        ctx["datasourceReferences"] = {"bq": {"tableReferences": tables}}
        mask.append("data_analytics_agent.published_context.datasource_references")
    if args.system_instruction is not None:
        ctx["systemInstruction"] = args.system_instruction
        mask.append("data_analytics_agent.published_context.system_instruction")
    if getattr(args, "python", False):
        ctx.setdefault("options", {})["analysis"] = {"python": {"enabled": True}}
        mask.append("data_analytics_agent.published_context.options.analysis")
    if ctx:
        agent["dataAnalyticsAgent"] = {"publishedContext": ctx}
    if not mask:
        _die("nothing to update; pass --display-name / --description / "
             "--bq-table / --system-instruction / --python")
    path = f"{c.parent}/dataAgents/{args.agent_id}:updateSync"
    out(c.request("PATCH", path, body=agent, query={"updateMask": ",".join(mask)}))


def agents_delete(c, args):
    out(c.request("DELETE", f"{c.parent}/dataAgents/{args.agent_id}:deleteSync"))


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

def conversations_create(c, args):
    body = {"agents": [f"{c.parent}/dataAgents/{args.agent_id}"]}
    q = {"conversationId": args.conversation_id}
    out(c.request("POST", f"{c.parent}/conversations", body=body, query=q))


def conversations_get(c, args):
    out(c.request("GET", f"{c.parent}/conversations/{args.conversation_id}"))


def conversations_list(c, args):
    q = {"pageSize": args.page_size, "pageToken": args.page_token, "filter": args.filter}
    out(c.request("GET", f"{c.parent}/conversations", query=q))


def conversations_delete(c, args):
    out(c.request("DELETE", f"{c.parent}/conversations/{args.conversation_id}"))


def conversations_messages(c, args):
    q = {"pageSize": args.page_size, "pageToken": args.page_token}
    path = f"{c.parent}/conversations/{args.conversation_id}/messages"
    out(c.request("GET", path, query=q))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _chat_body(c, args):
    # An agent brings its own context, so inline-context flags would be dropped
    # on the floor. Silently answering from the *agent's* table when the user
    # named a different one is a wrong answer, so refuse instead.
    ignored = [f for f, v in (("--bq-table", args.bq_table),
                              ("--system-instruction", args.system_instruction),
                              ("--python", getattr(args, "python", False))) if v]
    if (args.agent_id or args.conversation_id) and ignored:
        _die(", ".join(ignored) + " cannot be combined with --agent-id/"
             "--conversation-id: the agent's own context is used instead. "
             "Either drop --agent-id to chat with inline context, or bake these "
             "into the agent with `agents update`.")
    body = {
        "parent": c.parent,
        "messages": [{"userMessage": {"text": args.message}}],
    }
    if args.conversation_id:
        # Stateful chat against a persisted conversation + agent.
        if not args.agent_id:
            _die("--agent-id is required with --conversation-id")
        body["conversationReference"] = {
            "conversation": f"{c.parent}/conversations/{args.conversation_id}",
            "dataAgentContext": {
                "dataAgent": f"{c.parent}/dataAgents/{args.agent_id}"
            },
        }
    elif args.agent_id:
        # Stateless chat against an agent's context.
        body["dataAgentContext"] = {
            "dataAgent": f"{c.parent}/dataAgents/{args.agent_id}"
        }
    else:
        # Fully stateless: inline context built from --bq-table.
        body["inlineContext"] = build_context(args)
    return body


def chat(c, args):
    # :chat is the public chat endpoint. Over REST it returns a JSON array of
    # streamed Message objects (thoughts, generated queries, and the answer).
    body = _chat_body(c, args)
    resp = c.request("POST", f"{c.parent}:chat", body=body)
    if getattr(args, "answer_only", False):
        print(_final_answer(resp))
    else:
        out(resp)
    # A stream that fails partway still returns HTTP 200 with an `error` element
    # appended to the array. Printing the partial answer and exiting 0 would
    # report a truncated result as a complete one, so fail loudly instead.
    errors = [m["error"] for m in resp
              if isinstance(m, dict) and isinstance(m.get("error"), dict)] \
        if isinstance(resp, list) else []
    if errors:
        _die("the response stream ended in an error; the answer above is "
             "incomplete:\n" + json.dumps(errors, indent=2))


def _final_answer(messages):
    """Render the answer from a :chat response.

    A tabular answer arrives as a separate `data` system message; the
    FINAL_RESPONSE text is only a lead-in. Returning the text alone would
    silently drop the rows, so we append any data blocks too. If nothing is
    recognized we fall back to the full stream rather than dropping the answer.
    """
    if not isinstance(messages, list):
        return json.dumps(messages, indent=2)
    blocks = []
    for m in messages:
        sysmsg = m.get("systemMessage")
        if not isinstance(sysmsg, dict):
            continue
        text = sysmsg.get("text")
        if isinstance(text, dict) and text.get("textType") == "FINAL_RESPONSE":
            parts = text.get("parts", [])
            if parts:
                blocks.append("\n".join(parts))
        if "data" in sysmsg:
            rendered = _render_data(sysmsg["data"])
            if rendered:
                blocks.append(rendered)
    return "\n\n".join(blocks) if blocks else json.dumps(messages, indent=2)


def _render_data(data_msg):
    """Best-effort render of a DataMessage result as a TSV table; JSON fallback.

    The exact field names are inferred from the CA API DataMessage shape; if the
    structure isn't what we expect we return the raw JSON so the rows are never
    silently lost.
    """
    if not isinstance(data_msg, dict):
        return None
    result = data_msg.get("result") or {}
    fields = (result.get("schema") or {}).get("fields") or []
    rows = result.get("data")
    if fields and isinstance(rows, list):
        headers = [f.get("name", "") for f in fields]
        lines = ["\t".join(headers)]
        for row in rows:
            if isinstance(row, dict):
                lines.append("\t".join(str(row.get(h, "")) for h in headers))
            else:
                lines.append(str(row))
        return "\n".join(lines)
    return json.dumps(data_msg, indent=2)


# ---------------------------------------------------------------------------
# Raw escape hatch
# ---------------------------------------------------------------------------

def raw(c, args):
    # Allow {parent} / {location} / {project} substitution in both path and body.
    def tmpl(s):
        return s.replace("{parent}", c.parent).replace(
            "{project}", c.project).replace("{location}", c.location)

    body = None
    if args.body:
        raw_body = sys.stdin.read() if args.body == "-" else args.body
        body = json.loads(tmpl(raw_body))
    path = tmpl(args.path).lstrip("/")
    out(c.request(args.method.upper(), path, body=body))


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="gda", description=__doc__.splitlines()[0])
    p.add_argument("--project", required=True, help="GCP project id (billing/quota)")
    p.add_argument("--location", default=DEFAULT_LOCATION, help="default: global")
    p.add_argument("--version", default=DEFAULT_VERSION,
                   help="API version: v1 (GA, default), v1beta, v1alpha")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--access-token",
                   help="OAuth2 access token to use instead of gcloud ADC; "
                        "falls back to $GDA_ACCESS_TOKEN")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print request method/URL/body to stderr")
    sub = p.add_subparsers(dest="resource", required=True)

    def add_context_flags(sp):
        sp.add_argument("--bq-table", action="append", metavar="PROJ.DATASET.TABLE",
                        help="BigQuery table; repeatable")
        sp.add_argument("--system-instruction", help="business context / instructions")
        sp.add_argument("--python", action="store_true",
                        help="enable Python analysis in context")

    # agents ------------------------------------------------------------
    ag = sub.add_parser("agents", help="manage Data Agents")
    ags = ag.add_subparsers(dest="action", required=True)

    a = ags.add_parser("create")
    a.add_argument("--agent-id", required=True)
    a.add_argument("--display-name")
    a.add_argument("--description")
    add_context_flags(a)
    a.set_defaults(func=agents_create)

    a = ags.add_parser("get")
    a.add_argument("--agent-id", required=True)
    a.set_defaults(func=agents_get)

    a = ags.add_parser("list")
    a.add_argument("--accessible", action="store_true",
                   help="list agents the caller can access (listAccessible)")
    a.add_argument("--page-size", type=int)
    a.add_argument("--page-token")
    a.set_defaults(func=agents_list)

    a = ags.add_parser("update")
    a.add_argument("--agent-id", required=True)
    a.add_argument("--display-name")
    a.add_argument("--description")
    add_context_flags(a)
    a.set_defaults(func=agents_update)

    a = ags.add_parser("delete")
    a.add_argument("--agent-id", required=True)
    a.set_defaults(func=agents_delete)

    # conversations -----------------------------------------------------
    cv = sub.add_parser("conversations", help="manage Conversations")
    cvs = cv.add_subparsers(dest="action", required=True)

    a = cvs.add_parser("create")
    a.add_argument("--agent-id", required=True)
    a.add_argument("--conversation-id")
    a.set_defaults(func=conversations_create)

    a = cvs.add_parser("get")
    a.add_argument("--conversation-id", required=True)
    a.set_defaults(func=conversations_get)

    a = cvs.add_parser("list")
    a.add_argument("--filter", help="e.g. 'labels.key=value' or agent filter")
    a.add_argument("--page-size", type=int)
    a.add_argument("--page-token")
    a.set_defaults(func=conversations_list)

    a = cvs.add_parser("delete")
    a.add_argument("--conversation-id", required=True)
    a.set_defaults(func=conversations_delete)

    a = cvs.add_parser("messages", help="list messages in a conversation")
    a.add_argument("--conversation-id", required=True)
    a.add_argument("--page-size", type=int)
    a.add_argument("--page-token")
    a.set_defaults(func=conversations_messages)

    # chat --------------------------------------------------------------
    ch = sub.add_parser("chat", help="ask a question")
    ch.add_argument("--message", required=True, help="natural language question")
    ch.add_argument("--agent-id", help="chat against this agent")
    ch.add_argument("--conversation-id",
                    help="stateful chat in this conversation (needs --agent-id)")
    ch.add_argument("--answer-only", action="store_true",
                    help="print just the final answer text, not the full message stream")
    add_context_flags(ch)
    ch.set_defaults(func=chat)

    # raw ---------------------------------------------------------------
    rw = sub.add_parser("raw", help="arbitrary request against any endpoint")
    rw.add_argument("method", help="GET/POST/PATCH/DELETE")
    rw.add_argument("path", help="path after version; supports {parent} template")
    rw.add_argument("--body", help="JSON string, or '-' to read stdin")
    rw.set_defaults(func=raw)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    token = args.access_token or os.environ.get("GDA_ACCESS_TOKEN")
    c = Client(
        project=args.project,
        location=args.location,
        version=args.version,
        host=args.host,
        token=token,
        verbose=args.verbose,
    )
    args.func(c, args)


if __name__ == "__main__":
    main()
