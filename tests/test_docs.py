#!/usr/bin/env python3
"""Every command shown in SKILL.md / README.md runs and issues a request.

Checks that the documented invocations parse, exit 0, and produce a real
request against /v1/projects/... — not merely that argparse accepted them.
"""
import re, sys, subprocess, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import run, check, summary

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def commands(path):
    text = open(os.path.join(REPO, path), encoding="utf-8").read()
    cmds = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        # join backslash continuations
        block = re.sub(r"\\\n\s*", " ", block)
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("python3 scripts/gda.py"):
                cmds.append(line)
    return cmds

import shlex
for doc in ("SKILL.md", "README.md"):
    print(f"\n--- {doc} ---")
    for cmd in commands(doc):
        argv = shlex.split(cmd, comments=True)[2:]  # drop "python3 scripts/gda.py"
        # substitute doc placeholders with concrete values
        argv = [{"P": "myproj", "PROJECT": "myproj"}.get(a, a) for a in argv]
        argv = [a.replace("P.dataset.orders", "myproj.dataset.orders")
                 .replace("PROJECT.dataset.orders", "myproj.dataset.orders")
                 .replace("{parent}", "{parent}") for a in argv]
        stdin = "{}" if "-" in argv and "--body" in argv else None
        # Examples that omit --project rely on project auto-detection, so give
        # the run a detectable project the way a configured machine would.
        p, c = run(argv, stdin=stdin, body=[],
                   env_extra={"GOOGLE_CLOUD_PROJECT": "doc-project"})
        # Exit 0 alone would stay green if a command sent the wrong method,
        # the wrong path, or no request at all — so assert a request happened
        # and that it addressed the documented API surface.
        doctor = argv and argv[0] == "doctor"
        ok = (p.returncode == 0 and "Traceback" not in p.stderr
              and c is not None
              and c["path"].startswith("/v1/projects/")
              and (doctor or c["method"] in ("GET", "POST", "PATCH", "DELETE")))
        check(cmd[:96] + ("..." if len(cmd) > 96 else ""), ok,
              f"exit={p.returncode} request={c and (c['method'], c['path'])} "
              f"stderr={p.stderr.strip()[:160]}")

sys.exit(summary())
