#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")

def replace(path: str, old: str, new: str, count: int = 1) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    actual = source.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}: {old[:100]!r}")
    target.write_text(source.replace(old, new), encoding="utf-8")

# Make the worker-pool slot limiter race-free.
replace(
    "plugins/codex/scripts/orchestration/worker-pool.mjs",
    "    this.onEvent = options.onEvent ?? (() => {});\n    this.active = new Map(); this.waiters = [];\n  }\n  async waitForSlot() {\n    if (this.active.size < this.size) return;\n    await new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));\n  }\n  releaseSlot() { this.waiters.shift()?.resolve(); }",
    "    this.onEvent = options.onEvent ?? (() => {});\n    this.active = new Map(); this.waiters = []; this.availableSlots = this.size;\n  }\n  async waitForSlot() {\n    if (this.availableSlots > 0) { this.availableSlots -= 1; return; }\n    await new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));\n  }\n  releaseSlot() {\n    const waiter = this.waiters.shift();\n    if (waiter) waiter.resolve(); else this.availableSlots = Math.min(this.size, this.availableSlots + 1);\n  }"
)

write("plugins/codex/scripts/orchestration/cli.mjs", r'''
#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";

import { parseArgs } from "../lib/args.mjs";
import { resolveWorkspaceRoot } from "../lib/workspace.mjs";
import { getProjectConfigPath, getUserConfigPath, loadOrchestrationConfig } from "./config.mjs";
import { OrchestrationControllerClient } from "./controller-client.mjs";
import { ensureControllerServer } from "./controller-lifecycle.mjs";
import { buildOrchestrationResult } from "./result-contract.mjs";
import { isTerminalState, listOrchestrations, loadOrchestrationState, resolveOrchestrationReference } from "./state-store.mjs";

function output(value, json) { process.stdout.write(json ? `${JSON.stringify(value, null, 2)}\n` : render(value)); }
function render(value) {
  if (value?.orchestrationId && value?.packageCount != null) return `Multi-Codex orchestration ${value.orchestrationId} accepted.\nStatus: ${value.status}\nPackages: ${value.packageCount}\nStatus: /codex:status ${value.orchestrationId}\nResult: /codex:result ${value.orchestrationId}\nCancel: /codex:cancel ${value.orchestrationId}\n`;
  return `${JSON.stringify(value, null, 2)}\n`;
}
function readPiped() { if (process.stdin.isTTY) return ""; return fs.readFileSync(0, "utf8"); }
function durableStatus(cwd, reference) {
  if (!reference) return { workspaceRoot: cwd, orchestrations: listOrchestrations(cwd) };
  const resolved = resolveOrchestrationReference(cwd, reference); const state = loadOrchestrationState(cwd, resolved.orchestrationId);
  return resolved.kind === "package" ? { orchestrationId: state.id, packageSpec: state.plan.packages.find((pkg) => pkg.id === resolved.packageId), package: state.packages[resolved.packageId] } : state;
}
function durableResult(cwd, reference) {
  const resolved = resolveOrchestrationReference(cwd, reference); const state = loadOrchestrationState(cwd, resolved.orchestrationId);
  if (resolved.kind === "package") return state.packages[resolved.packageId].result;
  if (!isTerminalState(state)) throw new Error(`Orchestration ${state.id} is still running. Use /codex:status ${state.id}.`);
  return buildOrchestrationResult(state);
}
async function main() {
  const [command, ...argv] = process.argv.slice(2);
  const { options, positionals } = parseArgs(argv, { valueOptions: ["cwd", "plan-file"], booleanOptions: ["json"] });
  const cwd = resolveWorkspaceRoot(options.cwd ? path.resolve(options.cwd) : process.cwd());
  if (command === "config") {
    const effectiveConfig = loadOrchestrationConfig(cwd);
    output({ workspaceRoot: cwd, userConfigPath: getUserConfigPath(), projectConfigPath: getProjectConfigPath(cwd), effectiveConfig, autoEnabled: effectiveConfig.auto.enabled, autoThreshold: effectiveConfig.auto.threshold }, options.json); return;
  }
  if (command === "start") {
    const planText = options["plan-file"] ? fs.readFileSync(path.resolve(options["plan-file"]), "utf8") : readPiped();
    if (!planText.trim()) throw new Error("start requires --plan-file or piped plan JSON.");
    const session = await ensureControllerServer(cwd); const client = new OrchestrationControllerClient(session.endpoint);
    const summary = await client.start(JSON.parse(planText), { claudeSessionId: process.env.CODEX_COMPANION_SESSION_ID ?? null });
    output({ ...summary, commands: { status: `/codex:status ${summary.orchestrationId}`, result: `/codex:result ${summary.orchestrationId}`, cancel: `/codex:cancel ${summary.orchestrationId}` } }, options.json); return;
  }
  const reference = positionals[0] ?? "";
  if (command === "status") { output(durableStatus(cwd, reference), options.json); return; }
  if (command === "result") { if (!reference) throw new Error("result requires an orchestration or package reference."); output(durableResult(cwd, reference), options.json); return; }
  if (command === "cancel") {
    if (!reference) throw new Error("cancel requires an orchestration or package reference.");
    const state = durableStatus(cwd, reference); const orchestrationState = state.package ? loadOrchestrationState(cwd, state.orchestrationId) : state;
    if (isTerminalState(orchestrationState)) { output(orchestrationState, options.json); return; }
    const session = await ensureControllerServer(cwd); const client = new OrchestrationControllerClient(session.endpoint); output(await client.cancel(reference), options.json); return;
  }
  throw new Error("Usage: cli.mjs <config|start|status|result|cancel> ...");
}
main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
''')

write("plugins/codex/scripts/orchestration/dispatch.mjs", r'''
#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { resolveWorkspaceRoot } from "../lib/workspace.mjs";
import { listOrchestrations, resolveOrchestrationReference } from "./state-store.mjs";

const ROOT = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
function run(script, args) { const result = spawnSync(process.execPath, [script, ...args], { cwd: process.cwd(), env: process.env, encoding: "utf8" }); process.stdout.write(result.stdout ?? ""); process.stderr.write(result.stderr ?? ""); process.exitCode = result.status ?? 1; }
function isOrchestrationRef(reference) { if (!reference) return false; try { resolveOrchestrationReference(resolveWorkspaceRoot(process.cwd()), reference); return true; } catch { return false; } }
const [command, ...args] = process.argv.slice(2); const reference = args.find((arg) => !arg.startsWith("--")) ?? "";
const orchestrationCli = path.join(ROOT, "orchestration", "cli.mjs"); const companion = path.join(ROOT, "codex-companion.mjs");
if (command === "status" && !reference) {
  run(companion, ["status", ...args]);
  if (listOrchestrations(resolveWorkspaceRoot(process.cwd())).length) run(orchestrationCli, ["status", ...args]);
} else if (isOrchestrationRef(reference)) run(orchestrationCli, [command, ...args]);
else run(companion, [command, ...args]);
''')

write("plugins/codex/scripts/orchestration/setup-dispatch.mjs", r'''
#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { patchUserOrchestrationConfig } from "./config.mjs";
const ROOT = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const args = process.argv.slice(2); const enable = args.includes("--enable-orchestration"); const disable = args.includes("--disable-orchestration");
if (enable && disable) throw new Error("Choose either --enable-orchestration or --disable-orchestration.");
if (enable || disable) patchUserOrchestrationConfig({ auto: { enabled: enable } });
const forwarded = args.filter((arg) => !["--enable-orchestration", "--disable-orchestration"].includes(arg));
const result = spawnSync(process.execPath, [path.join(ROOT, "codex-companion.mjs"), "setup", ...forwarded], { cwd: process.cwd(), env: process.env, encoding: "utf8" });
process.stdout.write(result.stdout ?? ""); process.stderr.write(result.stderr ?? "");
if (enable || disable) process.stdout.write(`Orchestration auto-entry: ${enable ? "enabled" : "disabled"}.\n`);
process.exitCode = result.status ?? 1;
''')

write("plugins/codex/commands/orchestrate.md", r'''
---
description: Plan and start a Claude-managed read-only Multi-Codex orchestration
argument-hint: '<repository task>'
allowed-tools: Read, Glob, Grep, Write, Bash(node:*), Bash(git:*)
---

Use the `codex-orchestration` skill as the binding policy.

Inspect only enough repository context to identify genuinely independent read-only work packages, their dependencies, models, efforts, and acceptance criteria. Phase 1 rejects writer packages.

Create canonical plan JSON in a collision-safe temporary file. Before execution, show a compressed 3–6 line plan including package roles, model/effort, parallelism, budget, and the fact that no external actions are authorized. Do not wait for approval for this local read-only run.

Start it with:

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/cli.mjs" start --cwd "$PWD" --plan-file "<absolute-plan-path>"
```

Delete the temporary plan file after the command returns. Report only that the orchestration was accepted or started; never claim queued work has completed. Preserve the orchestration ID and status/result/cancel commands verbatim.

User objective:
$ARGUMENTS
''')

write("plugins/codex/skills/codex-orchestration/SKILL.md", r'''
---
name: codex-orchestration
description: Use for an explicit Multi-Codex request or, when automatic orchestration is enabled, for repository work with multiple genuinely independent packages that meets the Complexity Score threshold
user-invocable: false
---

# Claude-native Multi-Codex orchestration

Claude Root owns decomposition, the top-level DAG, model/effort routing, and final interpretation. Do not delegate those decisions to a Codex lead. Each top-level Codex Root receives one bounded package; native children remain owned by that Root.

Before automatic entry, run `node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/cli.mjs" config --cwd "$PWD" --json`. Do not auto-start when `autoEnabled` is false or the score is below `autoThreshold`. Explicit `/codex:orchestrate` bypasses those two entry checks but not Phase 1 restrictions or budgets.

Hard exclusions: a one-file obvious fix; a known root cause and fix; a single command or narrow lookup; no independent packages; orchestration overhead exceeds the work; every writer would touch the same semantic core; user asks for one agent.

Complexity Score, one point each: two independent packages; multiple modules/layers/services; material architecture judgment; unclear root cause; competing approaches; independent review warranted; implementation and verification can be separated; long single-agent run; previous single-agent failure; security/concurrency/migration/data-loss risk.

0–2: direct work or one rescue. 3–4: at most two Roots. 5–7: prefer orchestration when enabled. 8–10: include a Sol architecture, plan-validation, or reviewer package.

Model defaults: Luna for bounded exploration and repetitive verification; Terra for routine implementation-quality analysis; Sol for architecture, integration judgment, ambiguity, or adversarial review. Base capability is `Sol > Terra > Luna`; reasoning effort is a separate inference-budget dimension.

Phase 1 is strictly read-only: `access` is `read-only`, workspace mode is `shared`, sandbox is read-only, changedFiles must be empty, and no package may push, publish, deploy, change credentials, or mutate a remote system. Automatic local write orchestration begins in Phase 2.

Canonical plan fields: version 1, objective, complexityScore, requestedBy `{ explicit, sessionId }`, and packages containing id, title, role `{ class, label }`, objective, dependencies, optional boolean, access, workspace, model `{ name, effort }`, nativeSubagents `{ policy, maxChildren }`, acceptanceCriteria, expectedOutputs.

Show a compressed 3–6 line plan and start immediately. Treat the launch response as acceptance only. Use `/codex:status`, `/codex:result`, and `/codex:cancel` for lifecycle. Integrate conclusions from evidence and verification, never from confidence alone.
''')

write("plugins/codex/skills/codex-work-package-contract/SKILL.md", r'''
---
name: codex-work-package-contract
description: Internal contract for bounded top-level Codex work packages
user-invocable: false
---
A package must have one objective, explicit dependencies, one role class/label, a model and effort, a read-only access boundary, native-child policy and cap, observable acceptance criteria, and required evidence outputs. Package IDs are unique. Dependencies must exist and the DAG must be acyclic. Phase 1 packages may not modify files.
''')

write("plugins/codex/skills/codex-integration-policy/SKILL.md", r'''
---
name: codex-integration-policy
description: Internal policy for interpreting Phase 1 Multi-Codex results
user-invocable: false
---
Phase 1 integrates conclusions only. Compare claims against file, command, and observation evidence; preserve contradictions and residual risks. Do not apply patches, create integration branches, cherry-pick, merge, or create commits. Writer worktrees and Git integration are Phase 2.
''')

write("plugins/codex/skills/codex-orchestration-recovery/SKILL.md", r'''
---
name: codex-orchestration-recovery
description: Internal Phase 1 recovery and durable-result policy
user-invocable: false
---
Durable status and terminal results remain readable without a live controller. A live controller may be reconnected to for status or cancellation. Phase 1 does not automatically resume orphaned packages; report controller loss honestly and preserve available results. Automatic restart/resume and orphan reconciliation are Phase 3.
''')

# Update command entrypoints without changing the underlying legacy companion.
write("plugins/codex/commands/status.md", r'''
---
description: Show active and recent Codex jobs and Multi-Codex orchestrations for this repository
argument-hint: '[job-id|orchestration-id|package-id] [--wait] [--timeout-ms <ms>] [--all]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

!`node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/dispatch.mjs" status "$ARGUMENTS"`

Present the command output faithfully. A reference may identify a legacy job, orchestration, or package. Without a reference, preserve the compact legacy status and append orchestration status when present.
''')
write("plugins/codex/commands/result.md", r'''
---
description: Show the stored final output for a Codex job, orchestration, or package
argument-hint: '[job-id|orchestration-id|package-id]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

!`node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/dispatch.mjs" result "$ARGUMENTS"`

Present the complete stored result without condensing evidence, verification, residual risks, errors, file paths, or follow-up commands.
''')
write("plugins/codex/commands/cancel.md", r'''
---
description: Cancel an active Codex job, Multi-Codex orchestration, or package
argument-hint: '[job-id|orchestration-id|package-id]'
disable-model-invocation: true
allowed-tools: Bash(node:*)
---

!`node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/dispatch.mjs" cancel "$ARGUMENTS"`
''')
write("plugins/codex/commands/setup.md", r'''
---
description: Check the local Codex CLI and toggle review-gate or automatic orchestration settings
argument-hint: '[--enable-review-gate|--disable-review-gate] [--enable-orchestration|--disable-orchestration]'
allowed-tools: Bash(node:*), Bash(npm:*), AskUserQuestion
---

Run:

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/orchestration/setup-dispatch.mjs" $ARGUMENTS
```

Present the complete output. If Codex is unavailable, preserve the existing install/login guidance. Automatic orchestration is disabled by default; explicit `/codex:orchestrate` remains available.
''')

# Focused tests for contracts, state, scheduler, and skill surface.
write("tests/orchestration-contracts.test.mjs", r'''
import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_ORCHESTRATION_CONFIG } from "../plugins/codex/scripts/orchestration/config.mjs";
import { normalizeOrchestrationPlan } from "../plugins/codex/scripts/orchestration/plan-contract.mjs";
import { validatePackageResult } from "../plugins/codex/scripts/orchestration/result-contract.mjs";

function plan() { return { version: 1, objective: "Compare independent hypotheses", complexityScore: 5, requestedBy: { explicit: true, sessionId: null }, packages: [
  { id: "pkg-a", title: "A", role: { class: "explorer", label: "a" }, objective: "Inspect A", dependencies: [], optional: false, access: "read-only", workspace: { mode: "shared" }, model: { name: "gpt-5.6-luna", effort: "high" }, nativeSubagents: { policy: "allowed", maxChildren: 1 }, acceptanceCriteria: ["Evidence"], expectedOutputs: ["claims"] },
  { id: "pkg-b", title: "B", role: { class: "verifier", label: "b" }, objective: "Verify A", dependencies: ["pkg-a"], access: "read-only", workspace: { mode: "shared" }, model: { name: "gpt-5.6-terra", effort: "high" }, nativeSubagents: { policy: "forbidden", maxChildren: 0 }, acceptanceCriteria: ["Compare"], expectedOutputs: ["evidence"] }
] }; }
test("normalizes a valid read-only plan", () => { const value = normalizeOrchestrationPlan(plan(), { config: DEFAULT_ORCHESTRATION_CONFIG }); assert.equal(value.packages[1].optional, false); assert.equal(value.budget.workerParallelism, 3); });
test("rejects non-boolean optional", () => { const value = plan(); value.packages[0].optional = "false"; assert.throws(() => normalizeOrchestrationPlan(value, { config: DEFAULT_ORCHESTRATION_CONFIG }), /optional must be a boolean/); });
test("rejects write packages and cycles", () => { const value = plan(); value.packages[0].access = "write"; assert.throws(() => normalizeOrchestrationPlan(value, { config: DEFAULT_ORCHESTRATION_CONFIG }), /read-only/); const cycle = plan(); cycle.packages[0].dependencies = ["pkg-b"]; assert.throws(() => normalizeOrchestrationPlan(cycle, { config: DEFAULT_ORCHESTRATION_CONFIG }), /cycle/); });
test("validates canonical package results", () => { const result = validatePackageResult({ packageId: "pkg-a", status: "completed", summary: "done", claims: [], evidence: [], changedFiles: [], verification: { passed: true, commands: [] }, residualRisks: [], confidence: 0.8, followUpRequests: [] }, "pkg-a"); assert.equal(result.status, "completed"); });
''')

write("tests/orchestration-scheduler.test.mjs", r'''
import test from "node:test";
import assert from "node:assert/strict";
import { createSchedulerState, deriveOrchestrationStatus, getReadyPackageIds, markPackageCompleted, markPackageFailed, markPackageReady, markPackageRunning, propagateBlockedPackages } from "../plugins/codex/scripts/orchestration/scheduler.mjs";
const plan = { packages: [ { id: "a", dependencies: [], optional: false }, { id: "b", dependencies: [], optional: true }, { id: "c", dependencies: ["a"], optional: false } ] };
test("schedules dependency-ready packages", () => { let state = createSchedulerState(plan); assert.deepEqual(getReadyPackageIds(state), ["a", "b"]); state = markPackageReady(state, "a"); state = markPackageRunning(state, "a", 1); state = markPackageCompleted(state, "a"); assert.deepEqual(getReadyPackageIds(state), ["b", "c"]); });
test("blocks descendants of failed dependencies", () => { let state = createSchedulerState(plan); state = markPackageReady(state, "a"); state = markPackageRunning(state, "a", 1); state = markPackageFailed(state, "a", "x"); state = propagateBlockedPackages(state); assert.equal(state.packages.c.status, "blocked"); assert.equal(deriveOrchestrationStatus(state), "running"); });
''')

write("tests/orchestration-state.test.mjs", r'''
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { makeTempDir } from "./helpers.mjs";
import { createOrchestrationState, listOrchestrations, loadOrchestrationState, resolveOrchestrationReference, updateOrchestrationState } from "../plugins/codex/scripts/orchestration/state-store.mjs";
const plan = { objective: "state", packages: [{ id: "pkg-a", title: "A" }], budget: { timeoutMinutes: 15 } };
test("persists orchestration state atomically", async () => { const workspace = makeTempDir(); const pluginDataDir = makeTempDir(); const state = await createOrchestrationState(workspace, plan, { claudeSessionId: "s" }, { pluginDataDir }); await updateOrchestrationState(workspace, state.id, (value) => { value.status = "running"; return value; }, { pluginDataDir }); assert.equal(loadOrchestrationState(workspace, state.id, { pluginDataDir }).status, "running"); assert.equal(listOrchestrations(workspace, { pluginDataDir }).length, 1); assert.equal(resolveOrchestrationReference(workspace, "pkg-a", { pluginDataDir }).packageId, "pkg-a"); });
''')

write("tests/orchestration-skill.test.mjs", r'''
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
const read = (path) => fs.readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
test("ships the explicit orchestration command and automatic-entry skill", () => { const command = read("plugins/codex/commands/orchestrate.md"); const skill = read("plugins/codex/skills/codex-orchestration/SKILL.md"); assert.match(command, /3–6 line plan/); assert.match(command, /cli\.mjs/); assert.doesNotMatch(command, /codex-rescue/); assert.match(skill, /Complexity Score/); assert.match(skill, /Sol > Terra > Luna/); assert.match(skill, /strictly read-only/); assert.match(skill, /autoEnabled/); });
''')

# Document the Phase 1 feature without replacing existing README content.
readme = (ROOT / "README.md").read_text(encoding="utf-8")
section = r'''

## Claude-Native Multi-Codex Orchestration — Read-only Phase 1

`/codex:orchestrate <task>` lets Claude Root decompose a repository investigation into independent read-only Codex Root packages, schedule them through a bounded worker pool, and persist package evidence and results.

```bash
/codex:orchestrate investigate the cache regression and independently challenge the concurrency assumptions
/codex:status orch-...
/codex:result orch-...
/codex:cancel orch-...
```

Automatic entry is disabled by default:

```bash
/codex:setup --enable-orchestration
/codex:setup --disable-orchestration
```

The default workspace pool size is 3 and may be configured from 1–8. The default plugin-wide top-level Root limit is 8 and the active Codex limit, including observed native children, is 12. Model routing follows `Sol > Terra > Luna`, with reasoning effort as a separate dimension.

Phase 1 is read-only: package results must report no changed files. Writer worktrees, integration branches, and automatic Git integration are Phase 2.
'''
if "## Claude-Native Multi-Codex Orchestration — Read-only Phase 1" not in readme:
    (ROOT / "README.md").write_text(readme.rstrip() + section + "\n", encoding="utf-8")

changelog = ROOT / "plugins/codex/CHANGELOG.md"
source = changelog.read_text(encoding="utf-8")
if "## Unreleased" not in source:
    changelog.write_text("# Changelog\n\n## Unreleased\n\n- Add read-only Claude-native Multi-Codex orchestration with durable status, results, cancellation, adaptive budgets, and bounded parallel workers.\n\n" + source.removeprefix("# Changelog\n").lstrip(), encoding="utf-8")

print("Phase 1 CLI, command/skill surface, tests, and docs generated.")
