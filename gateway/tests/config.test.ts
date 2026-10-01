import { describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";

const base = {
  GITHUB_WEBHOOK_SECRET: "w",
  CORE_URL: "http://localhost:8000",
  CORE_API_TOKEN: "t",
  GITHUB_TOKEN: "g",
};

describe("loadConfig", () => {
  it("reads required values and applies defaults", () => {
    expect(loadConfig(base)).toEqual({
      githubWebhookSecret: "w",
      coreUrl: "http://localhost:8000",
      coreApiToken: "t",
      githubToken: "g",
      triggerLabel: "ai-fix",
      port: 3000,
    });
  });

  it("accepts overrides and strips a trailing slash from CORE_URL", () => {
    const c = loadConfig({ ...base, CORE_URL: "http://core/", TRIGGER_LABEL: "bot-fix", PORT: "8080" });
    expect(c.coreUrl).toBe("http://core");
    expect(c.triggerLabel).toBe("bot-fix");
    expect(c.port).toBe(8080);
  });

  it("names every missing variable in one error", () => {
    expect(() => loadConfig({})).toThrow(/GITHUB_WEBHOOK_SECRET.*CORE_URL.*CORE_API_TOKEN.*GITHUB_TOKEN/s);
  });

  it("rejects an empty secret and a bad port", () => {
    expect(() => loadConfig({ ...base, GITHUB_WEBHOOK_SECRET: "" })).toThrow(/GITHUB_WEBHOOK_SECRET/);
    expect(() => loadConfig({ ...base, PORT: "abc" })).toThrow(/PORT/);
  });
});
