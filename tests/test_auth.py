#!/usr/bin/env python3
"""Credential- and project-discovery suite.

Exercises every source in the chain against local fakes: a mock OAuth token
endpoint, a mock GCE metadata server (via the standard $GCE_METADATA_HOST),
a real RSA service-account key, and a stub `gcloud` on $PATH.

No real credentials and no network. Run:  python3 tests/test_auth.py
"""
import json, os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import run, check, summary, HOST  # noqa: E402

TMP = tempfile.mkdtemp(prefix="gda-auth-")
OAUTH_HITS = []
META_HITS = []


class Oauth(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        import urllib.parse
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        OAUTH_HITS.append(form)
        minted = {"refresh_token": "refreshed-token",
                  "urn:ietf:params:oauth:grant-type:jwt-bearer": "sa-jwt-token"}
        body = json.dumps({"access_token": minted.get(form.get("grant_type"), "unknown"),
                           "expires_in": 3599}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


class Meta(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        META_HITS.append((self.path, self.headers.get("Metadata-Flavor")))
        if self.headers.get("Metadata-Flavor") != "Google":
            self.send_response(403); self.end_headers(); return
        if self.path.endswith("/token"):
            body = json.dumps({"access_token": "metadata-token", "expires_in": 3599})
        elif self.path.endswith("project/project-id"):
            body = "metadata-project"
        else:
            self.send_response(404); self.end_headers(); return
        b = body.encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


oauth = HTTPServer(("127.0.0.1", 0), Oauth)
threading.Thread(target=oauth.serve_forever, daemon=True).start()
OAUTH_URI = f"http://127.0.0.1:{oauth.server_address[1]}/token"
meta = HTTPServer(("127.0.0.1", 0), Meta)
threading.Thread(target=meta.serve_forever, daemon=True).start()
META_HOST = f"127.0.0.1:{meta.server_address[1]}"

NOENV = {v: "" for v in ("GDA_ACCESS_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS",
                         "GDA_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT",
                         "CLOUDSDK_CORE_PROJECT", "GCE_METADATA_HOST")}


def env(**kw):
    e = dict(NOENV)
    e["CLOUDSDK_CONFIG"] = os.path.join(TMP, "empty-cfg")  # no ADC file by default
    e.update({k: v for k, v in kw.items() if v is not None})
    return e


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)
    return path


# --- fixtures -------------------------------------------------------------
adc_cfg = os.path.join(TMP, "gcloud-cfg")
write(os.path.join(adc_cfg, "application_default_credentials.json"), {
    "type": "authorized_user", "client_id": "cid.apps.googleusercontent.com",
    "client_secret": "csecret", "refresh_token": "rtoken",
    "quota_project_id": "adc-project", "token_uri": OAUTH_URI})

# `genrsa` works on OpenSSL and on the LibreSSL that macOS ships; `genpkey
# -pkeyopt` does not reliably.
_k = subprocess.run(["openssl", "genrsa", "2048"], capture_output=True, text=True)
if _k.returncode != 0 or "PRIVATE KEY" not in _k.stdout:
    print("SKIPPED: openssl could not generate an RSA key here.\n"
          f"  {(_k.stderr or '').strip()[:200]}")
    sys.exit(3)
key_pem = _k.stdout
sa_file = write(os.path.join(TMP, "sa.json"), {
    "type": "service_account", "client_email": "svc@sa-project.iam.gserviceaccount.com",
    "private_key": key_pem, "project_id": "sa-project", "token_uri": OAUTH_URI})

stub_bin = os.path.join(TMP, "bin")
os.makedirs(stub_bin, exist_ok=True)
gcloud = os.path.join(stub_bin, "gcloud")
with open(gcloud, "w") as f:
    f.write('#!/bin/sh\n'
            'case "$*" in\n'
            '  "auth application-default print-access-token") echo gcloud-adc-token ;;\n'
            '  "config get-value project") echo gcloud-project ;;\n'
            '  *) echo "unsupported: $*" >&2; exit 1 ;;\n'
            'esac\n')
os.chmod(gcloud, 0o755)
PATH_WITH_GCLOUD = stub_bin + os.pathsep + os.environ["PATH"]

# --- token chain ----------------------------------------------------------
print("--- Token discovery ---")
p, c = run(["--project", "p", "agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="env-tok"))
check("1. $GDA_ACCESS_TOKEN wins", c and c["headers"]["authorization"] == "Bearer env-tok",
      str(c and c["headers"].get("authorization")))

OAUTH_HITS.clear()
p, c = run(["--project", "p", "agents", "list"], env_extra=env(GOOGLE_APPLICATION_CREDENTIALS=sa_file))
check("2. service-account key -> signed JWT grant",
      c and c["headers"]["authorization"] == "Bearer sa-jwt-token",
      str(c and c["headers"].get("authorization")) + " " + (p.stderr or "")[:200])
check("2b. JWT grant type + assertion sent",
      OAUTH_HITS and OAUTH_HITS[0]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
      and OAUTH_HITS[0]["assertion"].count(".") == 2, str(OAUTH_HITS[:1])[:200])

if OAUTH_HITS:
    import base64
    hdr, claims, _ = OAUTH_HITS[0]["assertion"].split(".")
    pad = lambda x: x + "=" * (-len(x) % 4)
    h = json.loads(base64.urlsafe_b64decode(pad(hdr)))
    cl = json.loads(base64.urlsafe_b64decode(pad(claims)))
    check("2c. JWT is RS256 with correct iss/scope/aud",
          h == {"alg": "RS256", "typ": "JWT"} and cl["iss"].startswith("svc@")
          and cl["scope"] == "https://www.googleapis.com/auth/cloud-platform"
          and cl["aud"] == OAUTH_URI and cl["exp"] > cl["iat"], json.dumps([h, cl])[:300])

OAUTH_HITS.clear()
p, c = run(["--project", "p", "agents", "list"], env_extra=env(CLOUDSDK_CONFIG=adc_cfg))
check("3. gcloud ADC file -> refresh_token grant",
      c and c["headers"]["authorization"] == "Bearer refreshed-token",
      str(c and c["headers"].get("authorization")) + " " + (p.stderr or "")[:200])
check("3b. refresh grant sends client_id/secret/refresh_token",
      OAUTH_HITS and OAUTH_HITS[0]["grant_type"] == "refresh_token"
      and OAUTH_HITS[0]["refresh_token"] == "rtoken", str(OAUTH_HITS[:1])[:200])

META_HITS.clear()
p, c = run(["--project", "p", "agents", "list"], env_extra=env(GCE_METADATA_HOST=META_HOST))
check("4. metadata server", c and c["headers"]["authorization"] == "Bearer metadata-token",
      str(c and c["headers"].get("authorization")) + " " + (p.stderr or "")[:200])
check("4b. metadata request sends Metadata-Flavor: Google",
      META_HITS and META_HITS[0][1] == "Google", str(META_HITS[:1]))

p, c = run(["--project", "p", "agents", "list"], env_extra=dict(env(), PATH=PATH_WITH_GCLOUD))
check("5. gcloud CLI fallback", c and c["headers"]["authorization"] == "Bearer gcloud-adc-token",
      str(c and c["headers"].get("authorization")) + " " + (p.stderr or "")[:200])

print("\n--- Token precedence ---")
p, c = run(["--project", "p", "agents", "list"],
           env_extra=dict(env(GOOGLE_APPLICATION_CREDENTIALS=sa_file, CLOUDSDK_CONFIG=adc_cfg,
                              GCE_METADATA_HOST=META_HOST), PATH=PATH_WITH_GCLOUD))
check("service account beats ADC/metadata/gcloud",
      c and c["headers"]["authorization"] == "Bearer sa-jwt-token", str(c and c["headers"].get("authorization")))
p, c = run(["--project", "p", "agents", "list"],
           env_extra=dict(env(CLOUDSDK_CONFIG=adc_cfg, GCE_METADATA_HOST=META_HOST), PATH=PATH_WITH_GCLOUD))
check("ADC file beats metadata/gcloud",
      c and c["headers"]["authorization"] == "Bearer refreshed-token", str(c and c["headers"].get("authorization")))
p, c = run(["--project", "p", "--access-token", "flag", "agents", "list"],
           env_extra=env(GDA_ACCESS_TOKEN="env-tok"))
check("--access-token beats everything", c and c["headers"]["authorization"] == "Bearer flag",
      str(c and c["headers"].get("authorization")))

print("\n--- Project discovery ---")
p, c = run(["agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t", GOOGLE_CLOUD_PROJECT="env-project"))
check("1. $GOOGLE_CLOUD_PROJECT", c and "projects/env-project/" in c["path"], str(c and c["path"]))
check("1b. project also sent in x-goog-user-project",
      c and c["headers"]["x-goog-user-project"] == "env-project", str(c and c["headers"].get("x-goog-user-project")))
p, c = run(["agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t", GDA_PROJECT="gda-project"))
check("1c. $GDA_PROJECT honoured", c and "projects/gda-project/" in c["path"], str(c and c["path"]))
p, c = run(["agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t", GOOGLE_APPLICATION_CREDENTIALS=sa_file))
check("2. service-account project_id", c and "projects/sa-project/" in c["path"], str(c and c["path"]))
p, c = run(["agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t", CLOUDSDK_CONFIG=adc_cfg))
check("3. ADC quota_project_id", c and "projects/adc-project/" in c["path"], str(c and c["path"]))
p, c = run(["agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t", GCE_METADATA_HOST=META_HOST))
check("4. metadata project-id", c and "projects/metadata-project/" in c["path"], str(c and c["path"]))
p, c = run(["agents", "list"], env_extra=dict(env(GDA_ACCESS_TOKEN="t"), PATH=PATH_WITH_GCLOUD))
check("5. gcloud config get-value project", c and "projects/gcloud-project/" in c["path"], str(c and c["path"]))
p, c = run(["--project", "flag-project", "agents", "list"],
           env_extra=env(GDA_ACCESS_TOKEN="t", GOOGLE_CLOUD_PROJECT="env-project"))
check("--project beats every detected source", c and "projects/flag-project/" in c["path"], str(c and c["path"]))

print("\n--- Failure modes ---")
p, c = run(["--project", "p", "agents", "list"], env_extra=env())
check("no credentials anywhere -> exit 1, no request", p.returncode == 1 and c is None, p.stderr[:200])
check("error lists every source tried",
      all(x in p.stderr for x in ("GOOGLE_APPLICATION_CREDENTIALS", "ADC file", "metadata server", "gcloud")),
      p.stderr[:400])
p, c = run(["agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t"))
check("no project anywhere -> exit 1, actionable message",
      p.returncode == 1 and c is None and "--project" in p.stderr and "gcloud config set project" in p.stderr,
      p.stderr[:300])
p, c = run(["--project", "p", "agents", "list"],
           env_extra=env(GOOGLE_APPLICATION_CREDENTIALS=os.path.join(TMP, "nope.json")))
check("unreadable key file -> hard fail, never another identity",
      p.returncode == 1 and "cannot be read as JSON" in p.stderr
      and "falling back to" in p.stderr, p.stderr[:250])
bad = write(os.path.join(TMP, "bad.json"), {"type": "external_account"})
p, c = run(["--project", "p", "agents", "list"], env_extra=env(GOOGLE_APPLICATION_CREDENTIALS=bad))
check("unsupported credential type reported clearly",
      p.returncode == 1 and "external_account" in p.stderr, p.stderr[:250])

print("\n--- Validation still precedes credential discovery ---")
p, c = run(["agents", "update", "--agent-id", "a"], env_extra=env())
check("bad flags beat both auth and project errors",
      "nothing to update" in p.stderr and "credentials" not in p.stderr and "project" not in p.stderr,
      p.stderr[:250])

print("\n--- -v reports the sources ---")
p, c = run(["-v", "agents", "list"], env_extra=env(GDA_ACCESS_TOKEN="t", GOOGLE_CLOUD_PROJECT="env-project"))
check("-v names the auth source", "# auth: $GDA_ACCESS_TOKEN" in p.stderr, p.stderr[:250])
check("-v names the project source", "# project: env-project (from $GOOGLE_CLOUD_PROJECT)" in p.stderr, p.stderr[:250])

sys.exit(summary())
