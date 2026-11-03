# Handoff to the next iteration

Discarded so far: #1 triangle budget / segments (0.8712 vs 0.8708) and #2 no invented structure (0.8663 vs 0.8708).
Do not retry budget, segment or "don't add parts" rules. Baseline 0.8708, noise 0.0015, keep threshold +0.01.

Next: backlog "Silhouette first". Edit `ai-3d/scripts/prop_brief.md` only: make matching the outline from each of the
four views the first step and first check (before any detail), and say that a part which changes no silhouette is
dropped or made a decal. Prefer reorganising or replacing existing sentences over adding new ones, so the brief does
not grow. Keep triangle rules untouched. Look at `improve/runs/prop-model/002/work/sheet.png` first: Whiteboard
(0.70), VendingMachine (0.79) and ShelfSquare (0.80, 276 tris vs 88) are the weak ones; check whether their outline
differs from the original or only the inner detail does. If the outline already matches, "Silhouette first" is
unlikely to help: then take "Model the interior/volume the artist shows" instead.
