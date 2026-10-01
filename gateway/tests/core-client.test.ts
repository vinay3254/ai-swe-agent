import { describe, expect, it } from "vitest";
import { CoreError, CoreUnavailable, HttpCoreClient } from "../src/core-client.js";

const req = { issue_url: "https://github.com/octo/app/issues/7", job_key: "k1" };
const job = { key: "k1", issue_url: req.issue_url, state: "received", pr_url: null, reason: null };

function client(respond: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchImpl = async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init: init ?? {} });
    return respond(String(url), init ?? {});
  };
  return { c: new HttpCoreClient("http://core.test", "svc-token", fetchImpl as typeof fetch), calls };
}

describe("HttpCoreClient.createJob", () => {
  it("posts the request with the bearer token and returns the accepted job", async () => {
    const { c, calls } = client(() => Response.json(job, { status: 202 }));

    const result = await c.createJob(req);

    expect(result).toEqual({ kind: "accepted", job });
    expect(calls[0]?.url).toBe("http://core.test/jobs");
    expect((calls[0]?.init.headers as Record<string, string>).Authorization).toBe("Bearer svc-token");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual(req);
  });

  it("maps 403 to forbidden and 409 to conflict", async () => {
    const forbidden = client(() => Response.json({ detail: "not allowed" }, { status: 403 }));
    expect(await forbidden.c.createJob(req)).toEqual({ kind: "forbidden", detail: "not allowed" });

    const conflict = client(() => Response.json({ detail: "key reused" }, { status: 409 }));
    expect(await conflict.c.createJob(req)).toEqual({ kind: "conflict", detail: "key reused" });
  });

  it("treats 401 and 422 as our own bug (CoreError), not as an outage", async () => {
    await expect(client(() => new Response("no", { status: 401 })).c.createJob(req)).rejects.toBeInstanceOf(CoreError);
    await expect(client(() => new Response("bad", { status: 422 })).c.createJob(req)).rejects.toBeInstanceOf(CoreError);
  });

  it("treats 5xx and network failures as unavailable so the delivery can be retried", async () => {
    await expect(client(() => new Response("x", { status: 503 })).c.createJob(req)).rejects.toBeInstanceOf(CoreUnavailable);
    const down = client(() => {
      throw new TypeError("fetch failed");
    });
    await expect(down.c.createJob(req)).rejects.toBeInstanceOf(CoreUnavailable);
  });

  it("rejects a 202 whose body is not a job", async () => {
    await expect(client(() => Response.json({ nope: 1 }, { status: 202 })).c.createJob(req)).rejects.toBeInstanceOf(CoreError);
  });
});
