import { describe, expect, it } from "vitest";
import { GitHubNotifier, marker } from "../src/notifier.js";

function fakeOctokit(existing: string[]) {
  const created: { owner: string; repo: string; issue_number: number; body: string }[] = [];
  const octokit = {
    paginate: async (_route: unknown, _params: unknown) => existing.map((body) => ({ body })),
    rest: {
      issues: {
        listComments: () => undefined,
        createComment: async (p: { owner: string; repo: string; issue_number: number; body: string }) => {
          created.push(p);
          return {};
        },
      },
    },
  };
  return { octokit, created };
}

describe("GitHubNotifier.postStatus", () => {
  it("posts a comment carrying a hidden marker for the job key", async () => {
    const { octokit, created } = fakeOctokit(["unrelated"]);

    await new GitHubNotifier(octokit as never).postStatus("octo/app", 7, "issue-1:d", "Working on it.");

    expect(created).toHaveLength(1);
    expect(created[0]).toMatchObject({ owner: "octo", repo: "app", issue_number: 7 });
    expect(created[0]?.body).toContain("Working on it.");
    expect(created[0]?.body).toContain(marker("issue-1:d"));
  });

  it("does not post twice for the same job key", async () => {
    const { octokit, created } = fakeOctokit([`earlier\n${marker("issue-1:d")}`]);

    await new GitHubNotifier(octokit as never).postStatus("octo/app", 7, "issue-1:d", "again");

    expect(created).toEqual([]);
  });

  it("posts for a different job key on the same issue", async () => {
    const { octokit, created } = fakeOctokit([marker("issue-1:old")]);

    await new GitHubNotifier(octokit as never).postStatus("octo/app", 7, "issue-1:new", "retry");

    expect(created).toHaveLength(1);
  });

  it("rejects a repo name that is not owner/name", async () => {
    const { octokit } = fakeOctokit([]);
    await expect(new GitHubNotifier(octokit as never).postStatus("bad", 1, "k", "t")).rejects.toThrow(/owner\/name/);
  });
});
