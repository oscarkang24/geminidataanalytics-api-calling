# Iteration 1 — prerequisite behaviour

Baseline is the skill immediately before the prerequisite gate was added
(commit 5da1df5). Both configurations ran in sessions with no prior context,
in an environment with no Google credentials.

| eval | config | checks | task commands | failed checks |
| --- | --- | --- | --- | --- |
| eval-0-missing-prereq-on-a-real-task | with_skill | 8/8 | 2 | — |
| eval-0-missing-prereq-on-a-real-task | old_skill | 5/8 | 7 | checks the prerequisite early (doctor within the first 2 commands); does not investigate the environment for credentials; stops quickly (5 commands or fewer) |
| eval-1-blind-its-not-working | with_skill | 8/8 | 2 | — |
| eval-1-blind-its-not-working | old_skill | 5/8 | 16 | checks the prerequisite early (doctor within the first 2 commands); does not investigate the environment for credentials; stops quickly (5 commands or fewer) |
| eval-2-read-only-listing | with_skill | 8/8 | 4 | — |
| eval-2-read-only-listing | old_skill | 5/8 | 9 | checks the prerequisite early (doctor within the first 2 commands); does not investigate the environment for credentials; stops quickly (5 commands or fewer) |

Median task commands — with gate: 2, baseline: 9.

Caveat: the installed skill's description is visible to every session,
including the baseline ones, so the baseline was partly helped by the new
wording. The gap is a lower bound. See EVAL.md.
