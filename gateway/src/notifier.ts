import type { Octokit } from "@octokit/rest";

export interface Notifier {
  postStatus(repo: string, issueNumber: number, jobKey: string, text: string): Promise<void>;
}

/** Hidden marker that makes a status comment idempotent per job. */
export function marker(jobKey: string): string {
  return `<!-- ai-swe-agent job:${jobKey} -->`;
}

type CommentsApi = Pick<Octokit, "paginate"> & {
  rest: { issues: Pick<Octokit["rest"]["issues"], "listComments" | "createComment"> };
};

export class GitHubNotifier implements Notifier {
  constructor(private readonly octokit: CommentsApi) {}

  async postStatus(repo: string, issueNumber: number, jobKey: string, text: string): Promise<void> {
    const [owner, name, ...rest] = repo.split("/");
    if (!owner || !name || rest.length > 0) throw new Error(`repo must be owner/name, got ${repo}`);
    const tag = marker(jobKey);
    const comments = await this.octokit.paginate(this.octokit.rest.issues.listComments, {
      owner,
      repo: name,
      issue_number: issueNumber,
      per_page: 100,
    });
    if (comments.some((c) => c.body?.includes(tag))) return;
    await this.octokit.rest.issues.createComment({
      owner,
      repo: name,
      issue_number: issueNumber,
      body: `${text}\n\n${tag}`,
    });
  }
}
