import { Hono } from "hono";
import type { Config } from "./config.js";
import { CoreError, CoreUnavailable, type CoreClient } from "./core-client.js";
import { BadPayload, decide } from "./events.js";
import type { Notifier } from "./notifier.js";
import { verifySignature } from "./signature.js";

export interface Deps {
  config: Config;
  core: CoreClient;
  notifier: Notifier;
  log?: (message: string, detail?: unknown) => void;
}

export function createApp({ config, core, notifier, log = console.error }: Deps): Hono {
  const app = new Hono();

  app.get("/healthz", (c) => c.json({ status: "ok" }));

  app.post("/webhook", async (c) => {
    const raw = await c.req.text();
    if (!verifySignature(config.githubWebhookSecret, raw, c.req.header("x-hub-signature-256"))) {
      return c.json({ error: "invalid signature" }, 401);
    }
    const event = c.req.header("x-github-event");
    if (!event) return c.json({ error: "missing X-GitHub-Event" }, 400);
    if (event === "ping") return c.json({ status: "pong" });

    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch {
      return c.json({ error: "body is not JSON" }, 400);
    }

    let decision;
    try {
      decision = decide(event, payload, config.triggerLabel);
    } catch (err) {
      if (err instanceof BadPayload) return c.json({ error: err.message }, 400);
      throw err;
    }
    if (decision.kind === "ignore") return c.json({ status: "ignored", reason: decision.reason });

    // GitHub reuses the delivery id when it redelivers, so replays map to the same job.
    const delivery = c.req.header("x-github-delivery");
    if (!delivery) return c.json({ error: "missing X-GitHub-Delivery" }, 400);
    const jobKey = `issue-${decision.issueId}:${delivery}`;

    let result;
    try {
      result = await core.createJob({ issue_url: decision.issueUrl, job_key: jobKey });
    } catch (err) {
      log("core call failed", err);
      if (err instanceof CoreUnavailable) return c.json({ error: "core unavailable" }, 502);
      if (err instanceof CoreError) return c.json({ error: "core contract error" }, 500);
      throw err;
    }
    if (result.kind !== "accepted") {
      return c.json({ status: "ignored", reason: `core declined the job (${result.kind})` });
    }

    try {
      await notifier.postStatus(
        decision.repo,
        decision.issueNumber,
        jobKey,
        `The AI agent picked up this issue (label \`${config.triggerLabel}\`). ` +
          "It will open a pull request, or hand the issue back to a human and say why.",
      );
    } catch (err) {
      log("status comment failed", err); // the job is already queued; the comment is best effort
    }
    return c.json({ status: "accepted", job_key: jobKey }, 202);
  });

  return app;
}
