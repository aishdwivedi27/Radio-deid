// @vitest-environment node
// Proves the ESLint file-size rules fail on a planted violation (CLAUDE.md, File size).
import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

async function lint(code: string): Promise<string[]> {
  const eslint = new ESLint({ cwd: process.cwd() });
  const [result] = await eslint.lintText(code, { filePath: "src/planted.ts" });
  return result.messages.map((m) => m.ruleId ?? "");
}

describe("ESLint size rules", () => {
  it("fails a file over 1,000 lines", async () => {
    const code = Array.from({ length: 1001 }, (_, i) => `export const v${i} = ${i};`).join("\n");
    expect(await lint(code)).toContain("max-lines");
  });

  it("fails a function over 80 lines", async () => {
    const body = Array.from({ length: 81 }, (_, i) => `  total += ${i};`).join("\n");
    const code = `export function big(): number {\n  let total = 0;\n${body}\n  return total;\n}\n`;
    expect(await lint(code)).toContain("max-lines-per-function");
  });

  it("passes a small file", async () => {
    expect(await lint("export const ok = 1;\n")).toEqual([]);
  });
});
