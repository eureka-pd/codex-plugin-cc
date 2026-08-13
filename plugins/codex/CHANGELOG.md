# Changelog

## 1.0.7-eureka.1

- Added GPT-5.6 Sol, Terra, and Luna model/effort support based on the current Codex model catalog.
- Added `max` and `ultra` transport support for task and review flows.
- Refreshed stale shared brokers when the plugin or Codex CLI runtime changes while preserving active work and cancellation.
- Added model and effort selection to normal and adversarial review commands.
- Replaced the active GPT-5.4-specific rescue guidance with the version-neutral `codex-prompting` skill.
- Retained `gpt-5-4-prompting` as a deprecated compatibility alias.
- Identified this fork build separately from upstream plugin releases.

## 1.0.0

- Initial version of the Codex plugin for Claude Code
