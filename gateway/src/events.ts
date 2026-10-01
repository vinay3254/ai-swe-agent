import { z } from "zod";

const IssuesEvent = z.object({
  action: z.string(),
  label: z.object({ name: z.string() }).optional(),
  issue: z.object({
    id: z.number().int(),
    number: z.number().int().positive(),
    pull_request: z.unknown().optional(),
  }),
  repository: z.object({ full_name: z.string().regex(/^[\w.-]+\/[\w.-]+$/) }),
});

export type Decision =
  | { kind: "ignore"; reason: string }
  | { kind: "trigger"; repo: string; issueNumber: number; issueId: number; issueUrl: string };

export class BadPayload extends Error {}

/** Decide whether a webhook delivery should start a job. Throws BadPayload if it is malformed. */
export function decide(event: string, payload: unknown, triggerLabel: string): Decision {
  if (event !== "issues") return { kind: "ignore", reason: `event ${event} is not handled` };
  const parsed = IssuesEvent.safeParse(payload);
  if (!parsed.success) throw new BadPayload("malformed issues event");
  const e = parsed.data;
  if (e.action !== "labeled") return { kind: "ignore", reason: `action ${e.action} is not handled` };
  if (e.label?.name !== triggerLabel) return { kind: "ignore", reason: "label is not the trigger label" };
  if (e.issue.pull_request !== undefined) return { kind: "ignore", reason: "pull requests are not issues" };
  return {
    kind: "trigger",
    repo: e.repository.full_name,
    issueNumber: e.issue.number,
    issueId: e.issue.id,
    issueUrl: `https://github.com/${e.repository.full_name}/issues/${e.issue.number}`,
  };
}
