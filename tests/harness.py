#!/usr/bin/env python3
"""Mock-server test harness for scripts/gda.py.

Starts a local HTTP server that records the exact request the CLI sends, runs
the CLI against it, and asserts on method/path/query/headers/body plus the
CLI's stdout/stderr/exit code.
"""
import json, os, subprocess, sys, tempfile, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "scripts", "gda.py")

CAPTURED = []
RESPONSE = {"status": 200, "body": {"ok": True}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else None
        parsed = urllib.parse.urlsplit(self.path)
        CAPTURED.append({
            "method": self.command,
            "path": parsed.path,
            "raw_query": parsed.query,
            "query": dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)),
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body_raw": raw,
            "body": json.loads(raw) if raw else None,
        })
        body = RESPONSE["body"]
        payload = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(RESPONSE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = do_DELETE = do_PUT = _handle


srv = HTTPServer(("127.0.0.1", 0), Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
HOST = f"http://127.0.0.1:{PORT}"

results = []


# Credential and project discovery reads the ambient environment, so tests would
# otherwise behave differently on a machine that actually has credentials — a
# developer laptop with gcloud, an ADC file, or $GOOGLE_APPLICATION_CREDENTIALS
# set, or a GCE box whose metadata server answers. Every run starts from a
# scrubbed environment; a test that wants a source opts in through env_extra.
_EMPTY_CFG = tempfile.mkdtemp(prefix="gda-empty-cfg-")
_SCRUB = ("GDA_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS", "GDA_PROJECT",
          "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT",
          "CLOUDSDK_AUTH_ACCESS_TOKEN", "GOOGLE_CLOUD_QUOTA_PROJECT",
          "GDA_TIMEOUT")


def _path_shadowing_gcloud():
    """PATH with a failing `gcloud` shim in front.

    Dropping every directory that contains gcloud would also drop whatever else
    lives there — openssl among them, which the CLI needs to sign a JWT, and
    which shares /usr/bin or /opt/homebrew/bin with gcloud on many machines.
    Shadowing it keeps the rest of PATH intact.
    """
    shim_dir = tempfile.mkdtemp(prefix="gda-no-gcloud-")
    shim = os.path.join(shim_dir, "gcloud")
    with open(shim, "w") as f:
        f.write("#!/bin/sh\necho 'gcloud: not configured (test shim)' >&2\nexit 1\n")
    os.chmod(shim, 0o755)
    return shim_dir + os.pathsep + os.environ.get("PATH", "")


_NO_GCLOUD_PATH = _path_shadowing_gcloud()


def hermetic_env():
    env = dict(os.environ)
    for var in _SCRUB:
        env.pop(var, None)
    env["CLOUDSDK_CONFIG"] = _EMPTY_CFG          # no ADC file
    env["GCE_METADATA_HOST"] = "127.0.0.1:1"     # refused instantly, never resolves
    env["PATH"] = _NO_GCLOUD_PATH                # gcloud shadowed, not removed
    env.pop("http_proxy", None); env.pop("HTTP_PROXY", None)
    env["no_proxy"] = "127.0.0.1,localhost"; env["NO_PROXY"] = env["no_proxy"]
    return env


def run(argv, stdin=None, status=200, body=None, env_extra=None, expect_call=True):
    """Run the CLI against the mock; return (proc, captured_request_or_None)."""
    CAPTURED.clear()
    RESPONSE["status"] = status
    RESPONSE["body"] = {"ok": True} if body is None else body
    env = hermetic_env()
    env["GDA_ACCESS_TOKEN"] = "test-token-abc"   # the default; tests may clear it
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, CLI, "--host", HOST] + argv,
        input=stdin, capture_output=True, text=True, env=env, timeout=60)
    cap = CAPTURED[0] if CAPTURED else None
    if expect_call and cap is None:
        pass
    return proc, cap


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    icon = "PASS" if cond else "FAIL"
    print(f"[{icon}] {name}" + (f"\n        {detail}" if detail and not cond else ""))


def summary():
    n = len(results); ok = sum(1 for _, c, _ in results if c)
    print(f"\n{'='*60}\n{ok}/{n} checks passed")
    fails = [(nm, d) for nm, c, d in results if not c]
    if fails:
        print("\nFAILURES:")
        for nm, d in fails:
            print(f"  - {nm}: {d}")
    return 0 if not fails else 1
