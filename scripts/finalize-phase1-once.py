#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")


def replace_exact(path: str, old: str, new: str, count: int = 1) -> None:
    source = read(path)
    actual = source.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}: {old[:120]!r}")
    write(path, source.replace(old, new, count))


def normalize_shebang(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    stripped = source.lstrip("\r\n")
    if stripped.startswith("#!") and source != stripped:
        path.write_text(stripped, encoding="utf-8")


# Generated raw strings intentionally start on the following line. A shebang,
# however, must be the first bytes in the file for Node to parse it.
for candidate in (ROOT / "plugins/codex/scripts/orchestration").glob("*.mjs"):
    normalize_shebang(candidate)

# Keep the legacy command contract while adding the new command and dispatcher.
commands_test = read("tests/commands.test.mjs")
commands_test = commands_test.replace(
    '    "cancel.md",\n    "rescue.md",',
    '    "cancel.md",\n    "orchestrate.md",\n    "rescue.md",',
    1,
)
commands_test = commands_test.replace(
    '  const cancel = read("commands/cancel.md");\n  const resultHandling = read("skills/codex-result-handling/SKILL.md");',
    '  const cancel = read("commands/cancel.md");\n  const dispatcher = read("scripts/orchestration/dispatch.mjs");\n  const resultHandling = read("skills/codex-result-handling/SKILL.md");',
    1,
)
commands_test = commands_test.replace(
    '  assert.match(result, /codex-companion\\.mjs" result "\\$ARGUMENTS"/);',
    '  assert.match(result, /orchestration\\/dispatch\\.mjs" result "\\$ARGUMENTS"/);',
    1,
)
commands_test = commands_test.replace(
    '  assert.match(cancel, /codex-companion\\.mjs" cancel "\\$ARGUMENTS"/);',
    '  assert.match(cancel, /orchestration\\/dispatch\\.mjs" cancel "\\$ARGUMENTS"/);\n  assert.match(dispatcher, /codex-companion\\.mjs/);\n  assert.match(dispatcher, /orchestration.*cli\\.mjs/);',
    1,
)
commands_test = commands_test.replace(
    "  assert.match(setup, /argument-hint:\\s*'\\[--enable-review-gate\\|--disable-review-gate\\]'/);",
    "  assert.match(setup, /argument-hint:\\s*'\\[--enable-review-gate\\|--disable-review-gate\\] \\[--enable-orchestration\\|--disable-orchestration\\]'/);",
    1,
)
commands_test = commands_test.replace(
    '  assert.match(setup, /codex-companion\\.mjs" setup --json \\$ARGUMENTS/);',
    '  assert.match(setup, /orchestration\\/setup-dispatch\\.mjs" --json \\$ARGUMENTS/);\n  assert.match(read("scripts/orchestration/setup-dispatch.mjs"), /codex-companion\\.mjs.*setup/);',
    1,
)
write("tests/commands.test.mjs", commands_test)

# Remove transfer-only compatibility comments; the dispatcher tests now verify
# the actual routing instead of matching dead command text in comments.
for relative in [
    "plugins/codex/commands/status.md",
    "plugins/codex/commands/result.md",
    "plugins/codex/commands/cancel.md",
    "plugins/codex/commands/setup.md",
]:
    source = read(relative)
    source = "\n".join(
        line for line in source.splitlines() if "Compatibility reference for the legacy" not in line
    )
    write(relative, source)

# Make prebuild platform-neutral so the promised Windows CI can execute.
package_path = ROOT / "package.json"
package = json.loads(package_path.read_text(encoding="utf-8"))
package["scripts"]["prebuild"] = (
    "node scripts/prepare-generated-dir.mjs && "
    "codex app-server generate-ts --out plugins/codex/.generated/app-server-types"
)
package_path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
write(
    "scripts/prepare-generated-dir.mjs",
    '''import fs from "node:fs";\n\nfs.mkdirSync(new URL("../plugins/codex/.generated/app-server-types", import.meta.url), { recursive: true });''',
)

# Type-check the new deterministic runtime along with the existing App Server
# client surface.
tsconfig_path = ROOT / "tsconfig.app-server.json"
tsconfig = json.loads(tsconfig_path.read_text(encoding="utf-8"))
include = tsconfig.setdefault("include", [])
entry = "plugins/codex/scripts/orchestration/**/*.mjs"
if entry not in include:
    include.insert(-1 if include else 0, entry)
tsconfig_path.write_text(json.dumps(tsconfig, indent=2) + "\n", encoding="utf-8")

# Document the actual Phase 1 command and safety boundary without replacing the
# existing command reference sections.
readme_path = ROOT / "README.md"
readme = readme_path.read_text(encoding="utf-8")
section = '''\n## Claude-native Multi-Codex orchestration\n\nPhase 1 adds a read-only orchestration layer managed by the Claude root agent. It can run independent Codex Roots concurrently through separate App Server workers, persist their package results, and expose orchestration-aware status, result, and cancellation commands.\n\n```text\n/codex:orchestrate <repository task>\n/codex:status <orchestration-id|package-id>\n/codex:result <orchestration-id|package-id>\n/codex:cancel <orchestration-id|package-id>\n```\n\nAutomatic entry is disabled by default:\n\n```text\n/codex:setup --enable-orchestration\n/codex:setup --disable-orchestration\n```\n\nThe default workspace pool size is 3 and may be configured from 1 to 8. The plugin-wide top-level Root limit is 8 and the active Codex limit, including native children, is 12. Phase 1 is strictly read-only: writer worktrees, snapshot refs, integration branches, automatic commits, and Git integration are Phase 2 work.\n'''
if "## Claude-native Multi-Codex orchestration" not in readme:
    readme = readme.rstrip() + "\n" + section
readme_path.write_text(readme.rstrip() + "\n", encoding="utf-8")

# Pull-request CI validates all three supported desktop/server platforms. Real
# authenticated Codex smoke tests remain in the release workflow.
write(
    ".github/workflows/pull-request-ci.yml",
    '''name: Pull Request CI\n\non:\n  pull_request:\n\npermissions:\n  contents: read\n\njobs:\n  ci:\n    name: CI (${{ matrix.os }})\n    strategy:\n      fail-fast: false\n      matrix:\n        os: [ubuntu-latest, macos-latest, windows-latest]\n    runs-on: ${{ matrix.os }}\n    timeout-minutes: 20\n\n    steps:\n      - name: Check out repository\n        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2\n\n      - name: Set up Node.js\n        uses: actions/setup-node@53b83947a5a98c8d113130e565377fae1a50d02f # v6.3.0\n        with:\n          node-version: 22\n          cache: npm\n\n      - name: Install dependencies\n        run: npm ci\n\n      - name: Install Codex CLI\n        run: npm install -g @openai/codex\n\n      - name: Verify version metadata\n        run: npm run check-version\n\n      - name: Run test suite\n        run: npm test\n\n      - name: Run build\n        run: npm run build\n''',
)

print("Phase 1 final materialization repairs applied.")
