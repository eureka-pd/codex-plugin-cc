#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    if new in source:
        return
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:120]!r}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8")


replace_once(
    "plugins/codex/scripts/lib/codex.mjs",
    " *   activeSubagentTurns: Set<string>,\n *   completionTimer:",
    " *   activeSubagentTurns: Set<string>,\n *   nativeChildPeak: number,\n *   completionTimer:",
)

replace_once(
    "plugins/codex/scripts/orchestration/package-worker.mjs",
    '''function fail(error) {
  send({ type: "error", error: { message: error instanceof Error ? error.message : String(error), code: error?.code ?? null, transient: Boolean(error?.transient) } });
  process.exitCode = 1;
}''',
    '''function fail(error) {
  send({ type: "error", error: { message: error instanceof Error ? error.message : String(error), code: error?.code ?? null, transient: Boolean(error?.transient) } });
  process.stdin.removeAllListeners("data");
  process.stdin.destroy();
  process.exitCode = 1;
}''',
)

replace_once(
    "plugins/codex/scripts/orchestration/package-worker.mjs",
    '  const client = await CodexAppServerClient.connect(request.workspaceRoot, { disableBroker: true, env: process.env });\n  process.stdin.setEncoding("utf8");',
    '''  const client = await CodexAppServerClient.connect(request.workspaceRoot, { disableBroker: true, env: process.env });
  let interruptRequested = false;
  let interruptSent = false;
  const sendInterruptIfReady = () => {
    if (!interruptRequested || interruptSent || !threadId || !turnId) return;
    interruptSent = true;
    client.request("turn/interrupt", { threadId, turnId }).catch(() => {
      interruptSent = false;
    });
  };
  process.stdin.setEncoding("utf8");''',
)

replace_once(
    "plugins/codex/scripts/orchestration/package-worker.mjs",
    '''        if (message.type === "interrupt" && threadId && turnId) {
          client.request("turn/interrupt", { threadId, turnId }).catch(() => {});
        }''',
    '''        if (message.type === "interrupt") {
          interruptRequested = true;
          sendInterruptIfReady();
        }''',
)

replace_once(
    "plugins/codex/scripts/orchestration/package-worker.mjs",
    '''      threadId = normalized.threadId ?? threadId;
      turnId = normalized.turnId ?? turnId;
      const message = String(normalized.message ?? "");''',
    '''      threadId = normalized.threadId ?? threadId;
      turnId = normalized.turnId ?? turnId;
      sendInterruptIfReady();
      const message = String(normalized.message ?? "");''',
)

replace_once(
    "plugins/codex/scripts/orchestration/package-worker.mjs",
    '  if (result.status !== 0) throw Object.assign(new Error(result.error?.message ?? result.stderr ?? "Codex package failed."), { code: "CODEX_PACKAGE_FAILED" });',
    '''  if (result.status !== 0) {
    const resultErrorMessage = result.error instanceof Error ? result.error.message : null;
    throw Object.assign(new Error(resultErrorMessage ?? result.stderr ?? "Codex package failed."), { code: "CODEX_PACKAGE_FAILED" });
  }''',
)

replace_once(
    "plugins/codex/scripts/orchestration/package-worker.mjs",
    '  send({ type: "result", payload: { packageResult, threadId: result.threadId ?? threadId, turnId: result.turnId ?? turnId, nativeChildPeak, nativeChildThreadIds: result.nativeChildThreadIds ?? [...childThreadIds] } });\n}',
    '''  send({ type: "result", payload: { packageResult, threadId: result.threadId ?? threadId, turnId: result.turnId ?? turnId, nativeChildPeak, nativeChildThreadIds: result.nativeChildThreadIds ?? [...childThreadIds] } });
  // The parent keeps stdin open for cancellation. Release the read handle once
  // the final result is emitted so a successful package worker can terminate.
  process.stdin.removeAllListeners("data");
  process.stdin.destroy();
}''',
)

replace_once(
    "plugins/codex/scripts/orchestration/worker-pool.mjs",
    '    let stderr = ""; let buffer = ""; let finalPayload = null; let reportedError = null;',
    '''    let stderr = ""; let buffer = "";
    /** @type {Record<string, unknown> | null} */
    let finalPayload = null;
    /** @type {{ message?: string, code?: string, transient?: boolean } | null} */
    let reportedError = null;''',
)

replace_once(
    "plugins/codex/scripts/orchestration/worker-pool.mjs",
    '''    try {
      const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });''',
    '''    try {
      try {
        await options.onStarted?.({ workerId, pid: child.pid });
      } catch (error) {
        terminateProcessTree(child.pid ?? Number.NaN);
        throw error;
      }
      const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });''',
)

replace_once(
    "plugins/codex/scripts/orchestration/worker-pool.mjs",
    '      return { ...finalPayload, workerId, pid: child.pid };',
    '      return { ...(finalPayload ?? {}), workerId, pid: child.pid };',
)

replace_once(
    "plugins/codex/scripts/orchestration/controller.mjs",
    '''      const runningIds = Object.entries(state.packages).filter(([, pkg]) => pkg.status === "running").map(([pkgId]) => pkgId);
      const capacity = Math.max(0, state.plan.budget.workerParallelism - runningIds.length);
      const ready = Object.entries(state.packages).filter(([, pkg]) => pkg.status === "ready").slice(0, capacity).map(([pkgId]) => pkgId);''',
    '''      const activeKeys = [...this.runningPackages.keys()].filter((key) => key.startsWith(`${id}:`));
      const capacity = Math.max(0, state.plan.budget.workerParallelism - activeKeys.length);
      const ready = Object.entries(state.packages)
        .filter(([pkgId, pkg]) => pkg.status === "ready" && !this.runningPackages.has(`${id}:${pkgId}`))
        .slice(0, capacity)
        .map(([pkgId]) => pkgId);''',
)

replace_once(
    "plugins/codex/scripts/orchestration/controller.mjs",
    '''    await this.mutate(id, (current) => {
      let scheduler = schedulerFromState(current); scheduler = markPackageRunning(scheduler, packageId, attempt); applyScheduler(current, scheduler);
      current.packages[packageId].startedAt = current.packages[packageId].startedAt ?? now(); return current;
    });
    appendOrchestrationEvent(this.workspaceRoot, id, { packageId, type: "package-started", phase: "running", message: spec.title, data: { attempt } });
    const dependencyResults = spec.dependencies.map((dep) => ({ packageId: dep, result: loadOrchestrationState(this.workspaceRoot, id).packages[dep].result })).filter((entry) => entry.result);
    try {
      const execution = await this.pool.execute(id, spec, dependencyResults, { timeoutMinutes: state.plan.budget.timeoutMinutes });''',
    '''    await this.mutate(id, (current) => {
      current.packages[packageId].attempt = attempt;
      return current;
    });
    const dependencyResults = spec.dependencies.map((dep) => ({ packageId: dep, result: loadOrchestrationState(this.workspaceRoot, id).packages[dep].result })).filter((entry) => entry.result);
    try {
      const execution = await this.pool.execute(id, spec, dependencyResults, {
        timeoutMinutes: state.plan.budget.timeoutMinutes,
        onStarted: async ({ workerId, pid }) => {
          await this.mutate(id, (current) => {
            if (current.status === "cancelling" || current.packages[packageId].status === "cancelled") {
              throw Object.assign(new Error(`Package ${packageId} was cancelled before worker activation.`), { code: "PACKAGE_CANCELLED" });
            }
            let scheduler = schedulerFromState(current);
            scheduler = markPackageRunning(scheduler, packageId, attempt);
            applyScheduler(current, scheduler);
            Object.assign(current.packages[packageId], {
              workerId,
              pid,
              startedAt: current.packages[packageId].startedAt ?? now()
            });
            return current;
          });
          appendOrchestrationEvent(this.workspaceRoot, id, { packageId, type: "package-started", phase: "running", message: spec.title, data: { attempt } });
        }
      });''',
)

print("Phase 1 worker activation, lifecycle, cancellation, and type-check fixes applied.")
