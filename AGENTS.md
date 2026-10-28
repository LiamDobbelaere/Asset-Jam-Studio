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
