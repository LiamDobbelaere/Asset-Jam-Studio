---
name: public-repo-rules
description: "Repo is public as Asset Jam Studio: no private game names, voices, logos or local paths in tracked files; GPL-3.0; how screenshots were made"
metadata:
  type: project
---

On 2026-10-28 the repo got a fresh single-commit history for a public GitHub repo. Public name is **Asset Jam Studio**
(chosen 2026-10-28 after GenAI Studio turned out taken; the user wanted a gamedev-specific name with "Studio").

Private files stay on disk but are gitignored: `ai-voc/voices/*` except `voices/liam/` (the user's own example voice),
`ai-vid/assets/*` and `ai-vid/shots/*` except `example_intro.json`, and `memory/private/`.

**Why:** the toolkit was built for a private game that must not be named or shipped publicly; same for the actors'
voices and the studio logo.

**How to apply:** never write the game's name, its characters, the studio name or `D:\...` paths into tracked files
(the list is in [[public-repo-prep]], under `memory/private/`); use `liam`/`sam`/`Hero`/`assets/logo.png` style
placeholders. Anything that needs those names goes in `memory/private/`.

Licence is GPL-3.0 (ComfyUI patch + custom nodes); README has a not-legal-advice disclaimer. Setup lives in SETUP.md
(measured sizes), AGENTS.md (+ CLAUDE.md importing it) for agents, research notes in todo/. Commits use the GitHub
noreply email (repo-local git config).

README screenshots live in docs/screenshots/ (taken 2026-10-28 with Playwright + installed Edge at 1440x900; the
script drives Overview job titles then "Reuse settings"), with demo assets generated in a "cozy tavern" theme.
