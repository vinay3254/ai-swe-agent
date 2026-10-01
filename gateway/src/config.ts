import { z } from "zod";

const Env = z.object({
  GITHUB_WEBHOOK_SECRET: z.string().min(1),
  CORE_URL: z.string().url(),
  CORE_API_TOKEN: z.string().min(1),
  GITHUB_TOKEN: z.string().min(1),
  TRIGGER_LABEL: z.string().min(1).default("ai-fix"),
  PORT: z.coerce.number().int().min(1).max(65535).default(3000),
});

export interface Config {
  githubWebhookSecret: string;
  coreUrl: string;
  coreApiToken: string;
  githubToken: string;
  triggerLabel: string;
  port: number;
}

export function loadConfig(env: Record<string, string | undefined>): Config {
  const parsed = Env.safeParse(env);
  if (!parsed.success) {
    const problems = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
    throw new Error(`Invalid configuration: ${problems}`);
  }
  const e = parsed.data;
  return {
    githubWebhookSecret: e.GITHUB_WEBHOOK_SECRET,
    coreUrl: e.CORE_URL.replace(/\/+$/, ""),
    coreApiToken: e.CORE_API_TOKEN,
    githubToken: e.GITHUB_TOKEN,
    triggerLabel: e.TRIGGER_LABEL,
    port: e.PORT,
  };
}
