---
name: self-improvement-loop
description: "improve/: the propose -> score -> keep/revert -> reflect loop (2026-11-03), why the runner owns the metric and calibrates noise first, the prop-model track, what is still unverified"
metadata:
  type: project
---

Built 2026-11-03 at the user's request ("incrementally improve the studio by self-prompting, using the memory
folder"). `improve/loop.py` runs `claude -p` twice per iteration (propose with Opus, reflect with Sonnet) around a
scorer the runner runs itself; kept changes and the agent's notes land as commits on `improve/<track>`; nothing
merges itself. Design and sources in `improve/README.md`, the agent's rules in `improve/PROGRAM.md`.

**Why:** the published loops (Karpathy's autoresearch, DGM, Reflexion/Voyager) assume a cheap, low-noise metric
and let the agent score itself. Here every metric is expensive (minutes to hours, dollars of Claude), and the
style evals of 2026-11-02 showed the probe at the noise level with 12 props and a 1.3x-1.7x triangle spread across
runs of the same brief: a keep-any-positive-delta loop would random-walk. So the runner (not the agent) scores,
hashes the harness and read-only files, and `--calibrate N` sets the minimum delta from the measured spread.
Fresh sessions per iteration with `next.md` as the handoff keep context small and make the self-prompt reviewable.

**How to apply:** before a loop on any track, run `--calibrate 2` (or more) and look at the spread; a track whose
spread swallows every plausible gain needs a cheaper or less noisy metric, not more iterations. Review with
`git log main..improve/<track>`, `improve/tracks/<track>/results.tsv` and the `sheet.png` in `improve/runs/`.
The first track `prop-model` measures modelling only (picture = the artist's own render, metric = CLIP cosine of
clay views, `pair_clay`); the full chain stays with `style_eval.py`. Status 2026-11-03: dry-run and a two-prop
scorer smoke test done (Sonnet: box pair 0.899 at 96 vs 22 tris, cup 0.893 at 266 vs 160, size x0.6, $0.28, ~3 min);
calibration 2026-11-03 (Opus, two full scores, $7.47, ~6.5 min each): `pair_clay` 0.8715 / 0.8700, spread 0.0015,
so the 12-prop mean is far less noisy than the per-prop numbers (per-prop triangle counts vary up to 2.5x between
runs); keep threshold stays 0.01. First three iterations 2026-11-03 (Opus propose, Sonnet reflect, ~$4.1 and
~6.5 min each, mechanics all worked: edits confined to the brief, revert, notes commits): all three brief rule
additions were discarded (low tri budget +0.0004; "no invented structure" -0.0045; "see-through fronts as
recesses" -0.0104, which raised the triangle ratio to 1.67 on every prop). Lesson in `NOTES.md`: adding rules to
the brief does not move the 12-prop mean; the weak props (Whiteboard 0.71, VendingMachine 0.77-0.84) differ in
how the artist built them, not in a rule Claude breaks. Next the agent wants to test the opposite direction (a
shorter brief); if that is flat too, this track has plateaued and a picture-step track is the better spend. The
headless permission rules `Write(./path)` / `Edit(./path)` with `--permission-mode dontAsk` were verified to allow
exactly those files.
Related: [[prop-style-guides]], [[agent-modelling-exploration]].
