#!/usr/bin/env python3
"""Suite 3: every command shown in SKILL.md / README.md must actually run."""
import re, sys, subprocess, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from harness import run, check, summary

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def commands(path):
    text = open(os.path.join(REPO, path)).read()
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
        ok = p.returncode == 0
        check(cmd[:96] + ("..." if len(cmd) > 96 else ""), ok,
              f"exit={p.returncode} stderr={p.stderr.strip()[:160]}")

sys.exit(summary())
