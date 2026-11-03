# Handoff to the next iteration

Iteration 1 (low triangle budget + fewer segments) was discarded: pair_clay 0.8712 vs 0.8708, so triangle count is
not the lever. Do not retry budget or segment rules.

Next: backlog "No invented structure". First look at the contact sheet of run 001
(`improve/runs/prop-model/001/work/sheet.png`) and see what the weak props (Whiteboard 0.71, WaterCooler 0.84,
VendingMachine 0.84, ShelfSquare 0.85) add or miss against the originals. Then edit `ai-3d/scripts/prop_brief.md`:
add one short rule that nothing absent from the picture is modelled (no easel, braces, signs, extra feet) and that
each part must be visible in at least one of the four views. Keep the change small; do not touch the triangle
rules. Expect Whiteboard to rise most; the mean should gain 0.01 only if invented structure is common.
If it also fails, try "Silhouette first" next.
