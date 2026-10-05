import { describe, expect, it } from "vitest";
import { getHealth } from "./client";

describe("getHealth", () => {
  it("returns the server status", async () => {
    const fake = (async () => new Response(JSON.stringify({ status: "ok" }))) as typeof fetch;
    await expect(getHealth(fake)).resolves.toEqual({ status: "ok" });
  });

  it("throws on an HTTP error", async () => {
    const fake = (async () => new Response("", { status: 500 })) as typeof fetch;
    await expect(getHealth(fake)).rejects.toThrow("HTTP 500");
  });
});
