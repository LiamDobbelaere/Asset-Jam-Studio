# Notes for coding agents

- Installing or repairing the toolkit: follow [`SETUP.md`](SETUP.md) step by step; it lists every requirement and
  the expected harmless warnings. Ask the user before steps that need their accounts (Hugging Face login, Civitai token).
- What each module does and why its models and settings were chosen: the module READMEs (`ai-3d/`, `ai-voc/`,
  `ai-mus/`, `ai-sfx/`, `ai-vid/`, `ai-anim/`, `studio/`). Settings are tuned for an 8 GB RTX 2080; on a bigger GPU, adapt
  them only when the user asks.
- `todo/` holds research notes and open ideas, not instructions or pending setup steps.
- Generating assets through the running studio: `.claude/skills/asset-jam-studio/SKILL.md`.
- Private material stays out of git: `ai-voc/voices/` only tracks the `liam` example voice, `ai-vid/assets/` and
  `ai-vid/shots/` only track the example. Do not commit other voices, logos or shot lists.

## Memory

`memory/` is the shared memory for work on this project: decisions and why, rejected models, measured settings,
gotchas, dead ends and work in progress. Read `memory/MEMORY.md` (and `memory/private/MEMORY.md` if it exists) at the
start of a task, then open the files that look relevant. Memories reflect what was true when written; check a named
file, flag or setting still exists before relying on it.

Write to it whenever you learn something about this project that a later session would need and the code, READMEs
and git history don't already say: a user preference or correction, why an option was chosen or rejected, a measured
number, a trap. Use this folder instead of any per-user memory store for project facts.

- One fact per file, kebab-case name, with frontmatter: `name` (the slug), `description` (one line, used to judge
  relevance) and `metadata.type` (`user`, `feedback`, `project` or `reference`). For `feedback` and `project`, follow
  the fact with **Why:** and **How to apply:** lines. Link related memories with `[[name]]`. Write absolute dates.
- Add one line per memory to the index (`- [Title](file.md) — hook`); keep the index free of the facts themselves.
- Update an existing memory rather than adding a near-duplicate; delete one that turned out wrong.
- `memory/` is tracked and the repo is public, so the rules in `public-repo-rules.md` apply: anything that names the
  private game, its characters, actors, the studio logo or local `D:\...` paths goes in `memory/private/` (gitignored,
  indexed in `memory/private/MEMORY.md`).
