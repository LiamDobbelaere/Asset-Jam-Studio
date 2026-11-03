# Handoff to the next iteration

Discarded so far: #1 triangle budget / segments (0.8712 vs 0.8708), #2 no invented structure (0.8663), #3 see-through
fronts as recesses (0.8604, tris_ratio_median up to 1.665). Baseline 0.8708, noise 0.0015, keep threshold +0.01.
Every iteration that added a rule was neutral or worse; do not add rules about budget, invented parts or recesses.

Next: backlog "Shorter brief". Edit `ai-3d/scripts/prop_brief.md` only: delete (do not reword) the two or three rules
that are most specific or least likely to matter (e.g. detailed per-feature rules such as the visible mechanical parts
paragraph, and any duplicated advice), keeping the triangle rules and the build/check workflow intact. Hypothesis: the
brief's extra detail pushes Claude to over-model (tris ratio 1.05 at baseline, up to 1.67 when a rule invited more
detail), so less text keeps the score at least equal and may raise it. An equal score with less text is not kept by
the runner but note it in NOTES.md for the person. If that fails too, take "Silhouette first", then "Proportions
check", then the prop_kit helper.
