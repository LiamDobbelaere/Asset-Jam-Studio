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
a full calibration and real iterations not yet run, so the noise of `pair_clay` is unknown. The headless permission
rules `Write(./path)` / `Edit(./path)` with `--permission-mode dontAsk` were verified to allow exactly those files.
Related: [[prop-style-guides]], [[agent-modelling-exploration]].
