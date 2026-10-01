# AI Software Engineer Agent: Design

Date: 2026-10-01
Status: Draft, awaiting review

## 1. Purpose

Build an autonomous coding assistant that resolves GitHub issues and a companion watchdog that detects license-violating copies of the owner's repositories.

Stated by the owner:
- Autonomous agent that takes a GitHub issue, navigates the codebase, writes a fix, and opens a PR.
- Uses OpenHands as the agent framework, MCP for tools, and the GitHub API.
- Typesafe, using TypeSafe AI's Jev model (https://typesafe.ai/) for typed decisions.
- Triggered both by GitHub webhook and by CLI.
- Watchdog reports non-collaborators who use the owner's repo in violation of its license, and can send a notice after owner approval.

Assumptions (not stated, confirm in review):
- Claude is the LLM that drives OpenHands.
- The audience is the owner's own repositories, listed in an allowlist.
- Success is measured by eval results (section 9), not by feature count.

## 2. Key constraint: what Jev is

Jev is a "System One" model. It takes state plus typed questions and returns a `choice`, `score`, or Noul (yes/no probability), each with probabilities and a 0-1 `confidence`. It does not generate text, write code, or edit files. It is not a replacement for the LLM behind OpenHands.

Consequence: an LLM writes code. Jev makes the typed decisions around it. Jev appears in triage, review gating, and watchdog classification. It is never asked to generate.

Available SDKs: Python and JavaScript/TypeScript. Exact endpoint and auth details were not in the fetched docs; read `docs.typesafe.ai/api.md` and `docs.typesafe.ai/sdk/python.md` before implementing `jev_gate`.

## 3. Architecture

Three units sharing one contract.

- **core** (Python, `pyright --strict`, Pydantic). FastAPI service plus pipeline. Owns the OpenHands SDK runner, the Jev client, GitHub and MCP wiring, the watchdog, and persistence. Exposes `/jobs` and watchdog endpoints. Its OpenAPI schema is the source of truth for all types.
- **gateway** (TypeScript, Zod, Octokit). Receives GitHub webhooks, verifies the signature, filters on the `ai-fix` label, calls core. Posts status comments. Types are generated from core's OpenAPI with `openapi-typescript`. No hand-written duplicate types.
- **cli** (Python, Typer). `agent fix <issue-url>`, `agent watchdog scan`, `agent watchdog notice <case-id>`. Runs the same pipeline code in-process as the webhook path.

### Core modules

| Module | Purpose | Depends on |
|---|---|---|
| `jev_gate` | Wraps Jev SDK. Returns `Decision[T]` with `value`, `confidence`, `probabilities`. Threshold logic is pure functions. | Jev SDK, `config` |
| `runner` | Wraps OpenHands SDK. Input: repo path, issue text, tool list. Output: diff, test result, transcript. | OpenHands SDK, Docker |
| `github` | Clone, branch, PR, comment, fork and collaborator listing, code search. | GitHub API, GitHub MCP server |
| `pipeline` | State machine for one issue job. | `jev_gate`, `runner`, `github` |
| `watchdog` | Scheduled scan, verdicts, case store, notice flow. | `jev_gate`, `github` |
| `store` | SQLite persistence for jobs, cases, decision logs, send audit. | none |
| `config` | Pydantic settings: thresholds, model, timeouts, repo allowlist. | none |

## 4. Issue-fix pipeline

States: `triaged`, `running`, `reviewed`, `done`, `escalated`, `failed`.

1. Fetch issue and repo context.
2. **Jev triage.** Choice: bug / feature / question / unclear. Score: complexity. Noul: agent-fixable. Any low-confidence answer, or not fixable: comment, label `needs-human`, stop.
3. **OpenHands run** in a Docker sandbox on a fresh branch. The LLM writes the fix and runs tests. GitHub MCP and a repo-navigation MCP server are available as tools.
4. **Jev review gate.** Noul: diff addresses the issue. Score: risk. Test pass/fail is a deterministic code check, not a Jev question.
5. **Outcome by thresholds:** ready PR, draft PR, or escalate with report.

Thresholds live in one typed config file. Every Jev answer is logged with its confidence so thresholds can be tuned from data.

## 5. Watchdog

Runs on a schedule in core. Scans repos in the allowlist. Uses public data only.

### Violation definition

A violation is a copy or fork of the owner's repo that is derived from it and does not preserve the attribution or license terms the license requires (for example, the MIT copyright notice stripped). A plain fork that keeps the license is not a violation.

### Scan steps per repo

1. **Fork check.** List forks. Compare owner to the collaborator list. Non-collaborator forks become candidates.
2. **Copy check.** At setup, build fingerprints from the repo: hashes of key files and distinctive lines. Run GitHub code search for them. Matches outside the owner's own forks become candidates.
3. **License check (deterministic).** Fetch the candidate's `LICENSE`, `NOTICE`, README. Check that the original copyright line is present.
4. **Jev for fuzzy parts.** Noul: derived from original. Score: similarity level. Noul: attribution preserved when text was reworded.
5. **Verdict.** `violation` = derived (high confidence) and attribution missing. Low confidence goes to a human review queue. Never auto-flagged.

### Report

Evidence bundle per case: candidate URL, matched files, commit SHAs, timestamps, license check result, Jev confidences. Stored in SQLite. Delivered as an email digest to the owner plus ready-to-send draft issue text.

### Notice sending (owner-approved only)

1. A case reaches `violation`. It appears in the digest.
2. The owner reviews the evidence and approves with the CLI (`agent watchdog notice <case-id>`). There is no dashboard in v1.
3. The tool sends. There is no auto-send mode at any confidence level.

Notice types:
- **GitHub issue on the violator's repo.** Fixed factual template: which files, which license, what attribution is missing, how to fix. Sent with the owner's GitHub token. Default.
- **DMCA takedown.** The tool drafts the notice. The owner submits it. It contains a statement under penalty of perjury and must come from the owner.
- **No email scraping.** Addresses are not harvested from commits. Contact goes through GitHub.

Safeguards:
- Every send is logged: case id, text, timestamp, owner approval.
- One notice per case. A follow-up needs new approval.
- Low-confidence cases cannot reach the send step until a human upgrades or dismisses them.

### Limits

- Cannot see private repos or non-GitHub hosts. Reports state this.
- GitHub traffic API gives aggregate clone/view counts for 14 days, with no identities. The watchdog cannot tell who cloned.
- Handles API rate limits with backoff and scan cursors.

## 6. Error handling

- Jev API failure or timeout: retry via SDK `RetryPolicy`, then escalate to a human. Never fall through to "proceed".
- Agent exceeds step or time budget: capture partial diff, open a draft PR labeled `incomplete`.
- Sandbox crash: job marked `failed`, issue comment with reason. No silent drops.
- Webhook replay: job keyed by issue id plus label event id. Idempotent.
- Safety: repo allowlist, the agent never pushes to the default branch, secrets are not in the sandbox environment.

## 7. Type contract

core OpenAPI is the source of truth. gateway types are generated, never written by hand. Jev answers are wrapped in `Decision[T]` so low-confidence handling cannot be skipped by type. CI fails if generated TS types drift from core's OpenAPI.

## 8. Repository layout

```
ai-swe-agent/
  core/        Python package (pyproject, src/, tests/)
  gateway/     TypeScript service (package.json, src/, tests/)
  cli/         Typer entry points (may live inside core/)
  docs/superpowers/specs/
```

## 9. Testing and evaluation

- **Unit:** threshold logic, state transitions, schema round-trip (Pydantic to OpenAPI to TS type).
- **Contract:** generated TS types match core OpenAPI in CI.
- **Integration:** fake GitHub plus recorded Jev responses. Real OpenHands against a small fixture repo with seeded bugs.
- **Issue-fix eval:** 10-20 issues from a public repo. Metrics: fix rate, and Jev gate precision (did the gate block bad PRs and pass good ones).
- **Watchdog eval:** seeded fixture forks and copies, with and without attribution. Metric: precision of `violation` verdicts. A false positive is the costly error, since a wrong notice harms a third party.

## 10. Out of scope for v1

- Jev ranking files or symbols to narrow context (navigation assist). Stretch goal.
- Jev choosing each agent step. Not tuned for step control.
- Web dashboard. Digest and CLI only.
- Automatic notice or takedown submission.
- Non-GitHub hosts, private repos.

## 11. Open points for review

- Confirm Claude as the OpenHands LLM.
- Confirm Jev early-access is available to the owner (console.typesafe.ai). Without it, `jev_gate` needs a stub for development.
- Confirm email delivery mechanism (SMTP provider or local sendmail).
