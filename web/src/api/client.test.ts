import { describe, expect, it } from "vitest";
import { getHealth, getStatus } from "./client";

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

describe("getStatus", () => {
  it("returns the pre-approval flags", async () => {
    const body = { setup_required: false, setup_step: "done", preapproval: true, banner: "Pre-approval mode — synthetic data only." };
    const fake = (async () => new Response(JSON.stringify(body))) as typeof fetch;
    await expect(getStatus(fake)).resolves.toEqual(body);
  });
});
