import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PLUGIN_ROOT = path.join(ROOT, "plugins", "codex");
const read = (relativePath) => fs.readFileSync(path.join(PLUGIN_ROOT, relativePath), "utf8");

test("rescue uses version-neutral Codex prompting guidance", () => {
  const agent = read("agents/codex-rescue.md");
  const runtime = read("skills/codex-cli-runtime/SKILL.md");
  const skill = read("skills/codex-prompting/SKILL.md");

  assert.match(agent, /^\s*- codex-prompting\s*$/m);
  assert.doesNotMatch(agent, /^\s*- gpt-5-4-prompting\s*$/m);
  assert.match(agent, /Sol > Terra > Luna/);
  assert.match(agent, /reasoning effort is a separate/i);
  assert.match(runtime, /use the `codex-prompting` skill/i);
  assert.match(runtime, /current Codex model catalog/i);
  assert.match(skill, /Favor lean, outcome-first prompts/i);
  assert.match(skill, /Define autonomy and approval boundaries/i);
  assert.match(skill, /Sol > Terra > Luna/);
  assert.match(skill, /Do not treat a higher effort on a lower tier as reversing/i);
});

test("the GPT-5.4 skill remains only as a compatibility alias", () => {
  const legacy = read("skills/gpt-5-4-prompting/SKILL.md");
  assert.match(legacy, /Deprecated compatibility alias/i);
  assert.match(legacy, /codex-prompting/);
});

test("new prompting references are generation-neutral", () => {
  const files = [
    "skills/codex-prompting/SKILL.md",
    "skills/codex-prompting/references/prompt-blocks.md",
    "skills/codex-prompting/references/codex-prompt-recipes.md",
    "skills/codex-prompting/references/codex-prompt-antipatterns.md"
  ];
  for (const file of files) {
    assert.doesNotMatch(read(file), /GPT-5\.4/);
  }
});

test("README presents GPT-5.6 as the primary model family", () => {
  const readme = fs.readFileSync(path.join(ROOT, "README.md"), "utf8");
  assert.match(readme, /gpt-5\.6-sol/);
  assert.match(readme, /gpt-5\.6-terra/);
  assert.match(readme, /gpt-5\.6-luna/);
  assert.match(readme, /Sol > Terra > Luna/);
  assert.match(readme, /reasoning effort is a separate/i);
});
