#!/usr/bin/env python3
"""CLI for the Gemini Data Analytics (Conversational Analytics) HTTP API.

Talks to https://geminidataanalytics.googleapis.com using Application Default
Credentials. Covers full CRUD over Data Agents and Conversations, plus the
stateful and stateless Chat surfaces.

Auth and project are discovered automatically, in the same order the Google
client libraries use, so no flags are needed on a normally configured machine:

  token    $GDA_ACCESS_TOKEN -> $GOOGLE_APPLICATION_CREDENTIALS service account
           -> gcloud ADC file -> GCE/Cloud Run metadata server -> gcloud CLI
  project  --project -> $GDA_PROJECT / $GOOGLE_CLOUD_PROJECT / $GCLOUD_PROJECT /
           $CLOUDSDK_CORE_PROJECT -> ADC quota project -> service-account
           project -> metadata server -> `gcloud config get-value project`

`--access-token` and `--project` override the corresponding chain. `-v` reports
which source each came from. The project is sent in `x-goog-user-project`.

No third-party dependencies (urllib only; RS256 signing shells out to openssl).
"""

import argparse
import base64
import json
import os
import http.client
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_HOST = "https://geminidataanalytics.googleapis.com"
DEFAULT_VERSION = "v1"  # GA. Use v1beta for preview-only features (e.g. queryData).
DEFAULT_LOCATION = "global"
OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
# GCE_METADATA_HOST is the standard override honoured by Google's own libraries.
METADATA_HOST = os.environ.get("GCE_METADATA_HOST", "metadata.google.internal")
PROJECT_ENV_VARS = ("GDA_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT",
                    "CLOUDSDK_CORE_PROJECT")
# Generous, because :chat generates SQL and runs it against BigQuery — but never
# unbounded: without this a stalled connection hangs the CLI forever.
DEFAULT_TIMEOUT = 300


def _timeout_arg(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"must be a number of seconds, got {value!r}")
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"must be greater than 0, got {value!r}")
    return seconds


def _env_timeout():
    """$GDA_TIMEOUT, validated — a bad value must not crash even `--help`."""
    raw = os.environ.get("GDA_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_TIMEOUT
    try:
        return _timeout_arg(raw)
    except argparse.ArgumentTypeError as e:
        _die(f"$GDA_TIMEOUT {e}")


def _die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


class Unavailable(Exception):
    """No credential, or no project, could be discovered."""


def _load_json_file(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def adc_path():
    """Well-known location of the `gcloud auth application-default` file."""
    cfg = os.environ.get("CLOUDSDK_CONFIG")
    if not cfg:
        cfg = (os.path.join(os.environ.get("APPDATA", ""), "gcloud")
               if os.name == "nt" else os.path.expanduser("~/.config/gcloud"))
    return os.path.join(cfg, "application_default_credentials.json")


def _post_form(url, form, timeout=20):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with _urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", "replace")[:200])


def _metadata_get(path, timeout=2):
    """Read from the GCE/Cloud Run metadata server, bypassing any HTTP proxy."""
    req = urllib.request.Request(
        f"http://{METADATA_HOST}/computeMetadata/v1/{path}",
        headers={"Metadata-Flavor": "Google"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode().strip()


def _metadata_absence(e):
    """Off-cloud this always fails; say so instead of leaking a urllib error."""
    text = str(e)
    if "Name or service not known" in text or "Errno -2" in text or "refused" in text:
        return "not available (not running on GCE / Cloud Run)"
    return f"not available ({text})"


def _token_from_refresh(info):
    """`authorized_user` credentials -> access token."""
    for field in ("client_id", "client_secret", "refresh_token"):
        if not info.get(field):
            raise RuntimeError(f"missing {field}")
    r = _post_form(info.get("token_uri") or OAUTH_TOKEN_URI, {
        "client_id": info["client_id"],
        "client_secret": info["client_secret"],
        "refresh_token": info["refresh_token"],
        "grant_type": "refresh_token",
    })
    tok = r.get("access_token")
    if not tok:
        raise RuntimeError(f"token endpoint returned no access_token: {str(r)[:200]}")
    return tok


def _sign_rs256(private_key_pem, message):
    """RS256 via openssl, so service accounts work with no crypto dependency."""
    fd, key = tempfile.mkstemp(suffix=".pem")
    try:
        os.chmod(key, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(private_key_pem)
        p = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key],
                           input=message, capture_output=True, timeout=30)
        if p.returncode != 0:
            raise RuntimeError("openssl: " + p.stderr.decode().strip()[:200])
        return p.stdout
    except FileNotFoundError:
        raise RuntimeError("openssl not found on PATH (needed to sign the JWT)")
    finally:
        os.unlink(key)


def _token_from_service_account(info):
    """`service_account` credentials -> access token via a signed JWT grant."""
    for field in ("client_email", "private_key"):
        if not info.get(field):
            raise RuntimeError(f"missing {field}")
    uri = info.get("token_uri") or OAUTH_TOKEN_URI
    now = int(time.time())
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")
    signing_input = seg({"alg": "RS256", "typ": "JWT"}) + b"." + seg(
        {"iss": info["client_email"], "scope": SCOPE, "aud": uri,
         "iat": now, "exp": now + 3600})
    sig = base64.urlsafe_b64encode(
        _sign_rs256(info["private_key"], signing_input)).rstrip(b"=")
    r = _post_form(uri, {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": (signing_input + b"." + sig).decode()})
    tok = r.get("access_token")
    if not tok:
        raise RuntimeError(f"token endpoint returned no access_token: {str(r)[:200]}")
    return tok


def _token_from_credentials_file(info):
    kind = (info or {}).get("type")
    if kind == "authorized_user":
        return _token_from_refresh(info)
    if kind == "service_account":
        return _token_from_service_account(info)
    raise RuntimeError(f"unsupported credential type {kind!r}")


GCLOUD_INSTALL_DIRS = ("/usr/lib/google-cloud-sdk", "/opt/google-cloud-sdk",
                       "/usr/local/google-cloud-sdk", "/snap/google-cloud-sdk",
                       "~/google-cloud-sdk", "/usr/local/Caskroom/google-cloud-sdk")


def _gcloud_absence():
    """Distinguish 'installed but not on PATH' from 'not installed at all'.

    The remedies are completely different — one is a PATH export, the other a
    download — so saying only "not on PATH" sends people the wrong way.
    """
    for d in GCLOUD_INSTALL_DIRS:
        if os.path.isdir(os.path.expanduser(d)):
            return f"installed at {d} but not on PATH (add its bin/ to $PATH)"
    return "not installed (https://cloud.google.com/sdk/docs/install)"


def _gcloud(args):
    # stdin is closed so a reauth prompt fails fast instead of blocking on a
    # terminal read with its prompt swallowed by the captured pipe.
    try:
        p = subprocess.run(["gcloud"] + args, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("gcloud timed out after 30s (is it waiting for input?)")
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip().splitlines()[-1][:200]
                           if p.stderr.strip() else "gcloud failed")
    return p.stdout.strip()


def get_token():
    """Find an access token. Returns (token, source); exits if none is found."""
    tried = []
    tok = os.environ.get("GDA_ACCESS_TOKEN")
    if tok:
        return tok, "$GDA_ACCESS_TOKEN"
    # gcloud's own override. Honouring it keeps us consistent with the ecosystem;
    # naming the source keeps a bad value traceable.
    tok = os.environ.get("CLOUDSDK_AUTH_ACCESS_TOKEN")
    if tok:
        return tok, "$CLOUDSDK_AUTH_ACCESS_TOKEN"

    # Setting GOOGLE_APPLICATION_CREDENTIALS names the identity to use. Falling
    # through to a different one would run as the wrong principal, with
    # different IAM, so fail loudly instead - as Google's own libraries do.
    sa = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sa:
        info = _load_json_file(sa)
        if info is None:
            raise Unavailable(
                f"$GOOGLE_APPLICATION_CREDENTIALS is set to {sa}, which cannot "
                "be read as JSON. Fix or unset it rather than falling back to "
                "another identity.")
        try:
            return (_token_from_credentials_file(info),
                    f"$GOOGLE_APPLICATION_CREDENTIALS ({sa})")
        except Exception as e:
            if _is_cert_error(e):
                raise Unavailable(_connection_error(e))
            raise Unavailable(
                f"$GOOGLE_APPLICATION_CREDENTIALS is set to {sa} but no token "
                f"could be obtained from it:\n  {e}\nFix or unset it rather "
                "than silently using a different identity.")

    path = adc_path()
    info = _load_json_file(path)
    if info is None:
        tried.append(f"ADC file: not found at {path}")
    else:
        try:
            return _token_from_credentials_file(info), f"ADC file ({path})"
        except Exception as e:
            # A trust failure breaks every source, and "could not find
            # credentials" would send the user to fix perfectly good ADC.
            if _is_cert_error(e):
                raise Unavailable(_connection_error(e))
            tried.append(f"ADC file: {e}")

    try:
        blob = _metadata_get("instance/service-accounts/default/token")
        return json.loads(blob)["access_token"], f"metadata server ({METADATA_HOST})"
    except Exception as e:
        tried.append("metadata server: " + _metadata_absence(e))

    for cmd, label in ((["auth", "application-default", "print-access-token"],
                        "gcloud ADC"),
                       (["auth", "print-access-token"], "gcloud user")):
        try:
            t = _gcloud(cmd)
            if t:
                return t, f"{label} (`gcloud {' '.join(cmd)}`)"
        except FileNotFoundError:
            tried.append("gcloud: " + _gcloud_absence())
            break
        except Exception as e:
            if _is_cert_error(e):
                raise Unavailable(_connection_error(e))
            tried.append(f"{label}: {e}")

    no_gcloud = any(t.startswith("gcloud: not installed") for t in tried)
    if no_gcloud:
        # Leading with `gcloud auth ...` here would hand the user a command
        # that cannot run on this machine.
        remedy = ("gcloud is not installed here, so the usual "
                  "`gcloud auth application-default login` will not work. Either:\n"
                  "  - set $GOOGLE_APPLICATION_CREDENTIALS to a service-account "
                  "key file (best for containers and CI; no browser needed), or\n"
                  "  - export a token from a machine that has gcloud:\n"
                  "      export GDA_ACCESS_TOKEN=$(gcloud auth "
                  "application-default print-access-token), or\n"
                  "  - install the SDK: https://cloud.google.com/sdk/docs/install")
    else:
        remedy = ("Run `gcloud auth application-default login`, set "
                  "$GOOGLE_APPLICATION_CREDENTIALS to a service-account key, "
                  "or pass --access-token / $GDA_ACCESS_TOKEN.")
    raise Unavailable("could not find credentials. " + remedy
                      + "\nTried:\n  - " + "\n  - ".join(tried))


def detect_project():
    """Find the billing/quota project. Returns (project, source); exits if none."""
    tried = []
    for var in PROJECT_ENV_VARS:
        if os.environ.get(var):
            return os.environ[var], f"${var}"
    tried.append("env: none of $" + " $".join(PROJECT_ENV_VARS) + " set")

    for path, label in ((os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
                         "$GOOGLE_APPLICATION_CREDENTIALS"),
                        (adc_path(), "ADC file")):
        if not path:
            continue
        info = _load_json_file(path) or {}
        # A user ADC file carries quota_project_id; a key file carries project_id.
        proj = info.get("quota_project_id") or info.get("project_id")
        if proj:
            return proj, f"{label} ({path})"
        tried.append(f"{label}: no quota_project_id/project_id")

    try:
        return _metadata_get("project/project-id"), f"metadata server ({METADATA_HOST})"
    except Exception as e:
        tried.append("metadata server: " + _metadata_absence(e))

    try:
        proj = _gcloud(["config", "get-value", "project"])
        if proj and proj != "(unset)":
            return proj, "`gcloud config get-value project`"
        tried.append("gcloud config: project unset")
    except FileNotFoundError:
        tried.append("gcloud: " + _gcloud_absence())
    except Exception as e:
        tried.append(f"gcloud config: {e}")

    raise Unavailable(
        "could not determine the project. Pass --project, set "
        "$GOOGLE_CLOUD_PROJECT, or run `gcloud config set project PROJECT`."
        "\nTried:\n  - " + "\n  - ".join(tried))


class Client:
    def __init__(self, project, location, version, host, token=None, verbose=False,
                 timeout=DEFAULT_TIMEOUT):
        self._project = project
        self._project_source = "--project" if project else None
        self.location = location
        self.version = version
        self.host = host.rstrip("/")
        self._token = token
        self._token_source = "--access-token" if token else None
        self.verbose = verbose
        self.timeout = timeout

    # Both are resolved lazily, so a bad-flags error surfaces before we go
    # looking for credentials or a project.
    @property
    def token(self):
        if not self._token:
            try:
                self._token, self._token_source = get_token()
            except Unavailable as e:
                _die(f"{e}\n\nRun `gda.py doctor` for a full check.")
        return self._token

    @property
    def project(self):
        if not self._project:
            try:
                self._project, self._project_source = detect_project()
            except Unavailable as e:
                _die(f"{e}\n\nRun `gda.py doctor` for a full check.")
        return self._project

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
            print(f"# auth: {self._token_source}", file=sys.stderr)
            print(f"# project: {self.project} (from {self._project_source})",
                  file=sys.stderr)
            print(f"# {method} {url}", file=sys.stderr)
            if body is not None:
                print("# body: " + json.dumps(body), file=sys.stderr)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with _urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            _die(f"HTTP {e.code} {e.reason}\n{_error_detail(detail)}")
        except TimeoutError:
            _die(f"request timed out after {self.timeout}s. Raise it with "
                 "--timeout SECONDS (or $GDA_TIMEOUT) if the query is slow.")
        except urllib.error.URLError as e:
            _die(_connection_error(e))
        except (OSError, http.client.HTTPException) as e:
            # urllib only wraps errors raised while sending; a reset or a
            # truncated body during getresponse()/read() arrives raw. A long
            # :chat held open through a proxy is exactly when that happens.
            _die(f"connection failed mid-request: {type(e).__name__}: {e}")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


_CERTIFI_NOTED = False


def _is_cert_error(e):
    reason = getattr(e, "reason", e)
    return (isinstance(reason, ssl.SSLCertVerificationError)
            or "CERTIFICATE_VERIFY" in str(reason).upper())


def _certifi_context():
    """An SSL context using certifi's CA bundle, if certifi is installed."""
    try:
        import certifi
    except ImportError:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _urlopen(req, timeout=None):
    """urlopen, retrying once with certifi when the system store rejects the cert.

    Only ever runs after a verification failure, so a corporate CA that lives in
    the system store (and not in certifi) keeps working exactly as before.
    """
    global _CERTIFI_NOTED
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as e:
        ctx = _certifi_context() if _is_cert_error(e) else None
        if ctx is None:
            raise
        if not _CERTIFI_NOTED:
            print("# note: the system trust store rejected the certificate; "
                  "retrying with certifi.\n"
                  "#       make it permanent with: export SSL_CERT_FILE=$("
                  "python3 -c 'import certifi; print(certifi.where())')",
                  file=sys.stderr)
            _CERTIFI_NOTED = True
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _connection_error(e):
    """Explain a failed connection, singling out Python-side TLS trust.

    A certificate failure here while `curl` to the same URL succeeds means
    Python is not reading the trust store curl uses — a setup problem, not an
    outage — so say that rather than leaving it as "connection failed".
    """
    reason = e.reason
    msg = f"connection failed: {reason}"
    text = str(reason)
    if isinstance(reason, TimeoutError) or "timed out" in text.lower():
        return (msg + "\n\nRaise the deadline with --timeout SECONDS "
                "(or $GDA_TIMEOUT) if the endpoint is simply slow.")
    if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY" in text.upper():
        msg += (
            "\n\nThis is a TLS trust problem in Python, not a network outage:"
            "\ncurl can succeed on the same URL because it uses a different"
            "\ntrust store. Fixes, in order of likelihood:"
            "\n  - macOS python.org build: run"
            "\n      '/Applications/Python 3.x/Install Certificates.command'"
            "\n  - corporate TLS proxy: point Python at your CA bundle, e.g."
            "\n      export SSL_CERT_FILE=/path/to/corporate-ca.pem"
            "\n  - or install certifi and set SSL_CERT_FILE to"
            "\n      python3 -c 'import certifi; print(certifi.where())'"
            "\nDo not disable certificate verification.")
    return msg


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
        if not isinstance(m, dict):
            continue
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
    result = data_msg.get("result")
    result = result if isinstance(result, dict) else {}
    schema = result.get("schema")
    fields = (schema.get("fields") or []) if isinstance(schema, dict) else []
    fields = [f for f in fields if isinstance(f, dict)]
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
# Doctor
# ---------------------------------------------------------------------------

PLACEHOLDER_HINTS = ("proxy-inject", "changeme", "change-me", "placeholder",
                     "your-token", "replace_me", "replaceme", "xxx", "todo")


def _token_shape_warning(tok):
    """Flag a value that cannot be a Google OAuth access token.

    Real ones are long — `ya29.`-prefixed or a dotted JWT. Reporting a 14-char
    placeholder as working credentials sends people at the wrong failure, so
    say so before the API does.
    """
    t = (tok or "").strip()
    if not t:
        return "is empty"
    low = t.lower()
    for hint in PLACEHOLDER_HINTS:
        if hint in low:
            return f'looks like a placeholder, not a token: "{t[:40]}"'
    if any(c.isspace() for c in t):
        return "contains whitespace — it may be truncated or badly quoted"
    if len(t) < 20 and "." not in t:
        return (f'is {len(t)} characters with no dots — an OAuth access token '
                'is much longer (ya29.… or a dotted JWT)')
    return None


def _verify_token(c, project):
    """Ask the API whether the token is usable.

    Answerable without a real project: an unusable token is rejected with 401
    before the project is ever looked at, so this works even when project
    discovery failed.
    """
    url = f"{c.host}/{c.version}/projects/{project}/locations/global/dataAgents"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {c.token}",
        "x-goog-user-project": project})
    try:
        with _urlopen(req, timeout=min(c.timeout, 30)):
            return True, "accepted by the API"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 401:
            why = ("the token is not a usable OAuth token for this API "
                   "(ACCESS_TOKEN_TYPE_UNSUPPORTED)"
                   if "ACCESS_TOKEN_TYPE_UNSUPPORTED" in body
                   else "the API rejected the token")
            return False, f"HTTP 401 — {why}"
        return True, (f"accepted (HTTP {e.code} against a placeholder project, "
                      "which is expected)")
    except Exception as e:
        return None, f"could not check: {e}"


def _probe_host(c):
    """One unauthenticated request, purely to separate auth from network."""
    url = f"{c.host}/{c.version}/projects/-/locations/global/dataAgents"
    req = urllib.request.Request(url)
    try:
        with _urlopen(req, timeout=min(c.timeout, 30)):
            return f"[ok]   reachable: {c.host} answered"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return (f"[ok]   reachable: {c.host} answered {e.code} "
                    "(expected without credentials) — the network is fine, "
                    "this is purely an auth problem")
        return f"[??]   {c.host} answered {e.code} {e.reason}"
    except Exception as e:
        return (f"[FAIL] cannot reach {c.host}: {e}\n"
                "       so this may be a network/proxy problem, not only auth")


def doctor(c, args):
    """Report whether the CLI can run with no flags, and what is missing."""
    problems = []
    have_token = True
    if c._token:
        c._token_source = "--access-token"
    else:
        try:
            c._token, c._token_source = get_token()
        except Unavailable as e:
            have_token = False
            print("[FAIL] credentials: none found")
            problems.append(str(e))
    if have_token:
        warn = _token_shape_warning(c._token)
        if warn:
            print(f"[warn] credentials: {c._token_source} — the value {warn}")
        else:
            print(f"[ok]   credentials: found via {c._token_source} "
                  "(not proven until a call succeeds)")
    if c._project:
        print(f"[ok]   project: {c._project} (from --project)")
    else:
        try:
            c._project, c._project_source = detect_project()
            print(f"[ok]   project: {c._project} (from {c._project_source})")
        except Unavailable as e:
            print("[FAIL] project: could not be determined")
            problems.append(str(e))
    if problems:
        # Credentials and project fail independently. Stopping here on a
        # missing project would leave an unusable token undetected — exactly
        # what made the documented first command misdirect.
        if have_token:
            ok, detail = _verify_token(c, "gda-doctor-probe")
            mark = {True: "[ok]  ", False: "[FAIL]", None: "[??]  "}[ok]
            print(f"{mark} credentials checked against the API: {detail}")
        print("[..]   checking host reachability without credentials")
        print("       " + _probe_host(c))
        # Say plainly that this is a prerequisite, not a puzzle: without it a
        # session burns turns hunting for credentials that are not there.
        problems.append(
            "This is a prerequisite, not something to debug from here: every "
            "source above has already been checked. Obtaining a credential "
            "needs a browser login, a key file, or an environment that "
            "supplies one — all of which need the user. Report the above and "
            "stop rather than searching further.")
        _die("\n\n".join(problems))
    print(f"[..]   calling {c.host}/{c.version}/{c.parent}/dataAgents")
    # Any failure here (API disabled, no permission) _dies with the API's own
    # google.rpc.Status, which names the fix.
    resp = c.request("GET", f"{c.parent}/dataAgents")
    agents = resp.get("dataAgents") or [] if isinstance(resp, dict) else []
    print(f"[ok]   API reachable and authorized: {len(agents)} data agent(s) visible")
    print("\nReady: no flags needed.")


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
        try:
            body = json.loads(tmpl(raw_body))
        except ValueError as e:
            _die(f"--body is not valid JSON: {e}")
    path = tmpl(args.path).lstrip("/")
    out(c.request(args.method.upper(), path, body=body))


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="gda", description=__doc__.splitlines()[0])
    p.add_argument("--project",
                   help="GCP project id (billing/quota). Auto-detected from "
                        "$GOOGLE_CLOUD_PROJECT, the ADC file, the metadata "
                        "server, or gcloud config when omitted.")
    p.add_argument("--location", default=DEFAULT_LOCATION, help="default: global")
    p.add_argument("--version", default=DEFAULT_VERSION,
                   help="API version: v1 (GA, default), v1beta, v1alpha")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--access-token",
                   help="OAuth2 access token, skipping credential discovery; "
                        "falls back to $GDA_ACCESS_TOKEN")
    p.add_argument("--timeout", type=_timeout_arg, default=_env_timeout(),
                   help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT}); "
                        "also settable via $GDA_TIMEOUT")
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

    # doctor ------------------------------------------------------------
    dc = sub.add_parser("doctor", help="check credentials, project, and API access")
    dc.set_defaults(func=doctor)

    # raw ---------------------------------------------------------------
    rw = sub.add_parser("raw", help="arbitrary request against any endpoint")
    rw.add_argument("method", help="GET/POST/PATCH/DELETE")
    rw.add_argument("path", help="path after version; supports {parent} template")
    rw.add_argument("--body", help="JSON string, or '-' to read stdin")
    rw.set_defaults(func=raw)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # $GDA_ACCESS_TOKEN is handled inside get_token() so that -v can report the
    # source accurately; only the flag short-circuits discovery here.
    c = Client(
        project=args.project,
        location=args.location,
        version=args.version,
        host=args.host,
        token=args.access_token,
        verbose=args.verbose,
        timeout=args.timeout,
    )
    args.func(c, args)


def _run():
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        # Standard CPython dance: a closed pipe (`| head`, `| less` then q)
        # would otherwise print "BrokenPipeError ignored" at shutdown.
        try:
            sys.stdout.close()
        finally:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            sys.exit(0)


if __name__ == "__main__":
    _run()
