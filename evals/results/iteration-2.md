# Iteration 2 — uncontaminated baseline

The installed skill was **deleted** from `~/.claude/skills/` before these
baseline runs and reinstalled only after all three finished, so no baseline
session could see the new description. Iteration 1's baseline could, and is
shown alongside to make the size of that contamination visible.

| eval | with gate | baseline (contaminated) | baseline (clean) |
| --- | --- | --- | --- |
| eval-0-missing-prereq-on-a-real-task | 8/8 — 2 cmds | 6/9 — 7 cmds | 6/9 — 12 cmds |
| eval-1-blind-its-not-working | 8/8 — 2 cmds | 6/9 — 16 cmds | 6/9 — 15 cmds |
| eval-2-read-only-listing | 8/8 — 4 cmds | 6/9 — 9 cmds | 6/9 — 15 cmds |

Median task commands — **with gate 2**, baseline contaminated 9, baseline clean 15.

Removing the contamination made the baseline *worse*, which is the direction
that confirms it was contamination: those sessions had been picking up the
new skill's wording from its description and behaving better than the old
skill alone would. The clean numbers are the honest effect size.

Every clean baseline failed the same three checks: it did not check the
prerequisite first, it investigated the environment for credentials, and it
did not stop within five commands. All three swept the filesystem for key
files; two also read the proxy documentation. None of that could have found
anything — `doctor` had already reported every source it checked and why each
one missed.
