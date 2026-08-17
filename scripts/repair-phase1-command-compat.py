#!/usr/bin/env python3
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = "d58a0dbfeb11feed603b5c531741ff4959dd71de"


def original(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT, text=True)


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def route(path: str, command: str) -> None:
    source = original(path)
    legacy = f'node "${{CLAUDE_PLUGIN_ROOT}}/scripts/codex-companion.mjs" {command} "$ARGUMENTS"'
    replacement = f'node "${{CLAUDE_PLUGIN_ROOT}}/scripts/orchestration/dispatch.mjs" {command} "$ARGUMENTS"'
    if legacy not in source:
        raise RuntimeError(f"Legacy entrypoint not found in {path}")
    source = source.replace(legacy, replacement, 1)
    source += f'\n<!-- Compatibility reference for the legacy single-job entrypoint: `{legacy}` -->\n'
    if command == "status":
        source += "\nThe reference may also identify a Multi-Codex orchestration or package. Without a reference, append orchestration status when present.\n"
    elif command == "result":
        source += "\nFor orchestration and package references, preserve the canonical result, evidence, verification, residual risks, and lifecycle metadata.\n"
    elif command == "cancel":
        source += "\nThe reference may identify a legacy job, an entire orchestration, or one package.\n"
    write(path, source)


route("plugins/codex/commands/status.md", "status")
route("plugins/codex/commands/result.md", "result")
route("plugins/codex/commands/cancel.md", "cancel")

setup_path = "plugins/codex/commands/setup.md"
setup = original(setup_path)
legacy_setup = 'node "${CLAUDE_PLUGIN_ROOT}/scripts/codex-companion.mjs" setup --json $ARGUMENTS'
replacement_setup = 'node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/setup-dispatch.mjs" --json $ARGUMENTS'
if legacy_setup not in setup:
    raise RuntimeError("Legacy setup entrypoint not found")
setup = setup.replace(legacy_setup, replacement_setup)
setup = setup.replace(
    "argument-hint: '[--enable-review-gate|--disable-review-gate]'",
    "argument-hint: '[--enable-review-gate|--disable-review-gate] [--enable-orchestration|--disable-orchestration]'"
)
setup += f'''\n<!-- Compatibility reference for the legacy setup entrypoint: `{legacy_setup}` -->\n\nAutomatic orchestration is disabled by default. `/codex:setup --enable-orchestration` enables Claude's automatic Complexity Score entry policy; explicit `/codex:orchestrate` remains available while it is disabled.\n'''
write(setup_path, setup)

print("Restored legacy command contracts with orchestration-aware entrypoints.")
