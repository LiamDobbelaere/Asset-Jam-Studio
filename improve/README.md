# improve/: the self-improvement loop

A loop in which Claude improves Asset Jam Studio one measured change at a time, remembers what it learned in
`memory/`, and hands itself the next step. A person reviews a branch, not a stream of edits.

```
python improve/loop.py prop-model --calibrate 2          # once: score the unchanged files twice, record the noise
python improve/loop.py prop-model --iterations 5         # then: five propose -> score -> keep/revert -> reflect rounds
git log --oneline main..improve/prop-model               # review: kept changes + notes, one commit each
```

Stop it any time with Ctrl-C or by creating `improve/STOP`; it also stops at `--iterations`, `--hours` and
`--budget-usd`. `--dry-run` prints the next iteration's prompt and the score command without spending anything.

## How one iteration works

```
  PROGRAM.md (rules, person-owned)      track.json (goal, metric, editable + read-only files, scorer)
  NOTES.md, backlog.md, next.md (the agent's playbook, queue and handoff)      memory/ (shared project memory)
                     |
   1. propose  ──  claude -p  reads all of the above, picks ONE hypothesis, edits only the editable files,
                                answers {title, hypothesis, change, expect}
   2. check    ──  runner     any change outside the editable list, or to a hashed read-only file -> reverted, not scored
   3. score    ──  runner     runs the track's scorer (a fixed harness Claude never runs itself) -> score.json
   4. decide   ──  runner     keep = metric beats the best by max(min_delta, measured noise) and every guard holds
                              keep -> git commit on improve/<track>     else -> git revert of the editable files
   5. reflect  ──  claude -p  (cheaper model) records the result in NOTES.md and backlog.md, writes next.md for the
                              next iteration, writes a memory file when something durable was learned -> committed
```

Each iteration leaves `improve/runs/<track>/NNN/` with `hypothesis.json`, `change.patch`, `score.json`, the scorer's
work and both Claude logs, and one line in `improve/tracks/<track>/results.tsv`.

## Why it is built this way

The design takes the parts of the published self-improving systems that survive contact with a small, noisy,
GPU-bound project:

- **One metric, one editable surface, keep-if-better, a results table and a person-owned rules file** are
  Karpathy's [autoresearch](https://github.com/karpathy/autoresearch) loop (an agent edits `train.py`, a fixed
  `prepare.py` scores it, `results.tsv` logs it, `program.md` is "the research org code"; reported 700 experiments
  in two days, 20 kept). Here the rules file is `PROGRAM.md`, the editable surface is per track.
- **The agent never produces its own score.** autoresearch lets the agent run the scorer from the file it edits,
  which is the obvious hole. Here the runner runs the scorer, hashes the harness and the read-only files before
  and after the proposal, and reverts anything that stepped outside its list. Changing which props are scored or
  reading the originals is forbidden in `PROGRAM.md` and, where it matters, impossible by construction (the scorer
  owns the prop list and the renders).
- **Noise floor before deltas.** The project's own eval work showed the style probe at the noise level with 12
  props and a run-to-run triangle spread of 1.3x vs 1.7x with the same brief (`memory/private/house-style-work.md`).
  A loop that keeps every positive delta on such a metric random-walks. `--calibrate N` scores the unchanged files
  N times and the spread becomes the minimum delta.
- **Reflection in natural language, kept outside the model** is Reflexion ([Shinn et al.](https://arxiv.org/abs/2303.11366)):
  the reflect step writes what the result rules out, not just the number. The `NOTES.md` playbook plus `memory/`
  is the Voyager-style skill library (a frozen model, growing written knowledge). The Darwin Gödel Machine
  ([Sakana](https://sakana.ai/dgm/)) adds the archive idea: discarded attempts stay in `results.tsv` and
  `runs/NNN/change.patch`, so a later iteration can revisit a near miss instead of re-trying it blind.
- **Self-prompting through the handoff.** Every iteration is a fresh session (no context growth, no drift); what
  carries over is `next.md`, written by the previous iteration for a reader with none of its context. The person
  can rewrite it to steer the loop without touching the rules.
- **Caps and a kill switch** (iterations, hours, dollars per run and per session, `improve/STOP`) follow the
  common advice for unattended Claude Code loops ("Ralph"-style runners, cron-driven agents): the loop should never
  be the thing that decides when it is done.
- **Nothing merges itself.** Kept changes and notes land on `improve/<track>`; a person reads
  `git log main..improve/<track>`, the results table and the contact sheets, and merges or cherry-picks. Memory
  files the loop writes are reviewed the same way, since they are commits on that branch.

## Tracks

A track is a folder `improve/tracks/<name>/`:

| file | owner | what |
|---|---|---|
| `track.json` | person | `goal`, `metric` (`key`, `direction`, `min_delta`, `guards` {key: [lo, hi]}, `also` keys to print), `editable` and `readonly` globs, `context` files to read first, `score` (`cmd` with `{py}` `{out}` `{workdir}` `{track_runs}` `{model}` and any string field of the track, `timeout`, `model`), `gpu`, optional `private` overlay |
| `private.json` | person, gitignored | fields merged over `track.json` that must not be published (a private style id, a path to a library built from your own props) |
| `NOTES.md` | agent | the playbook: what the metric rewards, harness problems, what was tried |
| `backlog.md` | agent (seeded by the person) | hypotheses, best first; tried ones marked, not deleted |
| `next.md` | agent | the handoff: what the next iteration should do and how |
| `results.tsv` | runner | one row per iteration: status (`keep`, `discard`, `crash`, `violation`, `noop`, `error`, `baseline`), score, delta, cost, commit, summary |

### prop-model (the first track)

Measures Claude's modelling of a prop when the picture is the artist's own render of that prop: the four clay
views of the generated model against the original's on CLIP ViT-H (`pair_clay`, higher is closer), with guards on
failures and on the triangle ratio. Editable: `ai-3d/scripts/prop_brief.md` (the modelling brief) and
`ai-3d/scripts/prop_kit.py` (the helpers). Needs a style with a reference library (`ai-3d/README.md`, "private
styles") named in `private.json`. One full score = 12 modelling sessions (about $3-5 with Opus, ~10 min), Blender
renders and a few seconds of GPU for CLIP; no ComfyUI server. `--props A,B` runs a two-prop smoke test with its own
state folder.

What it cannot see: the picture step and the paint, which the existing `ai-3d/scripts/style_eval.py` covers at
~$4 and an hour of GPU per run; that is a second track once this one has stopped moving.

### Adding a track

Write a scorer that takes `--out score.json --workdir <dir>` (and `--props` if subsets make sense), owns the
list of what it measures, and prints one summary line; put its inputs on the track's `readonly` list. Pick a
metric that a change can move in an hour of compute at most, calibrate it, and set `guards` for the ways the
metric can be won by losing (fewer triangles than the artist, failed props not counted). Candidates in the
project's own notes: voice takes scored by speaker similarity + Whisper word error rate (`todo/ai-voc-improvements.md`),
world-texture seam discontinuity vs interior, game-asset post-processing on cached seeds (`memory/game-asset-small-parts.md`).

## Guard rails, in one place

- Editable list enforced by the Claude Code permission rules of the session **and** by a git status check after it.
- Read-only list hashed before and after the proposal; a changed hash is a `violation` (reverted, never scored).
- The scorer is run by the runner, in ai-3d's venv, with a timeout; a crash is logged as `crash` and reverted.
- `min_delta` = max(track `min_delta`, calibrated spread); guards are hard.
- `--max-budget-usd` on every Claude session, `--budget-usd` per run, `--hours`, `--iterations`, `improve/STOP`.
- The runner refuses to start on a dirty tree (outside `improve/tracks` and `memory/`) and works on `improve/<track>`.
- The GPU is checked through the studio's `/api/health` before scoring: a running studio job is waited for.
- Memory writes follow `AGENTS.md`; the reflect session may only write under `improve/tracks/<track>/` and `memory/`.
