#!/usr/bin/env python3
"""TLS trust handling: certifi rescue, and no silent identity fallback.

Stands up a real HTTPS server with a private CA the system store does not
trust, so the retry path is exercised end to end rather than mocked.
"""
import importlib.util, json, os, ssl, subprocess, sys, tempfile, threading
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import run, check, summary  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "gda", os.path.join(HERE, "..", "scripts", "gda.py"))
gda = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gda)

TMP = tempfile.mkdtemp(prefix="gda-tls-")
key, crt = os.path.join(TMP, "k.pem"), os.path.join(TMP, "c.pem")

# Python requires a SAN, but `-addext` is OpenSSL 1.1.1+ and macOS ships
# LibreSSL, which lacks it. A config file with an extension section works on
# both.
cnf = os.path.join(TMP, "openssl.cnf")
with open(cnf, "w") as f:
    f.write("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
            "[dn]\nCN=localhost\n"
            "[ext]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=CA:FALSE\n")
gen = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                      "-keyout", key, "-out", crt, "-days", "1",
                      "-config", cnf, "-extensions", "ext"],
                     capture_output=True, text=True)
if gen.returncode != 0:
    print("SKIPPED: could not generate a test certificate with this openssl.\n"
          f"  {(gen.stderr or '').strip()[:200]}\n"
          "  These checks need openssl to emit a cert with a subjectAltName.")
    sys.exit(3)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps({"ok": True}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


srv = HTTPServer(("127.0.0.1", 0), H)
sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
sctx.load_cert_chain(crt, key)
srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"https://localhost:{srv.server_address[1]}/"

print("--- certifi rescue ---")
req = urllib.request.Request(URL)
failed = False
try:
    urllib.request.urlopen(req, timeout=10)
except urllib.error.URLError as e:
    failed = gda._is_cert_error(e)
check("baseline: the system trust store rejects this cert", failed,
      "expected a verification failure from the default context")

gda._certifi_context = lambda: ssl.create_default_context(cafile=crt)
gda._CERTIFI_NOTED = False
try:
    with gda._urlopen(urllib.request.Request(URL), timeout=10) as r:
        body = json.loads(r.read().decode())
    check("_urlopen retries with the certifi bundle and succeeds", body == {"ok": True}, str(body))
except Exception as e:
    check("_urlopen retries with the certifi bundle and succeeds", False, f"{type(e).__name__}: {e}")

gda._certifi_context = lambda: None
try:
    gda._urlopen(urllib.request.Request(URL), timeout=10)
    check("without certifi the cert error still propagates", False, "should have raised")
except urllib.error.URLError as e:
    check("without certifi the cert error still propagates", gda._is_cert_error(e), str(e))

try:
    gda._urlopen(urllib.request.Request("http://127.0.0.1:1/"), timeout=5)
    check("non-cert errors are not retried", False, "should have raised")
except urllib.error.HTTPError as e:
    check("non-cert errors are not retried", False, f"unexpected HTTPError {e}")
except urllib.error.URLError as e:
    check("non-cert errors are not retried", not gda._is_cert_error(e), str(e))

check("_is_cert_error ignores unrelated failures",
      not gda._is_cert_error(urllib.error.URLError("Name or service not known")), "")

print("\n--- TLS guidance survives credential discovery ---")
# The first network call the CLI makes is a token exchange. If that fails on
# trust, "could not find credentials" would send a user with perfectly good
# ADC off to re-run gcloud login, which cannot fix it.
class _Tok(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        b = json.dumps({"access_token": "should-never-be-reached"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


tok_srv = HTTPServer(("127.0.0.1", 0), _Tok)
_c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); _c.load_cert_chain(crt, key)
tok_srv.socket = _c.wrap_socket(tok_srv.socket, server_side=True)
threading.Thread(target=tok_srv.serve_forever, daemon=True).start()

adc_dir = os.path.join(TMP, "adc"); os.makedirs(adc_dir, exist_ok=True)
with open(os.path.join(adc_dir, "application_default_credentials.json"), "w") as f:
    json.dump({"type": "authorized_user", "client_id": "c", "client_secret": "s",
               "refresh_token": "r", "quota_project_id": "p",
               "token_uri": f"https://localhost:{tok_srv.server_address[1]}/token"}, f)

p, c = run(["--project", "p", "agents", "list"],
           env_extra={"GDA_ACCESS_TOKEN": "", "CLOUDSDK_CONFIG": adc_dir,
                      "SSL_CERT_FILE": ""})
check("a trust failure during token refresh shows the TLS guidance",
      "TLS trust problem in Python" in p.stderr, p.stderr[-300:])
check("...and does not claim credentials are missing",
      "could not find credentials" not in p.stderr, p.stderr[-300:])

print("\n--- no silent identity fallback ---")
bad = os.path.join(TMP, "not-json.json")
open(bad, "w").write("this is not json")
p, c = run(["--project", "p", "agents", "list"],
           env_extra={"GDA_ACCESS_TOKEN": "", "GOOGLE_APPLICATION_CREDENTIALS": bad})
check("unreadable GOOGLE_APPLICATION_CREDENTIALS fails, never falls through",
      p.returncode == 1 and c is None and "cannot be read as JSON" in p.stderr, p.stderr[:250])

sa = os.path.join(TMP, "sa.json")
json.dump({"type": "service_account", "client_email": "svc@x.iam.gserviceaccount.com",
           "private_key": "-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n",
           "project_id": "sa-project", "token_uri": "http://127.0.0.1:1/token"}, open(sa, "w"))
p, c = run(["--project", "p", "agents", "list"],
           env_extra={"GDA_ACCESS_TOKEN": "", "GOOGLE_APPLICATION_CREDENTIALS": sa})
check("a broken service-account key fails loudly, not onto another identity",
      p.returncode == 1 and c is None and "no token could be obtained" in p.stderr
      and "different identity" in p.stderr, p.stderr[:300])
check("the failure names the file so the cause is obvious", sa in p.stderr, p.stderr[:200])

p, c = run(["--project", "p", "agents", "list"], env_extra={"GDA_ACCESS_TOKEN": "tok",
                                                            "GOOGLE_APPLICATION_CREDENTIALS": bad})
check("an explicit token still wins over a broken key file",
      p.returncode == 0 and c and c["headers"]["authorization"] == "Bearer tok", p.stderr[:200])

sys.exit(summary())
