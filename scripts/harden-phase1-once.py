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
        raise RuntimeError(f"{path}: expected {count}, found {actual}: {old[:100]!r}")
    target.write_text(source.replace(old, new), encoding="utf-8")


# Expose caller-owned App Server turn execution and native-child topology.
p = ROOT / "plugins/codex/scripts/lib/codex.mjs"
s = p.read_text(encoding="utf-8")
start = s.index("export async function runAppServerTurn(cwd, options = {}) {")
end = s.index("\nexport async function findLatestTaskThread", start)
block = s[start:end]
header = "export async function runAppServerTurn(cwd, options = {}) {\n"
wrapper = "  return withAppServer(cwd, async (client) => {\n"
if not (block.startswith(header) and wrapper in block and block.endswith("\n  });\n}")):
    raise RuntimeError("runAppServerTurn structure changed")
prefix = block[len(header):block.index(wrapper)]
inner = block[block.index(wrapper) + len(wrapper):-len("\n  });\n}")]
new_block = (
    "export async function runAppServerTurnWithClient(client, cwd, options = {}) {\n"
    + prefix
    + inner
    + "\n}\n\n"
    + header
    + prefix
    + "  return withAppServer(cwd, (client) => runAppServerTurnWithClient(client, cwd, options));\n}"
)
s = s[:start] + new_block + s[end:]
s = s.replace(
    "    activeSubagentTurns: new Set(),\n    completionTimer:",
    "    activeSubagentTurns: new Set(),\n    nativeChildPeak: 0,\n    completionTimer:",
    1,
)
s = s.replace(
    "        state.activeSubagentTurns.add(message.params.threadId);\n      }",
    "        state.activeSubagentTurns.add(message.params.threadId);\n        state.nativeChildPeak = Math.max(state.nativeChildPeak, state.activeSubagentTurns.size);\n      }",
    1,
)
s = s.replace(
    "      turnId: turnState.turnId,\n      finalMessage:",
    "      turnId: turnState.turnId,\n      threadIds: [...turnState.threadIds],\n      nativeChildThreadIds: [...turnState.threadIds].filter((candidate) => candidate !== threadId),\n      nativeChildPeak: turnState.nativeChildPeak,\n      finalMessage:",
    1,
)
p.write_text(s, encoding="utf-8")

# Let a package worker own its direct client and accept a soft interrupt over stdin.
p = ROOT / "plugins/codex/scripts/orchestration/package-worker.mjs"
s = p.read_text(encoding="utf-8")
s = s.replace(
    'import { runAppServerTurn } from "../lib/codex.mjs";',
    'import { CodexAppServerClient } from "../lib/app-server.mjs";\nimport { runAppServerTurnWithClient } from "../lib/codex.mjs";',
    1,
)
old = '''  process.env.CODEX_COMPANION_APP_SERVER_ENDPOINT = invalidEndpoint;
  let threadId = null;
  let turnId = null;
  let nativeChildren = 0;
  let nativeChildPeak = 0;
  const result = await runAppServerTurn(request.workspaceRoot, {'''
new = '''  process.env.CODEX_COMPANION_APP_SERVER_ENDPOINT = invalidEndpoint;
  let threadId = null;
  let turnId = null;
  let nativeChildren = 0;
  let nativeChildPeak = 0;
  let boundaryError = null;
  const childThreadIds = new Set();
  const client = await CodexAppServerClient.connect(request.workspaceRoot, { disableBroker: true, env: process.env });
  process.stdin.setEncoding("utf8");
  let controlBuffer = "";
  process.stdin.on("data", (chunk) => {
    controlBuffer += chunk;
    let index = controlBuffer.indexOf("\\n");
    while (index !== -1) {
      const line = controlBuffer.slice(0, index);
      controlBuffer = controlBuffer.slice(index + 1);
      index = controlBuffer.indexOf("\\n");
      try {
        const message = JSON.parse(line);
        if (message.type === "interrupt" && threadId && turnId) {
          client.request("turn/interrupt", { threadId, turnId }).catch(() => {});
        }
      } catch {}
    }
  });
  const result = await runAppServerTurnWithClient(client, request.workspaceRoot, {'''
if old not in s:
    raise RuntimeError("package worker run block not found")
s = s.replace(old, new, 1)
s = s.replace(
    '''      if (/Starting subagent|Native child started/i.test(message)) nativeChildren += 1;
      if (/Subagent .* completed|Native child completed/i.test(message)) nativeChildren = Math.max(0, nativeChildren - 1);
      nativeChildPeak = Math.max(nativeChildPeak, nativeChildren);''',
    '''      if (/Starting subagent|Native child started/i.test(message)) {
        nativeChildren += 1;
        if (normalized.threadId && normalized.threadId !== threadId) childThreadIds.add(normalized.threadId);
      }
      if (/Subagent .* completed|Native child completed/i.test(message)) nativeChildren = Math.max(0, nativeChildren - 1);
      nativeChildPeak = Math.max(nativeChildPeak, nativeChildren);
      if (request.packageSpec.nativeSubagents.policy === "forbidden" && nativeChildren > 0) {
        boundaryError = Object.assign(new Error("Native children are forbidden for this package."), { code: "NATIVE_CHILD_POLICY_VIOLATION" });
      }
      if (nativeChildren > request.packageSpec.nativeSubagents.maxChildren) {
        boundaryError = Object.assign(new Error(`Native child limit ${request.packageSpec.nativeSubagents.maxChildren} exceeded.`), { code: "NATIVE_CHILD_LIMIT_EXCEEDED" });
      }''',
    1,
)
s = s.replace(
    '  if (result.status !== 0) throw Object.assign(new Error(result.error?.message ?? result.stderr ?? "Codex package failed."), { code: "CODEX_PACKAGE_FAILED" });',
    '  await client.close().catch(() => {});\n  if (boundaryError) throw boundaryError;\n  if (result.status !== 0) throw Object.assign(new Error(result.error?.message ?? result.stderr ?? "Codex package failed."), { code: "CODEX_PACKAGE_FAILED" });',
    1,
)
s = s.replace(
    "nativeChildThreadIds: result.nativeChildThreadIds ?? []",
    "nativeChildThreadIds: result.nativeChildThreadIds ?? [...childThreadIds]",
    1,
)
p.write_text(s, encoding="utf-8")

# Reserve/release pool slots correctly and perform soft cancellation before forced termination.
p = ROOT / "plugins/codex/scripts/orchestration/worker-pool.mjs"
s = p.read_text(encoding="utf-8")
s = s.replace(
    'import { spawn } from "node:child_process";',
    'import { spawn } from "node:child_process";\nimport { fileURLToPath } from "node:url";',
    1,
)
s = s.replace(
    '''    const workerId = `root-${crypto.randomUUID()}`;
    const lease = await acquireGlobalWorkerLease({''',
    '''    const workerId = `root-${crypto.randomUUID()}`;
    let lease;
    try {
      lease = await acquireGlobalWorkerLease({''',
    1,
)
s = s.replace(
    '''      packageId: packageSpec.id, globalTopLevelLimit: this.globalTopLevelLimit, globalActiveCodexLimit: this.globalActiveCodexLimit
    });
    const requestFile''',
    '''        packageId: packageSpec.id, globalTopLevelLimit: this.globalTopLevelLimit, globalActiveCodexLimit: this.globalActiveCodexLimit
      });
    } catch (error) {
      this.releaseSlot();
      throw error;
    }
    const requestFile''',
    1,
)
s = s.replace(
    '''    const script = new URL("./package-worker.mjs", import.meta.url);
    const child = spawn(process.execPath, [script.pathname, requestFile], { cwd: this.workspaceRoot, env: process.env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });''',
    '''    const script = new URL("./package-worker.mjs", import.meta.url);
    const child = spawn(process.execPath, [fileURLToPath(script), requestFile], { cwd: this.workspaceRoot, env: process.env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });''',
    1,
)
s = s.replace(
    '    const record = { workerId, child, lease, packageId: packageSpec.id, settled: false, nativeChildren: 0 };',
    '''    let resolveExit;
    const exitPromise = new Promise((resolve) => { resolveExit = resolve; });
    const record = { workerId, child, lease, packageId: packageSpec.id, settled: false, nativeChildren: 0, exitPromise };
    child.once("exit", () => resolveExit());''',
    1,
)
s = s.replace(
    '''  async cancel(packageId) {
    const record = this.active.get(packageId);
    if (!record) return { attempted: false, interrupted: false };
    terminateProcessTree(record.child.pid ?? Number.NaN);
    return { attempted: true, interrupted: true };
  }''',
    '''  async cancel(packageId, options = {}) {
    const record = this.active.get(packageId);
    if (!record) return { attempted: false, interrupted: false };
    record.child.stdin?.write(`${JSON.stringify({ type: "interrupt" })}\\n`);
    const settled = await Promise.race([
      record.exitPromise.then(() => true),
      new Promise((resolve) => setTimeout(() => resolve(false), options.graceMs ?? 1000))
    ]);
    if (!settled) terminateProcessTree(record.child.pid ?? Number.NaN);
    return { attempted: true, interrupted: settled };
  }''',
    1,
)
p.write_text(s, encoding="utf-8")

# Finalize orphaned work honestly when the owning controller disappeared.
write(
    "plugins/codex/scripts/orchestration/recovery.mjs",
    r'''
import { buildOrchestrationResult } from "./result-contract.mjs";
import { appendOrchestrationEvent, isTerminalState, listOrchestrations, updateOrchestrationState, writePackageResult } from "./state-store.mjs";

function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error?.code !== "ESRCH"; }
}
function descendants(plan, roots) {
  const blocked = new Set();
  let changed = true;
  while (changed) {
    changed = false;
    for (const pkg of plan.packages) {
      if (!blocked.has(pkg.id) && pkg.dependencies.some((dep) => roots.has(dep) || blocked.has(dep))) {
        blocked.add(pkg.id); changed = true;
      }
    }
  }
  return blocked;
}
export async function reconcilePhase1ControllerLoss(workspaceRoot, identity) {
  for (const state of listOrchestrations(workspaceRoot)) {
    if (isTerminalState(state)) continue;
    const previous = state.controller;
    if (!previous?.pid || previous.instanceId === identity.instanceId || alive(previous.pid)) continue;
    const failedRoots = new Set(Object.entries(state.packages).filter(([, pkg]) => ["running", "cancelling"].includes(pkg.status)).map(([id]) => id));
    const blocked = descendants(state.plan, failedRoots);
    const finalized = await updateOrchestrationState(workspaceRoot, state.id, (current) => {
      for (const [id, pkg] of Object.entries(current.packages)) {
        if (failedRoots.has(id)) {
          pkg.status = "failed"; pkg.error = "PHASE1_CONTROLLER_LOST: the owning controller exited."; pkg.completedAt = new Date().toISOString();
        } else if (blocked.has(id)) {
          pkg.status = "blocked"; pkg.error = "Dependency was lost with the previous controller."; pkg.completedAt = new Date().toISOString();
        } else if (["planned", "ready", "queued"].includes(pkg.status)) {
          pkg.status = "cancelled"; pkg.error = "Phase 1 does not automatically resume orphaned packages."; pkg.completedAt = new Date().toISOString();
        }
      }
      const usable = Object.values(current.packages).some((pkg) => ["completed", "partial"].includes(pkg.status));
      current.status = usable ? "degraded" : "failed";
      current.completedAt = new Date().toISOString();
      current.controller = identity;
      return current;
    });
    writePackageResult(workspaceRoot, state.id, "_orchestration", buildOrchestrationResult(finalized));
    appendOrchestrationEvent(workspaceRoot, state.id, {
      type: "controller-loss-finalized",
      phase: finalized.status,
      message: "Previous controller was lost; Phase 1 finalized the orchestration without automatic resume."
    });
  }
}
''',
)
p = ROOT / "plugins/codex/scripts/orchestration/controller-server.mjs"
s = p.read_text(encoding="utf-8")
s = s.replace(
    'import { WorkerPool } from "./worker-pool.mjs";',
    'import { WorkerPool } from "./worker-pool.mjs";\nimport { reconcilePhase1ControllerLoss } from "./recovery.mjs";',
    1,
)
s = s.replace(
    '  const controller = new OrchestrationController({ workspaceRoot, config, pool, controllerIdentity: identity });\n  const target',
    '  const controller = new OrchestrationController({ workspaceRoot, config, pool, controllerIdentity: identity });\n  await reconcilePhase1ControllerLoss(workspaceRoot, identity);\n  const target',
    1,
)
p.write_text(s, encoding="utf-8")

print("Phase 1 runtime hardening applied.")
