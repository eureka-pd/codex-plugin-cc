#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content.rstrip() + "\n", encoding="utf-8")

write("plugins/codex/scripts/orchestration/global-worker-registry.mjs", r'''
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { withFileLock } from "./file-lock.mjs";

function root(options = {}) {
  const pluginData = options.pluginDataDir ?? process.env.CLAUDE_PLUGIN_DATA;
  return pluginData ? path.join(path.resolve(pluginData), "orchestrations", "_global") : path.join(os.tmpdir(), "codex-companion", "orchestrations", "_global");
}
function registryFile(options) { return path.join(root(options), "workers.json"); }
function lockFile(options) { return path.join(root(options), "workers.lock"); }
function alive(pid) { try { process.kill(pid, 0); return true; } catch (error) { return error?.code !== "ESRCH"; } }
function read(options) {
  const file = registryFile(options);
  if (!fs.existsSync(file)) return { leases: [] };
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return { leases: [] }; }
}
function write(options, value) {
  const file = registryFile(options); fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 }); fs.renameSync(temp, file);
}
function prune(registry) { return { leases: (registry.leases ?? []).filter((lease) => alive(lease.pid)) }; }
export async function acquireGlobalWorkerLease(options) {
  return withFileLock(lockFile(options), {}, async () => {
    const registry = prune(read(options));
    const topLevelLimit = options.globalTopLevelLimit ?? 8;
    const activeLimit = options.globalActiveCodexLimit ?? 12;
    const activeCount = registry.leases.reduce((sum, lease) => sum + 1 + (lease.activeNativeChildren ?? 0), 0);
    if (registry.leases.length >= topLevelLimit) throw Object.assign(new Error(`Global Codex worker limit ${topLevelLimit} reached.`), { code: "GLOBAL_WORKER_LIMIT" });
    if (activeCount + 1 > activeLimit) throw Object.assign(new Error(`Global active Codex limit ${activeLimit} reached.`), { code: "ACTIVE_CODEX_LIMIT_EXCEEDED" });
    const lease = {
      id: `worker-${process.pid}-${crypto.randomUUID()}`, pid: process.pid, workspaceKey: options.workspaceKey,
      workerId: options.workerId, packageId: options.packageId, acquiredAt: new Date().toISOString(),
      heartbeatAt: new Date().toISOString(), activeNativeChildren: 0
    };
    registry.leases.push(lease); write(options, registry); return lease;
  });
}
export async function updateGlobalWorkerLease(leaseId, patch, options = {}) {
  return withFileLock(lockFile(options), {}, async () => {
    const registry = prune(read(options));
    const index = registry.leases.findIndex((lease) => lease.id === leaseId);
    if (index === -1) return null;
    const next = { ...registry.leases[index], ...patch, heartbeatAt: new Date().toISOString() };
    const projected = registry.leases.reduce((sum, lease, current) => sum + 1 + (current === index ? next.activeNativeChildren ?? 0 : lease.activeNativeChildren ?? 0), 0);
    if (projected > (options.globalActiveCodexLimit ?? 12)) throw Object.assign(new Error(`Global active Codex limit ${options.globalActiveCodexLimit ?? 12} exceeded.`), { code: "ACTIVE_CODEX_LIMIT_EXCEEDED" });
    registry.leases[index] = next; write(options, registry); return next;
  });
}
export async function releaseGlobalWorkerLease(leaseId, options = {}) {
  return withFileLock(lockFile(options), {}, async () => {
    const registry = prune(read(options)); registry.leases = registry.leases.filter((lease) => lease.id !== leaseId); write(options, registry);
  });
}
export async function readGlobalWorkerRegistry(options = {}) { return prune(read(options)); }
export async function getGlobalActiveCodexCount(options = {}) { return (await readGlobalWorkerRegistry(options)).leases.reduce((sum, lease) => sum + 1 + (lease.activeNativeChildren ?? 0), 0); }
''')

write("plugins/codex/scripts/orchestration/package-worker.mjs", r'''
#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

import { runAppServerTurn } from "../lib/codex.mjs";
import { parseStructuredOutput } from "../lib/structured-output.mjs";
import { buildReadOnlyPackagePrompt } from "./package-prompt.mjs";
import { readPackageResultSchema, validatePackageResult } from "./result-contract.mjs";

function send(value) { process.stdout.write(`${JSON.stringify(value)}\n`); }
function fail(error) {
  send({ type: "error", error: { message: error instanceof Error ? error.message : String(error), code: error?.code ?? null, transient: Boolean(error?.transient) } });
  process.exitCode = 1;
}

async function main() {
  const specFile = process.argv[2];
  if (!specFile) throw new Error("Usage: package-worker.mjs <spec-file>");
  const request = JSON.parse(fs.readFileSync(specFile, "utf8"));
  const invalidEndpoint = process.platform === "win32"
    ? `pipe:\\\\.\\pipe\\codex-orchestration-direct-${process.pid}-${crypto.randomUUID()}`
    : `unix:${path.join(os.tmpdir(), `codex-orchestration-direct-${process.pid}-${crypto.randomUUID()}.sock`)}`;
  process.env.CODEX_COMPANION_APP_SERVER_ENDPOINT = invalidEndpoint;
  let threadId = null;
  let turnId = null;
  let nativeChildren = 0;
  let nativeChildPeak = 0;
  const result = await runAppServerTurn(request.workspaceRoot, {
    prompt: buildReadOnlyPackagePrompt(request.packageSpec, { dependencyResults: request.dependencyResults }),
    model: request.packageSpec.model.name,
    effort: request.packageSpec.model.effort,
    sandbox: "read-only",
    outputSchema: readPackageResultSchema(),
    onProgress(event) {
      const normalized = typeof event === "string" ? { message: event, phase: null } : event;
      threadId = normalized.threadId ?? threadId;
      turnId = normalized.turnId ?? turnId;
      const message = String(normalized.message ?? "");
      if (/Starting subagent|Native child started/i.test(message)) nativeChildren += 1;
      if (/Subagent .* completed|Native child completed/i.test(message)) nativeChildren = Math.max(0, nativeChildren - 1);
      nativeChildPeak = Math.max(nativeChildPeak, nativeChildren);
      send({ type: "progress", event: { ...normalized, threadId, turnId, activeNativeChildren: nativeChildren, nativeChildPeak } });
    }
  });
  if (result.status !== 0) throw Object.assign(new Error(result.error?.message ?? result.stderr ?? "Codex package failed."), { code: "CODEX_PACKAGE_FAILED" });
  if ((result.touchedFiles ?? []).length > 0 || (result.fileChanges ?? []).length > 0) throw Object.assign(new Error("Phase 1 read-only boundary violation: Codex reported file changes."), { code: "READ_ONLY_BOUNDARY_VIOLATION" });
  const parsed = parseStructuredOutput(result.finalMessage, { status: result.status, failureMessage: result.stderr });
  if (!parsed.parsed) throw Object.assign(new Error(`Codex package returned invalid JSON: ${parsed.parseError}`), { code: "PACKAGE_RESULT_PARSE_ERROR" });
  const packageResult = validatePackageResult(parsed.parsed, request.packageSpec.id);
  send({ type: "result", payload: { packageResult, threadId: result.threadId ?? threadId, turnId: result.turnId ?? turnId, nativeChildPeak, nativeChildThreadIds: result.nativeChildThreadIds ?? [] } });
}

main().catch(fail);
''')

write("plugins/codex/scripts/orchestration/worker-pool.mjs", r'''
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

import { terminateProcessTree } from "../lib/process.mjs";
import { acquireGlobalWorkerLease, releaseGlobalWorkerLease, updateGlobalWorkerLease } from "./global-worker-registry.mjs";

function workspaceKey(value) { return crypto.createHash("sha256").update(path.resolve(value)).digest("hex").slice(0, 16); }
function transientError(error) { return ["EPIPE", "ECONNRESET", "ECONNREFUSED", "ENOENT", "CODEX_WORKER_EXIT"].includes(error?.code) || error?.transient === true; }

export class WorkerPool {
  constructor(options) {
    this.workspaceRoot = options.workspaceRoot;
    this.size = options.size;
    this.globalTopLevelLimit = options.globalTopLevelLimit ?? 8;
    this.globalActiveCodexLimit = options.globalActiveCodexLimit ?? 12;
    this.pluginDataDir = options.pluginDataDir;
    this.onEvent = options.onEvent ?? (() => {});
    this.active = new Map(); this.waiters = [];
  }
  async waitForSlot() {
    if (this.active.size < this.size) return;
    await new Promise((resolve, reject) => this.waiters.push({ resolve, reject }));
  }
  releaseSlot() { this.waiters.shift()?.resolve(); }
  async execute(orchestrationId, packageSpec, dependencyResults, options = {}) {
    await this.waitForSlot();
    const workerId = `root-${crypto.randomUUID()}`;
    const lease = await acquireGlobalWorkerLease({
      pluginDataDir: this.pluginDataDir, workspaceKey: workspaceKey(this.workspaceRoot), workerId,
      packageId: packageSpec.id, globalTopLevelLimit: this.globalTopLevelLimit, globalActiveCodexLimit: this.globalActiveCodexLimit
    });
    const requestFile = path.join(os.tmpdir(), `codex-orchestration-${process.pid}-${crypto.randomUUID()}.json`);
    fs.writeFileSync(requestFile, JSON.stringify({ workspaceRoot: this.workspaceRoot, orchestrationId, packageSpec, dependencyResults }), { encoding: "utf8", mode: 0o600 });
    const script = new URL("./package-worker.mjs", import.meta.url);
    const child = spawn(process.execPath, [script.pathname, requestFile], { cwd: this.workspaceRoot, env: process.env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    const record = { workerId, child, lease, packageId: packageSpec.id, settled: false, nativeChildren: 0 };
    this.active.set(packageSpec.id, record);
    let stderr = ""; let buffer = ""; let finalPayload = null; let reportedError = null;
    child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      let index = buffer.indexOf("\n");
      while (index !== -1) {
        const line = buffer.slice(0, index); buffer = buffer.slice(index + 1); index = buffer.indexOf("\n");
        if (!line.trim()) continue;
        try {
          const message = JSON.parse(line);
          if (message.type === "progress") {
            const nextChildren = message.event?.activeNativeChildren ?? 0;
            if (nextChildren !== record.nativeChildren) {
              record.nativeChildren = nextChildren;
              updateGlobalWorkerLease(lease.id, { activeNativeChildren: nextChildren }, { pluginDataDir: this.pluginDataDir, globalActiveCodexLimit: this.globalActiveCodexLimit })
                .catch((error) => { if (error.code === "ACTIVE_CODEX_LIMIT_EXCEEDED") this.cancel(packageSpec.id); });
            }
            this.onEvent({ orchestrationId, packageId: packageSpec.id, type: "package-progress", ...message.event });
          } else if (message.type === "result") finalPayload = message.payload;
          else if (message.type === "error") reportedError = message.error;
        } catch (error) { reportedError = { message: `Invalid worker JSON: ${error.message}`, code: "WORKER_PROTOCOL_ERROR" }; }
      }
    });
    const hardTimeoutMs = Math.max(1000, (options.timeoutMinutes ?? 15) * 60_000);
    const timeout = setTimeout(() => {
      reportedError = { message: `Package ${packageSpec.id} exceeded ${options.timeoutMinutes ?? 15} minutes.`, code: "PACKAGE_TIMEOUT" };
      terminateProcessTree(child.pid ?? Number.NaN);
    }, hardTimeoutMs); timeout.unref?.();
    try {
      const code = await new Promise((resolve, reject) => { child.once("error", reject); child.once("exit", resolve); });
      if (code !== 0 || !finalPayload) {
        const error = Object.assign(new Error(reportedError?.message ?? stderr.trim() ?? `Package worker exited with code ${code}.`), {
          code: reportedError?.code ?? "CODEX_WORKER_EXIT", transient: reportedError?.transient ?? (reportedError?.code == null)
        });
        throw error;
      }
      return { ...finalPayload, workerId, pid: child.pid };
    } finally {
      clearTimeout(timeout); record.settled = true; this.active.delete(packageSpec.id); fs.rmSync(requestFile, { force: true });
      await releaseGlobalWorkerLease(lease.id, { pluginDataDir: this.pluginDataDir }); this.releaseSlot();
    }
  }
  async cancel(packageId) {
    const record = this.active.get(packageId);
    if (!record) return { attempted: false, interrupted: false };
    terminateProcessTree(record.child.pid ?? Number.NaN);
    return { attempted: true, interrupted: true };
  }
  getSnapshot() { return { size: this.size, active: [...this.active.values()].map((entry) => ({ packageId: entry.packageId, workerId: entry.workerId, pid: entry.child.pid, nativeChildren: entry.nativeChildren })), queued: this.waiters.length }; }
  async close() { for (const packageId of [...this.active.keys()]) await this.cancel(packageId); for (const waiter of this.waiters.splice(0)) waiter.reject(new Error("Worker pool closed.")); }
}
export { transientError as isTransientWorkerError };
''')

write("plugins/codex/scripts/orchestration/controller.mjs", r'''
import {
  createSchedulerState, deriveOrchestrationStatus, getReadyPackageIds, markPackageCompleted,
  markPackageFailed, markPackageReady, markPackageRunning, markPackageCancelled, propagateBlockedPackages
} from "./scheduler.mjs";
import { normalizeOrchestrationPlan } from "./plan-contract.mjs";
import { buildOrchestrationResult } from "./result-contract.mjs";
import {
  appendOrchestrationEvent, createOrchestrationState, isTerminalState, listOrchestrations,
  loadOrchestrationState, resolveOrchestrationReference, updateOrchestrationState, writePackageResult
} from "./state-store.mjs";
import { isTransientWorkerError } from "./worker-pool.mjs";

function now() { return new Date().toISOString(); }
function schedulerFromState(state) {
  const scheduler = createSchedulerState(state.plan);
  for (const [id, pkg] of Object.entries(state.packages)) scheduler.packages[id] = { ...scheduler.packages[id], status: pkg.status, attempt: pkg.attempt, error: pkg.error };
  return scheduler;
}
function applyScheduler(state, scheduler) { for (const [id, pkg] of Object.entries(scheduler.packages)) state.packages[id] = { ...state.packages[id], status: pkg.status, attempt: pkg.attempt, error: pkg.error }; return state; }

export class OrchestrationController {
  constructor(options) {
    this.workspaceRoot = options.workspaceRoot; this.config = options.config; this.pool = options.pool;
    this.controllerIdentity = options.controllerIdentity ?? null; this.activeRuns = new Map(); this.runningPackages = new Map();
    this.onMilestone = options.onMilestone ?? (() => {});
  }
  async start(planInput, context = {}) {
    const plan = normalizeOrchestrationPlan(planInput, { workspaceRoot: this.workspaceRoot, config: this.config });
    const state = await createOrchestrationState(this.workspaceRoot, plan, { ...context, controller: this.controllerIdentity });
    const promise = this.runOrchestration(state.id).catch((error) => this.failControllerRun(state.id, error)).finally(() => this.activeRuns.delete(state.id));
    this.activeRuns.set(state.id, promise);
    return { orchestrationId: state.id, status: "queued", objective: plan.objective, packageCount: plan.packages.length };
  }
  async mutate(id, callback) { return updateOrchestrationState(this.workspaceRoot, id, callback); }
  async runOrchestration(id) {
    await this.mutate(id, (state) => { state.status = "running"; state.startedAt = state.startedAt ?? now(); return state; });
    const budgetState = loadOrchestrationState(this.workspaceRoot, id);
    const deadline = Date.now() + budgetState.plan.budget.timeoutMinutes * 60_000;
    while (true) {
      let state = loadOrchestrationState(this.workspaceRoot, id);
      if (Date.now() >= deadline) { await this.cancel(id, { reason: "Orchestration time budget exceeded." }); return; }
      if (isTerminalState(state) || state.status === "cancelling") break;
      let scheduler = propagateBlockedPackages(schedulerFromState(state));
      for (const packageId of getReadyPackageIds(scheduler)) scheduler = markPackageReady(scheduler, packageId);
      state = await this.mutate(id, (current) => applyScheduler(current, scheduler));
      const runningIds = Object.entries(state.packages).filter(([, pkg]) => pkg.status === "running").map(([pkgId]) => pkgId);
      const capacity = Math.max(0, state.plan.budget.workerParallelism - runningIds.length);
      const ready = Object.entries(state.packages).filter(([, pkg]) => pkg.status === "ready").slice(0, capacity).map(([pkgId]) => pkgId);
      for (const packageId of ready) this.launchPackage(id, packageId);
      state = loadOrchestrationState(this.workspaceRoot, id);
      if (Object.values(state.packages).every((pkg) => ["completed", "partial", "blocked", "failed", "cancelled"].includes(pkg.status))) {
        await this.finalize(id); break;
      }
      const running = [...this.runningPackages.entries()].filter(([key]) => key.startsWith(`${id}:`)).map(([, promise]) => promise);
      if (running.length === 0) { await this.finalize(id); break; }
      await Promise.race([Promise.race(running), new Promise((resolve) => setTimeout(resolve, Math.min(1000, Math.max(10, deadline - Date.now()))))]);
    }
  }
  launchPackage(id, packageId) {
    const key = `${id}:${packageId}`;
    if (this.runningPackages.has(key)) return;
    const promise = this.executePackage(id, packageId).finally(() => this.runningPackages.delete(key));
    this.runningPackages.set(key, promise);
  }
  async executePackage(id, packageId) {
    let state = loadOrchestrationState(this.workspaceRoot, id);
    const spec = state.plan.packages.find((pkg) => pkg.id === packageId);
    const attempt = (state.packages[packageId].attempt ?? 0) + 1;
    await this.mutate(id, (current) => {
      let scheduler = schedulerFromState(current); scheduler = markPackageRunning(scheduler, packageId, attempt); applyScheduler(current, scheduler);
      current.packages[packageId].startedAt = current.packages[packageId].startedAt ?? now(); return current;
    });
    appendOrchestrationEvent(this.workspaceRoot, id, { packageId, type: "package-started", phase: "running", message: spec.title, data: { attempt } });
    const dependencyResults = spec.dependencies.map((dep) => ({ packageId: dep, result: loadOrchestrationState(this.workspaceRoot, id).packages[dep].result })).filter((entry) => entry.result);
    try {
      const execution = await this.pool.execute(id, spec, dependencyResults, { timeoutMinutes: state.plan.budget.timeoutMinutes });
      const result = execution.packageResult;
      writePackageResult(this.workspaceRoot, id, packageId, result);
      await this.mutate(id, (current) => {
        let scheduler = schedulerFromState(current); scheduler = markPackageCompleted(scheduler, packageId, result.status); applyScheduler(current, scheduler);
        Object.assign(current.packages[packageId], {
          result, workerId: execution.workerId, pid: null, threadId: execution.threadId ?? null, turnId: execution.turnId ?? null,
          nativeChildThreadIds: execution.nativeChildThreadIds ?? [], completedAt: now(),
          nativeSubagentDegraded: spec.nativeSubagents.policy === "required" && (execution.nativeChildPeak ?? 0) === 0,
          nativeSubagentDegradationReason: spec.nativeSubagents.policy === "required" && (execution.nativeChildPeak ?? 0) === 0 ? "No native child was observed; accepted Root-only execution." : null
        }); return current;
      });
      appendOrchestrationEvent(this.workspaceRoot, id, { packageId, type: "package-completed", phase: result.status, message: result.summary });
    } catch (error) {
      if (isTransientWorkerError(error) && attempt <= state.plan.budget.maxRetries) {
        await this.mutate(id, (current) => { current.packages[packageId].status = "ready"; current.packages[packageId].error = error.message; return current; });
        appendOrchestrationEvent(this.workspaceRoot, id, { packageId, type: "package-retry", phase: "queued", message: error.message, data: { nextAttempt: attempt + 1 } });
        return;
      }
      await this.mutate(id, (current) => {
        let scheduler = schedulerFromState(current); scheduler = markPackageFailed(scheduler, packageId, error.message); scheduler = propagateBlockedPackages(scheduler); applyScheduler(current, scheduler);
        current.packages[packageId].completedAt = now(); return current;
      });
      appendOrchestrationEvent(this.workspaceRoot, id, { packageId, type: "package-failed", phase: "failed", message: error.message, data: { code: error.code ?? null } });
    }
  }
  async finalize(id) {
    const state = await this.mutate(id, (current) => {
      const scheduler = propagateBlockedPackages(schedulerFromState(current)); applyScheduler(current, scheduler);
      current.status = current.status === "cancelling" ? "cancelled" : deriveOrchestrationStatus(scheduler);
      current.completedAt = now(); return current;
    });
    writePackageResult(this.workspaceRoot, id, "_orchestration", buildOrchestrationResult(state));
    appendOrchestrationEvent(this.workspaceRoot, id, { type: "orchestration-completed", phase: state.status, message: state.status });
    this.onMilestone({ orchestrationId: id, type: "orchestration-completed", status: state.status });
  }
  async failControllerRun(id, error) {
    await this.mutate(id, (state) => { state.status = "failed"; state.completedAt = now(); state.error = error.message; return state; });
    appendOrchestrationEvent(this.workspaceRoot, id, { type: "controller-failed", phase: "failed", message: error.message });
  }
  status(reference = "") {
    if (!reference) return { workspaceRoot: this.workspaceRoot, orchestrations: listOrchestrations(this.workspaceRoot), pool: this.pool.getSnapshot() };
    const resolved = resolveOrchestrationReference(this.workspaceRoot, reference); const state = loadOrchestrationState(this.workspaceRoot, resolved.orchestrationId);
    return resolved.kind === "package" ? { orchestrationId: state.id, package: state.packages[resolved.packageId], packageSpec: state.plan.packages.find((pkg) => pkg.id === resolved.packageId) } : { ...state, pool: this.pool.getSnapshot() };
  }
  result(reference = "") {
    const resolved = resolveOrchestrationReference(this.workspaceRoot, reference); const state = loadOrchestrationState(this.workspaceRoot, resolved.orchestrationId);
    if (resolved.kind === "package") return state.packages[resolved.packageId].result;
    if (!isTerminalState(state)) throw new Error(`Orchestration ${state.id} is still running. Use /codex:status ${state.id}.`);
    return buildOrchestrationResult(state);
  }
  async cancel(reference, options = {}) {
    const resolved = resolveOrchestrationReference(this.workspaceRoot, reference); const state = loadOrchestrationState(this.workspaceRoot, resolved.orchestrationId);
    if (isTerminalState(state)) return state;
    if (resolved.kind === "package") {
      await this.pool.cancel(resolved.packageId);
      return this.mutate(state.id, (current) => { let scheduler = schedulerFromState(current); scheduler = markPackageCancelled(scheduler, resolved.packageId); scheduler = propagateBlockedPackages(scheduler); applyScheduler(current, scheduler); return current; });
    }
    await this.mutate(state.id, (current) => { current.status = "cancelling"; return current; });
    for (const [packageId, pkg] of Object.entries(state.packages)) {
      if (pkg.status === "running") await this.pool.cancel(packageId);
      await this.mutate(state.id, (current) => { let scheduler = schedulerFromState(current); scheduler = markPackageCancelled(scheduler, packageId); applyScheduler(current, scheduler); return current; });
    }
    await this.finalize(state.id); return loadOrchestrationState(this.workspaceRoot, state.id);
  }
  async shutdown() { await this.pool.close(); }
}
''')

write("plugins/codex/scripts/orchestration/ipc.mjs", r'''
import net from "node:net";
import path from "node:path";
import os from "node:os";

export function createControllerEndpoint(workspaceKey, platform = process.platform) {
  if (platform === "win32") return `pipe:\\\\.\\pipe\\${workspaceKey}-codex-orchestrator`;
  return `unix:${path.join(os.tmpdir(), "codex-orchestration-runtime", workspaceKey, "controller.sock")}`;
}
export function parseControllerEndpoint(endpoint) {
  if (endpoint.startsWith("unix:")) return { kind: "unix", path: endpoint.slice(5) };
  if (endpoint.startsWith("pipe:")) return { kind: "pipe", path: endpoint.slice(5) };
  throw new Error(`Unsupported controller endpoint: ${endpoint}`);
}
export function requestController(endpoint, method, params = {}, options = {}) {
  const target = parseControllerEndpoint(endpoint);
  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ path: target.path }); let buffer = "";
    const timer = setTimeout(() => { socket.destroy(); reject(new Error(`Controller request timed out: ${method}`)); }, options.timeoutMs ?? 10_000);
    socket.setEncoding("utf8");
    socket.on("connect", () => socket.write(`${JSON.stringify({ id: 1, method, params })}\n`));
    socket.on("data", (chunk) => { buffer += chunk; const index = buffer.indexOf("\n"); if (index < 0) return; clearTimeout(timer); socket.end(); const message = JSON.parse(buffer.slice(0, index)); if (message.error) reject(Object.assign(new Error(message.error.message), { rpcCode: message.error.code })); else resolve(message.result); });
    socket.on("error", (error) => { clearTimeout(timer); reject(error); });
  });
}
''')

write("plugins/codex/scripts/orchestration/controller-client.mjs", r'''
import { requestController } from "./ipc.mjs";
export class OrchestrationControllerClient {
  constructor(endpoint) { this.endpoint = endpoint; }
  start(plan, context) { return requestController(this.endpoint, "orchestration/start", { plan, context }); }
  status(reference = "") { return requestController(this.endpoint, "orchestration/status", { reference }); }
  result(reference = "") { return requestController(this.endpoint, "orchestration/result", { reference }); }
  cancel(reference) { return requestController(this.endpoint, "orchestration/cancel", { reference }); }
  controllerStatus() { return requestController(this.endpoint, "controller/status", {}); }
  shutdown(force = false) { return requestController(this.endpoint, "controller/shutdown", { force }); }
}
''')

write("plugins/codex/scripts/orchestration/controller-lifecycle.mjs", r'''
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

import { createControllerEndpoint, parseControllerEndpoint } from "./ipc.mjs";
import { OrchestrationControllerClient } from "./controller-client.mjs";
import { withFileLock } from "./file-lock.mjs";

function key(workspaceRoot) { return crypto.createHash("sha256").update(path.resolve(workspaceRoot)).digest("hex").slice(0, 16); }
function runtimeDir(workspaceRoot) { return path.join(os.tmpdir(), "codex-orchestration-runtime", key(workspaceRoot)); }
function alive(pid) { try { process.kill(pid, 0); return true; } catch (error) { return error?.code !== "ESRCH"; } }
function read(file) { try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; } }
async function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
export async function ensureControllerServer(workspaceRoot, options = {}) {
  const dir = runtimeDir(workspaceRoot); fs.mkdirSync(dir, { recursive: true });
  const runtimeFile = path.join(dir, "controller.json"); const lockFile = path.join(dir, "controller.lock");
  return withFileLock(lockFile, {}, async () => {
    const existing = read(runtimeFile);
    if (existing && alive(existing.pid)) {
      try { await new OrchestrationControllerClient(existing.endpoint).controllerStatus(); return existing; } catch {}
    }
    fs.rmSync(runtimeFile, { force: true });
    if (existing?.endpoint?.startsWith("unix:")) fs.rmSync(parseControllerEndpoint(existing.endpoint).path, { force: true });
    const endpoint = createControllerEndpoint(key(workspaceRoot));
    const script = new URL("./controller-server.mjs", import.meta.url);
    const child = spawn(process.execPath, [script.pathname, "serve", "--workspace", workspaceRoot, "--endpoint", endpoint, "--runtime-file", runtimeFile], { cwd: workspaceRoot, env: options.env ?? process.env, detached: true, stdio: "ignore", windowsHide: true });
    child.unref();
    const deadline = Date.now() + 10_000;
    while (Date.now() < deadline) {
      const state = read(runtimeFile);
      if (state) { try { await new OrchestrationControllerClient(endpoint).controllerStatus(); return state; } catch {} }
      await sleep(100);
    }
    throw new Error("Timed out starting the Multi-Codex orchestration controller.");
  });
}
''')

write("plugins/codex/scripts/orchestration/controller-server.mjs", r'''
#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";

import { loadOrchestrationConfig } from "./config.mjs";
import { OrchestrationController } from "./controller.mjs";
import { parseControllerEndpoint } from "./ipc.mjs";
import { WorkerPool } from "./worker-pool.mjs";

function args(argv) { const result = {}; for (let i = 0; i < argv.length; i += 2) result[argv[i].replace(/^--/, "")] = argv[i + 1]; return result; }
function send(socket, message) { socket.write(`${JSON.stringify(message)}\n`); }
async function main() {
  if (process.argv[2] !== "serve") throw new Error("controller-server.mjs serve --workspace <path> --endpoint <endpoint> --runtime-file <path>");
  const options = args(process.argv.slice(3)); const workspaceRoot = path.resolve(options.workspace); const endpoint = options.endpoint; const runtimeFile = options["runtime-file"];
  const identity = { instanceId: `controller-${process.pid}-${crypto.randomUUID()}`, pid: process.pid, endpoint, workspaceRoot, startedAt: new Date().toISOString() };
  fs.mkdirSync(path.dirname(runtimeFile), { recursive: true }); fs.writeFileSync(runtimeFile, `${JSON.stringify(identity, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  const config = loadOrchestrationConfig(workspaceRoot);
  const pool = new WorkerPool({ workspaceRoot, size: config.workers.workspacePoolSize, globalTopLevelLimit: config.workers.globalTopLevelLimit, globalActiveCodexLimit: config.workers.globalActiveCodexLimit, onEvent: () => {} });
  const controller = new OrchestrationController({ workspaceRoot, config, pool, controllerIdentity: identity });
  const target = parseControllerEndpoint(endpoint); if (target.kind === "unix") { fs.mkdirSync(path.dirname(target.path), { recursive: true }); fs.rmSync(target.path, { force: true }); }
  const server = net.createServer((socket) => {
    socket.setEncoding("utf8"); let buffer = "";
    socket.on("data", async (chunk) => {
      buffer += chunk; const index = buffer.indexOf("\n"); if (index < 0) return;
      const line = buffer.slice(0, index); buffer = buffer.slice(index + 1); let message;
      try {
        message = JSON.parse(line); let result;
        switch (message.method) {
          case "orchestration/start": result = await controller.start(message.params.plan, message.params.context); break;
          case "orchestration/status": result = controller.status(message.params.reference); break;
          case "orchestration/result": result = controller.result(message.params.reference); break;
          case "orchestration/cancel": result = await controller.cancel(message.params.reference); break;
          case "controller/status": result = { ...identity, pool: pool.getSnapshot(), activeOrchestrationIds: [...controller.activeRuns.keys()] }; break;
          case "controller/shutdown": if (controller.activeRuns.size && !message.params.force) throw new Error("Controller has active orchestrations."); await controller.shutdown(); result = {}; send(socket, { id: message.id, result }); server.close(() => process.exit(0)); return;
          default: throw Object.assign(new Error(`Unknown method: ${message.method}`), { rpcCode: -32601 });
        }
        send(socket, { id: message.id, result });
      } catch (error) { send(socket, { id: message?.id ?? null, error: { code: error.rpcCode ?? -32000, message: error.message } }); }
    });
  });
  const cleanup = async () => { await controller.shutdown().catch(() => {}); fs.rmSync(runtimeFile, { force: true }); if (target.kind === "unix") fs.rmSync(target.path, { force: true }); };
  process.on("SIGTERM", async () => { await cleanup(); process.exit(0); }); process.on("SIGINT", async () => { await cleanup(); process.exit(0); });
  server.listen(target.path);
}
main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exit(1); });
''')

print("Phase 1 runtime, worker pool, controller, and IPC generated.")
