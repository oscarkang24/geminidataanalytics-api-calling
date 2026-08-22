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
SCRIPTS += [os.path.join(REPO, f) for f in sorted(os.listdir(REPO)) if f.endswith(".sh")]

check("there are shell scripts to check", bool(SCRIPTS), str(SCRIPTS))

for path in SCRIPTS:
    rel = os.path.relpath(path, REPO)
    src = open(path, encoding="utf-8").read()

    p = subprocess.run(["bash", "-n", path], capture_output=True, text=True,
                       timeout=60)
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

    # Linux CI parses these with bash 5, which happily accepts bash-4 syntax,
    # and runs GNU coreutils and OpenSSL. Nothing here would fail on Linux, so
    # the only defence against a macOS-only regression is a deny-list.
    BASH4 = [
        (r"declare\s+-A", "declare -A (associative arrays are bash 4+)"),
        (r"\$\{[A-Za-z_][A-Za-z_0-9]*\^\^", "${v^^} case modification is bash 4+"),
        (r"\$\{[A-Za-z_][A-Za-z_0-9]*,,", "${v,,} case modification is bash 4+"),
        (r"\bmapfile\b", "mapfile is bash 4+"),
        (r"\breadarray\b", "readarray is bash 4+"),
        (r"&>>", "&>> append-redirect is bash 4+"),
        (r";;&", ";;& in case is bash 4+"),
        (r"\blocal\s+-n\b", "local -n namerefs are bash 4.3+"),
        (r"\bwait\s+-n\b", "wait -n is bash 4.3+"),
        (r"globstar", "shopt globstar is bash 4+"),
        (r"\bcoproc\b", "coproc is bash 4+"),
        (r"printf\s+['\"]?%\(", "printf %(fmt)T is bash 4.2+"),
    ]
    GNU_ONLY = [
        (r"\bsed\s+-i\s+(?!['\"]{2})", "BSD sed -i requires an explicit suffix arg"),
        (r"\bgrep\s+-[A-Za-z]*P", "grep -P is GNU-only"),
        (r"\breadlink\s+-f", "readlink -f is GNU-only"),
        (r"\bbase64\s+-w", "base64 -w is GNU-only"),
        (r"\bxargs\s+-r", "xargs -r is GNU-only"),
        (r"--parents", "cp --parents is GNU-only"),
        (r"\bstat\s+-c", "stat -c is GNU-only (BSD uses -f)"),
        (r"\bdate\s+-d\b", "date -d is GNU-only"),
        (r"\btimeout\s+[0-9]", "timeout(1) is not installed on macOS by default"),
        (r"\brealpath\b", "realpath is not on stock macOS"),
        (r"-addext", "openssl -addext is OpenSSL 1.1.1+; LibreSSL lacks it"),
        (r"genpkey.*-pkeyopt", "genpkey -pkeyopt is unreliable on LibreSSL"),
        (r"\bopenssl\s+.*-noenc", "openssl -noenc is OpenSSL 3+; use -nodes"),
    ]
    for label, rules in (("bash 3.2", BASH4), ("BSD/LibreSSL userland", GNU_ONLY)):
        hits = []
        for pat, why in rules:
            for m in re.finditer(pat, code):
                hits.append(f"line {code[:m.start()].count(chr(10)) + 1}: {why}")
        check(f"{rel}: no constructs that break on {label}", not hits, "; ".join(hits))

    check(f"{rel}: optional vars are referenced with a default",
          not re.search(r'\$\{(GDA_HOST|GDA_SKIP_OFFLINE)\}', code),
          "use ${VAR:-} so `set -u` does not abort")

# The CLI and the tests shell out to openssl too; the deny-list has to reach
# those call sites, not just the .sh files.
for rel in ("scripts/gda.py", "tests/test_auth.py", "tests/test_tls.py"):
    raw_src = open(os.path.join(REPO, rel), encoding="utf-8").read()
    # Strip comments and docstring prose: naming a banned flag in an
    # explanation is not the same as calling it.
    src = "\n".join("" if l.lstrip().startswith("#") else l
                    for l in raw_src.splitlines())
    bad = []
    for pat, why in (("-addext", "openssl -addext is OpenSSL 1.1.1+; LibreSSL lacks it"),
                     ("-noenc", "openssl -noenc is OpenSSL 3+; use -nodes"),
                     ("rsa_keygen_bits", "genpkey -pkeyopt is unreliable on LibreSSL")):
        if pat in src:
            bad.append(why)
    check(f"{rel}: no openssl flags missing from LibreSSL", not bad, "; ".join(bad))

# Docs must not point at files install.sh deliberately leaves behind: a cold
# session following SKILL.md gets "No such file or directory" with no clue why.
INSTALLED = ("SKILL.md", "REFERENCE.md", "scripts/gda.py")
skill_md = open(os.path.join(REPO, "SKILL.md"), encoding="utf-8").read()
import re as _re
refs = set(_re.findall(r'`(?:bash|python3) ((?:tests|scripts)/[\w./-]+)', skill_md))
check("SKILL.md only references files that install.sh ships",
      refs <= set(INSTALLED), f"references not installed: {sorted(refs - set(INSTALLED))}")

installer = open(os.path.join(REPO, "install.sh"), encoding="utf-8").read()
for f in INSTALLED:
    check(f"install.sh ships {f}", f.split("/")[-1] in installer, "")

sys.exit(summary())
