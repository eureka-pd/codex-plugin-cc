---
name: gpt-5-4-prompting
description: Deprecated compatibility alias for the version-neutral codex-prompting skill
user-invocable: false
---

# Deprecated compatibility alias

Use `codex-prompting` for all new `codex:codex-rescue` prompt shaping. This legacy skill name remains only so older cached agent definitions and third-party references do not fail immediately.

Do not add this skill to new agent configurations. Its former reference files remain for compatibility, but the active guidance is in `../codex-prompting/SKILL.md`.

Compatibility summary:

- Use `task` when the task is diagnosis, planning, research, or implementation and direct prompt control is needed.
- Use `task --resume-last` for a follow-up on the same persistent Codex thread.
- Keep prompts lean, outcome-focused, grounded, and explicit about completion and approval boundaries.
