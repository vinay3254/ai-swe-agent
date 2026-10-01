# AI software engineer agent

Two things in one repo:

1. **Issue fixer.** Label a GitHub issue `ai-fix` (or run the CLI) and an OpenHands agent, driven by Claude, writes a fix in a Docker sandbox and opens a PR. TypeSafe's Jev model makes the typed decisions around it: is the issue fixable, is the diff right, how risky is it. Low confidence escalates to a human.
2. **Watchdog.** Scans your repos for forks and copies that dropped the license attribution, reports them to you, and sends a notice only after you approve it.

Design: `docs/superpowers/specs/2026-10-01-ai-swe-agent-design.md`.

## Layout

| Path | What |
|---|---|
| `core/` | Python 3.13+, `pyright --strict`. Pipeline, Jev gate, GitHub/git/OpenHands adapters, FastAPI, watchdog, `agent` CLI. |
| `gateway/` | TypeScript. Verifies GitHub webhooks, filters on the label, calls core, posts a status comment. Types are generated from core's OpenAPI. |
| `scripts/check-drift.sh` | Fails if the gateway's contract differs from core's. Runs in CI. |

## Run it

```bash
cp .env.example .env   # fill it in, then export the variables
cd core && uv sync --all-extras

uv run agent fix https://github.com/OWNER/REPO/issues/1   # one issue, in-process
uv run agent serve                                         # HTTP API for the gateway

cd ../gateway && npm ci && npm run build && npm start     # webhook receiver on :3000
```

Point a GitHub webhook (event: Issues, content type JSON, your secret) at `https://<host>/webhook`.
The sandbox needs Docker. `agent fix` exits 0 done, 2 escalated, 1 failed.

## Jev through OpenRouter

Jev is also served by OpenRouter as `typesafe/jev-1.13`, with the same request and response format. If you have an OpenRouter key but no TypeSafe key:

```bash
export TYPESAFE_API_KEY=<your OpenRouter key>
export SWE_AGENT_JEV_BASE_URL=https://openrouter.ai/api
export SWE_AGENT_JEV_MODEL=jev-1.13
```

Checked with a real call: triage and review both returned typed answers. Jev's confidence on a trivial bug came back around 0.6-0.66 for "fixable" and "complexity", below the default `triage_min_confidence` of 0.7, so such an issue would be escalated to a human. Tune `SWE_AGENT_THRESHOLDS__TRIAGE_MIN_CONFIDENCE` from the decision log (`decisions` table) once you have real runs.

## Use OmniRoute (or another OpenAI-compatible gateway) for the LLM

OmniRoute is a local gateway (`npm install -g omniroute && omniroute`, serves `http://localhost:20128/v1`) that routes one OpenAI-style endpoint across many providers. Point the agent at it:

```bash
export SWE_AGENT_LLM_BASE_URL=http://172.17.0.1:20128/v1   # see the Docker note below
export SWE_AGENT_LLM_API_KEY=<key from the OmniRoute dashboard>
export SWE_AGENT_AGENT_MODEL=openai/<model or combo name from OmniRoute>   # the "openai/" prefix is required
```

`ANTHROPIC_API_KEY` is then not needed. This only changes the coding agent's LLM. Jev still calls TypeSafe directly.

Docker note: the agent loop runs inside the sandbox container, so `localhost` there is the container, not your machine. Start OmniRoute listening on all interfaces and use an address the container can reach: on Linux the docker bridge IP (usually `172.17.0.1`); on Docker Desktop `host.docker.internal`.

## Watchdog

```bash
uv run agent watchdog scan                 # forks + code search, records cases, prints a digest (--email to send it)
uv run agent watchdog cases                # open cases
uv run agent watchdog show 3               # evidence and the notice that would be sent
uv run agent watchdog resolve 4 --violation   # or --dismiss: decide a low-confidence case
uv run agent watchdog notice 3             # shows the issue text, asks, then opens it on the other repo
uv run agent watchdog dmca 3 --out dmca.txt   # a draft only; you submit it yourself
```

Nothing is sent without your approval. There is no auto-send. Low-confidence cases cannot be sent until you confirm them. One notice per case; a second needs `--follow-up`. Every send is logged with its text, time and approver.
Limits: public GitHub repos only; GitHub's traffic API names no one.

## Develop

```bash
cd core && uv run pytest -q && uv run pyright
cd gateway && npm test && npm run check:types
scripts/check-drift.sh                     # after changing an API route in core:
(cd core && uv run agent openapi --out ../gateway/openapi.json) && (cd gateway && npm run gen:types)
```

## Not verified end to end

Unit and integration tests use fakes and mocked HTTP (`respx`, real git against a local bare repo, real HTTP between gateway and core). These paths need your credentials and were **not** run for real:
the Jev API, the OpenHands Docker sandbox with a live LLM, and GitHub itself. Try `agent fix` on a throwaway repo first.
Known gaps: the agent server receives the LLM key (the GitHub token never enters the sandbox); remote OpenHands conversations take an iteration cap but no cost cap; files the sandbox writes are owned by root, so temp-dir cleanup can leave debris.
