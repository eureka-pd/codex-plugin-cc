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
    '      return { ...finalPayload, workerId, pid: child.pid };',
    '      return { ...(finalPayload ?? {}), workerId, pid: child.pid };',
)

print("Phase 1 worker lifecycle, cancellation, and type-check fixes applied.")
