import { z } from "zod";
import type { components } from "./generated/core.js";

export type JobRequest = components["schemas"]["JobRequest"];
export type JobResponse = components["schemas"]["JobResponse"];

export type CreateJobResult =
  | { kind: "accepted"; job: JobResponse }
  | { kind: "forbidden"; detail: string }
  | { kind: "conflict"; detail: string };

export interface CoreClient {
  createJob(request: JobRequest): Promise<CreateJobResult>;
}

/** Core answered, but not in a way a retry can fix: wrong token, bad request, broken contract. */
export class CoreError extends Error {}
/** Core could not be reached or failed with 5xx. A redelivery may succeed. */
export class CoreUnavailable extends Error {}

const Job = z.object({
  key: z.string(),
  issue_url: z.string(),
  state: z.string(),
  pr_url: z.string().nullable(),
  reason: z.string().nullable(),
});

async function detailOf(res: Response): Promise<string> {
  const text = await res.text();
  try {
    const body: unknown = JSON.parse(text);
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    // not JSON: fall through to the raw text
  }
  return text;
}

export class HttpCoreClient implements CoreClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  async createJob(request: JobRequest): Promise<CreateJobResult> {
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}/jobs`, {
        method: "POST",
        headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
    } catch (err) {
      throw new CoreUnavailable(`core unreachable: ${String(err)}`);
    }
    if (res.status === 202) {
      const parsed = Job.safeParse(await res.json().catch(() => null));
      if (!parsed.success) throw new CoreError("core returned a 202 that is not a job");
      return { kind: "accepted", job: parsed.data as JobResponse };
    }
    if (res.status === 403) return { kind: "forbidden", detail: await detailOf(res) };
    if (res.status === 409) return { kind: "conflict", detail: await detailOf(res) };
    if (res.status >= 500) throw new CoreUnavailable(`core failed with HTTP ${res.status}`);
    throw new CoreError(`core rejected the request with HTTP ${res.status}`);
  }
}
