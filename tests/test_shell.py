#!/usr/bin/env python3
"""Shell-script portability checks.

macOS still ships bash 3.2 as /bin/bash. Under `set -u` that version aborts on
"${empty[@]}" with "unbound variable" — a failure bash >= 4.4 does not have and
which no amount of running the script on Linux will reveal. Bash 5 cannot
emulate it (BASH_COMPAT does not cover it), so this is a static check.
"""
import os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import check, summary  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = [os.path.join(REPO, "tests", f)
           for f in sorted(os.listdir(os.path.join(REPO, "tests"))) if f.endswith(".sh")]

check("there are shell scripts to check", bool(SCRIPTS), str(SCRIPTS))

for path in SCRIPTS:
    rel = os.path.relpath(path, REPO)
    src = open(path).read()

    p = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    check(f"{rel}: parses", p.returncode == 0, p.stderr.strip()[:200])

    nounset = re.search(r"^\s*set\s+-[a-z]*u", src, re.M) is not None
    if not nounset:
        continue

    # Blank out whole-line comments, keeping line numbers intact, so prose that
    # quotes the bad pattern is not mistaken for code.
    code = "\n".join("" if l.lstrip().startswith("#") else l
                     for l in src.splitlines())

    # Every "${name[@]}" / "${name[*]}" must carry the ${name[@]+...} guard.
    unguarded = []
    for m in re.finditer(r'\$\{([A-Za-z_][A-Za-z_0-9]*)\[([@*])\]\}', code):
        name, sub = m.group(1), m.group(2)
        # Guarded form looks like ${name[@]+"${name[@]}"} — the inner expansion
        # is preceded by '+' within an outer expansion of the same array.
        window = code[max(0, m.start() - len(name) - 8):m.start()]
        if not re.search(r'\$\{%s\[%s\]\+' % (re.escape(name), re.escape(sub)), window):
            line = code[:m.start()].count("\n") + 1
            unguarded.append(f"line {line}: {m.group(0)}")
    check(f"{rel}: no unguarded array expansion under `set -u` (bash 3.2)",
          not unguarded, "; ".join(unguarded))

    check(f"{rel}: optional vars are referenced with a default",
          not re.search(r'\$\{(GDA_HOST|GDA_SKIP_OFFLINE)\}', code),
          "use ${VAR:-} so `set -u` does not abort")

sys.exit(summary())
