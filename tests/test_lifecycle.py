#!/usr/bin/env python3
"""Full lifecycle with ZERO flags, against tests/fake_api.py.

Credentials and project come from a simulated GCE metadata server, so this is
the exact zero-touch path a configured machine takes. Proves both the CLI
lifecycle and tests/live_e2e.py itself, without real credentials.
"""
import json, os, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_api  # noqa: E402


class Meta(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Metadata-Flavor") != "Google":
            self.send_response(403); self.end_headers(); return
        body = (json.dumps({"access_token": "metadata-token", "expires_in": 3599})
                if self.path.endswith("/token") else "auto-project")
        b = body.encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


meta = HTTPServer(("127.0.0.1", 0), Meta)
threading.Thread(target=meta.serve_forever, daemon=True).start()
_, api_url = fake_api.start()

# Reuse the harness scrub so an ambient $GDA_TIMEOUT, credential, or gcloud on
# the developer's machine cannot change what this suite tests.
from harness import hermetic_env  # noqa: E402

env = hermetic_env()
env["GCE_METADATA_HOST"] = f"127.0.0.1:{meta.server_address[1]}"
env["GDA_HOST"] = api_url

print("=== doctor (no flags) ===")
d = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "gda.py"),
                    "--host", api_url, "doctor"], capture_output=True, text=True, env=env)
print(d.stdout.strip() or d.stderr.strip())
doctor_ok = d.returncode == 0 and "Ready: no flags needed." in d.stdout

print("\n=== full lifecycle (no --project, no token flag) ===")
p = subprocess.run([sys.executable, os.path.join(HERE, "live_e2e.py"),
                    "--bq-table", "auto-project.sales.orders"],
                   capture_output=True, text=True, env=env)
print(p.stdout.strip())
if p.returncode != 0 and p.stderr.strip():
    print(p.stderr.strip()[:500])

print("\n=== tests/run_live.sh end to end (skips its own offline suites) ===")
slim = dict(env)
slim["GDA_SKIP_OFFLINE"] = "1"
r = subprocess.run(["bash", os.path.join(HERE, "run_live.sh"), "auto-project.sales.orders"],
                   capture_output=True, text=True, env=slim)
runner_ok = r.returncode == 0 and "13/13" in r.stdout
if runner_ok:
    print("\n".join([l for l in r.stdout.splitlines() if l.strip()][-3:]))
else:
    # Show everything on failure: a truncated tail hides the actual cause.
    print(f"run_live.sh exited {r.returncode}")
    print("--- stdout ---\n" + (r.stdout.strip() or "(empty)"))
    print("--- stderr ---\n" + (r.stderr.strip() or "(empty)"))

ok = p.returncode == 0 and doctor_ok and runner_ok
print(f"\n{'='*60}\nzero-flag lifecycle: {'PASS' if ok else 'FAIL'}"
      f"  (doctor={'ok' if doctor_ok else 'FAIL'},"
      f" run_live.sh={'ok' if runner_ok else 'FAIL'})")
sys.exit(0 if ok else 1)
