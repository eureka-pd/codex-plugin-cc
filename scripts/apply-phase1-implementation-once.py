#!/usr/bin/env python3
from __future__ import annotations

import json
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
        raise RuntimeError(f"{path}: expected {count} occurrence(s), found {actual}: {old[:80]!r}")
    target.write_text(source.replace(old, new), encoding="utf-8")


write("plugins/codex/scripts/orchestration/constants.mjs", r'''
export const ORCHESTRATION_STATE_VERSION = 1;
export const ORCHESTRATION_PLAN_VERSION = 1;
export const DEFAULT_WORKSPACE_POOL_SIZE = 3;
export const MIN_WORKSPACE_POOL_SIZE = 1;
export const MAX_WORKSPACE_POOL_SIZE = 8;
export const DEFAULT_GLOBAL_TOP_LEVEL_LIMIT = 8;
export const DEFAULT_GLOBAL_ACTIVE_CODEX_LIMIT = 12;
export const DEFAULT_IDLE_TTL_MINUTES = 10;
export const DEFAULT_AUTO_THRESHOLD = 5;
export const DEFAULT_CANCEL_GRACE_MS = 10_000;
export const VALID_EFFORTS = new Set(["none", "low", "medium", "high", "xhigh", "max", "ultra"]);
export const ROLE_CLASSES = new Set([
  "planner", "architect", "explorer", "implementer", "tester", "reviewer", "verifier",
  "migration-specialist", "security-reviewer"
]);
export const PACKAGE_TERMINAL_STATUSES = new Set(["completed", "partial", "blocked", "failed", "cancelled"]);
export const ORCHESTRATION_TERMINAL_STATUSES = new Set([
  "completed", "completed-with-omissions", "degraded", "blocked", "failed", "cancelled"
]);
''')

write("plugins/codex/scripts/orchestration/config.mjs", r'''
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

import {
  DEFAULT_AUTO_THRESHOLD,
  DEFAULT_GLOBAL_ACTIVE_CODEX_LIMIT,
  DEFAULT_GLOBAL_TOP_LEVEL_LIMIT,
  DEFAULT_IDLE_TTL_MINUTES,
  DEFAULT_WORKSPACE_POOL_SIZE
} from "./constants.mjs";

export const DEFAULT_ORCHESTRATION_CONFIG = Object.freeze({
  auto: Object.freeze({ enabled: false, threshold: DEFAULT_AUTO_THRESHOLD }),
  workers: Object.freeze({
    workspacePoolSize: DEFAULT_WORKSPACE_POOL_SIZE,
    globalTopLevelLimit: DEFAULT_GLOBAL_TOP_LEVEL_LIMIT,
    globalActiveCodexLimit: DEFAULT_GLOBAL_ACTIVE_CODEX_LIMIT,
    idleTtlMinutes: DEFAULT_IDLE_TTL_MINUTES
  })
});

export function getUserConfigPath(options = {}) {
  return path.join(options.homeDir ?? os.homedir(), ".claude", "codex-orchestration.json");
}

export function getProjectConfigPath(workspaceRoot) {
  return path.join(workspaceRoot, ".claude", "codex-orchestration.json");
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function mergeObjects(base, patch) {
  const result = { ...base };
  for (const [key, value] of Object.entries(patch ?? {})) {
    result[key] = isPlainObject(value) && isPlainObject(base?.[key]) ? mergeObjects(base[key], value) : value;
  }
  return result;
}

function readConfig(filePath) {
  if (!fs.existsSync(filePath)) return {};
  const parsed = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (!isPlainObject(parsed)) throw new Error(`${filePath} must contain a JSON object.`);
  return parsed;
}

function integerIn(value, min, max, label) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`${label} must be between ${min} and ${max}.`);
  }
}

export function validateOrchestrationConfig(config) {
  for (const key of Object.keys(config)) {
    if (!new Set(["auto", "workers"]).has(key)) throw new Error(`Unknown orchestration configuration key: ${key}`);
  }
  if (!isPlainObject(config.auto) || typeof config.auto.enabled !== "boolean") {
    throw new Error("auto.enabled must be a boolean.");
  }
  integerIn(config.auto.threshold, 0, 10, "auto.threshold");
  if (!isPlainObject(config.workers)) throw new Error("workers must be an object.");
  integerIn(config.workers.workspacePoolSize, 1, 8, "workers.workspacePoolSize");
  integerIn(config.workers.globalTopLevelLimit, 1, 8, "workers.globalTopLevelLimit");
  integerIn(config.workers.globalActiveCodexLimit, 1, 12, "workers.globalActiveCodexLimit");
  integerIn(config.workers.idleTtlMinutes, 0, 60, "workers.idleTtlMinutes");
  return config;
}

export function loadOrchestrationConfig(workspaceRoot, options = {}) {
  const userPath = getUserConfigPath(options);
  const projectPath = getProjectConfigPath(workspaceRoot);
  const config = mergeObjects(
    mergeObjects(DEFAULT_ORCHESTRATION_CONFIG, readConfig(userPath)),
    readConfig(projectPath)
  );
  return validateOrchestrationConfig(config);
}

function writeJsonAtomic(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(tempPath, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(tempPath, filePath);
}

export function patchUserOrchestrationConfig(patch, options = {}) {
  const filePath = getUserConfigPath(options);
  const existing = readConfig(filePath);
  const mergedUser = mergeObjects(existing, patch);
  const effective = validateOrchestrationConfig(mergeObjects(DEFAULT_ORCHESTRATION_CONFIG, mergedUser));
  writeJsonAtomic(filePath, mergedUser);
  return effective;
}
''')

write("plugins/codex/scripts/orchestration/budget-policy.mjs", r'''
const ENVELOPES = [
  { min: 0, max: 2, maxTopLevelRoots: 1, workerParallelism: 1, maxNativeChildrenPerRoot: 0, timeoutMinutes: 15 },
  { min: 3, max: 4, maxTopLevelRoots: 2, workerParallelism: 2, maxNativeChildrenPerRoot: 1, timeoutMinutes: 15 },
  { min: 5, max: 7, maxTopLevelRoots: 4, workerParallelism: 3, maxNativeChildrenPerRoot: 2, timeoutMinutes: 30 },
  { min: 8, max: 10, maxTopLevelRoots: 6, workerParallelism: 3, maxNativeChildrenPerRoot: 3, timeoutMinutes: 60 }
];

export function deriveBudgetEnvelope(complexityScore, config) {
  if (!Number.isInteger(complexityScore) || complexityScore < 0 || complexityScore > 10) {
    throw new Error("complexityScore must be an integer between 0 and 10.");
  }
  const base = ENVELOPES.find((entry) => complexityScore >= entry.min && complexityScore <= entry.max);
  return {
    maxTopLevelRoots: Math.min(base.maxTopLevelRoots, config.workers.globalTopLevelLimit),
    workerParallelism: Math.min(base.workerParallelism, config.workers.workspacePoolSize),
    maxNativeChildrenPerRoot: base.maxNativeChildrenPerRoot,
    timeoutMinutes: base.timeoutMinutes,
    maxRetries: 1,
    maxReplans: 0,
    maxAdditionalPackages: 0,
    maxConcurrentSolUltra: 2
  };
}

export function validatePlanAgainstBudget(plan, envelope) {
  if (plan.packages.length > envelope.maxTopLevelRoots) {
    throw new Error(`Plan contains ${plan.packages.length} packages but the budget permits ${envelope.maxTopLevelRoots}.`);
  }
  for (const pkg of plan.packages) {
    if (pkg.nativeSubagents.maxChildren > envelope.maxNativeChildrenPerRoot) {
      throw new Error(`${pkg.id}.nativeSubagents.maxChildren exceeds the budget limit ${envelope.maxNativeChildrenPerRoot}.`);
    }
  }
}
''')

write("plugins/codex/scripts/orchestration/plan-contract.mjs", r'''
import {
  ORCHESTRATION_PLAN_VERSION,
  ROLE_CLASSES,
  VALID_EFFORTS
} from "./constants.mjs";
import { deriveBudgetEnvelope, validatePlanAgainstBudget } from "./budget-policy.mjs";

function object(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object.`);
  return value;
}
function text(value, label) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label} must be a non-empty string.`);
  return value.trim();
}
function stringArray(value, label) {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array.`);
  return value.map((entry, index) => text(entry, `${label}[${index}]`));
}
function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFreeze(child);
  }
  return value;
}

function normalizePackage(input, index) {
  const pkg = object(input, `packages[${index}]`);
  const id = text(pkg.id, `packages[${index}].id`);
  const role = object(pkg.role, `${id}.role`);
  const roleClass = text(role.class, `${id}.role.class`);
  if (!ROLE_CLASSES.has(roleClass)) throw new Error(`${id}.role.class is not supported: ${roleClass}`);
  if (pkg.access !== "read-only") throw new Error("Phase 1 only supports read-only packages.");
  if (object(pkg.workspace, `${id}.workspace`).mode !== "shared") throw new Error(`${id}.workspace.mode must be shared in Phase 1.`);
  const model = object(pkg.model, `${id}.model`);
  const effort = text(model.effort, `${id}.model.effort`).toLowerCase();
  if (!VALID_EFFORTS.has(effort)) throw new Error(`${id}.model.effort is not supported: ${effort}`);
  const nativeSubagents = object(pkg.nativeSubagents, `${id}.nativeSubagents`);
  const policy = text(nativeSubagents.policy, `${id}.nativeSubagents.policy`);
  if (!new Set(["allowed", "forbidden", "required"]).has(policy)) throw new Error(`${id}.nativeSubagents.policy is invalid.`);
  if (!Number.isInteger(nativeSubagents.maxChildren) || nativeSubagents.maxChildren < 0) {
    throw new Error(`${id}.nativeSubagents.maxChildren must be a non-negative integer.`);
  }
  if (pkg.optional !== undefined && typeof pkg.optional !== "boolean") {
    throw new Error(`${id}.optional must be a boolean.`);
  }
  return {
    id,
    title: text(pkg.title, `${id}.title`),
    role: { class: roleClass, label: text(role.label, `${id}.role.label`) },
    objective: text(pkg.objective, `${id}.objective`),
    dependencies: stringArray(pkg.dependencies ?? [], `${id}.dependencies`),
    optional: pkg.optional ?? false,
    access: "read-only",
    workspace: { mode: "shared" },
    model: { name: text(model.name, `${id}.model.name`), effort },
    nativeSubagents: { policy, maxChildren: nativeSubagents.maxChildren },
    acceptanceCriteria: stringArray(pkg.acceptanceCriteria, `${id}.acceptanceCriteria`),
    expectedOutputs: stringArray(pkg.expectedOutputs, `${id}.expectedOutputs`)
  };
}

function validateGraph(packages) {
  const byId = new Map(packages.map((pkg) => [pkg.id, pkg]));
  if (byId.size !== packages.length) throw new Error("Package IDs must be unique.");
  for (const pkg of packages) {
    for (const dependency of pkg.dependencies) {
      if (!byId.has(dependency)) throw new Error(`${pkg.id} depends on unknown package ${dependency}.`);
      if (dependency === pkg.id) throw new Error(`${pkg.id} cannot depend on itself.`);
    }
  }
  const visiting = new Set();
  const visited = new Set();
  const stack = [];
  const visit = (id) => {
    if (visiting.has(id)) {
      const start = stack.indexOf(id);
      throw new Error(`Package dependency cycle: ${[...stack.slice(start), id].join(" -> ")}`);
    }
    if (visited.has(id)) return;
    visiting.add(id); stack.push(id);
    for (const dep of byId.get(id).dependencies) visit(dep);
    stack.pop(); visiting.delete(id); visited.add(id);
  };
  for (const pkg of packages) visit(pkg.id);
}

export function normalizeOrchestrationPlan(input, context = {}) {
  const plan = object(input, "plan");
  if (plan.version !== ORCHESTRATION_PLAN_VERSION) throw new Error(`plan.version must be ${ORCHESTRATION_PLAN_VERSION}.`);
  if (!Number.isInteger(plan.complexityScore) || plan.complexityScore < 0 || plan.complexityScore > 10) {
    throw new Error("complexityScore must be an integer between 0 and 10.");
  }
  const packages = (Array.isArray(plan.packages) ? plan.packages : (() => { throw new Error("packages must be an array."); })())
    .map(normalizePackage);
  if (packages.length === 0) throw new Error("At least one package is required.");
  validateGraph(packages);
  const normalized = {
    version: ORCHESTRATION_PLAN_VERSION,
    objective: text(plan.objective, "objective"),
    complexityScore: plan.complexityScore,
    requestedBy: {
      explicit: Boolean(object(plan.requestedBy, "requestedBy").explicit),
      sessionId: plan.requestedBy.sessionId == null ? null : text(plan.requestedBy.sessionId, "requestedBy.sessionId")
    },
    packages
  };
  const budget = deriveBudgetEnvelope(normalized.complexityScore, context.config);
  validatePlanAgainstBudget(normalized, budget);
  return deepFreeze({ ...normalized, budget });
}
''')

write("plugins/codex/scripts/orchestration/result-contract.mjs", r'''
import fs from "node:fs";

const SCHEMA_URL = new URL("./schemas/package-result.schema.json", import.meta.url);
let cachedSchema = null;

function arrayOfStrings(value, label) {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) throw new Error(`${label} must be an array of strings.`);
  return value.map((entry) => entry.trim()).filter(Boolean);
}

export function readPackageResultSchema() {
  cachedSchema ??= JSON.parse(fs.readFileSync(SCHEMA_URL, "utf8"));
  return cachedSchema;
}

export function validatePackageResult(input, packageId) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Package result must be an object.");
  if (input.packageId !== packageId) throw new Error(`Package result ID ${input.packageId ?? "<missing>"} does not match ${packageId}.`);
  if (!new Set(["completed", "partial", "blocked", "failed"]).has(input.status)) throw new Error(`${packageId}.status is invalid.`);
  if (typeof input.summary !== "string" || !input.summary.trim()) throw new Error(`${packageId}.summary is required.`);
  if (!Array.isArray(input.changedFiles) || input.changedFiles.length !== 0) throw new Error("Phase 1 package results must contain an empty changedFiles array.");
  if (typeof input.confidence !== "number" || input.confidence < 0 || input.confidence > 1) throw new Error(`${packageId}.confidence must be between 0 and 1.`);
  if (!input.verification || typeof input.verification.passed !== "boolean") throw new Error(`${packageId}.verification.passed must be a boolean.`);
  const evidence = Array.isArray(input.evidence) ? input.evidence.map((entry, index) => {
    if (!entry || typeof entry !== "object" || !new Set(["file", "command", "observation"]).has(entry.type)) {
      throw new Error(`${packageId}.evidence[${index}] is invalid.`);
    }
    return {
      type: entry.type,
      description: String(entry.description ?? "").trim(),
      path: entry.path ?? null,
      lineStart: entry.lineStart ?? null,
      lineEnd: entry.lineEnd ?? null,
      command: entry.command ?? null,
      exitCode: entry.exitCode ?? null
    };
  }) : (() => { throw new Error(`${packageId}.evidence must be an array.`); })();
  return {
    packageId,
    status: input.status,
    summary: input.summary.trim(),
    claims: arrayOfStrings(input.claims, `${packageId}.claims`),
    evidence,
    changedFiles: [],
    verification: { passed: input.verification.passed, commands: arrayOfStrings(input.verification.commands, `${packageId}.verification.commands`) },
    residualRisks: arrayOfStrings(input.residualRisks, `${packageId}.residualRisks`),
    confidence: input.confidence,
    followUpRequests: arrayOfStrings(input.followUpRequests, `${packageId}.followUpRequests`)
  };
}

export function buildOrchestrationResult(state) {
  return {
    orchestrationId: state.id,
    status: state.status,
    objective: state.plan.objective,
    planRevision: state.planRevision,
    packages: state.plan.packages.map((pkg) => {
      const current = state.packages[pkg.id];
      return {
        id: pkg.id,
        title: pkg.title,
        role: pkg.role,
        model: pkg.model,
        status: current.status,
        result: current.result ?? null,
        threadId: current.threadId ?? null,
        nativeChildThreadIds: current.nativeChildThreadIds ?? [],
        nativeSubagentDegraded: Boolean(current.nativeSubagentDegraded),
        nativeSubagentDegradationReason: current.nativeSubagentDegradationReason ?? null
      };
    }),
    omissions: state.omissions ?? [],
    remainingWork: state.remainingWork ?? []
  };
}
''')

write("plugins/codex/scripts/orchestration/scheduler.mjs", r'''
import { PACKAGE_TERMINAL_STATUSES } from "./constants.mjs";

function copy(state) {
  return { packages: Object.fromEntries(Object.entries(state.packages).map(([id, value]) => [id, { ...value, dependencies: [...value.dependencies] }])) };
}
function transition(state, id, allowed, patch) {
  const current = state.packages[id];
  if (!current) throw new Error(`Unknown package ${id}.`);
  if (!allowed.includes(current.status)) throw new Error(`Cannot transition ${id} from ${current.status}.`);
  const next = copy(state); next.packages[id] = { ...next.packages[id], ...patch }; return next;
}

export function createSchedulerState(plan) {
  return {
    packages: Object.fromEntries(plan.packages.map((pkg) => [pkg.id, {
      id: pkg.id, status: "planned", dependencies: [...pkg.dependencies], optional: pkg.optional, attempt: 0, error: null
    }]))
  };
}
export function getReadyPackageIds(state) {
  return Object.values(state.packages).filter((pkg) => pkg.status === "planned" && pkg.dependencies.every((id) => {
    const status = state.packages[id].status; return status === "completed" || status === "partial";
  })).map((pkg) => pkg.id);
}
export function markPackageReady(state, id) { return transition(state, id, ["planned"], { status: "ready" }); }
export function markPackageRunning(state, id, attempt) { return transition(state, id, ["ready"], { status: "running", attempt }); }
export function markPackageCompleted(state, id, resultStatus = "completed") {
  if (!new Set(["completed", "partial", "blocked", "failed"]).has(resultStatus)) throw new Error(`Invalid result status ${resultStatus}.`);
  return transition(state, id, ["running"], { status: resultStatus });
}
export function markPackageFailed(state, id, error) { return transition(state, id, ["running", "ready", "planned"], { status: "failed", error: String(error ?? "failed") }); }
export function markPackageCancelled(state, id) {
  const current = state.packages[id];
  if (PACKAGE_TERMINAL_STATUSES.has(current.status)) return state;
  return transition(state, id, ["planned", "ready", "running", "cancelling"], { status: "cancelled" });
}
export function propagateBlockedPackages(state) {
  let next = state; let changed = true;
  while (changed) {
    changed = false;
    for (const pkg of Object.values(next.packages)) {
      if (!["planned", "ready"].includes(pkg.status)) continue;
      if (pkg.dependencies.some((id) => ["failed", "blocked", "cancelled"].includes(next.packages[id].status))) {
        next = transition(next, pkg.id, [pkg.status], { status: "blocked", error: "Required dependency did not complete." });
        changed = true;
      }
    }
  }
  return next;
}
export function deriveOrchestrationStatus(state) {
  const values = Object.values(state.packages);
  if (values.some((pkg) => !PACKAGE_TERMINAL_STATUSES.has(pkg.status))) return "running";
  const required = values.filter((pkg) => !pkg.optional);
  const usable = values.filter((pkg) => ["completed", "partial"].includes(pkg.status));
  if (required.every((pkg) => ["completed", "partial"].includes(pkg.status))) {
    return values.some((pkg) => pkg.optional && !["completed", "partial"].includes(pkg.status)) ? "completed-with-omissions" : "completed";
  }
  if (usable.length > 0) return "degraded";
  if (required.some((pkg) => pkg.status === "blocked") && required.every((pkg) => ["blocked", "cancelled"].includes(pkg.status))) return "blocked";
  return values.every((pkg) => pkg.status === "cancelled") ? "cancelled" : "failed";
}
''')

write("plugins/codex/scripts/orchestration/file-lock.mjs", r'''
import fs from "node:fs";
import path from "node:path";

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function processAlive(pid) {
  try { process.kill(pid, 0); return true; } catch (error) { return error?.code !== "ESRCH"; }
}
function removeStale(lockFile, staleMs) {
  try {
    const stat = fs.statSync(lockFile);
    const pid = Number.parseInt(fs.readFileSync(lockFile, "utf8").split(":", 1)[0], 10);
    if (Number.isFinite(pid) && processAlive(pid)) return false;
    if (!Number.isFinite(pid) && Date.now() - stat.mtimeMs <= staleMs) return false;
    fs.unlinkSync(lockFile); return true;
  } catch (error) { if (error?.code === "ENOENT") return true; throw error; }
}
export async function withFileLock(lockFile, options = {}, action) {
  fs.mkdirSync(path.dirname(lockFile), { recursive: true });
  const deadline = Date.now() + (options.timeoutMs ?? 5000);
  const staleMs = options.staleMs ?? 30_000;
  const token = `${process.pid}:${Date.now()}:${Math.random()}`;
  let fd = null;
  while (fd === null) {
    try { fd = fs.openSync(lockFile, "wx", 0o600); fs.writeFileSync(fd, token, "utf8"); }
    catch (error) {
      if (error?.code !== "EEXIST") throw error;
      if (removeStale(lockFile, staleMs)) continue;
      if (Date.now() >= deadline) throw new Error(`Timed out waiting for lock at ${lockFile}.`);
      await sleep(options.retryMs ?? 25);
    }
  }
  try { return await action(); }
  finally {
    try { fs.closeSync(fd); } finally {
      try { if (fs.readFileSync(lockFile, "utf8") === token) fs.unlinkSync(lockFile); }
      catch (error) { if (error?.code !== "ENOENT") throw error; }
    }
  }
}
''')

write("plugins/codex/scripts/orchestration/state-store.mjs", r'''
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { resolveWorkspaceRoot } from "../lib/workspace.mjs";
import { ORCHESTRATION_STATE_VERSION, ORCHESTRATION_TERMINAL_STATUSES } from "./constants.mjs";
import { withFileLock } from "./file-lock.mjs";

function workspaceKey(workspaceRoot) {
  const canonical = (() => { try { return fs.realpathSync.native(workspaceRoot); } catch { return path.resolve(workspaceRoot); } })();
  const slug = (path.basename(workspaceRoot) || "workspace").replace(/[^A-Za-z0-9._-]+/g, "-");
  return `${slug}-${crypto.createHash("sha256").update(canonical).digest("hex").slice(0, 16)}`;
}
export function resolveOrchestrationWorkspaceDir(cwd, options = {}) {
  const root = options.pluginDataDir ?? process.env.CLAUDE_PLUGIN_DATA;
  const base = root ? path.join(path.resolve(root), "orchestrations") : path.join(os.tmpdir(), "codex-companion", "orchestrations");
  return path.join(base, workspaceKey(resolveWorkspaceRoot(cwd)));
}
function orchDir(cwd, id, options) { return path.join(resolveOrchestrationWorkspaceDir(cwd, options), id); }
function stateFile(cwd, id, options) { return path.join(orchDir(cwd, id, options), "orchestration.json"); }
function writeAtomic(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temp, filePath);
}
export function generateOrchestrationId(now = Date.now()) { return `orch-${now.toString(36)}-${crypto.randomBytes(3).toString("hex")}`; }
export function loadOrchestrationState(cwd, id, options = {}) {
  const file = stateFile(cwd, id, options);
  if (!fs.existsSync(file)) throw new Error(`No orchestration found for "${id}". Run /codex:status.`);
  return JSON.parse(fs.readFileSync(file, "utf8"));
}
export async function createOrchestrationState(cwd, plan, context = {}, options = {}) {
  const workspaceRoot = resolveWorkspaceRoot(cwd);
  const id = context.id ?? generateOrchestrationId();
  const now = new Date().toISOString();
  const state = {
    version: ORCHESTRATION_STATE_VERSION, id, workspaceRoot, claudeSessionId: context.claudeSessionId ?? null,
    status: "queued", planRevision: 1, plan, createdAt: now, updatedAt: now, startedAt: null, completedAt: null,
    controller: context.controller ?? null, packages: Object.fromEntries(plan.packages.map((pkg) => [pkg.id, {
      id: pkg.id, status: "planned", attempt: 0, workerId: null, pid: null, threadId: null, turnId: null,
      nativeChildThreadIds: [], nativeSubagentDegraded: false, nativeSubagentDegradationReason: null,
      result: null, error: null, startedAt: null, completedAt: null
    }])), omissions: [], remainingWork: []
  };
  writeAtomic(stateFile(cwd, id, options), state);
  appendOrchestrationEvent(cwd, id, { type: "orchestration-created", message: plan.objective }, options);
  return state;
}
export async function updateOrchestrationState(cwd, id, mutate, options = {}) {
  const directory = orchDir(cwd, id, options);
  return withFileLock(path.join(directory, "state.lock"), {}, async () => {
    const state = loadOrchestrationState(cwd, id, options);
    const result = await mutate(state) ?? state;
    result.updatedAt = new Date().toISOString();
    writeAtomic(stateFile(cwd, id, options), result);
    return result;
  });
}
export function appendOrchestrationEvent(cwd, id, event, options = {}) {
  const file = path.join(orchDir(cwd, id, options), "events.jsonl");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.appendFileSync(file, `${JSON.stringify({ timestamp: new Date().toISOString(), orchestrationId: id, packageId: event.packageId ?? null, type: event.type, phase: event.phase ?? null, message: event.message ?? "", data: event.data ?? null })}\n`, { encoding: "utf8", mode: 0o600 });
}
export function writePackageResult(cwd, id, packageId, result, options = {}) {
  const file = path.join(orchDir(cwd, id, options), "results", `${packageId}.json`); writeAtomic(file, result); return file;
}
export function readPackageResult(cwd, id, packageId, options = {}) {
  const file = path.join(orchDir(cwd, id, options), "results", `${packageId}.json`); return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : null;
}
export function listOrchestrations(cwd, options = {}) {
  const directory = resolveOrchestrationWorkspaceDir(cwd, options);
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).filter((entry) => entry.isDirectory() && entry.name.startsWith("orch-"))
    .map((entry) => { try { return loadOrchestrationState(cwd, entry.name, options); } catch { return null; } }).filter(Boolean)
    .sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)));
}
export function resolveOrchestrationReference(cwd, reference, options = {}) {
  const states = listOrchestrations(cwd, options);
  const exact = states.find((state) => state.id === reference);
  if (exact) return { kind: "orchestration", orchestrationId: exact.id };
  const orchMatches = states.filter((state) => state.id.startsWith(reference));
  if (orchMatches.length === 1) return { kind: "orchestration", orchestrationId: orchMatches[0].id };
  const packageMatches = [];
  for (const state of states) for (const packageId of Object.keys(state.packages)) if (packageId === reference || packageId.startsWith(reference)) packageMatches.push({ kind: "package", orchestrationId: state.id, packageId });
  if (packageMatches.length === 1) return packageMatches[0];
  if (orchMatches.length > 1 || packageMatches.length > 1) throw new Error(`Reference "${reference}" is ambiguous. Run /codex:status.`);
  throw new Error(`No orchestration or package found for "${reference}". Run /codex:status.`);
}
export function isTerminalState(state) { return ORCHESTRATION_TERMINAL_STATUSES.has(state.status); }
''')

write("plugins/codex/scripts/orchestration/package-prompt.mjs", r'''
function xml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }
export function buildReadOnlyPackagePrompt(packageSpec, context = {}) {
  return [
    `<orchestration_package_id>${xml(packageSpec.id)}</orchestration_package_id>`,
    `<role>${xml(packageSpec.role.class)}: ${xml(packageSpec.role.label)}</role>`,
    `<objective>${xml(packageSpec.objective)}</objective>`,
    `<access_policy>Read-only. Do not modify files, create commits, change credentials, push, publish, deploy, or mutate remote systems.</access_policy>`,
    `<dependencies>${xml(JSON.stringify(context.dependencyResults ?? []))}</dependencies>`,
    `<acceptance_criteria>${xml(JSON.stringify(packageSpec.acceptanceCriteria))}</acceptance_criteria>`,
    `<native_subagent_policy>${xml(`${packageSpec.nativeSubagents.policy}; maximum ${packageSpec.nativeSubagents.maxChildren} child agents`)}</native_subagent_policy>`,
    `<verification>Run only non-destructive checks needed to support claims. Record exact commands and exit codes.</verification>`,
    `<output_contract>Return exactly one JSON object matching the supplied package-result schema. changedFiles must be an empty array.</output_contract>`
  ].join("\n\n");
}
''')

# JSON schemas
write("plugins/codex/scripts/orchestration/schemas/config.schema.json", json.dumps({
  "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
  "properties": {"auto": {"type": "object"}, "workers": {"type": "object"}}
}, indent=2))
write("plugins/codex/scripts/orchestration/schemas/orchestration-plan.schema.json", json.dumps({
  "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
  "required": ["version", "objective", "complexityScore", "requestedBy", "packages"]
}, indent=2))
write("plugins/codex/scripts/orchestration/schemas/package-result.schema.json", json.dumps({
  "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False,
  "required": ["packageId", "status", "summary", "claims", "evidence", "changedFiles", "verification", "residualRisks", "confidence", "followUpRequests"],
  "properties": {
    "packageId": {"type": "string"}, "status": {"enum": ["completed", "partial", "blocked", "failed"]},
    "summary": {"type": "string"}, "claims": {"type": "array", "items": {"type": "string"}},
    "evidence": {"type": "array"}, "changedFiles": {"type": "array", "maxItems": 0},
    "verification": {"type": "object"}, "residualRisks": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "followUpRequests": {"type": "array", "items": {"type": "string"}}
  }
}, indent=2))
write("plugins/codex/scripts/orchestration/schemas/orchestration-result.schema.json", json.dumps({
  "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
  "required": ["orchestrationId", "status", "objective", "packages"]
}, indent=2))

print("Phase 1 core contracts and state modules generated.")
