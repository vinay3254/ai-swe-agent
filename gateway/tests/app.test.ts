import { describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";
import { CoreError, CoreUnavailable } from "../src/core-client.js";
import { FakeCore, FakeNotifier, config, labeledPayload, sign } from "./helpers.js";

function setup() {
  const core = new FakeCore();
  const notifier = new FakeNotifier();
  const app = createApp({ config, core, notifier });
  async function deliver(
    payload: unknown,
    opts: { event?: string; delivery?: string | null; signature?: string | null; raw?: string } = {},
  ) {
    const body = opts.raw ?? JSON.stringify(payload);
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (opts.event !== undefined) headers["x-github-event"] = opts.event;
    else headers["x-github-event"] = "issues";
    if (opts.delivery !== null) headers["x-github-delivery"] = opts.delivery ?? "d-1";
    if (opts.signature !== null) headers["x-hub-signature-256"] = opts.signature ?? sign(body);
    return app.request("/webhook", { method: "POST", headers, body });
  }
  return { core, notifier, app, deliver };
}

describe("POST /webhook", () => {
  it("starts a job when the trigger label is added and posts one status comment", async () => {
    const { core, notifier, deliver } = setup();

    const res = await deliver(labeledPayload());

    expect(res.status).toBe(202);
    expect(core.calls).toEqual([
      { issue_url: "https://github.com/octo/app/issues/7", job_key: "issue-555:d-1" },
    ]);
    expect(notifier.posted).toHaveLength(1);
    expect(notifier.posted[0]).toMatchObject({ repo: "octo/app", issue: 7, key: "issue-555:d-1" });
    expect(await res.json()).toEqual({ status: "accepted", job_key: "issue-555:d-1" });
  });

  it("rejects a bad or missing signature before touching core", async () => {
    const { core, deliver } = setup();

    expect((await deliver(labeledPayload(), { signature: sign("{}", "wrong") })).status).toBe(401);
    expect((await deliver(labeledPayload(), { signature: null })).status).toBe(401);
    expect(core.calls).toEqual([]);
  });

  it("answers ping without starting anything", async () => {
    const { core, deliver } = setup();

    const res = await deliver({ zen: "x" }, { event: "ping" });

    expect(res.status).toBe(200);
    expect(core.calls).toEqual([]);
  });

  it("ignores other labels, other actions, other events and pull requests", async () => {
    const { core, notifier, deliver } = setup();

    for (const [payload, event] of [
      [labeledPayload({ label: { name: "bug" } }), "issues"],
      [labeledPayload({ action: "opened" }), "issues"],
      [labeledPayload(), "push"],
      [labeledPayload({ issue: { id: 1, number: 2, html_url: "u", pull_request: {} } }), "issues"],
    ] as const) {
      const res = await deliver(payload, { event });
      expect(res.status).toBe(200);
      expect((await res.json()).status).toBe("ignored");
    }
    expect(core.calls).toEqual([]);
    expect(notifier.posted).toEqual([]);
  });

  it("honours a custom trigger label", async () => {
    const core = new FakeCore();
    const app = createApp({ config: { ...config, triggerLabel: "bot-fix" }, core, notifier: new FakeNotifier() });
    const body = JSON.stringify(labeledPayload({ label: { name: "bot-fix" } }));

    const res = await app.request("/webhook", {
      method: "POST",
      headers: { "x-github-event": "issues", "x-github-delivery": "d", "x-hub-signature-256": sign(body) },
      body,
    });

    expect(res.status).toBe(202);
    expect(core.calls).toHaveLength(1);
  });

  it("rejects malformed JSON and malformed issue payloads with 400", async () => {
    const { deliver } = setup();

    expect((await deliver(null, { raw: "{not json" })).status).toBe(400);
    expect((await deliver({ action: "labeled", label: { name: "ai-fix" } })).status).toBe(400);
  });

  it("requires a delivery id because the job key depends on it", async () => {
    const { core, deliver } = setup();

    const res = await deliver(labeledPayload(), { delivery: null });

    expect(res.status).toBe(400);
    expect(core.calls).toEqual([]);
  });

  it("the same delivery always produces the same job key (replay is idempotent)", async () => {
    const { core, deliver } = setup();

    await deliver(labeledPayload(), { delivery: "same" });
    await deliver(labeledPayload(), { delivery: "same" });
    await deliver(labeledPayload(), { delivery: "other" });

    expect(core.calls.map((c) => c.job_key)).toEqual(["issue-555:same", "issue-555:same", "issue-555:other"]);
  });

  it("stays quiet when core refuses the repo (not allowlisted)", async () => {
    const { core, notifier, deliver } = setup();
    core.result = { kind: "forbidden", detail: "octo/app is not in the repo allowlist" };

    const res = await deliver(labeledPayload());

    expect(res.status).toBe(200);
    expect((await res.json()).status).toBe("ignored");
    expect(notifier.posted).toEqual([]);
  });

  it("returns 502 when core is down so GitHub marks the delivery failed and can redeliver", async () => {
    const { core, notifier, deliver } = setup();
    core.result = new CoreUnavailable("connection refused");

    const res = await deliver(labeledPayload());

    expect(res.status).toBe(502);
    expect(notifier.posted).toEqual([]);
  });

  it("returns 500 for a core contract error such as a bad token", async () => {
    const { core, deliver } = setup();
    core.result = new CoreError("core rejected our token (401)");

    expect((await deliver(labeledPayload())).status).toBe(500);
  });

  it("still accepts the job when the status comment cannot be posted", async () => {
    const { notifier, deliver } = setup();
    notifier.error = new Error("GitHub 502");

    const res = await deliver(labeledPayload());

    expect(res.status).toBe(202);
  });

  it("does not echo secrets or stack traces in error bodies", async () => {
    const { core, deliver } = setup();
    core.result = new CoreUnavailable("secret-token-leak");

    const text = await (await deliver(labeledPayload())).text();

    expect(text).not.toContain("secret-token-leak");
  });
});

describe("GET /healthz", () => {
  it("needs no signature", async () => {
    const res = await setup().app.request("/healthz");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
  });
});
