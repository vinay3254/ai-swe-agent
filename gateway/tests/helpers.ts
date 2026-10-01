import { createHmac } from "node:crypto";
import type { Config } from "../src/config.js";
import type { CoreClient, CreateJobResult } from "../src/core-client.js";
import type { Notifier } from "../src/notifier.js";
import type { components } from "../src/generated/core.js";

export const SECRET = "whsec";

export const config: Config = {
  githubWebhookSecret: SECRET,
  coreUrl: "http://core.test",
  coreApiToken: "svc",
  githubToken: "ghp_x",
  triggerLabel: "ai-fix",
  port: 3000,
};

export function sign(body: string, secret = SECRET): string {
  return "sha256=" + createHmac("sha256", secret).update(body).digest("hex");
}

export function labeledPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    action: "labeled",
    label: { name: "ai-fix" },
    issue: { id: 555, number: 7, html_url: "https://github.com/octo/app/issues/7" },
    repository: { full_name: "octo/app" },
    ...overrides,
  };
}

export class FakeCore implements CoreClient {
  calls: components["schemas"]["JobRequest"][] = [];
  result: CreateJobResult | Error = {
    kind: "accepted",
    job: {
      key: "k",
      issue_url: "https://github.com/octo/app/issues/7",
      state: "received",
      pr_url: null,
      reason: null,
    },
  };
  async createJob(req: components["schemas"]["JobRequest"]): Promise<CreateJobResult> {
    this.calls.push(req);
    if (this.result instanceof Error) throw this.result;
    return this.result;
  }
}

export class FakeNotifier implements Notifier {
  posted: { repo: string; issue: number; key: string; text: string }[] = [];
  error: Error | null = null;
  async postStatus(repo: string, issue: number, key: string, text: string): Promise<void> {
    if (this.error) throw this.error;
    this.posted.push({ repo, issue, key, text });
  }
}
