# agentic-sdlc

A team of Claude agents takes a free-text requirement to a GitHub pull request, the way a manual SDLC does: BA → Architect → parallel Devs → Integrator → Reviewer → QA → DevOps, with human approval gates. A React "mission control" GUI shows the run live: agent interactions, the messages passed between agents, context size, and input/output tokens.

> **Status:** under construction. Progress is tracked in the [milestones](https://github.com/gagandeepp/agentic-sdlc/wiki/Milestones).

- Design spec: [docs/superpowers/specs/2026-10-13-agentic-sdlc-design.md](docs/superpowers/specs/2026-10-13-agentic-sdlc-design.md)
- Design discussion: [#1](https://github.com/gagandeepp/agentic-sdlc/discussions/1)

## Quick start

Requires Docker.

```bash
cp .env.example .env
docker compose up --build
```

Open http://localhost:5173. API health: http://localhost:8080/api/health.

## Development

| | |
|---|---|
| API | `docker compose up -d mongo`, then `cd api && uv sync && uv run uvicorn app.main:app --reload --port 8080` |
| Web | `cd web && corepack pnpm install && corepack pnpm dev` (proxies `/api` to :8080) |
| Tests | `make test` (API tests start MongoDB with testcontainers, so Docker must be running) |
| Lint / types | `make lint` |
| Live agent test | `cd api && RUN_LIVE=1 uv run pytest tests/live` (needs `AGENT_ANTHROPIC_KEY` in `.env`; spends about $0.10–0.30) |

## Agents (M3)

Each SDLC role runs as a Claude Agent SDK session (the bundled Claude Code CLI):

- **No keys in agents.** In the container, sessions run as a separate `agent` user with an allowlisted environment. They reach the API only through a local credential proxy, using a placeholder token.
- **Exact metering.** The proxy reads usage from every response, emits `agent.usage` and `budget.updated` events (tokens, cost, context fill), and stops a run at its budget.
- **NeMo Guardrails** checks every Bash command (YARA rules: pushes, env dumps, `sudo`, `curl | sh`, ...) and scans tool output for prompt injection. File tools are confined to the workspace.
- **Fail closed outside Docker.** Without the separate `agent` user, roles that can run shell commands refuse to start unless `ALLOW_UNISOLATED_BASH=true`.

Try one real agent (needs `AGENT_ANTHROPIC_KEY` in `.env`, costs about $0.10):

```bash
docker compose up --build -d
curl -s -X POST localhost:5173/api/dev/agent-probe -H 'content-type: application/json' \
  -d '{"role": "ba", "requirement": "Add a low-stock report"}'
```

Open the returned run at http://localhost:5173/runs/<id>.

## Orchestrator (M4)

A run takes a requirement through BA → **requirements gate** → Architect → **design gate** → Dev ⇄ Review (≤3 fix rounds) ⇄ QA (≤2) → push → DevOps → **draft PR** → CI → **release gate** → PR ready for review.

- **Gates:** Approve, Request changes (the phase re-runs with your feedback) or Edit (your markdown goes to every later agent). Loops that hit their limit, and agent failures, open an escalation gate.
- **Durable:** each step saves a checkpoint. If the API restarts, the run shows **interrupted**; **Resume** continues from the last checkpoint. A pending gate re-opens without re-running its agent.
- **Limits:** a hard budget enforced by the credential proxy, and a 50-minute limit on active time (gate and CI waits don't count).
- **Guardrails (NeMo):**
  - the requirement and gate feedback pass an input rail, a Claude Haiku self-check billed to the run, which shows as the `guardrails` agent;
  - everything published to GitHub has keys, tokens and active content removed;
  - agents can't push, can't change CI workflows, and can't touch the git dir: it lives outside their working tree and only the API can write it.

Try it without keys (scripted agents, a local git remote and an in-memory GitHub): `docker compose up --build -d`, open http://localhost:5173, type a requirement and choose **Scripted demo (no keys)**.

Live runs need `AGENT_ANTHROPIC_KEY` and `AGENT_GITHUB_TOKEN` in `.env`. The token should be a fine-grained PAT for the target repo only, with Contents RW, Pull requests RW, Checks R and Commit statuses R. Choose **Start run**; the default target is [agentic-sdlc-sandbox](https://github.com/gagandeepp/agentic-sdlc-sandbox). `make smoke` runs one live run end to end (about $1–2).
